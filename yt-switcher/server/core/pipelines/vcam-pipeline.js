'use strict';

/**
 * VcamPipeline — the rendering pipeline + virtual camera engine.
 *
 * One FFmpeg process hardware-decodes the current Program source and produces:
 *   1. Raw NV12 frames → frame-aligned ring buffer → bridge → OBS Virtual Camera
 *   2. Low-res MJPEG stream → TCP server → browser Program preview
 *
 * The bridge process (Python, pyvirtualcam) outlives FFmpeg restarts, holding
 * the last frame so the virtual camera never disappears from downstream apps.
 *
 * FFmpeg is NOT auto-restarted here — the Switcher owns restart policy
 * because a failure may require a fresh yt-dlp URL resolution first.
 *
 * Key design choices:
 *   - No audioUrl is passed to FFmpeg (vcam is video-only; audio is handled
 *     by MpvController). This saves bandwidth for VOD split streams.
 *   - Windows process cleanup uses taskkill /t /f to kill the entire process
 *     tree (FFmpeg can spawn sub-processes for HLS/DASH).
 *   - All buffers (_previewBuf, _writeOffset, ring buffers) are fully reset
 *     on every stopFfmpeg() call to prevent stale data leaking between sources.
 *
 * Emits: 'exit' ({ code, uptimeMs, expected }), 'previewFrame',
 *        'bridgeStatus', 'perfUpdate' (performance metrics).
 */

const net = require('net');
const path = require('path');
const { spawn, execFile } = require('child_process');
const { EventEmitter } = require('events');
const config = require('../../lib/config');
const logger = require('../../lib/logger');
const { ManagedProcess } = require('../supervisor');

// JPEG markers for MJPEG frame extraction
const JPEG_SOI = Buffer.from([0xff, 0xd8]);
const JPEG_EOI = Buffer.from([0xff, 0xd9]);

const QUICK_FAIL_MS = 5000;
const PREVIEW_BUF_LIMIT = 5 * 1024 * 1024; // 5 MB max before reset
const IS_WIN = process.platform === 'win32';

/** Bytes per NV12 frame. */
function nv12Size(w, h) {
  return (w * h * 3) / 2;
}

/** A black NV12 frame (Y=16 studio black, U=V=128 neutral chroma). */
function blackFrame(w, h) {
  const buf = Buffer.alloc(nv12Size(w, h));
  buf.fill(0x10, 0, w * h);
  buf.fill(0x80, w * h);
  return buf;
}

const { execSync } = require('child_process');

/**
 * Devices that are OBS-internal read-only DirectShow filters.
 * They read from shared memory that only OBS itself can write to.
 * Writing to their shared memory sections corrupts the data and crashes OBS.
 * These MUST be excluded from the selectable device list.
 */
const OBS_INTERNAL_DEVICES = new Set([
  'OBS-Camera', 'OBS-Camera2', 'OBS-Camera3', 'OBS-Camera4',
]);

/** Enumerate virtual camera devices that pyvirtualcam can actually write to. */
function detectVirtualCameras() {
  const devices = new Set();

  if (IS_WIN) {
    try {
      const out = execSync(
        'reg query "HKCR\\CLSID\\{860BB310-5D01-11D0-BD3B-00A0C911CE86}\\Instance"',
        { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], windowsHide: true }
      );
      for (const line of out.split(/\r?\n/)) {
        if (!line.includes('\\Instance\\')) continue;
        const subkey = line.trim();
        try {
          const fnOut = execSync(`reg query "${subkey}" /v FriendlyName`, {
            encoding: 'utf8',
            stdio: ['ignore', 'pipe', 'ignore'],
            windowsHide: true,
          });
          const m = fnOut.match(/FriendlyName\s+REG_\w+\s+(.*)/i);
          if (m && m[1]) {
            const name = m[1].trim();
            // Skip OBS-internal read-only DirectShow devices
            if (!OBS_INTERNAL_DEVICES.has(name)) {
              devices.add(name);
            }
          }
        } catch (_) {}
      }
    } catch (_) {}
  }

  // Always ensure the main OBS Virtual Camera is listed (it's the one pyvirtualcam can write to)
  devices.add('OBS Virtual Camera');

  return Array.from(devices);
}

