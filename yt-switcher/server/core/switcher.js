'use strict';

/**
 * Switcher — the program state machine.
 *
 * Coordinates the MpvController (playback + audio) and VcamPipeline
 * (rendering + virtual camera) to present a unified "Program" concept
 * with clean state transitions.
 *
 * State machine:
 *   idle → resolving → starting → playing ⇄ paused
 *                                    ↓
 *                                  error
 *
 * Key design choices:
 *   - Generation counter (_gen) guards against async races: if the user
 *     switches sources while we're still resolving the previous one,
 *     the stale callback is harmlessly ignored.
 *   - ALL seek operations route through this class — no direct
 *     pipeline/monitor manipulation from routes.
 *   - Live streams retry faster (2s) and always force URL re-resolution
 *     since YouTube live URLs expire quickly.
 *   - On retry, BOTH pipeline and monitor are reloaded to maintain
 *     audio/video sync.
 *   - Periodic URL freshness checks prevent 403s on long-running
 *     sessions by re-resolving URLs proactively.
 *
 * Emits: 'programChanged' (full state snapshot for WebSocket push).
 */

const { EventEmitter } = require('events');
const config = require('../lib/config');
const logger = require('../lib/logger');

// Retry schedule: graduated delays, 4 attempts total
const RETRY_DELAYS = [2000, 5000, 10000, 20000];

// How often to check if the stream URL needs refresh
const REFRESH_CHECK_MS = 60000;

// If FFmpeg exits faster than this, it's probably a bad URL (403, etc.)
const FAST_EXIT_MS = 15000;

class Switcher extends EventEmitter {
  /**
   * @param {object} deps
   * @param {import('./state-store').StateStore} deps.store
   * @param {import('./source-manager').SourceManager} deps.sources
   * @param {import('./pipelines/vcam-pipeline').VcamPipeline} deps.pipeline
   * @param {import('./pipelines/mpv-controller').MpvController} deps.monitor
   */
  constructor({ store, sources, pipeline, monitor }) {
    super();

    this.store = store;
    this.sources = sources;
    this.pipeline = pipeline;
    this.monitor = monitor;

    /** Current program status. */
    this.status = 'idle';

    /** Current error info (null when no error). */
    this.error = null;

    /** Whether playback is paused. */
    this.isPaused = false;

    // Async race protection: incremented on every source switch or stop
    this._gen = 0;

    // Retry state
    this._retryCount = 0;
    this._retryTimer = null;

    // Currently active resolved streams (videoUrl, audioUrl, isLive, userAgent)
    this._currentStreams = null;

    // Wire up events from child components
    this.pipeline.on('exit', (info) => this._onPipelineLost(info));
    this.monitor.on('ended', () => this._onStreamEnded());

    // Periodic URL freshness check
    this._refreshTimer = setInterval(() => this._checkUrlFreshness(), REFRESH_CHECK_MS);
  }

  /* ================================================================
   *  Public Getters
   * ================================================================ */

  /** Currently active program source ID (persisted). */
  get programId() {
    return this.store.state.programId;
  }

  /** Full state snapshot for WebSocket push. */
  snapshot() {
    return {
      programId: this.programId,
      status: this.status,
      error: this.error,
      isPaused: this.isPaused,
      isLive: this._currentStreams ? this._currentStreams.isLive : false,
    };
  }

  /* ================================================================
   *  Program Switching
   * ================================================================ */

  /**
   * Switch to a new source. Resolves its stream URL, starts both the
   * vcam pipeline and mpv monitor.
   * @param {string} id - Source ID
   */
  async setProgram(id) {
    // Validate source exists
    this.sources.get(id);

    // New generation — invalidates any in-flight async work
    const gen = ++this._gen;
    clearTimeout(this._retryTimer);
    this._retryCount = 0;
    this.isPaused = false;

    // Persist the selection
    this.store.update((st) => (st.programId = id));
    this._setStatus('resolving');

    try {
      // Resolve the stream URL via yt-dlp
      const streams = await this.sources.getFreshStreams(id);
      if (gen !== this._gen) return; // Stale — user switched again

      this._currentStreams = streams;
      this._setStatus('starting');

      // Start vcam pipeline (video only — no audioUrl needed)
      const { volume, muted, speed } = this.store.state.settings;
      const currentSpeed = speed || 1;

      this.pipeline.start(streams.videoUrl, {
        isLive: streams.isLive,
        speed: currentSpeed,
        userAgent: streams.userAgent,
      });

      // Start monitor (handles audio + optional operator preview)
      await this.monitor.load(streams.videoUrl, streams.audioUrl, {
        volume,
        muted,
        speed: currentSpeed,
        isLive: streams.isLive,
        userAgent: streams.userAgent,
      });

      if (gen !== this._gen) return; // Stale
      this._setStatus('playing');
    } catch (err) {
      if (gen !== this._gen) return;
      logger.error({ id, err: err.message }, 'program switch failed');
      this._setStatus('error', {
        code: err.code || 'SWITCH_FAILED',
        message: err.message,
      });
    }
  }

