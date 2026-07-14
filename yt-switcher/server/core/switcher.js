'use strict';

const { EventEmitter } = require('events');
const config = require('../lib/config');
const logger = require('../lib/logger');

const RETRY_DELAYS_MS = [3000, 8000, 20000];
const REFRESH_CHECK_MS = 60000;
const FAST_EXIT_MS = 15000;

/**
 * The program state machine — the "T-bar" of the switcher. Coordinates the
 * SourceManager (fresh URLs), MediaPipeline (vcam feed) and MpvMonitor
 * (operator monitor) so that a card click swaps everything atomically.
 *
 * A generation counter guards every async step: if the operator switches again
 * mid-transition, the stale transition abandons itself silently.
 *
 * Emits: 'programChanged' ({ programId, status, error }).
 */
class Switcher extends EventEmitter {
  constructor({ store, sources, pipeline, monitor }) {
    super();
    this.store = store;
    this.sources = sources;
    this.pipeline = pipeline;
    this.monitor = monitor;

    this.status = 'idle'; // idle | resolving | starting | playing | error
    this.error = null;
    this._gen = 0;
    this._retryCount = 0;
    this._retryTimer = null;

    this.pipeline.on('exit', (info) => this._onPipelineExit(info));
    this.monitor.on('ended', () => this._onStreamEnded());

    // Periodically refresh expiring live-stream URLs and restart seamlessly.
    this._refreshTimer = setInterval(() => this._checkUrlFreshness(), REFRESH_CHECK_MS);
  }

  get programId() {
    return this.store.state.programId;
  }

  snapshot() {
    return { programId: this.programId, status: this.status, error: this.error };
  }

  /** Switch Program to a source. This is the hot path — must feel instant. */
  async setProgram(id) {
    this.sources.get(id); // throws NotFoundError for bad ids
    const gen = ++this._gen;
    clearTimeout(this._retryTimer);
    this._retryCount = 0;

    this.store.update((st) => (st.programId = id));
    this._setStatus('resolving');

    try {
      const streams = await this.sources.getFreshStreams(id);
      if (gen !== this._gen) return; // superseded by a newer switch

      this._setStatus('starting');
      this.pipeline.start(streams.videoUrl, { isLive: streams.isLive });
      const { volume, muted } = this.store.state.settings;
      await this.monitor.load(streams.videoUrl, streams.audioUrl, { volume, muted });
      if (gen !== this._gen) return;
      this._setStatus('playing');
    } catch (err) {
      if (gen !== this._gen) return;
      logger.error({ id, err: err.message }, 'switch failed');
      this._setStatus('error', { code: err.code || 'SWITCH_FAILED', message: err.message });
    }
  }

  async stopProgram() {
    this._gen++;
    clearTimeout(this._retryTimer);
    this.store.update((st) => (st.programId = null));
    this.pipeline.stopFfmpeg({ blank: true });
    await this.monitor.stopPlayback();
    this._setStatus('idle');
  }

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

  /**
   * FFmpeg died while we were live. Fast exits usually mean an expired or
   * broken stream URL, so retries force a fresh yt-dlp resolution. Retries
   * are bounded; then the operator sees an error card.
   */
  _onPipelineExit({ expected, uptimeMs }) {
    if (expected || !this.programId || this.status === 'idle') return;

    const id = this.programId;
    const attempt = this._retryCount;
    if (attempt >= RETRY_DELAYS_MS.length) {
      this._setStatus('error', {
        code: 'PLAYBACK_LOST',
        message: 'Stream failed repeatedly — it may have ended or be unavailable',
      });
      return;
    }
    this._retryCount = attempt + 1;
    const delay = uptimeMs > FAST_EXIT_MS ? RETRY_DELAYS_MS[0] : RETRY_DELAYS_MS[attempt];
    this._setStatus('starting', null);
    logger.warn({ id, attempt: attempt + 1, delay }, 'pipeline lost, scheduling reconnect');

    const gen = this._gen;
    this._retryTimer = setTimeout(async () => {
      if (gen !== this._gen) return;
      try {
        const streams = await this.sources.getFreshStreams(id, { force: uptimeMs < FAST_EXIT_MS });
        if (gen !== this._gen) return;
        this.pipeline.start(streams.videoUrl, { isLive: streams.isLive });
        this._setStatus('playing');
        // A healthy run for a while resets the retry budget.
        setTimeout(() => {
          if (gen === this._gen && this.status === 'playing') this._retryCount = 0;
        }, 60000);
      } catch (err) {
        if (gen !== this._gen) return;
        this._onPipelineExit({ expected: false, uptimeMs: 0 });
      }
    }, delay);
  }

  _onStreamEnded() {
    if (!this.programId) return;
    const source = this.store.state.sources.find((s) => s.id === this.programId);
    if (source && source.isLive) {
      logger.info({ id: source.id }, 'live stream ended');
      this.pipeline.stopFfmpeg({ blank: true });
      this._setStatus('error', { code: 'LIVE_ENDED', message: 'Live stream has ended' });
    } else {
      this._setStatus('idle');
    }
  }

  /** Proactively re-resolve long-running live URLs before they expire. */
  async _checkUrlFreshness() {
    const id = this.programId;
    if (!id || this.status !== 'playing') return;
    const entry = this.sources.resolved.get(id);
    if (!entry) return;
    const maxAge = entry.isLive ? config.resolve.liveRefreshMs : config.resolve.vodRefreshMs;
    if (Date.now() - entry.resolvedAt < maxAge) return;

    logger.info({ id }, 'stream URL nearing expiry, refreshing');
    const gen = this._gen;
    try {
      const streams = await this.sources.resolve(id);
      if (gen !== this._gen || this.status !== 'playing') return;
      this.pipeline.start(streams.videoUrl, { isLive: streams.isLive });
      const { volume, muted } = this.store.state.settings;
      await this.monitor.load(streams.videoUrl, streams.audioUrl, { volume, muted });
    } catch (err) {
      logger.warn({ id, err: err.message }, 'scheduled refresh failed; retry on next tick');
    }
  }

  _setStatus(status, error = null) {
    this.status = status;
    this.error = error;
    this.emit('programChanged', this.snapshot());
  }

  shutdown() {
    this._gen++;
    clearInterval(this._refreshTimer);
    clearTimeout(this._retryTimer);
  }
}

module.exports = { Switcher };
