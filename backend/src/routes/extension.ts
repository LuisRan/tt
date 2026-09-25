/**
 * Extensión de navegador (bóveda de datos a la mano).
 *  - Con sesión de la plataforma: info, generar código, descargar paquete,
 *    listar y desvincular extensiones.
 *  - Con el token de la extensión (Bearer): documentos validados y desvincular.
 */
import { Router, type Request } from 'express';
import rateLimit from 'express-rate-limit';
import { z } from 'zod';
import { config } from '../config.js';
import { noEncontrado } from '../lib/errores.js';
import { requiereReautenticacion, requiereSesion } from '../middleware/auth.js';
import {
  canjearCodigo,
  documentosParaExtension,
  generarCodigoVinculacion,
  infoExtension,
  listarDispositivos,
  paqueteExtension,
  requiereExtension,
  revocarDispositivo,
} from '../services/extension.js';

export const rutasExtension = Router();

const limiteVinculacion = rateLimit({
  windowMs: 15 * 60_000,
  limit: config.esTest ? 10_000 : 20,
  standardHeaders: 'draft-8',
  legacyHeaders: false,
  message: { error: 'Demasiados intentos de vinculación. Espera unos minutos.', codigo: 'LIMITE_INTENTOS' },
});

const idParam = (req: Request) => {
  const id = Number(req.params.id);
  if (!Number.isInteger(id) || id <= 0) throw noEncontrado();
  return id;
};

// ── Desde la plataforma (cookie de sesión) ──────────────────────────────────
rutasExtension.get('/info', requiereSesion, (_req, res) => {
  res.json(infoExtension());
});

rutasExtension.post('/codigo', requiereSesion, requiereReautenticacion, async (req, res) => {
  res.json(await generarCodigoVinculacion(req.usuario!.id));
});

rutasExtension.post('/paquete', requiereSesion, requiereReautenticacion, async (req, res) => {
  const zip = await paqueteExtension(req.usuario!.id, req);
  res.setHeader('Content-Type', 'application/zip');
  res.setHeader('Content-Disposition', 'attachment; filename="ocr-platform-extension.zip"');
  res.setHeader('Cache-Control', 'no-store');
  res.send(zip);
});

rutasExtension.get('/dispositivos', requiereSesion, async (req, res) => {
  res.json(await listarDispositivos(req.usuario!.id));
});

rutasExtension.delete('/dispositivos/:id', requiereSesion, async (req, res) => {
  await revocarDispositivo(req.usuario!.id, idParam(req), req);
  res.json({ ok: true });
});

// ── Desde la extensión ──────────────────────────────────────────────────────
rutasExtension.post('/vincular', limiteVinculacion, async (req, res) => {
  const { codigo, nombre } = z
    .object({ codigo: z.string().trim().min(8).max(12), nombre: z.string().trim().max(80).optional() })
    .parse(req.body);
  res.json(await canjearCodigo(codigo, nombre, req));
});

rutasExtension.get('/documentos', requiereExtension, async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.json(await documentosParaExtension(req.usuario!.id));
});

rutasExtension.post('/desvincular', requiereExtension, async (req, res) => {
  await revocarDispositivo(req.usuario!.id, req.dispositivoId!, req);
  res.json({ ok: true });
});
