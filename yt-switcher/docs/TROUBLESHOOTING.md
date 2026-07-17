# Troubleshooting

Logs: `logs/app.log` (JSON lines). Raise verbosity with `YTSW_LOG_LEVEL=debug`.

## Virtual camera

**"OBS Virtual Camera" doesn't appear in Teams/Zoom/OBS**
- OBS Studio must be installed once and its Virtual Camera started once from
  inside OBS (registers the DirectShow filter). Re-run
  `scripts\setup-windows.ps1` — it verifies and probes the driver.
- Fully restart the target app (Teams/Zoom enumerate cameras only at launch).

**Camera shows black**
- Black is the deliberate "no program" slate. Select a source card.
- Side panel → VIRTUAL CAMERA → Status must be `running`. If `failed`, click
  **Reset bridge** and check the log for the bridge's stderr (commonly:
  `pyvirtualcam` not installed for the Python on `YTSW_PYTHON_PATH`).

**Camera works in OBS but not Zoom/Teams**
- Some apps run in privacy-restricted mode: check Windows Settings → Privacy →
  Camera → allow desktop apps.
- Zoom < 5.x and some store-packaged apps only enumerate Media Foundation
  cameras; use the current desktop client versions.

**Frozen image after a switch**
- 0.5–1.5 s of held frame during a switch is by design. If it stays frozen,
  the new FFmpeg failed — the Program overlay will show the error; check
  `logs/app.log` for the ffmpeg stderr tail.

## Sources / yt-dlp

| Card error | Meaning / fix |
|---|---|
| `Not a valid YouTube URL` | Only youtube.com/youtu.be video, live, or shorts URLs are accepted |
| `Video is private` / `removed` | Nothing to do — the owner restricted it |
| `Region blocked` | Content is geo-restricted for your IP |
| `Age restricted` | Needs a signed-in session; pass cookies to yt-dlp (add `--cookies-from-browser chrome` support is a planned enhancement) |
| `Live stream has ended` | The broadcast finished; remove the card or wait for a new URL |
| `Stream has not started yet` | Premieres/scheduled live — add it again once live |
| `Failed to resolve video` | Usually an outdated yt-dlp: run `yt-dlp -U` |
| `yt-dlp not found` | Install it or set `YTSW_YTDLP_PATH` in `.env` |

## Playback

**Program never reaches "playing", overlay stuck on CONNECTING**
- Check ffmpeg is on PATH (`ffmpeg -version`) or set `YTSW_FFMPEG_PATH`.
- Try `YTSW_FFMPEG_HWACCEL=none` — if that fixes it, your GPU driver's
  d3d11va decode is broken; update GPU drivers, then switch back.

**Stuttering / dropped frames on the vcam**
- Confirm hardware decode is active: side panel GPU-decode % > 0 (NVIDIA), or
  ffmpeg CPU under ~10 %. Software-decoding 1080p60 on a weak CPU stutters.
- Lower `resolve.maxVideoHeight` to 720 in `config/default.json`.

**No audio**
- Click **🔊 Test audio (beep)** in the side panel first — it pinpoints the
  culprit in one shot:
  - `mpv: ERROR: mpv not found` → set `YTSW_MPV_PATH` in `.env` to the full
    exe path (e.g. `C:\Tools\mpv\mpv.exe`) and **restart the server** —
    `.env` is only read at startup. Common folders (C:\Tools\mpv,
    Program Files, winget/scoop/choco) are also auto-detected.
  - beep NOT audible → Windows output-device problem, not the app.
  - `IPC: NOT CONNECTED` with a valid mpv version → check the mpv.log tail
    shown under the button.
- MONITOR status must be `running` and not muted. The virtual camera itself
  never carries audio (Windows platform limitation for ALL virtual cameras).

**Audio in OBS's mixer**
- The vcam is video-only, so no meter will ever move for the Video Capture
  Device source. Program audio reaches OBS one of two ways:
  1. **Desktop Audio** (default): once mpv plays through your speakers, the
     Desktop Audio meter moves — nothing to configure.
  2. **Dedicated source**: install VB-Audio Cable, set
     `monitor.audioDevice` to the CABLE Input device (see INSTALL.md), and
     add an OBS *Audio Input Capture* source using **CABLE Output**. Same
     recipe feeds audio into Teams/Zoom.

**mpv window closed by accident**
- The supervisor restarts it automatically within seconds. If status is
  `failed` (crashed repeatedly), toggle the monitor off and on in the side panel.

## Server

**Port already in use** — set `YTSW_PORT` in `.env`.

**UI says "reconnecting…"** — the Node server exited; see the last lines of
`logs/app.log`. The UI reconnects and resyncs automatically once it's back.

**High RAM growth over hours** — expected flat. If Node's RSS climbs steadily,
capture `logs/app.log` and check for a looping restart (circuit breaker
messages); a child stuck in a restart storm is the usual cause.
