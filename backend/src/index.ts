/**
 * Punto de entrada del backend principal (Node.js) — capa de aplicación.
 */
import { config } from './config.js';
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
  await migrar();
  await sembrarAdmin();
  await conectarMongo();
  await recuperarPendientes();
  await completarIdentificadores().catch((err) => logger.warn({ err }, 'no se pudieron completar identificadores'));
  programarTareas();

  const servidor = crearApp().listen(config.PORT, () => {
    logger.info(
      { puerto: config.PORT, ia: config.IA_URL, correo: config.proveedorCorreo, entorno: config.NODE_ENV },
      `backend escuchando en :${config.PORT}`,
    );
  });
  // Subidas + espera del módulo de IA: tiempos generosos
  servidor.requestTimeout = 120_000;
  servidor.headersTimeout = 65_000;

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
