'use strict';

/**
 * MpvController — the playback + audio engine.
 *
 * Owns an mpv process and communicates over its JSON IPC named pipe.
 * Handles all playback (video + audio), seeking, volume, mute.
 * Speed is locked to 1× through three independent mechanisms:
 *   1. --speed=1 launch argument
 *   2. speed=1 in loadfile options string
 *   3. set_property speed 1 after every load
 *   4. observe_property guard that forces speed back if anything changes it
 *
 * Audio reliability:
 *   - volume/mute are set AFTER the file has loaded and mpv has initialized
 *     the audio pipeline (with a 200ms settle delay), not before.
 *   - loadfile options are a comma-separated string (mpv IPC requires this;
 *     passing a JS array silently fails and was the root cause of audio
 *     tracks not loading).
 *
 * Live stream stability:
 *   - cache=yes with 10s read-ahead and 15s cache window (the previous
 *     cache=no / readahead=0 gave mpv zero buffer, causing freeze after
 *     15-20s of playback).
 *
 * Property updates:
 *   - Polled every 500ms in parallel (Promise.all) for smooth timeline.
 *   - observe_property for speed (auto-correct) and eof-reached (instant
 *     end detection).
 *
 * Emits: 'props' (property snapshot), 'status' (process status),
 *        'ended' (playback reached end / stream ended).
 */

const net = require('net');
const { EventEmitter } = require('events');
const config = require('../../lib/config');
const logger = require('../../lib/logger');
const { ManagedProcess } = require('../supervisor');

// Timing constants
const IPC_RETRY_MS = 300;
const IPC_TIMEOUT_MS = 15000;
const POLL_MS = 500;
const CMD_TIMEOUT_MS = 5000;
const AUDIO_SETTLE_MS = 200;

// Properties polled every POLL_MS for timeline + UI
const POLLED_PROPS = [
  'time-pos',
  'duration',
  'demuxer-cache-duration',
  'estimated-vf-fps',
  'video-params',
  'pause',
  'mute',
  'volume',
  'speed',
  'percent-pos',
  'playtime-remaining',
];

// Observer IDs for reactive property changes
const OBS_SPEED = 1;
const OBS_EOF = 2;

class MpvController extends EventEmitter {
  constructor() {
    super();

    /** Whether the monitor is enabled (user toggle). */
    this.enabled = false;

    /** Active IPC socket (null when disconnected). */
    this.sock = null;

    // IPC bookkeeping
    this._reqId = 100; // Start above observer IDs to avoid collisions
    this._pending = new Map(); // request_id → resolve callback
    this._rxBuf = '';

    // Timers
    this._pollTimer = null;
    this._connectTimer = null;

    // Playback state
    this._hasMedia = false;
    this._isLive = false;
    this.currentSpeed = 1;

    // Supervised mpv process
    this.proc = new ManagedProcess('mpv', () => ({
      cmd: config.paths.mpv,
      args: this._buildArgs(),
      opts: { stdio: ['ignore', 'ignore', 'pipe'] },
    }));

    this.proc.on('started', () => {
      this.emit('status', 'starting');
      this._connectIpc(Date.now());
    });
    this.proc.on('exited', () => {
      this._teardown();
      this.emit('status', this.proc.status);
    });
    this.proc.on('failed', () => this.emit('status', 'failed'));
  }

  /* ================================================================
   *  Launch Arguments
   * ================================================================ */

  _buildArgs() {
    const args = [
      `--input-ipc-server=${config.mpvIpcPath}`,
      '--idle=yes',         // Start idle, wait for loadfile commands
      '--keep-open=yes',    // Don't exit when playback ends
      '--no-terminal',      // No terminal output (we use IPC)

      // Speed lock — mechanism 1 of 4
      '--speed=1',

      // Cache defaults (overridden per-source via IPC before each load)
      '--cache=yes',
      '--demuxer-max-bytes=64MiB',
      '--demuxer-max-back-bytes=16MiB',

      // Hardware decoding
      `--hwdec=${config.monitor.hwdec}`,
    ];

    // Video vs audio-only mode
    if (config.monitor.mode === 'audio-only') {
      args.push('--video=no');
    } else {
      args.push(
        '--force-window=yes',
        '--osc=yes',
        '--title=YT Switcher \u2014 Program Monitor',
        '--autofit=45%'
      );
    }

    // Audio device routing (e.g. VB-Cable for conferencing apps)
    const ad = config.monitor.audioDevice;
    if (ad && ad !== 'auto') {
      args.push(`--audio-device=${ad}`);
    }

    return args;
  }

  /* ================================================================
   *  Lifecycle
   * ================================================================ */

  start() {
    this.enabled = true;
    this.proc.start();
  }

  stop() {
    this.enabled = false;
    this._teardown();
    this.proc.stop();
    this.emit('status', 'stopped');
  }

  get status() {
    if (!this.enabled) return 'disabled';
    return this.sock ? 'running' : this.proc.status;
  }

