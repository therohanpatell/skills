'use strict';

const fs = require('fs');
const path = require('path');
const config = require('../lib/config');
const logger = require('../lib/logger');

const STATE_FILE = path.join(config.paths.dataDir, 'state.json');
const TMP_FILE = STATE_FILE + '.tmp';
const SAVE_DEBOUNCE_MS = 500;

const DEFAULT_STATE = {
  sources: [], // [{ id, url, title, thumbnail, isLive, addedAt }]
  programId: null,
  settings: {
    volume: 100,
    muted: false,
    speed: 1,
    monitorEnabled: null, // null = follow config default
    monitorMode: null,    // null = follow config; 'video-audio' | 'audio-only'
    vcamDevice: null,     // null = follow default plugin device
  },
};

/**
 * Persistent application state, saved as an atomically-replaced JSON file.
 * Writes are debounced so rapid UI actions don't thrash the disk; the final
 * state is always flushed on shutdown via flushSync().
 */
class StateStore {
  constructor() {
    this._state = this._load();
    this._timer = null;
    this._dirty = false;
  }

  get state() {
    return this._state;
  }

  _load() {
    try {
      if (fs.existsSync(STATE_FILE)) {
        const parsed = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
        return {
          ...structuredClone(DEFAULT_STATE),
          ...parsed,
          settings: { ...DEFAULT_STATE.settings, ...(parsed.settings || {}) },
        };
      }
    } catch (err) {
      logger.error({ err }, 'state file corrupt, starting fresh');
    }
    return structuredClone(DEFAULT_STATE);
  }

  /** Mutate state via fn(state), then schedule a debounced save. */
  update(fn) {
    fn(this._state);
    this._dirty = true;
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this._write(), SAVE_DEBOUNCE_MS);
  }

  _write() {
    try {
      fs.writeFileSync(TMP_FILE, JSON.stringify(this._state, null, 2));
      fs.renameSync(TMP_FILE, STATE_FILE);
      this._dirty = false;
    } catch (err) {
      logger.error({ err }, 'failed to persist state');
    }
  }

  flushSync() {
    clearTimeout(this._timer);
    if (this._dirty) this._write();
  }
}

module.exports = { StateStore };
