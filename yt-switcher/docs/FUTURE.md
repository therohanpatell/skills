# Future Enhancements

Ordered roughly by value-to-effort.

1. **Native vcam bridge (drop Python).** Reimplement the OBS Virtual Camera
   shared-memory protocol as a small N-API addon or a prebuilt Rust/C++ exe —
   removes the Python runtime dependency entirely.
2. **Single-decode monitor.** Use the libmpv render API in a native module to
   pull frames from the *same* mpv decode that drives the monitor, eliminating
   the second decode when the monitor is on (the "perfect" architecture noted
   in ARCHITECTURE.md §2.7).
3. **Preview/Program (PVW/PGM) workflow.** A second, low-res decode slot to
   audition the next source before taking it, with a TAKE/CUT button —
   true broadcast-switcher operation.
4. **Cookies support** (`--cookies-from-browser`) for age-restricted and
   members-only content.
5. **Hotkeys.** Number keys 1–20 switch Program; space = pause; M = mute.
6. **Transition effects.** Crossfade in the bridge (blend last N frames of old
   source with first N of the new) — cheap because both are already raw frames.
7. **Audio in one process.** Move audio into the FFmpeg pipeline (second pipe →
   bridge → WASAPI via sounddevice) for guaranteed A/V sync without mpv.
8. **Overlay/branding.** Optional PNG lower-third or logo composited by the
   existing FFmpeg filter graph at ~zero extra cost.
9. **Windows 11 Media Foundation virtual camera** as an alternative backend
   (`MFCreateVirtualCamera`) once a maintained wrapper exists.
10. **Multi-output.** NDI output alongside the vcam for network-native
    production setups.
11. **Health webhook.** POST to a URL (or show a toast) when a live source
    drops, for unattended operation.
12. **Installer.** Package as a single `winget`-installable app with a tray
    icon (still no Electron — the tray can be a tiny PowerShell/AutoHotkey shim
    or a native stub).
