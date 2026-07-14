'use strict';

/** Program selection (the switch itself) and the MJPEG program preview. */
module.exports = async function programRoutes(app, { ctx }) {
  const { switcher, pipeline } = ctx;

  app.post('/api/program/:id', async (req) => {
    // Fire the switch without awaiting resolution: the UI gets instant
    // feedback and progress arrives over the WebSocket.
    switcher.setProgram(req.params.id).catch(() => {});
    return { ok: true };
  });

  app.delete('/api/program', async () => {
    await switcher.stopProgram();
    return { ok: true };
  });

  // Long-lived multipart MJPEG stream for the Program panel.
  app.get('/api/preview.mjpeg', (req, reply) => {
    reply.hijack();
    pipeline.addPreviewClient(reply.raw);
  });
};
