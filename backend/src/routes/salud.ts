import { Router } from 'express';
import { mongoSano } from '../db/mongo.js';
import { query } from '../db/pg.js';

export const rutasSalud = Router();

/** Salud del backend (lo usa Docker/Nginx). No expone detalles internos. */
rutasSalud.get('/', async (_req, res) => {
  const pg = await query('SELECT 1').then(() => true).catch(() => false);
  const mongo = await mongoSano();
  res.status(pg && mongo ? 200 : 503).json({ status: pg && mongo ? 'ok' : 'degradado', postgres: pg, mongo });
});
