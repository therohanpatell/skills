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

/**
 * Resolve an external tool to something spawnable.
 *  - A configured path that exists on disk is used as-is.
 *  - A bare command name is accepted if `where`/`which` finds it on PATH.
 *  - Otherwise, well-known install locations are probed (winget, scoop,
 *    chocolatey, Program Files, C:\Tools) so a missing PATH entry doesn't
 *    silently kill the whole audio/monitor subsystem.
 * Returns { path, note } — note is a human-readable warning for the log/UI.
 */
function findBinary(configured, candidates) {
  const looksLikePath = /[\\/]/.test(configured);
  if (looksLikePath) {
    if (fs.existsSync(configured)) return { path: configured, note: null };
  } else {
    const probe = process.platform === 'win32' ? 'where' : 'which';
    try {
      require('child_process').execSync(`${probe} ${configured}`, {
        stdio: 'pipe',
        windowsHide: true,
      });
      return { path: configured, note: null };
    } catch (_) {
      /* not on PATH — fall through to candidates */
    }
  }
  for (const c of candidates) {
    if (c && fs.existsSync(c)) {
      return { path: c, note: `'${configured}' not found — auto-detected ${c}` };
    }
  }
  return {
    path: configured,
    note: looksLikePath
      ? `configured path does not exist: ${configured}`
      : `'${configured}' not found on PATH and no known install location — set the YTSW_*_PATH in .env (and RESTART the server)`,
  };
}

function windowsCandidates(exe, extraDirs = []) {
  if (process.platform !== 'win32') return [];
  const dirs = [
    ...extraDirs,
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Microsoft', 'WinGet', 'Links'),
    process.env.USERPROFILE && path.join(process.env.USERPROFILE, 'scoop', 'shims'),
    'C:\\ProgramData\\chocolatey\\bin',
  ].filter(Boolean);
  return dirs.map((d) => path.join(d, exe));
}

function buildConfig() {
  loadDotEnv();
  const defaults = JSON.parse(
    fs.readFileSync(path.join(ROOT, 'config', 'default.json'), 'utf8')
  );

  const pf = process.env.ProgramFiles || 'C:\\Program Files';
  const binaryNotes = [];
  const resolveTool = (label, configured, candidates) => {
    const r = findBinary(configured, candidates);
    if (r.note) binaryNotes.push(`${label}: ${r.note}`);
    return r.path;
  };

  const cfg = {
    ...defaults,
    root: ROOT,
    logLevel: env('YTSW_LOG_LEVEL', 'info'),
    server: {
      host: env('YTSW_HOST', defaults.server.host),
      port: envInt('YTSW_PORT', defaults.server.port),
    },
    paths: {
      ytdlp: resolveTool(
        'yt-dlp',
        env('YTSW_YTDLP_PATH', defaults.paths.ytdlp),
        windowsCandidates('yt-dlp.exe', ['C:\\Tools\\yt-dlp'])
      ),
      ffmpeg: resolveTool(
        'ffmpeg',
        env('YTSW_FFMPEG_PATH', defaults.paths.ffmpeg),
        windowsCandidates('ffmpeg.exe', ['C:\\Tools\\ffmpeg\\bin', path.join(pf, 'ffmpeg', 'bin')])
      ),
      mpv: resolveTool(
        'mpv',
        env('YTSW_MPV_PATH', defaults.paths.mpv),
        windowsCandidates('mpv.exe', ['C:\\Tools\\mpv', path.join(pf, 'mpv')])
      ),
      // Optional: akvirtualcamera manager — powers the branded
      // "YT Switcher Virtual Cam" device. Missing = OBS vcam fallback.
      akvcam:
        process.platform === 'win32'
          ? findBinary(env('YTSW_AKVCAM_PATH', 'AkVCamManager'), [
              path.join(pf, 'AkVirtualCamera', 'x64', 'AkVCamManager.exe'),
              'C:\\Tools\\AkVirtualCamera\\x64\\AkVCamManager.exe',
            ]).path
          : env('YTSW_AKVCAM_PATH', 'AkVCamManager'),
      python: env('YTSW_PYTHON_PATH', defaults.paths.python),
      dataDir: path.resolve(ROOT, defaults.paths.dataDir),
      logDir: path.resolve(ROOT, defaults.paths.logDir),
    },
    binaryNotes,
    vcam: {
      ...defaults.vcam,
      enabled: envBool('YTSW_VCAM_ENABLED', defaults.vcam.enabled),
      device: env('YTSW_VCAM_DEVICE', defaults.vcam.device),
      width: envInt('YTSW_VCAM_WIDTH', defaults.vcam.width),
      height: envInt('YTSW_VCAM_HEIGHT', defaults.vcam.height),
      fps: envInt('YTSW_VCAM_FPS', defaults.vcam.fps),
    },
    monitor: {
      ...defaults.monitor,
      enabled: envBool('YTSW_MONITOR_ENABLED', defaults.monitor.enabled),
      mode: env('YTSW_MONITOR_MODE', defaults.monitor.mode),
    },
    audioLoopback: {
      ...defaults.audioLoopback,
      enabled: envBool('YTSW_AUDIO_LOOPBACK', defaults.audioLoopback.enabled),
      device: env('YTSW_AUDIO_DEVICE', defaults.audioLoopback.device),
    },
    ffmpeg: {
      ...defaults.ffmpeg,
      hwaccel: env('YTSW_FFMPEG_HWACCEL', defaults.ffmpeg.hwaccel),
    },
    isWindows: process.platform === 'win32',
    // Per-process pipe name: a zombie mpv from a previous run holding the
    // fixed name would stop the new mpv's IPC server from ever coming up
    // (symptom: monitor stuck on 'starting', no audio, no timeline).
    mpvIpcPath:
      process.platform === 'win32'
        ? `\\\\.\\pipe\\ytswitcher-mpv-${process.pid}`
        : path.join(require('os').tmpdir(), `ytswitcher-mpv-${process.pid}.sock`),
  };

  fs.mkdirSync(cfg.paths.dataDir, { recursive: true });
  fs.mkdirSync(cfg.paths.logDir, { recursive: true });
  return cfg;
}

module.exports = buildConfig();
