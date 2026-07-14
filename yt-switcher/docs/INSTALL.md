# Installation Guide (Windows)

## Requirements

- Windows 10 (1903+) or Windows 11, 64-bit
- Any GPU from the last ~8 years (for hardware decode; software fallback is automatic)
- ~500 MB free RAM headroom

## Automatic setup (recommended)

From the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
```

The script installs anything missing via winget (Node.js LTS, Python 3,
yt-dlp, FFmpeg, mpv), installs the npm and pip dependencies, verifies the OBS
Virtual Camera driver, and probes that the camera can actually be opened.

## Manual setup

1. **Node.js ≥ 18** — `winget install OpenJS.NodeJS.LTS`
2. **Python 3.9+** — `winget install Python.Python.3.12`
3. **yt-dlp** — `winget install yt-dlp.yt-dlp` (keep it updated: `yt-dlp -U`)
4. **FFmpeg** — `winget install Gyan.FFmpeg`
5. **mpv** — `winget install mpv.net` or a build from mpv.io
6. **OBS Studio** (for its virtual camera driver only) —
   `winget install OBSProject.OBSStudio`, launch OBS once, click
   *Start Virtual Camera*, then *Stop*, close OBS. Done — OBS never runs again.
7. Project deps:
   ```powershell
   npm install
   python -m pip install -r bridge\requirements.txt
   ```

If any binary is not on `PATH`, set its absolute path in `.env`
(`YTSW_FFMPEG_PATH`, `YTSW_MPV_PATH`, `YTSW_YTDLP_PATH`, `YTSW_PYTHON_PATH`).

## Running

```powershell
npm start
```

Open <http://127.0.0.1:8300>. The server binds to localhost only by default;
set `YTSW_HOST=0.0.0.0` to control it from another machine on your LAN
(there is no authentication — only do this on a trusted network).

### Start automatically with Windows

Create a scheduled task (runs hidden at logon):

```powershell
schtasks /Create /TN "YT Switcher" /SC ONLOGON /TR "cmd /c cd /d C:\path\to\yt-switcher && npm start" /RL LIMITED
```

## Selecting the camera in other apps

In OBS/vMix/Teams/Zoom/Discord/Meet, pick the video device named
**OBS Virtual Camera**. It exists as soon as yt-switcher is running (showing
black until a Program is selected) and stays alive across every switch.

## Routing program audio into calls (optional)

Virtual cameras carry no audio (a Windows platform constraint, true for every
vendor). To feed program audio into Teams/Zoom:

1. Install [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) (free).
2. Find the exact device name: `mpv --audio-device=help`
3. Add to `config/default.json` under `monitor`:
   `"audioDevice": "wasapi/{...CABLE Input id...}"` — or leave `auto` for
   normal speakers.
4. In Teams/Zoom pick **CABLE Output** as the microphone.

## Uninstalling

Delete the folder. State lives entirely in `data/` and `logs/` inside it.
