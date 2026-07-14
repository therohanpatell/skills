'use strict';

/** Source CRUD: add/remove/clear/refresh YouTube URLs + cached thumbnails. */
module.exports = async function sourcesRoutes(app, { ctx }) {
  const { sources, switcher } = ctx;

  app.post('/api/sources', {
    schema: {
      body: {
        type: 'object',
        required: ['url'],
        properties: { url: { type: 'string', maxLength: 500 } },
      },
    },
  }, async (req) => {
    const source = await sources.add(req.body.url);
    return { ok: true, source };
  });

  app.delete('/api/sources/:id', async (req) => {
    const wasProgram = switcher.programId === req.params.id;
    if (wasProgram) await switcher.stopProgram();
    sources.remove(req.params.id);
    return { ok: true };
  });

  app.delete('/api/sources', async () => {
    await switcher.stopProgram();
    sources.clear();
    return { ok: true };
  });

  app.post('/api/sources/:id/refresh', async (req) => {
    await sources.resolve(req.params.id);
    return { ok: true };
  });

  app.get('/api/thumb/:id', async (req, reply) => {
    const p = sources.thumbnailPath(req.params.id);
    if (!p) return reply.code(404).send({ error: 'no thumbnail' });
    reply.header('Cache-Control', 'max-age=3600');
    return reply.type('image/jpeg').send(require('fs').createReadStream(p));
  });
};