  /** Stop the current program. */
  async stopProgram() {
    this._gen++;
    clearTimeout(this._retryTimer);
    this.isPaused = false;
    this._currentStreams = null;

    this.store.update((st) => (st.programId = null));
    this.pipeline.stopFfmpeg({ blank: true });
    await this.monitor.stopPlayback();
    this._setStatus('idle');
  }

  /** Pause playback (both monitor audio and vcam freeze frame). */
  async pauseProgram() {
    if (this.status !== 'playing' || !this.programId) return;
    this.isPaused = true;
    this.pipeline.pause();
    await this.monitor.setPause(true);
    this._setStatus('paused');
  }

  /** Resume playback. For live streams, restarts from the live edge. */
  async resumeProgram() {
    if (this.status !== 'paused' || !this.programId) return;

    if (this._currentStreams && this._currentStreams.isLive) {
      // Live: can't resume mid-stream, must restart from live edge
      this.isPaused = false;
      await this.setProgram(this.programId);
    } else {
      // VOD: unpause in place
      this.isPaused = false;
      this.pipeline.resume();
      await this.monitor.setPause(false);
      this._setStatus('playing');
    }
  }

  /** Change playback speed for both monitor and vcam pipeline. */
  async setSpeed(speed) {
    const spd = Math.max(0.1, Math.min(4.0, Number(speed) || 1));
    await this.monitor.setSpeed(spd);
    this.store.update((st) => (st.settings.speed = spd));

    if (this.programId && this._currentStreams && !this._currentStreams.isLive) {
      const pos = await this.monitor.getTimePos();
      this.pipeline.start(this._currentStreams.videoUrl, {
        isLive: false,
        seekTo: pos || 0,
        speed: spd,
        userAgent: this._currentStreams.userAgent,
      });
    }
  }

  /* ================================================================
   *  Seeking (VOD Only — all methods gate on isLive)
   * ================================================================ */

  /**
   * Seek to an absolute position.
   * @param {number} seconds - Target position in seconds
   */
  async seekTo(seconds) {
    if (!this.programId || !this._currentStreams) return;
    if (this._currentStreams.isLive) return;

    const gen = this._gen;
    this._setStatus('starting');

    const streams = this._currentStreams;
    const targetPos = Math.max(0, Number(seconds) || 0);
    const currentSpeed = this.monitor.currentSpeed || this.store.state.settings.speed || 1;

    // 1. Tell mpv to seek to target position
    await this.monitor.seekAbsolute(targetPos);

    // 2. Restart FFmpeg pipeline at exact target position with current speed
    this.pipeline.start(streams.videoUrl, {
      isLive: false,
      seekTo: targetPos,
      speed: currentSpeed,
      userAgent: streams.userAgent,
    });

    if (this.isPaused) {
      this.isPaused = false;
      await this.monitor.setPause(false);
    }

    if (gen === this._gen) this._setStatus('playing');
  }

  /**
   * Seek to a percentage of total duration.
   * @param {number} percent - 0 to 100
   */
  async seekPercent(percent) {
    if (!this.programId || !this._currentStreams) return;
    if (this._currentStreams.isLive) return;

    const dur = await this.monitor.getDuration();
    if (dur && dur > 0) {
      const targetPos = (Math.max(0, Math.min(100, Number(percent) || 0)) / 100) * dur;
      await this.seekTo(targetPos);
    } else {
      // Fallback
      await this.monitor.seekPercent(percent);
      const pos = await this.monitor.getTimePos();
      if (pos != null) await this.seekTo(pos);
    }
  }

  /**
   * Seek relative (forward/backward).
   * @param {number} seconds - Offset (positive = forward, negative = backward)
   */
  async seekRelative(seconds) {
    if (!this.programId || !this._currentStreams) return;
    if (this._currentStreams.isLive) return;

    const currentPos = await this.monitor.getTimePos();
    const targetPos = Math.max(0, (currentPos || 0) + Number(seconds));
    await this.seekTo(targetPos);
  }

  /* ================================================================
   *  Restore
   * ================================================================ */

  /** Restore the persisted Program selection on startup. */
  async restore() {
    const id = this.programId;
    if (!id) return;
    if (!this.store.state.sources.some((s) => s.id === id)) {
      this.store.update((st) => (st.programId = null));
      return;
    }
    logger.info({ id }, 'restoring program from previous session');
    await this.setProgram(id).catch(() => {});
  }

  /* ================================================================
   *  Recovery (Pipeline Lost / Stream Ended)
   * ================================================================ */

