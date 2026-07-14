'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');

/**
 * Minimal .env loader (avoids a dotenv dependency). Existing environment
 * variables always win over .env file values.
 */
function loadDotEnv() {
  const envPath = path.join(ROOT, '.env');
  if (!fs.existsSync(envPath)) return;
  for (const line of fs.readFileSync(envPath, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (!m || line.trim().startsWith('#')) continue;
    if (process.env[m[1]] === undefined) {
      process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  }
}

function env(name, fallback) {
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : v;
}

function envBool(name, fallback) {
  const v = process.env[name];
  if (v === undefined || v === '') return fallback;
  return ['1', 'true', 'yes', 'on'].includes(v.toLowerCase());
}

function envInt(name, fallback) {
  const v = parseInt(process.env[name], 10);
  return Number.isFinite(v) ? v : fallback;
}

function buildConfig() {
  loadDotEnv();
  const defaults = JSON.parse(
    fs.readFileSync(path.join(ROOT, 'config', 'default.json'), 'utf8')
  );

  const cfg = {
    ...defaults,
    root: ROOT,
    logLevel: env('YTSW_LOG_LEVEL', 'info'),
    server: {
      host: env('YTSW_HOST', defaults.server.host),
      port: envInt('YTSW_PORT', defaults.server.port),
    },
    paths: {
      ytdlp: env('YTSW_YTDLP_PATH', defaults.paths.ytdlp),
      ffmpeg: env('YTSW_FFMPEG_PATH', defaults.paths.ffmpeg),
      mpv: env('YTSW_MPV_PATH', defaults.paths.mpv),
      python: env('YTSW_PYTHON_PATH', defaults.paths.python),
      dataDir: path.resolve(ROOT, defaults.paths.dataDir),
      logDir: path.resolve(ROOT, defaults.paths.logDir),
    },
    vcam: {
      ...defaults.vcam,
      enabled: envBool('YTSW_VCAM_ENABLED', defaults.vcam.enabled),
      width: envInt('YTSW_VCAM_WIDTH', defaults.vcam.width),
      height: envInt('YTSW_VCAM_HEIGHT', defaults.vcam.height),
      fps: envInt('YTSW_VCAM_FPS', defaults.vcam.fps),
    },
    monitor: {
      ...defaults.monitor,
      enabled: envBool('YTSW_MONITOR_ENABLED', defaults.monitor.enabled),
    },
    ffmpeg: {
      ...defaults.ffmpeg,
      hwaccel: env('YTSW_FFMPEG_HWACCEL', defaults.ffmpeg.hwaccel),
    },
    isWindows: process.platform === 'win32',
    mpvIpcPath:
      process.platform === 'win32'
        ? '\\\\.\\pipe\\ytswitcher-mpv'
        : path.join(require('os').tmpdir(), 'ytswitcher-mpv.sock'),
  };

  fs.mkdirSync(cfg.paths.dataDir, { recursive: true });
  fs.mkdirSync(cfg.paths.logDir, { recursive: true });
  return cfg;
}

module.exports = buildConfig();