class VcamPipeline extends EventEmitter {
  constructor(store = null) {
    super();
    this.store = store;
    const { width, height, fps, device: configDevice } = config.vcam;
    this.frameSize = nv12Size(width, height);

    this.availableDevices = detectVirtualCameras();

    // Determine active virtual camera device
    const savedDevice = store && store.state && store.state.settings ? store.state.settings.vcamDevice : null;
    let selectedDevice = savedDevice || configDevice;

    // Prefer Unity Video Capture or any non-OBS virtual camera if available, to leave OBS Virtual Camera free
    if (!savedDevice) {
      const standalone = this.availableDevices.find((d) => d === 'Unity Video Capture') ||
                         this.availableDevices.find((d) => d !== 'OBS Virtual Camera');
      selectedDevice = standalone || 'OBS Virtual Camera';
    } else if (OBS_INTERNAL_DEVICES.has(selectedDevice)) {
      logger.warn({ device: selectedDevice }, 'configured vcam device is an OBS-internal read-only device, falling back to standalone/OBS virtual camera');
      const fallback = this.availableDevices.find((d) => d === 'Unity Video Capture') ||
                       this.availableDevices.find((d) => d !== 'OBS Virtual Camera') ||
                       'OBS Virtual Camera';
      selectedDevice = fallback;
    }
    this.currentDevice = selectedDevice;

    this.notification = null;

    // ---- FFmpeg process state ----
    this.ffmpeg = null;
    this._startedAt = 0;
    this._stopping = false;
    this._paused = false;
    this._hwFailed = false;  // true after a hw decode failure → fallback to sw
    this._stderr = [];       // last 20 stderr lines for diagnostics
    this._gen = 0;           // generation counter: reject frames from killed processes

    // ---- Pre-allocated ring buffer for zero-allocation frame assembly ----
    this._numBufs = 4;
    this._bufs = Array.from({ length: this._numBufs }, () =>
      Buffer.allocUnsafe(this.frameSize)
    );
    this._bufIdx = 0;
    this._writeOff = 0;

    // ---- Performance metrics ----
    this._perf = {
      outputFps: 0,
      renderFps: 0,
      frameTimeMs: 0,
      droppedFrames: 0,
      skippedFrames: 0,
      totalFrames: 0,
      videoBitrate: '0 kbps',
      audioBitrate: '0 kbps',
      bufferSize: 0,
      encoderStatus: 'idle',
      ffmpegFps: 0,
      ffmpegSpeed: '0x',
      positionSec: 0,
    };
    this._frameTimes = [];
    this._lastFrameTime = 0;
    this._perfInterval = null;
    this._seekBase = 0;   // -ss offset of the current decode
    this._posSpeed = 1;   // setpts factor: source-seconds per output-second

    // ---- Virtual camera bridge (persistent, supervised) ----
    this._initBridge();

    // ---- MJPEG preview server ----
    this._previewPort = 0;
    this._previewServer = null;
    this._previewBuf = Buffer.alloc(0);
    /** @type {Set<import('http').ServerResponse>} */
    this._previewClients = new Set();

    // ---- Cached black frame ----
    this._blackFrame = blackFrame(width, height);
  }