  /**
   * FFmpeg died unexpectedly. Retry with graduated delays.
   * Live streams retry faster and always force URL re-resolution.
   */
  _onPipelineLost({ expected, uptimeMs }) {
    if (expected || !this.programId || this.status === 'idle') return;

    const id = this.programId;
    const source = this.store.state.sources.find((s) => s.id === id);
    const isLive = source && source.isLive;
    const attempt = this._retryCount;

    if (attempt >= RETRY_DELAYS.length) {
      this._setStatus('error', {
        code: 'PLAYBACK_LOST',
        message: 'Stream failed repeatedly \u2014 it may have ended or be unavailable',
      });
      return;
    }

    this._retryCount = attempt + 1;

    // Live streams retry faster; VOD uses graduated delays
    const delay = isLive
      ? Math.min(RETRY_DELAYS[0], 2000)
      : uptimeMs > FAST_EXIT_MS
        ? RETRY_DELAYS[0]
        : RETRY_DELAYS[attempt];

    this._setStatus('starting', null);
    logger.warn({ id, attempt: attempt + 1, delay, isLive }, 'pipeline lost, scheduling retry');

    const gen = this._gen;
    this._retryTimer = setTimeout(async () => {
      if (gen !== this._gen) return;
      try {
        // Live streams: always force re-resolution (URLs expire fast)
        // VOD: only force if it crashed quickly (likely a 403)
        const streams = await this.sources.getFreshStreams(id, {
          force: isLive || uptimeMs < FAST_EXIT_MS,
        });
        if (gen !== this._gen) return;

        this._currentStreams = streams;

        // Restart pipeline
        this.pipeline.start(streams.videoUrl, {
          isLive: streams.isLive,
          userAgent: streams.userAgent,
        });

        // Also reload monitor to keep A/V in sync after recovery
        const { volume, muted, speed } = this.store.state.settings;
        await this.monitor.load(streams.videoUrl, streams.audioUrl, {
          volume,
          muted,
          speed: speed || 1,
          isLive: streams.isLive,
          userAgent: streams.userAgent,
        });

        if (gen !== this._gen) return;
        this._setStatus('playing');

        // A healthy run resets the retry budget
        setTimeout(() => {
          if (gen === this._gen && this.status === 'playing') {
            this._retryCount = 0;
          }
        }, 60000);
      } catch (err) {
        if (gen !== this._gen) return;
        this._onPipelineLost({ expected: false, uptimeMs: 0 });
      }
    }, delay);
  }

  /** mpv reported end of stream. */
  _onStreamEnded() {
    if (!this.programId) return;
    const source = this.store.state.sources.find((s) => s.id === this.programId);

    if (source && source.isLive) {
      logger.info({ id: source.id }, 'live stream ended');
      this.pipeline.stopFfmpeg({ blank: true });
      this._setStatus('error', {
        code: 'LIVE_ENDED',
        message: 'Live stream has ended',
      });
    } else {
      this._setStatus('idle');
    }
  }

  /* ================================================================
   *  URL Freshness
   * ================================================================ */

  /** Proactively refresh stream URLs before they expire. */
  async _checkUrlFreshness() {
    const id = this.programId;
    if (!id || this.status !== 'playing') return;

    const entry = this.sources.resolved.get(id);
    if (!entry) return;

    const maxAge = entry.isLive
      ? config.resolve.liveRefreshMs
      : config.resolve.vodRefreshMs;

    if (Date.now() - entry.resolvedAt < maxAge) return;

    logger.info({ id }, 'proactively refreshing stream URL');
    const gen = this._gen;

    try {
      const streams = await this.sources.resolve(id);
      if (gen !== this._gen || this.status !== 'playing') return;

      this._currentStreams = streams;

      // Hot-swap: restart both pipeline and monitor with new URLs
      this.pipeline.start(streams.videoUrl, {
        isLive: streams.isLive,
        userAgent: streams.userAgent,
      });

      const { volume, muted, speed } = this.store.state.settings;
      await this.monitor.load(streams.videoUrl, streams.audioUrl, {
        volume,
        muted,
        speed: speed || 1,
        isLive: streams.isLive,
        userAgent: streams.userAgent,
      });
    } catch (err) {
      logger.warn({ id, err: err.message }, 'proactive URL refresh failed');
    }
  }

  /* ================================================================
   *  Status
   * ================================================================ */

  _setStatus(status, error = null) {
    this.status = status;
    this.error = error;
    this.emit('programChanged', this.snapshot());
  }

  /* ================================================================
   *  Shutdown
   * ================================================================ */

  shutdown() {
    this._gen++;
    clearInterval(this._refreshTimer);
    clearTimeout(this._retryTimer);
  }
}

module.exports = { Switcher };
