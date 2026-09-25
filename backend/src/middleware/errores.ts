import type { NextFunction, Request, Response } from 'express';
import multer from 'multer';
import { ZodError } from 'zod';
import { config } from '../config.js';
import { ErrorApp } from '../lib/errores.js';
import { ErrorIA } from '../services/ia-cliente.js';
import { logger } from '../lib/logger.js';

export function manejadorErrores(err: unknown, req: Request, res: Response, _next: NextFunction) {
  if (err instanceof ErrorApp) {
    return res.status(err.status).json({ error: err.message, codigo: err.codigo, detalles: err.detalles });
  }
  if (err instanceof ErrorIA) {
    logger.warn({ status: err.status, codigo: err.codigo, ruta: req.originalUrl }, err.message);
    return res.status(502).json({ error: 'El módulo de IA no respondió correctamente. Intenta más tarde.', codigo: err.codigo });
  }
  if (err instanceof ZodError) {
    const campos = Object.fromEntries(err.issues.map((i) => [i.path.join('.') || 'body', i.message]));
    return res.status(400).json({ error: 'Datos inválidos', codigo: 'VALIDACION', detalles: { campos } });
  }
  if (err instanceof multer.MulterError) {
    const msg = err.code === 'LIMIT_FILE_SIZE' ? `El archivo excede ${config.MAX_UPLOAD_MB} MB` : err.message;
    return res.status(err.code === 'LIMIT_FILE_SIZE' ? 413 : 400).json({ error: msg, codigo: err.code });
  }
  if ((err as any)?.type === 'entity.parse.failed') {
    return res.status(400).json({ error: 'JSON inválido', codigo: 'JSON_INVALIDO' });
  }
  logger.error({ err, ruta: req.originalUrl }, 'error no controlado');
  // RNF-04: notificar al usuario sin exponer detalles internos
  res.status(500).json({ error: 'Ocurrió un error inesperado. Intenta de nuevo más tarde.', codigo: 'ERROR_INTERNO' });
}