  _initBridge() {
    if (!config.vcam.enabled) {
      this.bridge = null;
      return;
    }
    const { width, height, fps } = config.vcam;
    const targetDev = this.currentDevice;

    this.bridge = new ManagedProcess('vcam-bridge', () => ({
      cmd: config.paths.python,
      args: [
        path.join(config.root, 'bridge', 'vcam_bridge.py'),
        '--width', String(width),
        '--height', String(height),
        '--fps', String(fps),
        '--device', targetDev,
      ],
      opts: { stdio: ['pipe', 'pipe', 'pipe'] },
    }));

    this.bridge.on('started', () => {
      this.notification = null;
      this.emit('bridgeStatus', 'running');
    });
    this.bridge.on('exited', () => this.emit('bridgeStatus', this.bridge.status));
    this.bridge.on('failed', () => {
      this.emit('bridgeStatus', 'failed');
      this._handleBridgeFailure(targetDev);
    });
  }

  /** Auto failover if selected virtual camera fails to open. */
  _handleBridgeFailure(failedDev) {
    logger.warn({ failedDev }, 'virtual camera device failed, checking fallback devices');
    // All devices in availableDevices are writable (OBS-internal devices already excluded)
    const fallback = this.availableDevices.find((d) => d !== failedDev);

    if (fallback && fallback !== failedDev) {
      const msg = `Virtual camera "${failedDev}" unavailable. Switched to "${fallback}".`;
      logger.info({ fallback }, msg);
      this.notification = msg;
      // Failover is temporary: don't persist it, so the user's preferred
      // device is tried again on the next launch.
      this.setDevice(fallback, { persist: false }).catch(() => {});
    }
  }

  /** Change output device dynamically. persist=false for automatic failover. */
  async setDevice(deviceName, { persist = true } = {}) {
    if (!deviceName || deviceName === this.currentDevice) return;
    logger.info({ from: this.currentDevice, to: deviceName }, 'switching virtual camera device');

    this.currentDevice = deviceName;
    if (persist && this.store) {
      this.store.update((st) => {
        st.settings.vcamDevice = deviceName;
      });
    }

    if (this.bridge) {
      this.bridge.stop();
      this._initBridge();
      this.bridge.start();
    }
  }

  /* ================================================================
   *  Initialization
   * ================================================================ */

  async init() {
    if (this.bridge) this.bridge.start();
    await this._startPreviewServer();
    this._perfInterval = setInterval(() => this._emitPerf(), 1000);
  }

  _startPreviewServer() {
    return new Promise((resolve) => {
      this._previewServer = net.createServer((sock) => {
        sock.on('data', (d) => this._onPreviewData(d));
        sock.on('error', () => {});
      });
      this._previewServer.listen(0, '127.0.0.1', () => {
        this._previewPort = this._previewServer.address().port;
        logger.info({ port: this._previewPort }, 'preview ingest listening');
        resolve();
      });
    });
  }

  /* ================================================================
   *  MJPEG Preview
   * ================================================================ */

  /** Extract complete JPEGs from the MJPEG byte stream. */
  _onPreviewData(chunk) {
    this._previewBuf = Buffer.concat([this._previewBuf, chunk]);
    for (;;) {
      const start = this._previewBuf.indexOf(JPEG_SOI);
      if (start < 0) break;
      const end = this._previewBuf.indexOf(JPEG_EOI, start + 2);
      if (end < 0) break;
      const frame = this._previewBuf.subarray(start, end + 2);
      this._previewBuf = this._previewBuf.subarray(end + 2);
      this._broadcastPreview(frame);
    }
    // Prevent unbounded growth from malformed streams
    if (this._previewBuf.length > PREVIEW_BUF_LIMIT) {
      this._previewBuf = Buffer.alloc(0);
    }
  }

  /** Fan out a JPEG frame to all connected browser clients. */
  _broadcastPreview(jpeg) {
    this.emit('previewFrame');
    const header = Buffer.from(
      `--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpeg.length}\r\n\r\n`
    );
    for (const res of this._previewClients) {
      // Drop frames for slow clients (backpressure)
      if (res.writableLength > 2 * 1024 * 1024) continue;
      res.write(header);
      res.write(jpeg);
      res.write('\r\n');
    }
  }

