import pg from 'pg';
import { config } from '../config.js';
import { logger } from '../lib/logger.js';

// DATE -> string 'YYYY-MM-DD' (evita corrimientos por zona horaria)
pg.types.setTypeParser(1082, (v) => v);
// NUMERIC -> number
pg.types.setTypeParser(1700, (v) => (v === null ? null : parseFloat(v)));
// COUNT(*) (int8) -> number
pg.types.setTypeParser(20, (v) => (v === null ? null : parseInt(v, 10)));

export const pool = new pg.Pool({
  connectionString: config.DATABASE_URL,
  max: 10,
  idleTimeoutMillis: 30_000,
});

pool.on('error', (err) => logger.error({ err }, 'error en conexión inactiva de PostgreSQL'));

/** Consultas SIEMPRE parametrizadas ($1, $2...) para prevenir inyección SQL (2.7.2.6). */
export async function query<T extends pg.QueryResultRow = any>(texto: string, params: unknown[] = []) {
  return pool.query<T>(texto, params);
}

export async function uno<T extends pg.QueryResultRow = any>(texto: string, params: unknown[] = []): Promise<T | null> {
  const r = await pool.query<T>(texto, params);
  return r.rows[0] ?? null;
}

export async function transaccion<T>(fn: (cliente: pg.PoolClient) => Promise<T>): Promise<T> {
  const cliente = await pool.connect();
  try {
    await cliente.query('BEGIN');
    const r = await fn(cliente);
    await cliente.query('COMMIT');
    return r;
  } catch (e) {
    await cliente.query('ROLLBACK');
    throw e;
  } finally {
    cliente.release();
  }
}
