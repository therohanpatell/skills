# YouTube Broadcast Switcher — Architecture Proposal

**Status:** Awaiting approval — no application code has been written yet.

A lightweight, Windows-native video routing/switching system for up to 20 YouTube
videos and live streams, exposing the selected Program output as a Virtual Camera,
controlled from a vanilla HTML/CSS/JS web UI served by a local Node.js (Fastify) server.

---

## 1. Research Summary

### 1.1 Playback engine comparison

| Option | HW decode | Remote control | Frame export (for vcam) | Footprint | Verdict |
|---|---|---|---|---|---|
| **mpv** | Excellent (`d3d11va`, NVDEC, QuickSync) | JSON IPC (named pipe), full pause/seek/volume | ✗ Renders to GPU/window only; encoding mode (`--o=`) is not live-controllable | ~80–150 MB | ✅ **Program monitor + audio** |
| **FFmpeg** | Excellent (`-hwaccel d3d11va`) | Process-level only (no pause/seek) | ✅ Native — rawvideo to a pipe at fixed fps/size | ~60–120 MB | ✅ **Virtual camera feeder + UI preview** |
| VLC | Inconsistent on Windows | RC/HTTP interface, clunky | Poor (smem is fragile) | Heavier | ✗ |
| Browser `<video>`/YT iframe | Browser-dependent, per-tab overhead | JS only | ✗ Cannot reach a system vcam | Huge (each embed is a full YT player) | ✗ (thumbnails only in UI) |
| GStreamer | Good | Good | Good | Large runtime, complex Windows install | ✗ Overkill |

**Key insight:** every Windows virtual camera implementation ultimately needs raw
frames **in system memory** at a fixed resolution/fps. A GPU renderer like mpv keeps
frames on the GPU (that's why it's efficient), so it cannot feed a vcam without a
custom libmpv render-API native module. FFmpeg, by contrast, is *designed* to emit
raw frames to a pipe. So the two tools split the job along their natural strengths.

### 1.2 Windows Virtual Camera comparison

| Option | API | Works in Teams/Zoom/Meet/OBS/vMix | Driver install | Effort / risk | Verdict |
|---|---|---|---|---|---|
| **OBS Virtual Camera** (fed directly via its shared-memory queue — OBS itself does **not** run) | DirectShow (+ MF proxy on Win 11) | ✅ Universally supported — it is the most widely tested vcam on Windows | Signed, ships with OBS Studio installer (one-time) | Low — the `pyvirtualcam` library implements the shared-memory protocol | ✅ **Recommended** |
| Unity Capture / softcam | DirectShow | Mostly, but invisible to MF-only apps; unsigned DLL trips SmartScreen/AV | Manual `regsvr32` of unsigned DLL | Medium, flaky | ✗ |
| akvirtualcamera | DirectShow + own service | Good | Own installer/service | Medium | Fallback option |
| Media Foundation `MFCreateVirtualCamera` (Win 11) | MF | New apps only; older DirectShow-only apps can't see it | Requires a custom C++ media-source DLL | High (weeks of native dev) | Future enhancement |
| GStreamer | — | No Windows vcam sink exists | — | — | ✗ |
| WebRTC | — | Not a system camera device | — | — | ✗ |

**Decision: OBS Virtual Camera device, fed directly over its shared-memory frame
queue.** OBS Studio is installed once (most broadcast PCs already have it); after
that OBS never runs — our bridge process writes frames straight into the
"OBS Virtual Camera" device that every conferencing/production app already knows.
This is the same approach used by many production tools and avoids shipping or
signing any driver ourselves.

**The bridge:** a tiny helper process (~40 lines of Python using `pyvirtualcam`,
~25 MB RSS) that reads raw frames from FFmpeg's stdout and pushes them into the
vcam, holding the last frame during source switches so downstream apps never see
the camera disappear. Python is used *only* here because `pyvirtualcam` is the one
mature, maintained implementation of the OBS shared-memory protocol; a native Node
addon reimplementing it is listed as a future enhancement to drop the dependency.

### 1.3 How professional switchers do it (and what we borrow)

vMix/OBS/CasparCG follow the same pattern: **decode only what's on Program**,
represent everything else as cold metadata (thumbnail + status), keep the output
device alive continuously and swap what feeds it. We replicate exactly that:
the vcam never closes; sources swap behind it.

---

## 2. Proposed Architecture

