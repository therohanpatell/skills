# YT Switcher

A lightweight, local Windows application that routes up to 20 YouTube videos or
live streams like a broadcast switcher: preview cards, one-click Program
switching, and the selected Program exposed as a **Windows Virtual Camera**
usable in OBS Studio, vMix, Teams, Zoom, Discord, Google Meet, and anything
else that accepts a webcam.

Built for 24/7 reliability and minimal resource use: **only the selected
Program source is ever decoded** — idle sources are just cached thumbnails.

```
Browser UI (vanilla JS)  ←WebSocket/MJPEG→  Node.js + Fastify
                                              ├─ yt-dlp   (URL resolution, exits immediately)
                                              ├─ FFmpeg   (ONE instance, hw decode → raw NV12 + MJPEG preview)
                                              ├─ bridge   (raw frames → OBS Virtual Camera, holds frame on switch)
                                              └─ mpv      (optional full-quality monitor window + audio, JSON IPC)
```

See [ARCHITECTURE.md](../ARCHITECTURE.md) for the full design rationale and
trade-off analysis.

## Quick start (Windows)

```powershell
# one-time setup: installs/verifies node, python, yt-dlp, ffmpeg, mpv, pyvirtualcam
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1

npm start
# open http://127.0.0.1:8300
```

**Prerequisite for the virtual camera:** install OBS Studio once
(`winget install OBSProject.OBSStudio`), start its Virtual Camera once from
inside OBS, then close OBS forever — its signed driver is what this app feeds
directly. In Teams/Zoom/etc. select the camera named **"OBS Virtual Camera"**.

## Using it

1. Paste a YouTube video or live URL into **Add Source** (up to 20).
2. Cards populate with thumbnail, title, LIVE badge, and status.
3. Click a card — it becomes Program: the virtual camera, the in-browser
   Program panel, and the mpv monitor window all switch. During the ~1 s
   handover the camera holds the last frame (never goes black/disconnected).
4. Transport controls (play/pause/stop/mute/volume/fullscreen) drive mpv.
5. Everything (sources, selection, volume) is persisted and restored on restart.

**Audio note:** virtual cameras are video-only by definition (true of every
vcam product). Program audio plays through the mpv monitor; to send audio into
a call, install [VB-Audio Cable](https://vb-audio.com/Cable/) and point mpv's
audio at it (see `docs/INSTALL.md`).

## Configuration

Copy `.env.example` to `.env`. Key settings: port, binary paths, vcam
resolution/fps (default 1280×720@30), hardware decoder (`d3d11va` default,
auto-falls-back to software), mpv monitor on/off. Full reference in
[.env.example](.env.example) and `config/default.json`.

## Documentation

| Doc | Contents |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Full Windows installation & audio routing |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | Tuning, expected footprint, 24/7 soak guidance |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Every failure mode and its fix |
| [docs/FUTURE.md](docs/FUTURE.md) | Roadmap / enhancement ideas |

## Development

```bash
npm run dev          # auto-restart on change
# run headless (no vcam/mpv) on any OS:
YTSW_VCAM_ENABLED=false YTSW_MONITOR_ENABLED=false npm start
```

No build step — the frontend is plain ES modules served statically.
