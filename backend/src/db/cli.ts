/** Migración explícita: esta cuenta nunca se entrega al servidor HTTP. */
import { migrar, sembrarAdmin } from './migrar.js';
import { pool } from './pg.js';

try {
  await migrar();
  await sembrarAdmin();
} catch {
  console.error('No se pudo aplicar el esquema. Revisa la conexión y los permisos de migración.');
  process.exitCode = 1;
} finally {
  await pool.end();
}
