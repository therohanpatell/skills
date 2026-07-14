#!/usr/bin/env python3
"""Virtual camera bridge: raw NV12 frames on stdin -> OBS Virtual Camera.

This tiny process is the only Python in the system. It exists because
pyvirtualcam is the one mature implementation of the OBS Virtual Camera
shared-memory protocol, which lets us feed the universally-supported
"OBS Virtual Camera" device without running OBS itself.

Design notes:
  * A reader thread blocks on stdin and always keeps only the LATEST frame
    (a 1-slot mailbox) — if the consumer ever lags, old frames are dropped,
    never buffered.
  * The main loop paces output at the configured fps and REPEATS the last
    frame when no new one arrives, so the camera stays alive and smooth
    across program switches and upstream restarts.
  * Node always writes whole frames (it frame-aligns the FFmpeg output), so
    a plain read of exactly frame_size bytes is safe.

Exit codes: 2 = bad args, 3 = pyvirtualcam missing, 4 = no virtual camera
device (OBS not installed / vcam driver unregistered).
"""

import argparse
import sys
import threading


def read_exact(stream, n):
    """Read exactly n bytes or return None on EOF."""
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def main():
    parser = argparse.ArgumentParser(description="stdin NV12 -> OBS Virtual Camera")
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=int, required=True)
    args = parser.parse_args()

    try:
        import numpy as np
        import pyvirtualcam
        from pyvirtualcam import PixelFormat
    except ImportError as exc:
        print(f"FATAL: missing dependency: {exc}. Run: pip install pyvirtualcam numpy",
              file=sys.stderr, flush=True)
        sys.exit(3)

    frame_size = args.width * args.height * 3 // 2  # NV12 = 1.5 bytes/px
    stdin = sys.stdin.buffer

    # 1-slot mailbox guarded by a lock: reader overwrites, sender snapshots.
    latest = {"frame": None}
    lock = threading.Lock()
    eof = threading.Event()

    def reader():
        while True:
            data = read_exact(stdin, frame_size)
            if data is None:
                eof.set()
                return
            arr = np.frombuffer(data, dtype=np.uint8)
            with lock:
                latest["frame"] = arr

    threading.Thread(target=reader, daemon=True).start()

    # Standby slate until the first real frame arrives: black NV12.
    black = np.zeros(frame_size, dtype=np.uint8)
    black[: args.width * args.height] = 16
    black[args.width * args.height:] = 128
    last_frame = black

    try:
        cam = pyvirtualcam.Camera(
            width=args.width, height=args.height, fps=args.fps, fmt=PixelFormat.NV12
        )
    except Exception as exc:
        print(f"FATAL: could not open virtual camera: {exc}. "
              "Is OBS Studio installed (its Virtual Camera driver is required)?",
              file=sys.stderr, flush=True)
        sys.exit(4)

    print(f"vcam bridge live on '{cam.device}' "
          f"{args.width}x{args.height}@{args.fps} NV12", file=sys.stderr, flush=True)

    with cam:
        while True:
            with lock:
                frame = latest["frame"]
                latest["frame"] = None
            if frame is not None:
                last_frame = frame
            cam.send(last_frame)
            cam.sleep_until_next_frame()
            # Exit only when the feeder is gone AND its final frames are drained;
            # Node keeps our stdin open across FFmpeg restarts, so EOF here means
            # the whole application is shutting down.
            if eof.is_set() and latest["frame"] is None:
                print("stdin closed, exiting", file=sys.stderr, flush=True)
                return


if __name__ == "__main__":
    main()
