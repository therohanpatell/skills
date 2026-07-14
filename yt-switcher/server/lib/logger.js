'use strict';

const path = require('path');
const pino = require('pino');
const config = require('./config');

// Console (pretty enough as JSON lines) + append-only file. The file stream is
// opened with sync:false so logging never blocks the event loop.
const streams = [
  { level: config.logLevel, stream: process.stdout },
  {
    level: config.logLevel,
    stream: pino.destination({
      dest: path.join(config.paths.logDir, 'app.log'),
      sync: false,
      mkdir: true,
    }),
  },
];

const logger = pino(
  { level: config.logLevel, base: undefined, timestamp: pino.stdTimeFunctions.isoTime },
  pino.multistream(streams)
);

module.exports = logger;