```
                    ┌────────────────────────────────────────────┐
                    │  Browser UI (vanilla HTML/CSS/JS, dark)     │
                    │  Program panel ◄─ MJPEG preview (~12 fps)   │
                    │  20 preview cards ◄─ thumbnails only        │
                    └────────▲──────────────────▲────────────────┘
                             │ WebSocket (state) │ HTTP (MJPEG, thumbs, REST)
┌────────────────────────────┴──────────────────┴────────────────┐
│  Node.js + Fastify  (the only always-on brain)                  │
│  ├─ SourceManager   yt-dlp resolve/refresh, metadata, thumbs    │
│  ├─ Switcher        program selection, instant switch logic     │
│  ├─ ProcessSupervisor  spawn/heal ffmpeg, mpv, bridge           │
│  ├─ StateStore      JSON file (atomic writes): urls, program,   │
│  │                  settings — restored on startup              │
│  └─ StatsMonitor    CPU/RAM (pidusage), GPU (nvidia-smi if any) │
└───────┬───────────────────────┬───────────────────┬────────────┘
        │ spawn                 │ spawn              │ JSON IPC (named pipe)
   ┌────▼─────┐          ┌──────▼───────┐      ┌─────▼─────┐
   │ yt-dlp   │          │ FFmpeg       │      │ mpv       │
   │ (resolve │          │ hw decode →  │      │ Program   │
   │  only,   │          │ 1) rawvideo ─┼──►───┤ monitor   │
   │  exits)  │          │    pipe      │ vcam │ window +  │
   └──────────┘          │ 2) MJPEG     │bridge│ audio,    │
                         │    low-res   │(py)  │ pause/seek│
                         └──────────────┘  │   └───────────┘
                                     ┌─────▼──────────────┐
                                     │ OBS Virtual Camera │
                                     │ → Teams/Zoom/vMix… │
                                     └────────────────────┘
```

### 2.1 Component responsibilities

**Node/Fastify server** — serves the static UI, REST endpoints
(`POST /sources`, `DELETE /sources/:id`, `POST /program/:id`, `POST /playback/...`),
one WebSocket for all state/stat pushes (no polling), supervises child processes
with exponential-backoff restarts, rotating file + console logging, config via
`.env` + `config/default.json`.

**yt-dlp (on demand, exits immediately)** — `yt-dlp -j <url>` resolves direct
media URLs, title, thumbnail, live/VOD flag, duration. Results cached. Live-stream
manifest URLs are refreshed automatically before their expiry (~4 h) and immediately
on a 403 mid-playback. Structured errors (private / removed / region-blocked /
age-restricted / copyright) are parsed from stderr and shown on the card.

**FFmpeg (exactly one instance, only for the Program source)** —
`-hwaccel d3d11va -i <resolved_url>` with two cheap outputs:
1. `rawvideo` (NV12→RGB, fixed 1280×720@30 by default, configurable) → stdout → vcam bridge.
2. Down-scaled MJPEG (~640×360@12fps, q≈8) → HTTP multipart to the browser Program panel — so the browser never decodes the real stream.

No re-encode of the source ever happens; decode is mandatory only because a vcam
requires raw frames — this is the theoretical minimum work.

**Vcam bridge (persistent)** — never exits across switches; repeats the last frame
(or a "STAND BY" slate) while FFmpeg restarts, so the camera device stays rock-solid
in Teams/Zoom during switching.

**mpv (one instance, optional but on by default)** — the operator's full-quality
Program monitor window + audio output, controlled over JSON IPC: play/pause/stop/
mute/volume/fullscreen, and property observers feeding resolution/fps/cache/time
back to the UI. Audio can be routed to any output device (e.g. VB-Audio Cable if
audio must reach Teams/Zoom — virtual *cameras* never carry audio; no vcam solution
does). Can be toggled off in settings to halve bandwidth/decode when only the vcam
matters.

### 2.2 Instant switching

1. Click card → server marks new Program, pushes UI update instantly (<10 ms perceived).
2. mpv: `loadfile <url> replace` — no process restart.
3. FFmpeg: old instance SIGKILLed, new one spawned with cached resolved URL; bridge holds last frame meanwhile. Typical gap on the vcam: **0.5–1.5 s of held frame**, never a black/disconnected camera.
4. Resolved-URL cache means no yt-dlp call on the switch path.

### 2.3 Idle sources cost literally zero

