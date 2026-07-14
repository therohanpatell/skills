'use strict';

const { execFile } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { EventEmitter } = require('events');
const config = require('../lib/config');
const logger = require('../lib/logger');
const { classifyYtdlpError } = require('../lib/yt-errors');
const { ValidationError, NotFoundError, AppError } = require('../lib/errors');

const YT_URL_RE =
  /^https?:\/\/(www\.|m\.|music\.)?(youtube\.com\/(watch\?|live\/|shorts\/|embed\/)|youtu\.be\/)/i;

const THUMB_DIR = path.join(config.paths.dataDir, 'thumbs');

/**
 * Owns the source list: validation, yt-dlp resolution, resolved-URL caching
 * with expiry, and thumbnail caching. Pure metadata layer — it never decodes
 * video, so 20 idle sources cost nothing beyond a few cached JPEGs.
 *
 * Emits: 'sourceChanged' (source), 'sourcesChanged' ().
 */
class SourceManager extends EventEmitter {
  constructor(stateStore) {
    super();
    this.store = stateStore;
    /** @type {Map<string, {videoUrl, audioUrl, isLive, resolvedAt}>} */
    this.resolved = new Map();
    /** runtime-only status per source id: idle|resolving|ready|error */
    this.runtime = new Map();
    fs.mkdirSync(THUMB_DIR, { recursive: true });
  }

  list() {
    return this.store.state.sources.map((s) => ({
      ...s,
      ...(this.runtime.get(s.id) || { status: 'idle' }),
    }));
  }

  get(id) {
    const s = this.store.state.sources.find((x) => x.id === id);
    if (!s) throw new NotFoundError(`No source with id ${id}`);
    return s;
  }

  async add(url) {
    url = String(url || '').trim();
    if (!YT_URL_RE.test(url)) {
      throw new ValidationError('Not a recognized YouTube video or live URL');
    }
    const { sources } = this.store.state;
    if (sources.length >= config.limits.maxSources) {
      throw new ValidationError(`Maximum of ${config.limits.maxSources} sources reached`);
    }
    if (sources.some((s) => s.url === url)) {
      throw new ValidationError('URL already added');
    }

    const source = {
      id: crypto.randomUUID().slice(0, 8),
      url,
      title: url,
      thumbnail: null,
      isLive: false,
      addedAt: Date.now(),
    };
    this.store.update((st) => st.sources.push(source));
    this.emit('sourcesChanged');

    // Resolve in the background; the card shows a spinner meanwhile.
    this.resolve(source.id).catch(() => {});
    return source;
  }

  remove(id) {
    this.get(id);
    this.store.update((st) => {
      st.sources = st.sources.filter((s) => s.id !== id);
      if (st.programId === id) st.programId = null;
    });
    this.resolved.delete(id);
    this.runtime.delete(id);
    this._deleteThumb(id);
    this.emit('sourcesChanged');
  }

  clear() {
    for (const s of this.store.state.sources) this._deleteThumb(s.id);
    this.store.update((st) => {
      st.sources = [];
      st.programId = null;
    });
    this.resolved.clear();
    this.runtime.clear();
    this.emit('sourcesChanged');
  }

  /**
   * Run yt-dlp for a source, updating metadata + resolved stream URLs.
   * Safe to call repeatedly; concurrent calls for the same id are coalesced.
   */
  async resolve(id) {
    const source = this.get(id);
    const rt = this.runtime.get(id);
    if (rt && rt.status === 'resolving' && rt.promise) return rt.promise;

    const promise = this._doResolve(source);
    this._setRuntime(id, { status: 'resolving', error: null, promise });
    try {
      const info = await promise;
      this._setRuntime(id, { status: 'ready', error: null });
      return info;
    } catch (err) {
      this._setRuntime(id, {
        status: 'error',
        error: { code: err.code || 'RESOLVE_FAILED', message: err.message },
      });
      throw err;
    }
  }

