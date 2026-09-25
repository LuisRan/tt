import pino from 'pino';
import { config } from '../config.js';

/** Logger estructurado (JSON). Nunca se registran contraseñas, códigos ni datos personales. */
export const logger = pino({
  level: config.esTest ? 'silent' : config.LOG_LEVEL,
  base: { servicio: 'backend' },
  redact: {
    paths: [
      'req.headers.cookie',
      'req.headers.authorization',
      'req.headers["x-api-key"]',
      '*.password',
      '*.codigo',
      '*.curp',
    ],
    censor: '[oculto]',
  },
  timestamp: pino.stdTimeFunctions.isoTime,
});
