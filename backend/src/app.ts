import cookieParser from 'cookie-parser';
import express from 'express';
import rateLimit from 'express-rate-limit';
import helmet from 'helmet';
import { pinoHttp } from 'pino-http';
import { config } from './config.js';
import { ErrorApp, noEncontrado } from './lib/errores.js';
import { logger } from './lib/logger.js';
import { manejadorErrores } from './middleware/errores.js';
import { contarTrafico } from './middleware/trafico.js';
import { rutasAdmin } from './routes/admin.js';
import { rutasArchivos } from './routes/archivos.js';
import { rutasAuth } from './routes/auth.js';
import { rutasDocumentos } from './routes/documentos.js';
import { rutasExtension } from './routes/extension.js';
import { rutasPerfil } from './routes/perfil.js';
import { rutasSalud } from './routes/salud.js';

export function crearApp() {
  const app = express();
  app.disable('x-powered-by');
  // Detrás de Nginx (reverse proxy): confiar en X-Forwarded-For del primer salto
  app.set('trust proxy', 1);

  app.use(
    helmet({
      contentSecurityPolicy: { directives: { defaultSrc: ["'none'"], frameAncestors: ["'self'"] } },
      crossOriginResourcePolicy: { policy: 'same-origin' },
    }),
  );
  if (!config.esTest) app.use(pinoHttp({ logger, autoLogging: { ignore: (req) => req.url === '/api/salud' } }));

  // CORS solo para desarrollo (Astro dev server); en Docker todo es mismo origen vía Nginx
  if (config.corsOrigins.length) {
    app.use((req, res, next) => {
      const origen = req.headers.origin;
      if (origen && config.corsOrigins.includes(origen)) {
        res.setHeader('Access-Control-Allow-Origin', origen);
        res.setHeader('Access-Control-Allow-Credentials', 'true');
        res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
        res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS');
        res.setHeader('Vary', 'Origin');
      }
      if (req.method === 'OPTIONS') return res.sendStatus(204);
      next();
    });
  }

  app.use(express.json({ limit: '200kb' }));
  app.use(cookieParser());
  app.use(contarTrafico);
  app.use(
    '/api',
    rateLimit({
      windowMs: 60_000,
      limit: config.esTest ? 100_000 : 300,
      standardHeaders: 'draft-8',
      legacyHeaders: false,
      message: { error: 'Demasiadas solicitudes', codigo: 'LIMITE' },
    }),
  );

  // Protección CSRF adicional a SameSite=Strict: las peticiones que modifican
  // estado deben venir de nuestro propio origen.
  app.use('/api', (req, _res, next) => {
    if (['GET', 'HEAD', 'OPTIONS'].includes(req.method)) return next();
    // La extensión no usa cookies (token Bearer o código de un solo uso): no aplica CSRF
    if (req.path === '/extension/vincular' || (req.path.startsWith('/extension/') && req.headers.authorization?.startsWith('Bearer '))) {
      return next();
    }
    const origen = req.headers.origin;
    if (!origen) return next();
    const permitido = [new URL(config.PUBLIC_URL).origin, ...config.corsOrigins];
    const mismoHost = (() => {
      try {
        return new URL(origen).host === req.headers.host;
      } catch {
        return false;
      }
    })();
    if (mismoHost || permitido.includes(origen)) return next();
    next(new ErrorApp(403, 'ORIGEN_NO_PERMITIDO', 'Origen no permitido'));
  });

  app.use('/api/salud', rutasSalud);
  app.use('/api/auth', rutasAuth);
  app.use('/api/documentos', rutasDocumentos);
  app.use('/api/archivos', rutasArchivos);
  app.use('/api/perfil', rutasPerfil);
  app.use('/api/admin', rutasAdmin);
  app.use('/api/extension', rutasExtension);

  app.use((_req, _res, next) => next(noEncontrado('Ruta no encontrada')));
  app.use(manejadorErrores);
  return app;
}
