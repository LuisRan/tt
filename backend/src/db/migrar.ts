import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import bcrypt from 'bcryptjs';
import { config } from '../config.js';
import { logger } from '../lib/logger.js';
import { pool, uno } from './pg.js';

const aqui = path.dirname(fileURLToPath(import.meta.url));

/** Aplica el esquema (idempotente). Reintenta mientras PostgreSQL arranca. */
export async function migrar(reintentos = 30): Promise<void> {
  const sql = readFileSync(path.join(aqui, 'schema.sql'), 'utf8');
  for (let intento = 1; ; intento++) {
    try {
      await pool.query('SELECT pg_advisory_lock(424242)');
      try {
        await pool.query(sql);
      } finally {
        await pool.query('SELECT pg_advisory_unlock(424242)');
      }
      logger.info('esquema de PostgreSQL aplicado');
      return;
    } catch (err: any) {
      if (intento >= reintentos || !['ECONNREFUSED', '57P03', 'ENOTFOUND', 'EAI_AGAIN'].includes(err.code)) throw err;
      logger.warn({ intento, code: err.code }, 'PostgreSQL aún no está listo, reintentando...');
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
}

/** Crea (o asegura) la cuenta de administrador inicial definida en .env. */
export async function sembrarAdmin(): Promise<void> {
  if (!config.ADMIN_EMAIL || !config.ADMIN_PASSWORD) return;
  const correo = config.ADMIN_EMAIL.toLowerCase();
  const existe = await uno<{ id: number }>('SELECT id FROM usuarios WHERE correo = $1', [correo]);
  if (existe) return;
  const hash = await bcrypt.hash(config.ADMIN_PASSWORD, 12);
  await pool.query(
    `INSERT INTO usuarios (correo, password_hash, rol, correo_verificado, registro_completo, terminos_aceptados_en)
     VALUES ($1, $2, 'administrador', TRUE, TRUE, NOW())`,
    [correo, hash],
  );
  logger.info({ correo }, 'cuenta de administrador inicial creada');
}
