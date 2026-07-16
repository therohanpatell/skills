#!/usr/bin/env python3
"""Virtual camera bridge: raw NV12 frames on stdin -> Virtual Camera Device.

This bridge uses pyvirtualcam to feed frames to any supported virtual camera
device. On Windows the supported backends are:
  - "obs"          → "OBS Virtual Camera"  (the main OBS vcam driver, uses NV12 directly)
  - "unitycapture" → "Unity Video Capture"  (or custom Unity Capture devices, uses RGB format)

Design notes:
  * A reader thread blocks on stdin and always keeps only the LATEST frame
    (a 1-slot mailbox) — if the consumer ever lags, old frames are dropped,
    never buffered.
  * The main loop sends frames as fast as they arrive from FFmpeg (FFmpeg
    controls the frame rate via the fps= filter). When no new frame arrives
    within one frame period, the last frame is REPEATED so the camera stays
    alive and smooth across program switches and upstream restarts.
  * Auto-detects whether the target device expects NV12 or RGB/RGBA, and
    performs ultra-fast high-performance color conversion using OpenCV if required.
"""

import argparse
import ctypes
import os
import sys
import threading
import time
from collections import deque

try:
    import numpy as np
    import pyvirtualcam
    from pyvirtualcam import PixelFormat
except ImportError as exc:
    print(f"FATAL: missing dependency: {exc}. Run: pip install pyvirtualcam numpy",
          file=sys.stderr, flush=True)
    sys.exit(3)

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


def read_exact_into(stream, buf):
    """Read exactly len(buf) bytes into the pre-allocated buffer or return False on EOF."""
    n = len(buf)
    view = memoryview(buf)
    pos = 0
    while pos < n:
        try:
            nbytes = stream.readinto(view[pos:])
            if not nbytes:
                return False
            pos += nbytes
        except Exception:
            return False
    return True