  get pid() {
    return this.proc.pid;
  }

  /* ================================================================
   *  IPC Connection
   * ================================================================ */

  _connectIpc(startedAt) {
    clearTimeout(this._connectTimer);
    if (!this.enabled || !this.proc.proc) return;

    if (Date.now() - startedAt > IPC_TIMEOUT_MS) {
      logger.error('mpv IPC connect timed out');
      return;
    }

    const sock = net.connect(config.mpvIpcPath);

    sock.once('connect', () => {
      logger.info('mpv IPC connected');
      this.sock = sock;
      this._rxBuf = '';

      sock.on('data', (d) => this._onData(d));
      sock.on('close', () => {
        if (this.sock === sock) this._teardown();
      });
      sock.on('error', () => {});

      // Set up property observers for reactive state changes
      this._initObservers();

      // Start periodic polling for timeline properties
      this._pollTimer = setInterval(() => this._poll(), POLL_MS);

      this.emit('status', 'running');
    });

    sock.once('error', () => {
      sock.destroy();
      this._connectTimer = setTimeout(
        () => this._connectIpc(startedAt),
        IPC_RETRY_MS
      );
    });
  }

  /** Set up observe_property for critical reactive state. */
  async _initObservers() {
    // Speed observer — mechanism 4 of 4: auto-correct any drift
    await this.command(['observe_property', OBS_SPEED, 'speed']);
    // EOF observer — instant end detection
    await this.command(['observe_property', OBS_EOF, 'eof-reached']);
  }

  /** Tear down IPC socket, timers, and pending requests. */
  _teardown() {
    clearInterval(this._pollTimer);
    this._pollTimer = null;
    if (this.sock) {
      this.sock.destroy();
      this.sock = null;
    }
    for (const resolve of this._pending.values()) resolve(null);
    this._pending.clear();
  }

  /* ================================================================
   *  IPC Protocol
   * ================================================================ */

