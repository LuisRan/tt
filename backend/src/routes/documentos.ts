/** Gestión documental del usuario: CUP-03, CUP-04, CUP-05, CUP-06, CUP-07. */
import { Router, type Request } from 'express';
import multer from 'multer';
import { z } from 'zod';
import { config } from '../config.js';
import { uno } from '../db/pg.js';
import { firmarToken } from '../lib/crypto.js';
import { ErrorApp, noEncontrado } from '../lib/errores.js';
import { requiereReautenticacion, requiereSesion } from '../middleware/auth.js';
import {
  crearDocumento,
  descartarDocumento,
  editarDocumentoValidado,
  esquemaValidacion,
  listarDocumentos,
  obtenerDocumento,
  reintentarProcesamiento,
  resumenUsuario,
  TIPOS,
  validarDocumento,
} from '../services/documentos.js';

export const rutasDocumentos = Router();

const subida = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: config.MAX_UPLOAD_MB * 1024 * 1024, files: 1, fields: 5 },
});

const idParam = (req: Request) => {
  const id = Number(req.params.id);
  if (!Number.isInteger(id) || id <= 0) throw noEncontrado();
  return id;
};

rutasDocumentos.get('/resumen', requiereSesion, async (req, res) => {
  res.json(await resumenUsuario(req.usuario!.id));
});

rutasDocumentos.get('/', requiereSesion, async (req, res) => {
  res.json(await listarDocumentos(req.usuario!.id));
});

rutasDocumentos.post(
  '/',
  requiereSesion,
  requiereReautenticacion,
  subida.single('archivo'),
  async (req, res) => {
    const { tipo } = z.object({ tipo: z.enum(TIPOS) }).parse(req.body);
    if (!req.file) throw new ErrorApp(400, 'ARCHIVO_REQUERIDO', 'Selecciona un archivo PDF, JPG o PNG.');
    const doc = await crearDocumento({
      usuarioId: req.usuario!.id,
      tipo,
      archivo: req.file.buffer,
      nombreOriginal: req.file.originalname || `${tipo}.pdf`,
    });
    res.status(202).json({ ...doc, mensaje: 'Documento cargado. Procesando con OCR + IA…' });
  },
);

rutasDocumentos.get('/:id', requiereSesion, async (req, res) => {
  const id = idParam(req);
  res.json(await obtenerDocumento(req.usuario!.id, id));
});

/** Estado ligero para "polling" mientras se procesa. */
rutasDocumentos.get('/:id/estado', requiereSesion, async (req, res) => {
  const d = await uno(
    `SELECT d.id, d.estado, d.motivo_rechazo, d.advertencias, t.clave AS tipo
       FROM documentos d LEFT JOIN tipos_documento t ON t.id = d.tipo_id
      WHERE d.id = $1 AND d.usuario_id = $2`,
    [idParam(req), req.usuario!.id],
  );
  if (!d) throw noEncontrado();
  res.json(d);
});

rutasDocumentos.post('/:id/validar', requiereSesion, requiereReautenticacion, async (req, res) => {
  const id = idParam(req);
  const { datos } = esquemaValidacion.parse(req.body);
  res.json(await validarDocumento(req.usuario!.id, id, datos));
});

rutasDocumentos.post('/:id/procesar', requiereSesion, async (req, res) => {
  const id = idParam(req);
  res.json(await reintentarProcesamiento(req.usuario!.id, id));
});

rutasDocumentos.delete('/:id', requiereSesion, async (req, res) => {
  const id = idParam(req);
  await descartarDocumento(req.usuario!.id, id);
  res.json({ ok: true });
});

/** URL firmada y temporal (5 min) para ver el archivo original (Fig. 19). */
rutasDocumentos.get('/:id/archivo-url', requiereSesion, async (req, res) => {
  const id = idParam(req);
  const d = await uno('SELECT id FROM documentos WHERE id = $1 AND usuario_id = $2', [id, req.usuario!.id]);
  if (!d) throw noEncontrado();
  const token = firmarToken({ d: id, u: req.usuario!.id }, 300);
  res.json({ url: `/api/archivos/${token}`, expira_en_segundos: 300 });
});

/** Editar la información de un documento ya validado (no afecta la métrica de precisión de la IA). */
rutasDocumentos.put('/:id/datos', requiereSesion, requiereReautenticacion, async (req, res) => {
  const { datos } = esquemaValidacion.parse(req.body);
  res.json(await editarDocumentoValidado(req.usuario!.id, idParam(req), datos));
});
