'use strict';

const os = require('os');
const { execFile } = require('child_process');
const { EventEmitter } = require('events');
const pidusage = require('pidusage');
const config = require('../lib/config');
const logger = require('../lib/logger');

/**
 * Samples CPU/RAM for every process in the pipeline plus GPU utilization
 * (NVIDIA only — nvidia-smi is the sole vendor tool that is reliably
 * scriptable; other GPUs show "n/a"). Emits 'stats' on each tick, which the
 * app broadcasts over the WebSocket — the UI never polls.
 */
class StatsMonitor extends EventEmitter {
  /** @param {() => Record<string, number|null>} getPids returns {label: pid} */
  constructor(getPids) {
    super();
    this.getPids = getPids;
    this._timer = null;
    this._nvidiaSmi = undefined; // undefined = not probed yet
  }

  start() {
    this._timer = setInterval(() => this._sample().catch(() => {}), config.stats.intervalMs);
  }

  stop() {
    clearInterval(this._timer);
    pidusage.clear();
  }

  async _sample() {
    const pids = this.getPids();
    const entries = Object.entries(pids).filter(([, pid]) => pid);
    const processes = {};
    let cpuTotal = 0;
    let ramTotal = 0;

    if (entries.length) {
      try {
        const usage = await pidusage(entries.map(([, pid]) => pid));
        for (const [label, pid] of entries) {
          const u = usage[pid];
          if (!u) continue;
          const cpu = u.cpu / os.cpus().length; // normalize to whole-machine %
          processes[label] = { cpu: round1(cpu), ramMb: Math.round(u.memory / 1048576) };
          cpuTotal += cpu;
          ramTotal += u.memory;
        }
      } catch (err) {
        logger.debug({ err: err.message }, 'pidusage sample failed');
      }
    }

    this.emit('stats', {
      processes,
      appCpu: round1(cpuTotal),
      appRamMb: Math.round(ramTotal / 1048576),
      system: {
        ramUsedMb: Math.round((os.totalmem() - os.freemem()) / 1048576),
        ramTotalMb: Math.round(os.totalmem() / 1048576),
      },
      gpu: await this._sampleGpu(),
    });
  }

  async _sampleGpu() {
    if (this._nvidiaSmi === false) return null;
    return new Promise((resolve) => {
      execFile(
        'nvidia-smi',
        ['--query-gpu=utilization.gpu,utilization.decoder,memory.used', '--format=csv,noheader,nounits'],
        { timeout: 3000, windowsHide: true },
        (err, stdout) => {
          if (err) {
            if (this._nvidiaSmi === undefined) {
              this._nvidiaSmi = false;
              logger.info('nvidia-smi not available; GPU stats disabled');
            }
            return resolve(null);
          }
          this._nvidiaSmi = true;
          const [gpu, decoder, mem] = stdout.trim().split(',').map((s) => parseFloat(s));
          resolve({ utilization: gpu, decoder, memMb: mem });
        }
      );
    });
  }
}

function round1(n) {
  return Math.round(n * 10) / 10;
}

module.exports = { StatsMonitor };
