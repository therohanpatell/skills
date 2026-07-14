'use strict';

/** Full-state snapshot (used on page load / ws reconnect) and health check. */
module.exports = async function systemRoutes(app, { ctx }) {
  app.get('/api/state', async () => ctx.fullState());

  app.get('/healthz', async () => ({
    ok: true,
    uptime: Math.round(process.uptime()),
  }));
};
