'use strict';

const net = require('net');
const path = require('path');
const { spawn } = require('child_process');
const { EventEmitter } = require('events');
const config = require('../../lib/config');
const logger = require('../../lib/logger');
const { ManagedProcess } = require('../supervisor');

const JPEG_SOI = Buffer.from([0xff, 0xd8]);
const JPEG_EOI = Buffer.from([0xff, 0xd9]);
const QUICK_FAIL_MS = 5000;

/** Bytes per NV12 frame at the configured vcam size. */
function nv12FrameSize(w, h) {
  return (w * h * 3) / 2;
}

/** A black NV12 frame (Y=16, U=V=128), written to the vcam on deliberate stop. */
function blackNv12Frame(w, h) {
  const buf = Buffer.alloc(nv12FrameSize(w, h));
  buf.fill(0x10, 0, w * h);
  buf.fill(0x80, w * h);
  return buf;
}

/**
 * The single active-decode pipeline. One FFmpeg process hardware-decodes the
 * current Program source and produces:
 *   1. raw NV12 frames -> (frame-aligned) -> persistent vcam bridge -> OBS Virtual Camera
 *   2. a low-res MJPEG stream over local TCP -> browser Program preview
 *
 * The bridge process outlives FFmpeg restarts, holding the last frame so the
 * virtual camera never disappears from downstream apps during a switch.
 *
 * FFmpeg is deliberately not auto-restarted here: the Switcher owns restart
 * policy because a failure may require a fresh yt-dlp resolution first.
 * Emits: 'exit' ({ code, uptimeMs, expected }), 'previewFrame', 'bridgeStatus'.
 */
class MediaPipeline extends EventEmitter {
  constructor() {
    super();
    const { width, height, fps } = config.vcam;
    this.frameSize = nv12FrameSize(width, height);
    this.ffmpeg = null;
    this._ffmpegStartedAt = 0;
    this._stopping = false;
    this._frameAcc = Buffer.alloc(0);
    this._hwaccelDisabled = false;
    this._lastStderr = [];

    // --- Virtual camera bridge (persistent, supervised) ---
    this.bridge = config.vcam.enabled
      ? new ManagedProcess('vcam-bridge', () => ({
          cmd: config.paths.python,
          args: [
            path.join(config.root, 'bridge', 'vcam_bridge.py'),
            '--width', String(width),
            '--height', String(height),
            '--fps', String(fps),
          ],
          opts: { stdio: ['pipe', 'pipe', 'pipe'] },
        }))
      : null;

    if (this.bridge) {
      this.bridge.on('started', () => this.emit('bridgeStatus', 'running'));
      this.bridge.on('exited', () => this.emit('bridgeStatus', this.bridge.status));
      this.bridge.on('failed', () => this.emit('bridgeStatus', 'failed'));
    }

    // --- MJPEG preview fan-out ---
    this._previewPort = 0;
    this._previewServer = null;
    this._previewBuf = Buffer.alloc(0);
    /** @type {Set<import('http').ServerResponse>} */
    this._previewClients = new Set();
  }

  async init() {
    if (this.bridge) this.bridge.start();
    await this._startPreviewServer();
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

  /** Extract complete JPEGs from the MJPEG byte stream and fan them out. */
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
    // Never let a malformed stream grow the buffer unbounded.
    if (this._previewBuf.length > 5 * 1024 * 1024) this._previewBuf = Buffer.alloc(0);
  }

