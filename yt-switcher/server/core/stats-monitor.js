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
 * scriptable; other GPUs show "n/a"). Also aggregates real-time performance
 * metrics from the MediaPipeline for the performance panel.
 *
 * Emits 'stats' on each tick, which the app broadcasts over the WebSocket —
 * the UI never polls.
 */
class StatsMonitor extends EventEmitter {
  /**
   * @param {() => Record<string, number|null>} getPids returns {label: pid}
   * @param {() => object} getPerfMetrics returns pipeline performance metrics
   */
  constructor(getPids, getPerfMetrics) {
    super();
    this.getPids = getPids;
    this.getPerfMetrics = getPerfMetrics || (() => ({}));
    this._timer = null;
    this._nvidiaSmi = undefined; // undefined = not probed yet
  }

  start() {
    this._timer = setInterval(() => this._sample().catch(() => { }), config.stats.intervalMs);
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

    const gpu = await this._sampleGpu();
    const perf = this.getPerfMetrics();

    this.emit('stats', {
      processes,
      appCpu: round1(cpuTotal),
      appRamMb: Math.round(ramTotal / 1048576),
      system: {
        cpuCount: os.cpus().length,
        ramUsedMb: Math.round((os.totalmem() - os.freemem()) / 1048576),
        ramTotalMb: Math.round(os.totalmem() / 1048576),
      },
      gpu,
      // Performance panel metrics from the media pipeline
      performance: {
        outputFps: perf.outputFps || 0,
        renderFps: perf.renderFps || 0,
        frameTimeMs: perf.frameTimeMs || 0,
        droppedFrames: perf.droppedFrames || 0,
        skippedFrames: perf.skippedFrames || 0,
        totalFrames: perf.totalFrames || 0,
        videoBitrate: perf.videoBitrate || '0 kbps',
        audioBitrate: perf.audioBitrate || '0 kbps',
        bufferSize: perf.bufferSize || 0,
        encoderStatus: perf.encoderStatus || 'idle',
        ffmpegFps: perf.ffmpegFps || 0,
        ffmpegSpeed: perf.ffmpegSpeed || '0x',
      },
    });
  }

  async _sampleGpu() {
    if (this._nvidiaSmi === false) return null;
    return new Promise((resolve) => {
      execFile(
        'nvidia-smi',
        ['--query-gpu=utilization.gpu,utilization.decoder,memory.used,memory.total', '--format=csv,noheader,nounits'],
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
          const parts = stdout.trim().split(',').map((s) => parseFloat(s.trim()));
          resolve({
            utilization: parts[0] || 0,
            decoder: parts[1] || 0,
            memMb: parts[2] || 0,
            memTotalMb: parts[3] || 0,
          });
        }
      );
    });
  }
}

function round1(n) {
  return Math.round(n * 10) / 10;
}

module.exports = { StatsMonitor };
