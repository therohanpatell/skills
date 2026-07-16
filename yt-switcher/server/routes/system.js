'use strict';

const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const config = require('../lib/config');

function run(cmd, args, timeout) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout, windowsHide: true }, (err, stdout, stderr) => {
      resolve({ err, stdout: String(stdout || ''), stderr: String(stderr || '') });
    });
  });
}

/** Full-state snapshot, health check, and the audio/mpv self-test. */
module.exports = async function systemRoutes(app, { ctx }) {
  app.get('/api/state', async () => ctx.fullState());

  app.get('/healthz', async () => ({
    ok: true,
    uptime: Math.round(process.uptime()),
  }));

  /**
   * One-click audio & monitor diagnostic:
   *  1. mpv binary + version (catches wrong/missing binary, mpv.net, etc.)
   *  2. Whether the mpv IPC control pipe is actually connected
   *  3. A 1.5s 440Hz test tone through ffplay — this exercises the machine's
   *     audio device INDEPENDENTLY of mpv. Tone audible + mpv silent ⇒ mpv
   *     problem; tone inaudible ⇒ Windows output-device problem.
   *  4. The tail of mpv's own log for the "why".
   */
  app.post('/api/system/audio-test', async () => {
    const result = {
      mpvPath: config.paths.mpv,
      mpvVersion: null,
      monitorStatus: ctx.monitor.status,
      ipcConnected: Boolean(ctx.monitor.sock),
      toneTest: null,
      mpvLogTail: [],
    };

    const ver = await run(config.paths.mpv, ['--version'], 5000);
    result.mpvVersion = ver.err
      ? `ERROR: ${ver.err.code === 'ENOENT' ? 'mpv not found — check YTSW_MPV_PATH' : ver.err.message}`
      : (ver.stdout.split('\n')[0] || '').trim();

    // ffplay lives next to ffmpeg in every standard distribution.
    const ffplay = config.paths.ffmpeg.replace(/ffmpeg(\.exe)?$/i, (m, ext) => `ffplay${ext || ''}`);
    const tone = await run(
      ffplay,
      ['-hide_banner', '-loglevel', 'error', '-nodisp', '-autoexit',
       '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.5'],
      10000
    );
    result.toneTest = tone.err
      ? `FAILED: ${tone.err.code === 'ENOENT' ? 'ffplay not found next to ffmpeg' : (tone.stderr || tone.err.message).slice(0, 200)}`
      : 'tone played — did you hear a 1.5s beep?';

    try {
      const log = fs.readFileSync(path.join(config.paths.logDir, 'mpv.log'), 'utf8');
      result.mpvLogTail = log.trim().split('\n').slice(-12);
    } catch (_) {
      result.mpvLogTail = ['(no mpv.log yet — mpv may never have started)'];
    }

    return result;
  });
};
