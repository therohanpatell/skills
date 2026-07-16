'use strict';

/**
 * Playback routes — all transport actions delegate to the Switcher.
 *
 * No direct pipeline or monitor manipulation happens here. The Switcher
 * is the single source of truth for playback state; routes just validate
 * input and call the appropriate Switcher method.
 *
 * Exceptions: mute/unmute/volume/fullscreen/audio-device are applied
 * directly to the monitor (they're transport-level controls, not
 * program-level state transitions) and persisted to the store.
 */

const { ValidationError } = require('../lib/errors');

module.exports = async function playbackRoutes(app, { ctx }) {
  const { switcher, store, monitor, pipeline } = ctx;

  app.post('/api/playback', {
    schema: {
      body: {
        type: 'object',
        required: ['action'],
        properties: {
          action: {
            type: 'string',
            enum: [
              'play', 'pause', 'stop',
              'mute', 'unmute', 'volume', 'speed',
              'fullscreen',
              'seek-forward', 'seek-backward', 'seek-to', 'seek-percent',
              'audio-device', 'vcam-device',
            ],
          },
          value: {},
        },
      },
    },
  }, async (req) => {
    const { action, value } = req.body;

    switch (action) {
      // ---- Program controls ----

      case 'play':
        if (switcher.isPaused) {
          await switcher.resumeProgram();
        } else if (switcher.status === 'idle' && switcher.programId) {
          await switcher.setProgram(switcher.programId);
        }
        break;

      case 'pause':
        await switcher.pauseProgram();
        break;

      case 'stop':
        await switcher.stopProgram();
        break;

      // ---- Audio & Speed controls ----

      case 'mute':
      case 'unmute': {
        const muted = action === 'mute';
        await monitor.setMute(muted);
        store.update((st) => (st.settings.muted = muted));
        break;
      }

      case 'volume': {
        const vol = Number(value);
        if (!Number.isFinite(vol)) {
          throw new ValidationError('volume requires a numeric value');
        }
        const clamped = Math.max(0, Math.min(130, vol));
        await monitor.setVolume(clamped);
        store.update((st) => (st.settings.volume = clamped));
        break;
      }

      case 'speed': {
        const spd = Number(value);
        if (!Number.isFinite(spd) || spd <= 0) {
          throw new ValidationError('speed requires a positive numeric value');
        }
        const clamped = Math.max(0.1, Math.min(4.0, spd));
        await switcher.setSpeed(clamped);
        break;
      }

      // ---- Display ----

      case 'fullscreen':
        await monitor.setFullscreen(Boolean(value));
        break;

      // ---- Seeking (all routed through Switcher) ----

      case 'seek-forward':
        await switcher.seekRelative(Number(value) || 10);
        break;

      case 'seek-backward':
        await switcher.seekRelative(-(Number(value) || 10));
        break;

      case 'seek-to': {
        const pos = Number(value);
        if (!Number.isFinite(pos) || pos < 0) {
          throw new ValidationError('seek-to requires a non-negative number');
        }
        await switcher.seekTo(pos);
        break;
      }

      case 'seek-percent': {
        const pct = Number(value);
        if (!Number.isFinite(pct) || pct < 0 || pct > 100) {
          throw new ValidationError('seek-percent requires a value between 0 and 100');
        }
        await switcher.seekPercent(pct);
        break;
      }

      // ---- Audio device ----

      case 'audio-device':
        if (typeof value !== 'string' || !value) {
          throw new ValidationError('audio-device requires a device identifier string');
        }
        await monitor.setAudioDevice(value);
        break;

      // ---- VCam device ----

      case 'vcam-device':
        if (typeof value !== 'string' || !value) {
          throw new ValidationError('vcam-device requires a device identifier string');
        }
        await pipeline.setDevice(value);
        break;
    }

    return { ok: true };
  });

  // ---- Monitor toggle ----

  app.post('/api/monitor/toggle', async () => {
    if (monitor.enabled) {
      monitor.stop();
      store.update((st) => (st.settings.monitorEnabled = false));
    } else {
      monitor.start();
      store.update((st) => (st.settings.monitorEnabled = true));
      // Re-load current program if one is active
      if (switcher.programId) {
        switcher.setProgram(switcher.programId).catch(() => {});
      }
    }
    return { ok: true, enabled: monitor.enabled };
  });

  // ---- VCam bridge reset ----

  app.post('/api/vcam/reset', async () => {
    if (pipeline.bridge) pipeline.bridge.reset();
    return { ok: true };
  });
};
