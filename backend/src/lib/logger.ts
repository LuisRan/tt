import pino from 'pino';
import { config } from '../config.js';

/** Logger estructurado (JSON). Nunca se registran contraseñas, códigos ni datos personales. */
export const logger = pino({
  level: config.esTest ? 'silent' : config.LOG_LEVEL,
  base: { servicio: 'backend' },
  serializers: config.esProduccion ? {
    err: (e: any) => ({ type: typeof e?.name === 'string' ? e.name : 'Error', code: e?.code }),
    req: (r: any) => ({ id: r.id, method: r.method, url: String(r.url ?? '').split('?')[0] }),
  } : undefined,
  redact: {
    paths: [
      'req.headers.cookie',
      'req.headers.authorization',
      'req.headers["x-api-key"]',
      '*.password',
      '*.codigo',
      '*.curp', '*.direccion', '*.correo', '*.para', '*.token', '*.secret',
      'password', 'codigo', 'curp', 'direccion', 'correo', 'para', 'token', 'secret',
      'texto', 'cuerpo', 'error', 'mensaje', '*.texto', '*.cuerpo', '*.error', '*.mensaje',
    ],
    censor: '[oculto]',
  },
  timestamp: pino.stdTimeFunctions.isoTime,
});
