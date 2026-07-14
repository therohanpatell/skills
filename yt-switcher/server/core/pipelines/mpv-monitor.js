'use strict';

const net = require('net');
const { EventEmitter } = require('events');
const config = require('../../lib/config');
const logger = require('../../lib/logger');
const { ManagedProcess } = require('../supervisor');

const IPC_CONNECT_RETRY_MS = 300;
const IPC_CONNECT_TIMEOUT_MS = 15000;
const PROPS_POLL_MS = 1000;

const POLLED_PROPS = [
  'time-pos',
  'duration',
  'demuxer-cache-duration',
  'estimated-vf-fps',
  'video-params',
  'pause',
  'mute',
  'volume',
];

/**
 * The operator's full-quality Program monitor: an mpv window with hardware
 * decoding and audio, remote-controlled over mpv's JSON IPC named pipe.
 * Optional — when disabled the system runs vcam-only with a single decode.
 *
 * Emits: 'props' (polled property snapshot), 'status' (process status),
 * 'ended' (playback reached end / stream ended).
 */
class MpvMonitor extends EventEmitter {
  constructor() {
    super();
    this.enabled = false;
    this.sock = null;
    this._reqId = 1;
    this._pending = new Map(); // request_id -> resolve
    this._rxBuf = '';
    this._pollTimer = null;
    this._connectTimer = null;
    this._hasMedia = false;

    this.proc = new ManagedProcess('mpv', () => ({
      cmd: config.paths.mpv,
      args: [
        `--input-ipc-server=${config.mpvIpcPath}`,
        '--idle=yes',
        '--force-window=yes',
        '--keep-open=yes',
        `--hwdec=${config.monitor.hwdec}`,
        '--no-terminal',
        '--osc=yes',
        '--title=YT Switcher — Program Monitor',
        '--autofit=45%',
        '--cache=yes',
        '--demuxer-max-bytes=64MiB',
        '--demuxer-max-back-bytes=16MiB',
        // 'auto' = system default output; anything else (e.g. a VB-Cable
        // wasapi id from `mpv --audio-device=help`) is passed through.
        ...(config.monitor.audioDevice && config.monitor.audioDevice !== 'auto'
          ? [`--audio-device=${config.monitor.audioDevice}`]
          : []),
      ],
      opts: { stdio: ['ignore', 'ignore', 'pipe'] },
    }));

    this.proc.on('started', () => {
      this.emit('status', 'starting');
      this._connectIpc(Date.now());
    });
    this.proc.on('exited', () => {
      this._teardownIpc();
      this.emit('status', this.proc.status);
    });
    this.proc.on('failed', () => this.emit('status', 'failed'));
  }

  start() {
    this.enabled = true;
    this.proc.start();
  }

  stop() {
    this.enabled = false;
    this._teardownIpc();
    this.proc.stop();
    this.emit('status', 'stopped');
  }

  get status() {
    if (!this.enabled) return 'disabled';
    return this.sock ? 'running' : this.proc.status;
  }

  _connectIpc(startedAt) {
    clearTimeout(this._connectTimer);
    if (!this.enabled || !this.proc.proc) return;
    if (Date.now() - startedAt > IPC_CONNECT_TIMEOUT_MS) {
      logger.error('mpv IPC connect timed out');
      return;
    }
    const sock = net.connect(config.mpvIpcPath);
    sock.once('connect', () => {
      logger.info('mpv IPC connected');
      this.sock = sock;
      this._rxBuf = '';
      sock.on('data', (d) => this._onIpcData(d));
      sock.on('close', () => {
        if (this.sock === sock) this._teardownIpc();
      });
      sock.on('error', () => {});
      this._pollTimer = setInterval(() => this._pollProps(), PROPS_POLL_MS);
      this.emit('status', 'running');
    });
    sock.once('error', () => {
      sock.destroy();
      this._connectTimer = setTimeout(() => this._connectIpc(startedAt), IPC_CONNECT_RETRY_MS);
    });
  }

  _teardownIpc() {
    clearInterval(this._pollTimer);
    this._pollTimer = null;
    if (this.sock) {
      this.sock.destroy();
      this.sock = null;
    }
    for (const resolve of this._pending.values()) resolve(null);
    this._pending.clear();
  }

  _onIpcData(data) {
    this._rxBuf += data.toString();
    let idx;
    while ((idx = this._rxBuf.indexOf('\n')) >= 0) {
      const line = this._rxBuf.slice(0, idx);
      this._rxBuf = this._rxBuf.slice(idx + 1);
      if (!line.trim()) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch (_) {
        continue;
      }
      if (msg.request_id && this._pending.has(msg.request_id)) {
        this._pending.get(msg.request_id)(msg);
        this._pending.delete(msg.request_id);
      } else if (msg.event === 'end-file' && msg.reason === 'eof') {
        this.emit('ended');
      }
    }
  }

  /** Send a command over IPC; resolves with mpv's reply (or null if disconnected). */
  command(args) {
    return new Promise((resolve) => {
      if (!this.sock) return resolve(null);
      const request_id = this._reqId++;
      this._pending.set(request_id, resolve);
      this.sock.write(JSON.stringify({ command: args, request_id }) + '\n');
      // Never leak a pending entry if mpv doesn't answer.
      setTimeout(() => {
        if (this._pending.has(request_id)) {
          this._pending.delete(request_id);
          resolve(null);
        }
      }, 5000);
    });
  }

  async _pollProps() {
    if (!this.sock || !this._hasMedia) return;
    const out = {};
    for (const name of POLLED_PROPS) {
      const r = await this.command(['get_property', name]);
      if (r && r.error === 'success') out[name] = r.data;
    }
    this.emit('props', out);
  }

  /** Load a stream (replacing whatever is playing) — this IS the instant switch. */
  async load(videoUrl, audioUrl, { volume, muted } = {}) {
    if (!this.enabled) return;
    // Separate audio track for VOD (yt-dlp picks split A/V); live HLS is muxed.
    await this.command(['set_property', 'audio-files', audioUrl ? [audioUrl] : []]);
    if (typeof volume === 'number') await this.command(['set_property', 'volume', volume]);
    if (typeof muted === 'boolean') await this.command(['set_property', 'mute', muted]);
    await this.command(['loadfile', videoUrl, 'replace']);
    await this.command(['set_property', 'pause', false]);
    this._hasMedia = true;
  }

  async stopPlayback() {
    this._hasMedia = false;
    await this.command(['stop']);
  }

  setPause(v) { return this.command(['set_property', 'pause', Boolean(v)]); }
  setMute(v) { return this.command(['set_property', 'mute', Boolean(v)]); }
  setVolume(v) { return this.command(['set_property', 'volume', Math.max(0, Math.min(130, Number(v)))]); }
  setFullscreen(v) { return this.command(['set_property', 'fullscreen', Boolean(v)]); }

  get pid() {
    return this.proc.pid;
  }
}

module.exports = { MpvMonitor };