def validate_nv12_frame(data, width, height):
    """Quick sanity check on NV12 frame data."""
    y_size = width * height
    if len(data) != y_size * 3 // 2:
        return False

    sample_step = max(1, y_size // 64)
    all_zero = True
    for i in range(0, y_size, sample_step):
        if data[i] != 0:
            all_zero = False
            break
    if all_zero:
        return False

    return True


def main():
    parser = argparse.ArgumentParser(description="stdin NV12 -> Virtual Camera")
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=int, required=True)
    parser.add_argument("--device", type=str, default=None,
                        help="Virtual camera device name (e.g. 'Unity Video Capture' or 'OBS Virtual Camera')")
    args = parser.parse_args()

    frame_size = args.width * args.height * 3 // 2  # NV12 = 1.5 bytes/px

    # Open stdin in binary unbuffered mode for lowest latency.
    try:
        stdin = os.fdopen(sys.stdin.fileno(), 'rb', buffering=0)
    except Exception:
        stdin = sys.stdin.buffer

    # Jitter buffer: a bounded FIFO of ~1 second of frames. The old 1-slot
    # mailbox made live playback rubber-band — when FFmpeg burst ahead the
    # extra frames were dropped (looked fast), when the network hiccuped the
    # last frame repeated (looked slow). A FIFO drained at exactly `fps`
    # absorbs both directions of jitter; when full, the OLDEST frame is
    # dropped so latency stays bounded (~1s worst case).
    queue = deque(maxlen=max(args.fps, 10))
    read_buf = bytearray(frame_size)
    lock = threading.Lock()
    eof = threading.Event()

    def reader():
        """Blocking reader: fills the scratch buffer, copies into the queue."""
        while True:
            if not read_exact_into(stdin, read_buf):
                eof.set()
                return
            if not validate_nv12_frame(read_buf, args.width, args.height):
                continue
            # Copy: queue entries must not alias the reused scratch buffer.
            arr = np.frombuffer(read_buf, dtype=np.uint8).reshape(
                (args.height * 3 // 2, args.width)).copy()
            with lock:
                queue.append(arr)

    threading.Thread(target=reader, daemon=True).start()

    # Standby slate: black NV12
    black = np.zeros((args.height * 3 // 2, args.width), dtype=np.uint8)
    black[: args.height, :] = 16
    black[args.height :, :] = 128
    last_frame = black

    # Build Camera kwargs
    cam_kwargs = {
        "width": args.width,
        "height": args.height,
        "fps": args.fps,
        "print_fps": False,
    }
    if args.device and args.device not in ("auto", ""):
        cam_kwargs["device"] = args.device

    # OBS Virtual Camera backend uses NV12 natively via shared memory.
    # Unity Video Capture (and all other DirectShow virtual cameras) work through
    # the DirectShow filter chain which outputs ARGB — pyvirtualcam handles the
    # conversion internally when you feed it RGB, so we must use RGB for those.
    is_obs_vcam = (args.device or "").strip().lower() in ("obs virtual camera", "")
    need_rgb_conversion = not is_obs_vcam

    cam = None
    target_fmt = PixelFormat.NV12 if is_obs_vcam else PixelFormat.RGB

    try:
        cam = pyvirtualcam.Camera(fmt=target_fmt, **cam_kwargs)
    except Exception as exc_primary:
        # If the primary format failed, try the other one before giving up
        try:
            alt_fmt = PixelFormat.RGB if is_obs_vcam else PixelFormat.NV12
            cam = pyvirtualcam.Camera(fmt=alt_fmt, **cam_kwargs)
            need_rgb_conversion = not need_rgb_conversion  # flipped
        except Exception:
            print(f"FATAL: could not open virtual camera '{args.device}': {exc_primary}",
                  file=sys.stderr, flush=True)
            sys.exit(4)

    dev_name = getattr(cam, "device", args.device or "default")
    fmt_str = "RGB→ARGB (DirectShow)" if need_rgb_conversion else "NV12 (native OBS)"
    print(f"vcam bridge live on '{dev_name}' "
          f"{args.width}x{args.height}@{args.fps} [{fmt_str}]", file=sys.stderr, flush=True)

    def prepare_payload(nv12_frame):
        if need_rgb_conversion:
            if HAS_CV2:
                return cv2.cvtColor(nv12_frame, cv2.COLOR_YUV2RGB_NV12)
            else:
                # Fallback: copy Y plane into all RGB channels (grayscale, no cv2)
                rgb = np.zeros((args.height, args.width, 3), dtype=np.uint8)
                rgb[:, :, :] = nv12_frame[: args.height, :, None]
                return rgb
        else:
            return nv12_frame.reshape(-1)


    # Self-test: send 3 black frames to verify camera handshake
    try:
        for _ in range(3):
            cam.send(prepare_payload(black))
            cam.sleep_until_next_frame()
        print("vcam self-test passed (3 black frames)", file=sys.stderr, flush=True)
    except Exception as exc:
        print(f"WARNING: vcam self-test failed: {exc}", file=sys.stderr, flush=True)

    # Set high process priority on Windows
    try:
        if sys.platform == 'win32':
            ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ABOVE_NORMAL_PRIORITY_CLASS,
            )
    except Exception:
        pass

    frames_sent = 0
    frames_repeated = 0
    last_report = time.monotonic()

    with cam:
        while True:
            with lock:
                frame = queue.popleft() if queue else None
                queue_empty = not queue

            if frame is not None:
                last_frame = frame
                frames_sent += 1
            else:
                frames_repeated += 1

            payload = prepare_payload(last_frame)
            cam.send(payload)
            cam.sleep_until_next_frame()

            now = time.monotonic()
            if now - last_report >= 10.0:
                total = frames_sent + frames_repeated
                pct_new = (frames_sent / total * 100) if total else 0
                print(f"vcam perf: {frames_sent} new + {frames_repeated} repeated "
                      f"in {now - last_report:.1f}s ({pct_new:.0f}% fresh, "
                      f"queue {len(queue)})",
                      file=sys.stderr, flush=True)
                frames_sent = 0
                frames_repeated = 0
                last_report = now

            if eof.is_set() and queue_empty:
                print("stdin closed, exiting", file=sys.stderr, flush=True)
                return


if __name__ == "__main__":
    main()
