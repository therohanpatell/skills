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
**YT Switcher Virtual Cam** (or **OBS Virtual Camera** if you use that
backend). It exists as soon as yt-switcher is running (showing black until a
Program is selected) and stays alive across every switch.

## Branded devices: "YT Switcher Virtual Cam" + "YT Switcher Audio"

**Camera** — powered by [akvirtualcamera](https://github.com/webcamoid/akvirtualcamera)
(MIT), a standalone DirectShow virtual camera with a name we choose — no OBS
involved. `setup-windows.ps1` installs it and creates the device (run the
script as Administrator for this step); manually it is:

```powershell
AkVCamManager add-device "YT Switcher Virtual Cam"   # prints the device id
AkVCamManager add-format <id> NV12 1920 1080 60
AkVCamManager update
```

Once it exists, the app auto-selects it (the Device dropdown always lets you
switch back to OBS Virtual Camera or any other virtual camera). It appears in
vMix, OBS, Zoom, Teams, and Google Meet like any webcam.

**Audio** — virtual *audio* devices require a signed kernel driver, which is
why nobody rolls their own; instead, install VB-Audio Cable once and rename
its endpoints so every app shows **YT Switcher Audio**:
`mmsys.cpl` → Playback → "CABLE Input" → Properties → rename to
`YT Switcher Audio`, and Recording → "CABLE Output" → the same. Then pick it
in the side panel's **Audio out** dropdown, and select it as the microphone
in your calling/production app.

## Routing program audio into calls (virtual microphone)

Virtual cameras carry no audio (a Windows platform constraint, true for every
vendor — including OBS's own virtual camera). The industry solution is to pair
the virtual camera with a virtual MICROPHONE:

1. Install [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) (free), reboot.
2. In the yt-switcher side panel → MONITOR → **Audio out**, pick
   **CABLE Input (VB-Audio Virtual Cable)**. Program audio now flows into the
   virtual cable instead of (or, see tip below, as well as) your speakers.
   The choice persists across restarts.
3. In Teams/Zoom/Meet pick **CABLE Output** as the *microphone* (disable the
   app's noise suppression for clean program sound). In OBS add an
   *Audio Input Capture* source using CABLE Output.
4. Sync: video and audio are aligned by the A/V SYNC ◀ ▶ buttons — set once
   per stream by ear; the lock-step 1× pacing keeps it stable.

Tip — hearing it yourself while feeding the call: enable "Listen to this
device" on CABLE Output (Windows Sound → Recording → CABLE Output →
Properties → Listen), which mirrors the cable to your speakers.

## Uninstalling

Delete the folder. State lives entirely in `data/` and `logs/` inside it.
