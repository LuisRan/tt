/**
 * Punto de entrada del backend principal (Node.js) — capa de aplicación.
 */
import { config } from './config.js';
import { readFileSync } from 'node:fs';
import { createServer as httpServer } from 'node:http';
import { createServer as httpsServer, Server as HttpsServer } from 'node:https';
import { migrar, sembrarAdmin } from './db/migrar.js';
import { cerrarMongo, conectarMongo } from './db/mongo.js';
import { pool } from './db/pg.js';
import { logger } from './lib/logger.js';
import { volcarTrafico } from './middleware/trafico.js';
import { crearApp } from './app.js';
import { completarIdentificadores, recuperarPendientes } from './services/documentos.js';
import { programarTareas } from './services/tareas.js';

async function main() {
  process.env.TZ = config.TZ;
  if (config.RUN_DB_MIGRATIONS) {
    await migrar();
    await sembrarAdmin();
  } else {
    // Las migraciones se ejecutan por separado con una cuenta de administración.
    await pool.query('SELECT id FROM usuarios LIMIT 0');
  }
  await conectarMongo();
  await recuperarPendientes();
  await completarIdentificadores().catch((err) => logger.warn({ err }, 'no se pudieron completar identificadores'));
  programarTareas();

  if (Boolean(config.BACKEND_TLS_CERT_FILE) !== Boolean(config.BACKEND_TLS_KEY_FILE)) {
    throw new Error('La configuración TLS del backend requiere certificado y llave');
  }
  const opcionesTLS = () => ({
    key: readFileSync(config.BACKEND_TLS_KEY_FILE),
    cert: readFileSync(config.BACKEND_TLS_CERT_FILE),
    minVersion: 'TLSv1.2' as const,
  });
  const app = crearApp();
  const servidor = (config.BACKEND_TLS_CERT_FILE ? httpsServer(opcionesTLS(), app) : httpServer(app)).listen(config.PORT, () => {
    logger.info(
      { puerto: config.PORT, ia: config.IA_URL, correo: config.proveedorCorreo, entorno: config.NODE_ENV },
      `backend escuchando en :${config.PORT}`,
    );
  });
  // Subidas + espera del módulo de IA: tiempos generosos
  servidor.requestTimeout = 120_000;
  servidor.headersTimeout = 65_000;
  process.on('SIGHUP', () => {
    if (servidor instanceof HttpsServer) {
      try { servidor.setSecureContext(opcionesTLS()); }
      catch { logger.error('No se pudo recargar el certificado del backend'); }
    }
  });

  const apagar = async (senal: string) => {
    logger.info({ senal }, 'apagando backend…');
    servidor.close();
    await volcarTrafico().catch(() => undefined);
    await cerrarMongo().catch(() => undefined);
    await pool.end().catch(() => undefined);
    process.exit(0);
  };
  process.on('SIGTERM', () => void apagar('SIGTERM'));
  process.on('SIGINT', () => void apagar('SIGINT'));
}

main().catch((err) => {
  logger.fatal({ err }, 'no se pudo iniciar el backend');
  process.exit(1);
});