  /** Parse newline-delimited JSON messages from mpv. */
  _onData(data) {
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

      // Command response — resolve the pending promise
      if (msg.request_id && this._pending.has(msg.request_id)) {
        this._pending.get(msg.request_id)(msg);
        this._pending.delete(msg.request_id);
        continue;
      }

      // Observed property change
      if (msg.event === 'property-change') {
        this._onObservedChange(msg);
        continue;
      }

      // End-of-file event
      if (msg.event === 'end-file' && msg.reason === 'eof') {
        this.emit('ended');
      }
    }
  }

  /** Handle observed property changes. */
  _onObservedChange(msg) {
    if (msg.id === OBS_SPEED && msg.data != null && Math.abs(msg.data - this.currentSpeed) > 0.01) {
      // Speed drifted from selected speed — correct it
      logger.warn({ speed: msg.data, target: this.currentSpeed }, 'speed drifted, correcting');
      this.command(['set_property', 'speed', this.currentSpeed]);
    }
    if (msg.id === OBS_EOF && msg.data === true && this._hasMedia) {
      this.emit('ended');
    }
  }

  /**
   * Send a command to mpv over IPC.
   * Resolves with mpv's JSON reply, or null if disconnected/timed out.
   * @param {any[]} args - mpv command array
   * @returns {Promise<object|null>}
   */
  command(args) {
    return new Promise((resolve) => {
      if (!this.sock) return resolve(null);

      const id = this._reqId++;
      this._pending.set(id, resolve);

      try {
        this.sock.write(JSON.stringify({ command: args, request_id: id }) + '\n');
      } catch (_) {
        this._pending.delete(id);
        return resolve(null);
      }

      // Timeout — never leak a pending entry if mpv doesn't respond
      setTimeout(() => {
        if (this._pending.has(id)) {
          this._pending.delete(id);
          resolve(null);
        }
      }, CMD_TIMEOUT_MS);
    });
  }

  /* ================================================================
   *  Property Polling
   * ================================================================ */

  /** Poll all timeline properties in parallel for fast snapshots. */
  async _poll() {
    if (!this.sock || !this._hasMedia) return;

    const results = await Promise.all(
      POLLED_PROPS.map((name) => this.command(['get_property', name]))
    );

    const out = {};
    for (let i = 0; i < POLLED_PROPS.length; i++) {
      const r = results[i];
      if (r && r.error === 'success') out[POLLED_PROPS[i]] = r.data;
    }
    out.isLive = this._isLive;
    this.emit('props', out);
  }

  /* ================================================================
   *  Playback
   * ================================================================ */

  /**
   * Load a stream (replacing whatever is playing).
   * @param {string} videoUrl - Video stream URL
   * @param {string|null} audioUrl - Separate audio URL (VOD split streams)
   * @param {object} opts
   * @param {number}  [opts.volume]    - Volume level (0-130)
   * @param {boolean} [opts.muted]     - Mute state
   * @param {number}  [opts.speed]     - Playback speed (0.25-2.0)
   * @param {boolean} [opts.isLive]    - Live stream flag
   * @param {string}  [opts.userAgent] - HTTP User-Agent
   */
  async load(videoUrl, audioUrl, { volume, muted, speed, isLive = false, userAgent = null } = {}) {
    if (!this.enabled) return;
    this._isLive = isLive;

    const spd = (typeof speed === 'number' && speed > 0) ? speed : this.currentSpeed;
    this.currentSpeed = spd;

    // ---- Configure cache/buffering BEFORE loading ----
    // Live streams need a real buffer to absorb network jitter.
    // Zero buffer was the root cause of the 15-20s freeze.
    if (isLive) {
      await this.command(['set_property', 'cache', 'yes']);
      await this.command(['set_property', 'demuxer-readahead-secs', 10]);
      await this.command(['set_property', 'cache-secs', 15]);
    } else {
      await this.command(['set_property', 'cache', 'yes']);
      await this.command(['set_property', 'demuxer-readahead-secs', 5]);
      await this.command(['set_property', 'cache-secs', 30]);
    }

    // ---- Build loadfile options as comma-separated string ----
    // mpv's JSON IPC expects options as a single string: "key1=val1,key2=val2"
    // Passing a JS array silently fails — this was the root cause of audio
    // tracks not loading in the old implementation.
    const optParts = [`speed=${spd}`];
    if (audioUrl) {
      optParts.push(`audio-file=${audioUrl}`);
      optParts.push('aid=1');
    }
    if (userAgent) {
      optParts.push(`user-agent=${userAgent}`);
    }

    // ---- Load the file ----
    await this.command(['loadfile', videoUrl, 'replace', optParts.join(',')]);

    // ---- Wait for mpv to initialize the new file's audio pipeline ----
    // Without this delay, volume/mute commands arrive before mpv has set up
    // audio for the new file, and are silently dropped. This was the root
    // cause of "need to click mute/unmute multiple times".
    await new Promise((r) => setTimeout(r, AUDIO_SETTLE_MS));

    // ---- Set playback state AFTER load ----
    await this.command(['set_property', 'pause', false]);
    await this.command(['set_property', 'speed', spd]);

    if (typeof volume === 'number') {
      await this.command(['set_property', 'volume', volume]);
    }
    if (typeof muted === 'boolean') {
      await this.command(['set_property', 'mute', muted]);
    }

    this._hasMedia = true;
  }

  /** Stop all playback. */
  async stopPlayback() {
    this._hasMedia = false;
    this._isLive = false;
    await this.command(['stop']);
  }

  /* ================================================================
   *  Transport Controls
   * ================================================================ */

  setPause(v) {
    return this.command(['set_property', 'pause', Boolean(v)]);
  }

  setMute(v) {
    return this.command(['set_property', 'mute', Boolean(v)]);
  }

  setVolume(v) {
    return this.command(['set_property', 'volume', Math.max(0, Math.min(130, Number(v)))]);
  }

  setSpeed(v) {
    const s = Math.max(0.1, Math.min(4.0, Number(v) || 1));
    this.currentSpeed = s;
    return this.command(['set_property', 'speed', s]);
  }

  setFullscreen(v) {
    return this.command(['set_property', 'fullscreen', Boolean(v)]);
  }

  /* ================================================================
   *  Seek Controls (VOD only — all gated by _isLive check)
   * ================================================================ */

  /**
   * Seek relative (positive = forward, negative = backward).
   * @param {number} seconds
   */
  async seek(seconds) {
    if (!this._hasMedia || this._isLive) return null;
    return this.command(['seek', seconds, 'relative']);
  }

  /**
   * Seek to an absolute position in seconds.
   * @param {number} seconds
   */
  async seekAbsolute(seconds) {
    if (!this._hasMedia || this._isLive) return null;
    return this.command(['seek', Math.max(0, seconds), 'absolute']);
  }

  /**
   * Seek to a percentage of total duration.
   * @param {number} percent - 0 to 100
   */
  async seekPercent(percent) {
    if (!this._hasMedia || this._isLive) return null;
    return this.command([
      'seek',
      Math.max(0, Math.min(100, percent)),
      'absolute-percent',
    ]);
  }

  /**
   * Get the current playback position in seconds.
   * @returns {Promise<number|null>}
   */
  async getTimePos() {
    const r = await this.command(['get_property', 'time-pos']);
    return r && r.error === 'success' ? r.data : null;
  }

  /**
   * Get total stream duration in seconds.
   * @returns {Promise<number|null>}
   */
  async getDuration() {
    const r = await this.command(['get_property', 'duration']);
    return r && r.error === 'success' ? r.data : null;
  }

  /* ================================================================
   *  Audio Device
   * ================================================================ */

  /**
   * Change the audio output device at runtime.
   * @param {string} device - Audio device identifier
   */
  async setAudioDevice(device) {
    return this.command(['set_property', 'audio-device', device]);
  }
}

module.exports = { MpvController };