  /** Attach a raw HTTP response as a multipart MJPEG consumer. */
  addPreviewClient(res) {
    res.writeHead(200, {
      'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
      'Cache-Control': 'no-store',
      Connection: 'close',
    });
    this._previewClients.add(res);
    res.on('close', () => this._previewClients.delete(res));
  }

  /* ================================================================
   *  Getters
   * ================================================================ */

  get running() {
    return Boolean(this.ffmpeg);
  }

  get paused() {
    return this._paused;
  }

  get bridgeStatus() {
    if (!config.vcam.enabled) return 'disabled';
    return this.bridge ? this.bridge.status : 'disabled';
  }

  /** Current performance metrics snapshot. */
  getPerformanceMetrics() {
    return { ...this._perf };
  }

  /** Current decode position in source seconds (0 when idle/live). */
  get positionSec() {
    return this._perf.positionSec || 0;
  }

  /* ================================================================
   *  FFmpeg Lifecycle
   * ================================================================ */

  /**
   * Start decoding a resolved stream URL.
   * Kills any previous FFmpeg first; the bridge holds the last frame.
   *
   * @param {string} videoUrl  - Resolved video stream URL
   * @param {object} opts
   * @param {boolean} [opts.isLive]    - Whether this is a live stream
   * @param {number}  [opts.seekTo]    - Start position in seconds (VOD only)
   * @param {number}  [opts.speed]     - Playback speed multiplier (default 1.0)
   * @param {string}  [opts.userAgent] - HTTP User-Agent header
   */
  start(videoUrl, { isLive = false, seekTo = 0, speed = 1, userAgent = null } = {}) {
    // Kill previous FFmpeg (bridge holds last frame during the gap)
    this.stopFfmpeg({ blank: false });

    // Reset all state for the new source
    this._stopping = false;
    this._paused = false;
    this._writeOff = 0;
    this._stderr = [];
    this._frameTimes = [];
    this._lastFrameTime = 0;
    this._perf.droppedFrames = 0;
    this._perf.skippedFrames = 0;
    this._perf.totalFrames = 0;
    this._perf.encoderStatus = 'starting';
    this._seekBase = (!isLive && seekTo > 0) ? seekTo : 0;
    this._posSpeed = (!isLive && Number(speed) > 0) ? Number(speed) : 1;
    this._perf.positionSec = this._seekBase;

    const gen = ++this._gen;
    const { width, height, fps } = config.vcam;
    const p = config.preview;
    const hwaccel = this._hwFailed ? 'none' : config.ffmpeg.hwaccel;

    // ---- Build FFmpeg arguments ----
    const args = ['-hide_banner', '-loglevel', 'warning', '-nostdin'];

    // Progress output for performance monitoring
    args.push('-progress', 'pipe:2', '-stats_period', '1');

    // Hardware acceleration
    if (hwaccel && hwaccel !== 'none') args.push('-hwaccel', hwaccel);

    // Input flags: live vs VOD.
    // Live: smoothness beats latency for a vcam feed — no `nobuffer`, and
    // start a few HLS segments behind the live edge so there is always a
    // download cushion (this is what stops the fast/slow rubber-banding).
    if (isLive) {
      args.push(
        '-fflags', '+discardcorrupt',
        '-probesize', '1000000',
        '-analyzeduration', '1000000',
        '-rw_timeout', '10000000', // 10s network timeout
      );
      if (/m3u8/i.test(videoUrl)) {
        args.push('-live_start_index', '-3');
      }
    } else {
      // 2 MB probe is plenty for googlevideo mp4/webm and shaves start-up time.
      args.push(
        '-fflags', '+discardcorrupt',
        '-probesize', '2000000',
      );
      // CRITICAL: pace VOD input at (speed ×) real time. Without this FFmpeg
      // decodes a file/URL flat out (observed 4.7×/280fps), overflowing the
      // bridge queue whose drop-oldest policy then fast-forwards the camera.
      // Live streams are paced by the source and must NOT be rate-limited.
      const rate = Number(speed) > 0 ? Number(speed) : 1;
      args.push('-readrate', String(rate));
    }

    // VOD seek position (input seek with accurate_seek)
    if (seekTo > 0 && !isLive) {
      args.push('-ss', String(seekTo), '-accurate_seek');
    }

    // User-Agent
    if (userAgent) {
      args.push('-user_agent', userAgent);
    }

    // HTTP reconnect
    if (/^https?:/i.test(videoUrl)) {
      args.push(
        '-reconnect', '1',
        '-reconnect_at_eof', isLive ? '0' : '1', // Live doesn't have EOF
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', isLive ? '5' : '2',
      );
    }

    // Input
    args.push('-i', videoUrl);
    // Note: audioUrl is NOT passed to FFmpeg — the vcam is video-only.
    // Audio is handled entirely by MpvController.

    // Speed filter for video (setpts=(1/speed)*PTS)
    const spdVal = Number(speed) || 1;
    const speedFilter = (!isLive && spdVal !== 1 && spdVal > 0)
      ? `setpts=(1/${spdVal})*PTS,`
      : '';

    // ---- Output 1: raw NV12 for virtual camera ----
    args.push(
      '-map', '0:v:0', '-an',
      '-vf', [
        // force_divisible_by=2: NV12 requires even dimensions; an odd
        // scaled size shifts the chroma plane and tints the image green.
        `${speedFilter}scale=${width}:${height}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=fast_bilinear`,
        `pad=${width}:${height}:-1:-1:color=black`,
        `fps=${fps}`,
        'format=nv12',
      ].join(','),
      '-pix_fmt', 'nv12',
      '-vsync', 'cfr',
      '-f', 'rawvideo', 'pipe:1'
    );

    // ---- Output 2: low-res MJPEG for browser preview ----
    args.push(
      '-map', '0:v:0', '-an',
      '-vf', `${speedFilter}scale=${p.width}:-2:force_divisible_by=2:flags=fast_bilinear,fps=${p.fps}`,
      '-c:v', 'mjpeg', '-q:v', String(p.quality),
      '-f', 'mjpeg', `tcp://127.0.0.1:${this._previewPort}`
    );

    // ---- Spawn ----
    logger.info({ isLive, hwaccel, seekTo, width, height, fps }, 'starting ffmpeg pipeline');
    const proc = spawn(config.paths.ffmpeg, args, {
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    this.ffmpeg = proc;
    this._startedAt = Date.now();
    this._perf.encoderStatus = 'running';

    // Wire up data handlers
    proc.stdout.on('data', (chunk) => this._onRawVideo(proc, gen, chunk));
    proc.stderr.on('data', (d) => this._onStderr(d));
    proc.on('error', (err) => logger.error({ err }, 'ffmpeg spawn error'));
    proc.once('exit', (code) =>
      this._onExit(proc, code, videoUrl, { isLive, seekTo, speed, userAgent }, hwaccel));
  }

  /* ================================================================
   *  FFmpeg Stderr / Progress
   * ================================================================ */

  _onStderr(d) {
    const text = d.toString();
    this._parseProgress(text);
    for (const line of text.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      // Skip progress key=value lines (parsed above)
      if (/^(frame|fps|bitrate|total_size|out_time|dup_frames|drop_frames|speed|progress)=/.test(trimmed)) continue;
      logger.debug({ name: 'ffmpeg' }, trimmed);
      this._stderr.push(trimmed);
      if (this._stderr.length > 20) this._stderr.shift();
    }
  }

  _parseProgress(text) {
    for (const line of text.split('\n')) {
      const eq = line.indexOf('=');
      if (eq < 0) continue;
      const key = line.substring(0, eq).trim();
      const val = line.substring(eq + 1).trim();
      switch (key) {
        case 'fps':
          this._perf.ffmpegFps = parseFloat(val) || 0;
          break;
        case 'out_time_ms': {
          // Misnamed by ffmpeg: the value is in MICROseconds. Combined with
          // the -ss base this is the true program position of the vcam
          // decode — the timeline/pause/seek fallback when mpv is absent.
          const us = parseInt(val, 10);
          if (Number.isFinite(us) && us >= 0) {
            this._perf.positionSec = this._seekBase + (us / 1e6) * this._posSpeed;
          }
          break;
        }
        case 'bitrate':
          this._perf.videoBitrate = val || '0 kbps';
          break;
        case 'speed':
          this._perf.ffmpegSpeed = val || '0x';
          break;
        case 'drop_frames':
          this._perf.droppedFrames = parseInt(val, 10) || 0;
          break;
        case 'dup_frames':
          this._perf.skippedFrames = parseInt(val, 10) || 0;
          break;
      }
    }
  }

  /* ================================================================
   *  FFmpeg Exit
   * ================================================================ */

  _onExit(proc, code, videoUrl, startOpts, hwaccel) {
    if (this.ffmpeg !== proc) return; // Superseded by a newer start()
    this.ffmpeg = null;
    this._perf.encoderStatus = code === 0 ? 'idle' : 'error';
    const uptimeMs = Date.now() - this._startedAt;
    const expected = this._stopping;

    // Auto-retry without hardware accel if hw decode init failed fast.
    // startOpts carries the full original options (isLive, seekTo, speed,
    // userAgent) so the software retry is identical apart from hwaccel.
    if (!expected && !this._hwFailed && uptimeMs < QUICK_FAIL_MS &&
        config.ffmpeg.hwaccelFallback && hwaccel !== 'none' &&
        /d3d11|dxva|cuda|qsv|hwaccel|hardware/i.test(this._stderr.join('\n'))) {
      logger.warn('hardware decode failed, falling back to software mode');
      this._hwFailed = true;
      this.start(videoUrl, startOpts);
      return;
    }

    logger[expected ? 'info' : 'warn']({ code, uptimeMs, expected }, 'ffmpeg exited');
    this.emit('exit', { code, uptimeMs, expected, stderr: this._stderr.join('\n') });
  }

  /* ================================================================
   *  Raw Video Frame Processing
   * ================================================================ */

  /**
   * Assemble complete NV12 frames from FFmpeg's stdout stream and send
   * them to the bridge. Uses a ring buffer to avoid Buffer.concat().
   */
  _onRawVideo(proc, gen, chunk) {
    // Reject frames from a killed FFmpeg instance
    if (gen !== this._gen) return;
    // Don't forward frames when paused (bridge holds last frame)
    if (this._paused) return;

    let offset = 0;
    while (offset < chunk.length) {
      const activeBuf = this._bufs[this._bufIdx];
      const needed = this.frameSize - this._writeOff;
      const available = chunk.length - offset;
      const toCopy = Math.min(available, needed);

      chunk.copy(activeBuf, this._writeOff, offset, offset + toCopy);
      this._writeOff += toCopy;
      offset += toCopy;

      if (this._writeOff === this.frameSize) {
        // Frame complete — send to bridge
        this._writeToBridge(activeBuf, proc);

        // Advance to next buffer in the ring
        this._bufIdx = (this._bufIdx + 1) % this._numBufs;
        this._writeOff = 0;

        // Track frame timing
        const now = Date.now();
        if (this._lastFrameTime > 0) {
          this._perf.frameTimeMs = now - this._lastFrameTime;
        }
        this._lastFrameTime = now;
        this._frameTimes.push(now);
        this._perf.totalFrames++;
      }
    }
  }

  /** Write a complete frame to the bridge stdin with backpressure. */
  _writeToBridge(frame, sourceProc) {
    const b = this.bridge && this.bridge.proc;
    if (!b || !b.stdin || !b.stdin.writable) return;

    this._perf.bufferSize = b.stdin.writableLength;

    // COPY the frame: stream writes queue the buffer by reference, and the
    // ring buffer slot gets overwritten by later frames while the old bytes
    // may still be sitting in the pipe's write queue. Sending an aliased
    // buffer is what produced the intermittent green/corrupted camera frames.
    const payload = Buffer.from(frame);

    if (!b.stdin.write(payload) && sourceProc && sourceProc.stdout) {
      // Backpressure: pause FFmpeg output until the bridge drains
      sourceProc.stdout.pause();
      b.stdin.once('drain', () => {
        if (sourceProc.stdout) sourceProc.stdout.resume();
      });
    }
  }

  /* ================================================================
   *  Controls
   * ================================================================ */

  /** Pause frame forwarding — bridge holds last frame (freeze frame). */
  pause() {
    this._paused = true;
    this._perf.encoderStatus = 'paused';
    this.emit('perfUpdate', this._perf);
  }

  /** Resume frame forwarding. */
  resume() {
    this._paused = false;
    if (this.ffmpeg) this._perf.encoderStatus = 'running';
    this.emit('perfUpdate', this._perf);
  }

  /**
   * Stop FFmpeg and fully reset all buffers.
   * With blank=true, sends one black frame to the vcam (deliberate stop).
   */
  stopFfmpeg({ blank = true } = {}) {
    if (this.ffmpeg) {
      this._stopping = true;
      this._gen++; // Invalidate in-flight frames from old process

      const pid = this.ffmpeg.pid;
      try {
        if (IS_WIN && pid) {
          // Windows: kill entire process tree (FFmpeg can spawn sub-processes)
          execFile('taskkill', ['/t', '/f', '/pid', String(pid)],
            { windowsHide: true }, () => {});
        } else {
          this.ffmpeg.kill('SIGKILL');
        }
      } catch (_) { /* already gone */ }

      this.ffmpeg = null;
    }

    // Full buffer reset — prevents stale data leaking between sources
    this._writeOff = 0;
    this._paused = false;
    this._perf.encoderStatus = 'idle';
    this._previewBuf = Buffer.alloc(0);

    if (blank && config.vcam.enabled) {
      this._writeToBridge(this._blackFrame, null);
    }
  }

  /* ================================================================
   *  Performance Metrics
   * ================================================================ */

  /** Emit performance metrics (called every 1s). */
  _emitPerf() {
    if (!this.ffmpeg) {
      this._perf.outputFps = 0;
      this._perf.renderFps = 0;
      this._perf.frameTimeMs = 0;
    } else {
      // Calculate FPS from frame timestamps in the last 2 seconds
      const now = Date.now();
      const cutoff = now - 2000;
      this._frameTimes = this._frameTimes.filter((t) => t > cutoff);
      const count = this._frameTimes.length;
      const elapsed = count > 1
        ? (this._frameTimes[count - 1] - this._frameTimes[0]) / 1000
        : 0;
      this._perf.renderFps = elapsed > 0
        ? Math.round(((count - 1) / elapsed) * 10) / 10
        : 0;
      this._perf.outputFps = this._perf.ffmpegFps;
    }

    if (this.bridge && this.bridge.proc) {
      this._perf.bufferSize = this.bridge.proc.stdin
        ? this.bridge.proc.stdin.writableLength
        : 0;
    }

    this.emit('perfUpdate', this._perf);
  }

  /* ================================================================
   *  Shutdown
   * ================================================================ */

  async shutdown() {
    clearInterval(this._perfInterval);
    this.stopFfmpeg({ blank: true });
    if (this.bridge) this.bridge.stop();
    if (this._previewServer) this._previewServer.close();
    for (const res of this._previewClients) res.end();
  }
}

module.exports = { VcamPipeline };
