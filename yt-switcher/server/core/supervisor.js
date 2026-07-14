'use strict';

const { spawn } = require('child_process');
const { EventEmitter } = require('events');
const config = require('../lib/config');
const logger = require('../lib/logger');

/**
 * A supervised child process with exponential-backoff restarts and a circuit
 * breaker. Emits: 'started' (proc), 'exited' ({ code, expected }),
 * 'failed' (circuit breaker tripped), 'stderr' (line).
 *
 * Used for the long-lived children (vcam bridge, mpv). FFmpeg is intentionally
 * NOT auto-restarted here — the Switcher owns its lifecycle because a restart
 * may require a fresh resolved URL first.
 */
class ManagedProcess extends EventEmitter {
  /**
   * @param {string} name       Label for logs/UI.
   * @param {() => {cmd: string, args: string[], opts?: object}} buildSpawn
   *                            Called on every (re)start so args can change.
   * @param {{ autoRestart?: boolean }} [options]
   */
  constructor(name, buildSpawn, { autoRestart = true } = {}) {
    super();
    this.name = name;
    this.buildSpawn = buildSpawn;
    this.autoRestart = autoRestart;
    this.proc = null;
    this.status = 'stopped'; // stopped | running | restarting | failed
    this._stopping = false;
    this._restartTimer = null;
    this._failures = []; // timestamps of recent unexpected exits
    this._delay = config.supervisor.restartBaseDelayMs;
  }

  get pid() {
    return this.proc ? this.proc.pid : null;
  }

  start() {
    if (this.proc) return;
    this._stopping = false;
    const { cmd, args, opts = {} } = this.buildSpawn();
    logger.info({ name: this.name, cmd, args: args.join(' ') }, 'spawning process');
    try {
      this.proc = spawn(cmd, args, { windowsHide: true, ...opts });
    } catch (err) {
      logger.error({ name: this.name, err }, 'spawn threw');
      this._onExit(-1);
      return;
    }
    this.status = 'running';
    this._delay = config.supervisor.restartBaseDelayMs;

    if (this.proc.stderr) {
      let buf = '';
      this.proc.stderr.on('data', (d) => {
        buf += d.toString();
        let idx;
        while ((idx = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, idx).trim();
          buf = buf.slice(idx + 1);
          if (line) {
            logger.debug({ name: this.name }, line);
            this.emit('stderr', line);
          }
        }
      });
    }

    this.proc.on('error', (err) => logger.error({ name: this.name, err }, 'process error'));
    this.proc.once('exit', (code) => this._onExit(code));
    this.emit('started', this.proc);
  }

  _onExit(code) {
    const expected = this._stopping;
    logger[expected ? 'info' : 'warn']({ name: this.name, code, expected }, 'process exited');
    this.proc = null;
    this.emit('exited', { code, expected });
    if (expected || !this.autoRestart) {
      this.status = 'stopped';
      return;
    }

    // Circuit breaker: too many unexpected exits inside the window -> give up.
    const now = Date.now();
    this._failures = this._failures
      .filter((t) => now - t < config.supervisor.circuitBreakerWindowMs)
      .concat(now);
    if (this._failures.length >= config.supervisor.circuitBreakerFailures) {
      this.status = 'failed';
      logger.error({ name: this.name }, 'circuit breaker tripped, not restarting');
      this.emit('failed');
      return;
    }

    this.status = 'restarting';
    logger.info({ name: this.name, delayMs: this._delay }, 'scheduling restart');
    this._restartTimer = setTimeout(() => this.start(), this._delay);
    this._delay = Math.min(this._delay * 2, config.supervisor.restartMaxDelayMs);
  }

  /** Reset the circuit breaker and start again (operator action). */
  reset() {
    this._failures = [];
    this._delay = config.supervisor.restartBaseDelayMs;
    if (!this.proc) this.start();
  }

  stop() {
    this._stopping = true;
    clearTimeout(this._restartTimer);
    if (this.proc) {
      try {
        this.proc.kill('SIGKILL');
      } catch (_) {
        /* already gone */
      }
    }
  }
}

module.exports = { ManagedProcess };