  _broadcastPreview(jpeg) {
    this.emit('previewFrame');
    const header = Buffer.from(
      `--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpeg.length}\r\n\r\n`
    );
    for (const res of this._previewClients) {
      // Drop frames for slow clients rather than buffering (writableLength check).
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

  get running() {
    return Boolean(this.ffmpeg);
  }

  /**
   * Start decoding a resolved stream URL. Kills any previous FFmpeg first;
   * the vcam bridge holds the last frame during the gap.
   */
  start(videoUrl, { isLive = false } = {}) {
    this.stopFfmpeg({ blank: false });
    this._stopping = false;
    this._frameAcc = Buffer.alloc(0);
    this._lastStderr = [];

    const { width, height, fps } = config.vcam;
    const p = config.preview;
    const args = ['-hide_banner', '-loglevel', 'warning', '-nostdin'];

    const hwaccel = this._hwaccelDisabled ? 'none' : config.ffmpeg.hwaccel;
    if (hwaccel && hwaccel !== 'none') args.push('-hwaccel', hwaccel);

    if (/^https?:/i.test(videoUrl)) {
      args.push('-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5');
    }
    if (isLive) args.push('-fflags', 'nobuffer');

    args.push('-i', videoUrl);

    // Output 1: fixed-format raw NV12 for the virtual camera.
    args.push(
      '-map', '0:v:0', '-an',
      '-vf',
      `scale=${width}:${height}:force_original_aspect_ratio=decrease:flags=fast_bilinear,` +
        `pad=${width}:${height}:-1:-1:color=black,fps=${fps},format=nv12`,
      '-f', 'rawvideo', 'pipe:1'
    );

    // Output 2: low-res MJPEG for the browser Program panel.
    args.push(
      '-map', '0:v:0', '-an',
      '-vf', `scale=${p.width}:-2:flags=fast_bilinear,fps=${p.fps}`,
      '-c:v', 'mjpeg', '-q:v', String(p.quality),
      '-f', 'mjpeg', `tcp://127.0.0.1:${this._previewPort}`
    );

    logger.info({ isLive, hwaccel }, 'starting ffmpeg pipeline');
    const proc = spawn(config.paths.ffmpeg, args, {
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    this.ffmpeg = proc;
    this._ffmpegStartedAt = Date.now();

    proc.stdout.on('data', (chunk) => this._onRawVideo(proc, chunk));
    proc.stderr.on('data', (d) => {
      const line = d.toString().trim();
      if (!line) return;
      logger.debug({ name: 'ffmpeg' }, line);
      this._lastStderr.push(line);
      if (this._lastStderr.length > 20) this._lastStderr.shift();
    });
    proc.on('error', (err) => logger.error({ err }, 'ffmpeg spawn error'));
    proc.once('exit', (code) => {
      if (this.ffmpeg !== proc) return; // superseded by a newer start()
      this.ffmpeg = null;
      const uptimeMs = Date.now() - this._ffmpegStartedAt;
      const expected = this._stopping;

      // One automatic retry without hardware accel if hw decode init failed fast.
      if (!expected && !this._hwaccelDisabled && uptimeMs < QUICK_FAIL_MS &&
          config.ffmpeg.hwaccelFallback && hwaccel !== 'none' &&
          /d3d11|dxva|cuda|qsv|hwaccel|hardware/i.test(this._lastStderr.join('\n'))) {
        logger.warn('hardware decode failed, retrying in software mode');
        this._hwaccelDisabled = true;
        this.start(videoUrl, { isLive });
        return;
      }

      logger[expected ? 'info' : 'warn']({ code, uptimeMs, expected }, 'ffmpeg exited');
      this.emit('exit', { code, uptimeMs, expected, stderr: this._lastStderr.join('\n') });
    });
  }

  /**
   * Forward decoded frames to the bridge in whole-frame units so a mid-frame
   * FFmpeg kill can never misalign the bridge's reader.
   */
  _onRawVideo(proc, chunk) {
    this._frameAcc = this._frameAcc.length
      ? Buffer.concat([this._frameAcc, chunk])
      : chunk;
    while (this._frameAcc.length >= this.frameSize) {
      const frame = this._frameAcc.subarray(0, this.frameSize);
      this._frameAcc = this._frameAcc.subarray(this.frameSize);
      this._writeToBridge(frame, proc);
    }
  }

  _writeToBridge(frame, sourceProc) {
    const b = this.bridge && this.bridge.proc;
    if (!b || !b.stdin.writable) return;
    if (!b.stdin.write(frame) && sourceProc && sourceProc.stdout) {
      // Backpressure: pause ffmpeg output until the bridge drains.
      sourceProc.stdout.pause();
      b.stdin.once('drain', () => {
        if (sourceProc.stdout) sourceProc.stdout.resume();
      });
    }
  }

  /** Stop FFmpeg. With blank=true the vcam is fed one black frame (deliberate stop). */
  stopFfmpeg({ blank = true } = {}) {
    if (this.ffmpeg) {
      this._stopping = true;
      try {
        this.ffmpeg.kill('SIGKILL');
      } catch (_) { /* already gone */ }
      this.ffmpeg = null;
    }
    this._frameAcc = Buffer.alloc(0);
    if (blank && config.vcam.enabled) {
      this._writeToBridge(blackNv12Frame(config.vcam.width, config.vcam.height), null);
    }
  }

  async shutdown() {
    this.stopFfmpeg({ blank: true });
    if (this.bridge) this.bridge.stop();
    if (this._previewServer) this._previewServer.close();
    for (const res of this._previewClients) res.end();
  }

  get bridgeStatus() {
    if (!config.vcam.enabled) return 'disabled';
    return this.bridge ? this.bridge.status : 'disabled';
  }
}

module.exports = { MediaPipeline };
