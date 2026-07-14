'use strict';

const path = require('path');
const fastify = require('fastify');
const fastifyStatic = require('@fastify/static');
const fastifyWebsocket = require('@fastify/websocket');
const config = require('./lib/config');
const logger = require('./lib/logger');
const { AppError } = require('./lib/errors');

/**
 * Wires Fastify around the core context (store, sources, switcher, pipeline,
 * monitor, stats). All server->client communication flows over one WebSocket;
 * client->server actions are plain REST. No polling anywhere.
 */
async function buildApp(ctx) {
  const app = fastify({ logger: false });

  app.setErrorHandler((err, req, reply) => {
    if (err.validation) {
      return reply.code(400).send({ error: err.message, code: 'VALIDATION' });
    }
    const status = err instanceof AppError ? err.status : 500;
    if (status >= 500) logger.error({ err, url: req.url }, 'request failed');
    reply.code(status).send({ error: err.message, code: err.code || 'INTERNAL' });
  });

  await app.register(fastifyStatic, {
    root: path.join(config.root, 'public'),
    cacheControl: false,
  });
  await app.register(fastifyWebsocket, {
    options: { maxPayload: 4096 },
  });

  // --- WebSocket state push ---
  const wsClients = new Set();
  const broadcast = (type, payload) => {
    if (!wsClients.size) return;
    const msg = JSON.stringify({ type, payload });
    for (const sock of wsClients) {
      if (sock.readyState === 1) sock.send(msg);
    }
  };
  ctx.broadcast = broadcast;

  app.register(async (scope) => {
    scope.get('/ws', { websocket: true }, (conn) => {
      const sock = conn.socket || conn; // @fastify/websocket v10 passes the socket
      wsClients.add(sock);
      sock.on('close', () => wsClients.delete(sock));
      sock.on('error', () => wsClients.delete(sock));
      sock.send(JSON.stringify({ type: 'state', payload: ctx.fullState() }));
    });
  });

  // --- Event wiring: core emits -> WebSocket ---
  const pushState = () => broadcast('state', ctx.fullState());
  ctx.sources.on('sourcesChanged', pushState);
  ctx.sources.on('sourceChanged', pushState);
  ctx.switcher.on('programChanged', pushState);
  ctx.pipeline.on('bridgeStatus', pushState);
  ctx.monitor.on('status', pushState);
  ctx.monitor.on('props', (props) => broadcast('mpvProps', props));
  ctx.stats.on('stats', (stats) => broadcast('stats', stats));

  // --- Routes ---
  await app.register(require('./routes/sources'), { ctx });
  await app.register(require('./routes/program'), { ctx });
  await app.register(require('./routes/playback'), { ctx });
  await app.register(require('./routes/system'), { ctx });

  return app;
}

module.exports = { buildApp };
