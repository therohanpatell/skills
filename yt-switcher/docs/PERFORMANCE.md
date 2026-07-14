# Performance & 24/7 Operation Guide

## Why this app is light by construction

- **Single active decode.** Exactly one FFmpeg process decodes exactly one
  stream (the Program). The other 19 sources are cached JPEG thumbnails —
  zero CPU, zero network, zero timers.
- **Hardware decode by default** (`d3d11va`, works on Intel/AMD/NVIDIA). If it
  fails the pipeline retries once in software automatically.
- **No re-encoding.** The vcam path is decode → scale → raw frames; the only
  encode anywhere is the tiny 360p MJPEG UI preview (~1–2 % CPU).
- **Push, never poll.** One WebSocket carries all state/stats; the UI redraws
  only changed DOM nodes.
- **Bounded memory everywhere.** Frame hand-off is a 1-slot latest-frame
  mailbox (drops, never queues); the MJPEG parser caps its buffer at 5 MB;
  slow preview clients get frames dropped past 2 MB of backlog; mpv's demuxer
  cache is capped at 64 MiB.

## Expected footprint (1080p live source → 720p30 vcam)

| Process | RAM | CPU (typical quad-core) |
|---|---|---|
| Node/Fastify | ~50 MB | <1 % |
| FFmpeg (hw decode) | ~120 MB | 3–8 % |
| vcam bridge (Python) | ~25 MB | 1–2 % |
| mpv monitor (optional) | ~150 MB | 2–5 % |
| Browser tab | ~80 MB | 1–2 % |

The side panel shows live per-process CPU/RAM plus NVIDIA GPU/decoder
utilization when `nvidia-smi` is present.

## Tuning knobs

| Goal | Change |
|---|---|
| Lowest possible footprint | Toggle the mpv monitor OFF (side panel) → single decode, no audio |
| Less CPU on the vcam path | Keep 1280×720@30 (default). 1080p roughly doubles raw-frame bandwidth (~93 MB/s vs 41 MB/s) |
| Lower bandwidth | Reduce `resolve.maxVideoHeight` in `config/default.json` (e.g. 720) — yt-dlp then picks smaller source streams |
| Lighter UI preview | Lower `preview.fps` / `preview.width` in config |
| Specific GPU decoder | `YTSW_FFMPEG_HWACCEL=cuda` (NVIDIA), `qsv` (Intel), `auto`, or `none` |
| No vcam at all (preview-only rig) | `YTSW_VCAM_ENABLED=false` |

## 24/7 checklist

1. **Power settings:** disable USB selective suspend and display-off GPU
   throttling on the broadcast PC; set the Windows power plan to High
   performance.
2. **Keep yt-dlp fresh** (`yt-dlp -U` weekly) — YouTube changes break old
   versions and manifest as `RESOLVE_FAILED` errors.
3. **Live URL refresh is automatic** (default every 3 h, plus instant
   re-resolve when a stream 403s), so day-long streams keep playing.
4. **Crash containment:** every child process is supervised with exponential
   backoff and a circuit breaker (6 failures/2 min stops the restart storm and
   surfaces "failed" in the UI; "Reset bridge" re-arms it).
5. **Logs** rotate nothing by default but are plain JSON lines in `logs/app.log`;
   for multi-week runs add a scheduled task that truncates it nightly.
6. **Soak-test before the event:** run your exact source list for a few hours
   and watch the side-panel RAM numbers — they should be flat after the first
   few minutes.

## Known scaling limits

- The raw-frame pipe (FFmpeg → Node → bridge) moves ~41 MB/s at 720p30 NV12.
  Node handles this at ~1–2 % CPU; at 4K60 it would become the bottleneck —
  stay at or below 1080p30 for the vcam format.
- MJPEG preview cost scales with connected browser tabs; each open tab is an
  extra ~0.5 MB/s. Close unused tabs on show machines.