Non-program cards are a cached thumbnail JPEG + title + badges in the DOM. No
decoder, no network, no timers. 20 idle sources ≈ a few MB of images.

### 2.4 Storage

`data/state.json`, atomically written (temp file + rename), debounced: source list,
program id, volume/mute, vcam resolution/fps, mpv-monitor toggle. Restored on
launch; live streams re-resolved and program auto-reconnected. JSON over SQLite:
≤20 records, zero native dependencies, human-inspectable. (Swappable behind a
`StateStore` interface if it ever grows.)

### 2.5 Reliability for 24/7 operation

- Supervisor auto-restarts ffmpeg/mpv/bridge with backoff + circuit breaker; UI shows per-process health.
- Live URL refresh scheduler + 403-triggered immediate refresh.
- "Stream ended" detection → card status + vcam slate, no crash.
- Bounded queues everywhere (frame pipe backpressure = drop-oldest); no unbounded buffers in Node.
- Long-run soak checklist in the performance guide.

### 2.6 Expected steady-state footprint (1080p live program → 720p30 vcam)

| Process | RAM | CPU (modern quad-core+) |
|---|---|---|
| Node/Fastify | ~50 MB | <1 % |
| FFmpeg (hw decode) | ~120 MB | 3–8 % |
| Vcam bridge | ~25 MB | 1–2 % |
| mpv monitor (optional) | ~150 MB | 2–5 % (GPU-rendered) |
| Browser tab (thumbnails + MJPEG) | ~80 MB | 1–2 % |
| **Total** | **≈ 300–425 MB** | **≈ 5–15 %** |

### 2.7 Trade-offs made explicit

1. **Dual decode when the mpv monitor is on** (ffmpeg for vcam + mpv for monitor). Hardware decoders handle this trivially; the cost is ~2× network for one stream. A single-decode design (libmpv render API → native addon) is the "perfect" answer but requires custom C++ and weeks of work — listed as a future enhancement. Monitor can be switched off for single-decode operation.
2. **Python for the vcam bridge only.** `pyvirtualcam` is the only mature OBS-protocol implementation. Isolated behind one pipe; replaceable by a native Node addon later.
3. **OBS Studio must be installed once** (for its signed virtual-camera driver). Alternative (shipping our own driver) means unsigned-driver pain — not acceptable for production.
4. **Fake pause on the vcam for live streams** (freeze frame + mute). Real pause of a live stream is impossible by definition; VOD gets true pause via mpv.
5. **MJPEG for the in-UI Program preview** instead of WebRTC/HLS: zero-latency-ish, zero client complexity, and it taps the existing decode for ~free at 360p.

---

## 3. Folder structure (proposed)

```
yt-switcher/
├─ package.json / .env.example / config/default.json
├─ server/
│  ├─ index.js              # bootstrap, graceful shutdown
│  ├─ app.js                # Fastify wiring, static, ws
│  ├─ routes/               # sources.js, program.js, playback.js, system.js
│  ├─ core/
│  │  ├─ source-manager.js  # yt-dlp resolve/cache/refresh
│  │  ├─ switcher.js        # program state machine
│  │  ├─ supervisor.js      # child-process lifecycle + backoff
│  │  ├─ pipelines/         # ffmpeg.js, mpv-ipc.js, vcam-bridge.js
│  │  ├─ state-store.js     # atomic JSON persistence
│  │  └─ stats-monitor.js   # cpu/ram/gpu sampling → ws
│  └─ lib/                  # logger.js, config.js, errors.js, yt-errors.js
├─ bridge/vcam_bridge.py    # raw pipe → pyvirtualcam (holds last frame)
├─ public/                  # index.html, css/, js/ (ES modules, no framework)
├─ data/state.json
├─ scripts/setup-windows.ps1  # checks/downloads yt-dlp, ffmpeg, mpv, python deps
└─ docs/                    # install, performance, troubleshooting guides
```

---

## 4. What I need from you

Approve this architecture (or request changes) and I will implement the complete
system: backend, frontend, bridge, setup script, and all documentation listed in
the deliverables.

Open choices you can override (defaults in bold):
- Vcam format: **1280×720 @ 30 fps** or 1920×1080 @ 30 (more CPU for raw copies)
- mpv Program monitor window: **on by default** vs vcam-only mode
- Audio monitor device: **system default** (VB-Cable selectable in settings)