  async _doResolve(source) {
    const args = [
      '--no-warnings',
      '--no-playlist',
      '-f',
      `bestvideo[height<=${config.resolve.maxVideoHeight}]+bestaudio/best[height<=${config.resolve.maxVideoHeight}]/best`,
      '-j',
      source.url,
    ];
    logger.info({ id: source.id, url: source.url }, 'resolving with yt-dlp');

    const json = await new Promise((resolvePromise, reject) => {
      execFile(
        config.paths.ytdlp,
        args,
        { timeout: config.resolve.timeoutMs, maxBuffer: 64 * 1024 * 1024, windowsHide: true },
        (err, stdout, stderr) => {
          if (err) {
            if (err.code === 'ENOENT') {
              return reject(new AppError('yt-dlp not found — check YTSW_YTDLP_PATH', { code: 'YTDLP_MISSING' }));
            }
            const cls = classifyYtdlpError(stderr);
            const e = new AppError(cls.message, { code: cls.code, status: 422 });
            return reject(e);
          }
          try {
            resolvePromise(JSON.parse(stdout));
          } catch (_) {
            reject(new AppError('yt-dlp returned unparseable output', { code: 'RESOLVE_FAILED' }));
          }
        }
      );
    });

    // For VOD, yt-dlp usually selects separate video+audio streams; for live,
    // a single HLS manifest carries both.
    let videoUrl = json.url || null;
    let audioUrl = null;
    if (Array.isArray(json.requested_formats) && json.requested_formats.length) {
      const vid = json.requested_formats.find((f) => f.vcodec && f.vcodec !== 'none');
      const aud = json.requested_formats.find((f) => f.acodec && f.acodec !== 'none' && (!f.vcodec || f.vcodec === 'none'));
      videoUrl = (vid && vid.url) || videoUrl;
      audioUrl = (aud && aud.url) || null;
    }
    if (!videoUrl) {
      throw new AppError('No playable stream URL found', { code: 'RESOLVE_FAILED', status: 422 });
    }

    const isLive = Boolean(json.is_live);
    this.resolved.set(source.id, { videoUrl, audioUrl, isLive, resolvedAt: Date.now() });

    this.store.update((st) => {
      const s = st.sources.find((x) => x.id === source.id);
      if (!s) return;
      s.title = json.title || s.title;
      s.isLive = isLive;
      s.duration = json.duration || null;
      s.channel = json.channel || json.uploader || null;
    });

    if (json.thumbnail) this._cacheThumbnail(source.id, json.thumbnail).catch(() => {});
    return this.resolved.get(source.id);
  }

  /**
   * Return resolved stream URLs, re-resolving when the cache entry is missing
   * or older than the freshness window (YouTube URLs expire after ~6h; live
   * manifests sooner). `force` bypasses the cache (used after a 403).
   */
  async getFreshStreams(id, { force = false } = {}) {
    const entry = this.resolved.get(id);
    const maxAge = entry && entry.isLive ? config.resolve.liveRefreshMs : config.resolve.vodRefreshMs;
    if (!force && entry && Date.now() - entry.resolvedAt < maxAge) return entry;
    return this.resolve(id);
  }

  thumbnailPath(id) {
    const p = path.join(THUMB_DIR, `${id}.jpg`);
    return fs.existsSync(p) ? p : null;
  }

  async _cacheThumbnail(id, url) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(15000) });
      if (!res.ok) return;
      const buf = Buffer.from(await res.arrayBuffer());
      fs.writeFileSync(path.join(THUMB_DIR, `${id}.jpg`), buf);
      this.store.update((st) => {
        const s = st.sources.find((x) => x.id === id);
        if (s) s.thumbnail = `/api/thumb/${id}`;
      });
      this.emit('sourceChanged', id);
    } catch (err) {
      logger.warn({ id, err: err.message }, 'thumbnail fetch failed');
    }
  }

  _deleteThumb(id) {
    try {
      fs.unlinkSync(path.join(THUMB_DIR, `${id}.jpg`));
    } catch (_) {
      /* not cached */
    }
  }

  _setRuntime(id, patch) {
    this.runtime.set(id, { ...(this.runtime.get(id) || {}), ...patch });
    this.emit('sourceChanged', id);
  }
}

module.exports = { SourceManager };
