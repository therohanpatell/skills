'use strict';

const config = require('./lib/config');
const logger = require('./lib/logger');
const { StateStore } = require('./core/state-store');
const { SourceManager } = require('./core/source-manager');
const { VcamPipeline } = require('./core/pipelines/vcam-pipeline');
const { MpvController } = require('./core/pipelines/mpv-controller');
const { Switcher } = require('./core/switcher');
const { StatsMonitor } = require('./core/stats-monitor');
const { buildApp } = require('./app');

async function main() {
  logger.info({ platform: process.platform, node: process.version }, 'yt-switcher starting');

  const store = new StateStore();
  const sources = new SourceManager(store);
  const pipeline = new VcamPipeline(store);
  const monitor = new MpvController();
  const switcher = new Switcher({ store, sources, pipeline, monitor });

  const stats = new StatsMonitor(
    () => ({
      server: process.pid,
      ffmpeg: pipeline.ffmpeg ? pipeline.ffmpeg.pid : null,
      vcam: pipeline.bridge ? pipeline.bridge.pid : null,
      mpv: monitor.pid,
    }),
    () => pipeline.getPerformanceMetrics()
  );

  const ctx = {
    store,
    sources,
    pipeline,
    monitor,
    switcher,
    stats,
    fullState() {
      return {
        sources: sources.list(),
        program: switcher.snapshot(),
        settings: store.state.settings,
        vcam: {
          ...config.vcam,
          status: pipeline.bridgeStatus,
          device: pipeline.currentDevice,
          availableDevices: pipeline.availableDevices,
          notification: pipeline.notification,
        },
        monitor: { status: monitor.status },
        limits: config.limits,
      };
    },
  };

  await pipeline.init();

  const monitorEnabled =
    store.state.settings.monitorEnabled === null
      ? config.monitor.enabled
      : store.state.settings.monitorEnabled;
  if (monitorEnabled) monitor.start();

  const app = await buildApp(ctx);
  await app.listen({ host: config.server.host, port: config.server.port });
  logger.info(`UI ready at http://${config.server.host}:${config.server.port}`);

  stats.start();

  // Restore the previous session's program once children have had a beat to start.
  setTimeout(() => switcher.restore(), 1500);

  let shuttingDown = false;
  const shutdown = async (signal) => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info({ signal }, 'shutting down');
    // Failsafe: never hang on shutdown (e.g. a half-open WebSocket keeping
    // fastify's close() waiting). unref'd so it can't keep the process alive.
    setTimeout(() => process.exit(1), 5000).unref();
    try {
      stats.stop();
      switcher.shutdown();
      monitor.stop();
      await pipeline.shutdown();
      store.flushSync();
      await app.close();
    } catch (err) {
      logger.error({ err }, 'error during shutdown');
    }
    process.exit(0);
  };
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('uncaughtException', (err) => {
    logger.fatal({ err }, 'uncaught exception');
    shutdown('uncaughtException');
  });
  process.on('unhandledRejection', (reason) => {
    logger.error({ reason: String(reason) }, 'unhandled rejection');
  });
}

main().catch((err) => {
  console.error('startup failed:', err.message);
  logger.fatal({ err }, 'startup failed');
  // Give pino's async file stream a beat to flush before exiting.
  setTimeout(() => process.exit(1), 200);
});
