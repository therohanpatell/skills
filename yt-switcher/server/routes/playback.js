'use strict';

const { ValidationError } = require('../lib/errors');

/** Transport controls, routed to mpv over JSON IPC. Volume/mute persist. */
module.exports = async function playbackRoutes(app, { ctx }) {
  const { monitor, store, pipeline, switcher } = ctx;

  app.post('/api/playback', {
    schema: {
      body: {
        type: 'object',
        required: ['action'],
        properties: {
          action: { type: 'string', enum: ['play', 'pause', 'stop', 'mute', 'unmute', 'volume', 'fullscreen'] },
          value: {},
        },
      },
    },
  }, async (req) => {
    const { action, value } = req.body;
    switch (action) {
      case 'play':
        await monitor.setPause(false);
        break;
      case 'pause':
        await monitor.setPause(true);
        break;
      case 'stop':
        await switcher.stopProgram();
        break;
      case 'mute':
      case 'unmute': {
        const muted = action === 'mute';
        await monitor.setMute(muted);
        store.update((st) => (st.settings.muted = muted));
        break;
      }
      case 'volume': {
        const v = Number(value);
        if (!Number.isFinite(v)) throw new ValidationError('volume requires a numeric value');
        await monitor.setVolume(v);
        store.update((st) => (st.settings.volume = Math.max(0, Math.min(130, v))));
        break;
      }
      case 'fullscreen':
        await monitor.setFullscreen(Boolean(value));
        break;
    }
    return { ok: true };
  });

  app.post('/api/monitor/toggle', async () => {
    if (monitor.enabled) {
      monitor.stop();
      store.update((st) => (st.settings.monitorEnabled = false));
    } else {
      monitor.start();
      store.update((st) => (st.settings.monitorEnabled = true));
      // Re-feed the monitor if a program is live right now.
      if (switcher.programId) switcher.setProgram(switcher.programId).catch(() => {});
    }
    return { ok: true, enabled: monitor.enabled };
  });

  app.post('/api/vcam/reset', async () => {
    if (pipeline.bridge) pipeline.bridge.reset();
    return { ok: true };
  });
};
