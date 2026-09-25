/**
 * Contador de tráfico de la API agregado por hora (RF-ADM-04). Se acumula en
 * memoria y se vuelca a PostgreSQL cada minuto para no escribir en cada request.
 */
import type { NextFunction, Request, Response } from 'express';
import { query } from '../db/pg.js';
import { logger } from '../lib/logger.js';

const contadores = new Map<string, number>();

/**
 * Ruta agregable: se usa la URL original (Express 5 restaura req.baseUrl cuando
 * un error sale del router, lo que perdía el prefijo /api/...) y se sustituyen
 * identificadores numéricos y tokens por marcadores.
 */
function normalizarRuta(req: Request): string {
  const ruta = (req.originalUrl || req.url).split('?')[0];
  return (
    ruta
      .replace(/\/\d+(?=\/|$)/g, '/:id')
      .replace(/\/[A-Za-z0-9_.-]{24,}(?=\/|$)/g, '/:token')
      .replace(/\/+$/, '')
      .slice(0, 80) || '/'
  );
}

export function contarTrafico(req: Request, res: Response, next: NextFunction) {
  res.on('finish', () => {
    const hora = new Date();
    hora.setMinutes(0, 0, 0);
    const clave = [hora.toISOString(), req.method, (res.statusCode === 404 && !req.route ? '(ruta desconocida)' : normalizarRuta(req)), `${String(res.statusCode)[0]}xx`].join('|');
    contadores.set(clave, (contadores.get(clave) ?? 0) + 1);
  });
  next();
}

export async function volcarTrafico(): Promise<void> {
  if (!contadores.size) return;
  const entradas = [...contadores.entries()];
  contadores.clear();
  for (const [clave, total] of entradas) {
    const [hora, metodo, ruta, estado] = clave.split('|');
    try {
      await query(
        `INSERT INTO trafico_api (hora, metodo, ruta, estado, total) VALUES ($1, $2, $3, $4, $5)
         ON CONFLICT (hora, metodo, ruta, estado) DO UPDATE SET total = trafico_api.total + EXCLUDED.total`,
        [hora, metodo, ruta, estado, total],
      );
    } catch (err) {
      logger.warn({ err }, 'no se pudo guardar tráfico');
    }
  }
}
