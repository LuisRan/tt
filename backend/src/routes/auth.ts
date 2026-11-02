/**
 * Autenticación: registro (CUP-01/CUA-01) e inicio de sesión con 2FA (CUP/CUA-02).
 */
import bcrypt from 'bcryptjs';
import { Router } from 'express';
import multer from 'multer';
import rateLimit from 'express-rate-limit';
import { z } from 'zod';
import { config } from '../config.js';
import { query, uno } from '../db/pg.js';
import { ErrorApp, noAutorizado } from '../lib/errores.js';
import { emitir, leer, limpiar } from '../lib/sesion.js';
import { requiereRegistroINE, requiereSesion } from '../middleware/auth.js';
import { crearDocumento, obtenerDocumento, validarDocumento, esquemaValidacion } from '../services/documentos.js';
import { registrarAcceso, registrarEvento } from '../services/auditoria.js';
import { emitirCodigo, verificarCodigo } from '../services/dosfa.js';

export const rutasAuth = Router();

const limiteAuth = rateLimit({
  windowMs: 15 * 60_000,
  limit: config.esTest ? 10_000 : 20,
  standardHeaders: 'draft-8',
  legacyHeaders: false,
  message: { error: 'Demasiados intentos. Espera unos minutos.', codigo: 'LIMITE_INTENTOS' },
});

const correo = z.string().trim().toLowerCase().email('Correo inválido').max(254);
// Política de contraseñas: mínimo 10, con mayúscula, minúscula y número
const password = z
  .string()
  .min(10, 'Mínimo 10 caracteres')
  .max(128)
  .regex(/[a-z]/, 'Debe incluir una minúscula')
  .regex(/[A-Z]/, 'Debe incluir una mayúscula')
  .regex(/\d/, 'Debe incluir un número');
const codigo = z.string().trim().regex(/^\d{6}$/, 'El código tiene 6 dígitos');

type FilaUsuario = {
  id: number;
  correo: string;
  password_hash: string;
  rol: 'usuario' | 'administrador';
  activo: boolean;
  correo_verificado: boolean;
  registro_completo: boolean;
  terminos_aceptados_en: string | null;
  token_version: number;
  dosfa_activo: boolean;
  tutorial_visto: boolean;
};

// Hash ficticio para que el tiempo de respuesta no revele si el correo existe
const HASH_FICTICIO = bcrypt.hashSync('contraseña-ficticia-para-tiempo-constante', 12);

// ── Registro ─────────────────────────────────────────────────────────────────

rutasAuth.post('/registro', limiteAuth, async (req, res) => {
  const body = z.object({ correo, password }).parse(req.body);
  const existente = await uno<FilaUsuario>('SELECT * FROM usuarios WHERE correo = $1', [body.correo]);
  if (existente?.registro_completo) {
    throw new ErrorApp(409, 'CORREO_REGISTRADO', 'Este correo ya está registrado. Inicia sesión o usa otro correo.');
  }
  const hash = await bcrypt.hash(body.password, 12);
  let usuarioId: number;
  if (existente) {
    // Registro previo sin terminar: se reinicia con la nueva contraseña
    await query(
      `UPDATE usuarios SET password_hash = $2, correo_verificado = FALSE, terminos_aceptados_en = NULL, registro_completo = FALSE,
                           token_version = token_version + 1, actualizado_en = NOW() WHERE id = $1`,
      [existente.id, hash],
    );
    usuarioId = existente.id;
  } else {
    const nuevo = await uno<{ id: number }>(
      'INSERT INTO usuarios (correo, password_hash) VALUES ($1, $2) RETURNING id',
      [body.correo, hash],
    );
    usuarioId = nuevo!.id;
  }
  await registrarAcceso({ usuarioId, correo: body.correo, tipo: 'registro', exito: true, req });
  if (!config.REGISTRO_VERIFICAR_CORREO) {
    const u = await uno<FilaUsuario>('SELECT * FROM usuarios WHERE id = $1', [usuarioId]);
    await query('UPDATE usuarios SET correo_verificado = TRUE WHERE id = $1', [usuarioId]);
    emitir(res, { sub: u!.id, rol: u!.rol, tv: u!.token_version }, 'registro');
    return res.status(201).json({ ok: true, requiereCodigo: false, siguiente: 'terminos', correo: body.correo });
  }
  await emitirCodigo(usuarioId, body.correo, 'registro');
  res.status(201).json({
    ok: true,
    requiereCodigo: true,
    mensaje: 'Te enviamos un código de verificación a tu correo.',
    correo: body.correo,
  });
});

rutasAuth.post('/registro/verificar', limiteAuth, async (req, res) => {
  const body = z.object({ correo, codigo }).parse(req.body);
  const u = await uno<FilaUsuario>('SELECT * FROM usuarios WHERE correo = $1', [body.correo]);
  if (!u || u.registro_completo) throw new ErrorApp(400, 'CODIGO_INVALIDO', 'Código incorrecto');
  const ok = await verificarCodigo(u.id, 'registro', body.codigo);
  await registrarAcceso({ usuarioId: u.id, correo: u.correo, tipo: '2fa', exito: ok, req });
  if (!ok) throw new ErrorApp(400, 'CODIGO_INVALIDO', 'Código incorrecto o expirado');

  await query('UPDATE usuarios SET correo_verificado = TRUE, actualizado_en = NOW() WHERE id = $1', [u.id]);
  emitir(res, { sub: u.id, rol: u.rol, tv: u.token_version }, 'registro');
  res.json({ ok: true, siguiente: 'terminos' });
});

rutasAuth.post('/registro/reenviar', limiteAuth, async (req, res) => {
  const body = z.object({ correo }).parse(req.body);
  const u = await uno<FilaUsuario>('SELECT * FROM usuarios WHERE correo = $1', [body.correo]);
  if (u && !u.registro_completo) await emitirCodigo(u.id, u.correo, 'registro');
  res.json({ ok: true, mensaje: 'Si el registro está pendiente, enviamos un nuevo código.' });
});

rutasAuth.get('/registro/estado', async (req, res) => {
  const p = leer(req.cookies, 'registro');
  if (!p) throw noAutorizado('Tu sesión de registro expiró. Vuelve a registrarte.');
  const u = await uno<FilaUsuario>('SELECT * FROM usuarios WHERE id = $1', [p.sub]);
  if (!u || u.token_version !== p.tv) throw noAutorizado('Tu sesión de registro expiró.');
  res.json({
    correo: u.correo,
    terminos_aceptados: !!u.terminos_aceptados_en,
    registro_completo: u.registro_completo,
    siguiente: u.terminos_aceptados_en ? 'ine' : 'terminos',
  });
});

const VERSION_TERMINOS = 'TT-2026-10';
const VERSION_AVISO = 'TT-2026-10';
const subidaINE = multer({ storage: multer.memoryStorage(), limits: {
  fileSize: config.MAX_UPLOAD_MB * 1024 * 1024, files: 1, fields: 2,
}});

rutasAuth.post('/registro/terminos', async (req, res) => {
  const p = leer(req.cookies, 'registro');
  if (!p) throw noAutorizado('Tu sesión de registro expiró.');
  const u = await uno<FilaUsuario>('SELECT * FROM usuarios WHERE id = $1', [p.sub]);
  if (!u || u.token_version !== p.tv || !u.correo_verificado || u.registro_completo) throw noAutorizado();
  const { acepta, aceptaAviso, consentimientoExpreso } = z.object({
    acepta: z.boolean(), aceptaAviso: z.boolean().optional(), consentimientoExpreso: z.boolean().optional(),
  }).parse(req.body);
  if (!acepta) {
    await query('DELETE FROM usuarios WHERE id = $1 AND NOT registro_completo', [p.sub]);
    limpiar(res, 'registro');
    await registrarEvento({ tipo: 'auditoria', tabla: 'usuarios', operacion: 'DELETE', descripcion: 'Registro cancelado', req });
    return res.json({ ok: true, cancelado: true });
  }
  if (!aceptaAviso || !consentimientoExpreso) {
    throw new ErrorApp(400, 'CONSENTIMIENTO_REQUERIDO', 'Debes aceptar el aviso de privacidad y el tratamiento de la INE.');
  }
  await query('UPDATE usuarios SET terminos_aceptados_en = NOW(), actualizado_en = NOW() WHERE id = $1', [p.sub]);
  await query(`INSERT INTO consentimientos_privacidad
    (usuario_id, version_terminos, version_aviso, acepta_terminos, acepta_aviso, consentimiento_expreso, ip, user_agent)
    VALUES ($1,$2,$3,TRUE,TRUE,TRUE,$4,$5)`,
    [p.sub, VERSION_TERMINOS, VERSION_AVISO, req.ip ?? null, (req.headers['user-agent'] ?? '').slice(0, 500)]);
  res.json({ ok: true, siguiente: 'ine' });
});

/** Una cuenta limitada sólo puede subir su propia INE durante el registro. */
rutasAuth.post('/registro/ine', requiereRegistroINE, subidaINE.single('archivo'), async (req, res) => {
  if (!req.file) throw new ErrorApp(400, 'ARCHIVO_REQUERIDO', 'Selecciona tu INE en PDF, JPG o PNG.');
  const doc = await crearDocumento({ usuarioId: req.usuario!.id, tipo: 'ine', archivo: req.file.buffer,
    nombreOriginal: req.file.originalname || 'ine.pdf' });
  res.status(202).json({ ...doc, mensaje: 'INE recibida. Esperando la extracción de datos.' });
});

async function inePropia(usuarioId: number, id: number) {
  const d = await uno<{ id: number; estado: string }>(
    `SELECT d.id, d.estado FROM documentos d JOIN tipos_documento t ON t.id=d.tipo_id
      WHERE d.usuario_id=$1 AND d.id=$2 AND t.clave='ine'`, [usuarioId,id]);
  if (!d) throw new ErrorApp(404, 'INE_NO_ENCONTRADA', 'INE no encontrada para esta cuenta.');
  return d;
}
function idINE(raw: unknown) {
  // Express 5 puede tipar los parámetros de ruta como string | string[].
  // Rechazamos arreglos antes de convertir el identificador.
  if (typeof raw !== 'string') {
    throw new ErrorApp(400, 'ID_INVALIDO', 'Identificador de INE inválido.');
  }
  const n = Number(raw);
  if (!Number.isSafeInteger(n) || n < 1) throw new ErrorApp(400, 'ID_INVALIDO', 'Identificador de INE inválido.');
  return n;
}
rutasAuth.get('/registro/ine/ultima', requiereRegistroINE, async (req, res) => {
  const d = await uno<{ id: number; estado: string }>(
    `SELECT d.id,d.estado FROM documentos d JOIN tipos_documento t ON t.id=d.tipo_id
      WHERE d.usuario_id=$1 AND t.clave='ine' ORDER BY d.id DESC LIMIT 1`, [req.usuario!.id]);
  res.json(d || { id: null, estado: null });
});
rutasAuth.get('/registro/ine/:id/estado', requiereRegistroINE, async (req, res) => {
  res.setHeader('Cache-Control','no-store');
  res.json(await inePropia(req.usuario!.id, idINE(req.params.id)));
});
rutasAuth.get('/registro/ine/:id', requiereRegistroINE, async (req, res) => {
  res.setHeader('Cache-Control','no-store');
  const id = idINE(req.params.id);
  await inePropia(req.usuario!.id, id);
  res.json(await obtenerDocumento(req.usuario!.id,id));
});
rutasAuth.post('/registro/ine/:id/validar', requiereRegistroINE, async (req,res) => {
  const id = idINE(req.params.id);
  const doc = await inePropia(req.usuario!.id,id);
  if (doc.estado !== 'procesado') throw new ErrorApp(409, 'INE_NO_PROCESADA', 'La INE debe procesarse antes de revisarla.');
  const { datos, declaraTitular } = z.object({
    datos: esquemaValidacion.shape.datos,
    declaraTitular: z.literal(true),
  }).parse(req.body);
  res.json(await validarDocumento(req.usuario!.id, id, datos));
});

/** La transición a sesión normal se decide SIEMPRE en PostgreSQL, no en Astro. */
rutasAuth.post('/registro/finalizar', requiereRegistroINE, async (req,res) => {
  const usuarioId = req.usuario!.id;
  const ine = await uno<{ id: number }>(
    `SELECT d.id FROM documentos d JOIN tipos_documento t ON t.id=d.tipo_id
      WHERE d.usuario_id=$1 AND d.estado='validado' AND t.clave='ine'
      ORDER BY d.actualizado_en DESC LIMIT 1`, [usuarioId]);
  const perfil = await uno<{ verificado: boolean }>('SELECT verificado FROM perfil_usuario WHERE usuario_id=$1', [usuarioId]);
  const consentimiento = await uno<{ id: number }>(
    `SELECT id FROM consentimientos_privacidad WHERE usuario_id=$1 AND acepta_terminos
       AND acepta_aviso AND consentimiento_expreso ORDER BY aceptado_en DESC LIMIT 1`, [usuarioId]);
  if (!ine || !perfil?.verificado || !consentimiento) {
    throw new ErrorApp(409, 'REGISTRO_INCOMPLETO', 'Falta validar tu INE y confirmar tu información.');
  }
  const u = await uno<FilaUsuario>(
    `UPDATE usuarios SET registro_completo=TRUE, ultimo_acceso=NOW(), actualizado_en=NOW()
      WHERE id=$1 AND correo_verificado AND terminos_aceptados_en IS NOT NULL RETURNING *`, [usuarioId]);
  if (!u) throw noAutorizado();
  limpiar(res, 'registro');
  emitir(res, { sub:u.id, rol:u.rol, tv:u.token_version }, 'app');
  await registrarEvento({ usuarioId, tipo:'auditoria', tabla:'usuarios', operacion:'REGISTRO',
    descripcion:'Registro completado con INE confirmada', req });
  res.json({ ok:true, rol:u.rol, redirigir:'/app/tutorial' });
});

// ── Inicio de sesión con 2FA ─────────────────────────────────────────────────

rutasAuth.post('/login', limiteAuth, async (req, res) => {
  const body = z.object({ correo, password: z.string().min(1).max(128) }).parse(req.body);
  const u = await uno<FilaUsuario>('SELECT * FROM usuarios WHERE correo = $1', [body.correo]);
  const ok = await bcrypt.compare(body.password, u?.password_hash ?? HASH_FICTICIO);

  if (!u || !ok) {
    await registrarAcceso({ usuarioId: u?.id ?? null, correo: body.correo, tipo: 'intento', exito: false, req });
    throw new ErrorApp(401, 'CREDENCIALES_INVALIDAS', 'Correo o contraseña incorrectos');
  }
  if (!u.activo) {
    await registrarAcceso({ usuarioId: u.id, correo: u.correo, tipo: 'intento', exito: false, req });
    throw new ErrorApp(403, 'CUENTA_DESHABILITADA', 'Tu cuenta está deshabilitada. Contacta al administrador.');
  }
  if (!u.registro_completo) {
    // Puede retomar su registro: se reenvía el código de verificación
    await emitirCodigo(u.id, u.correo, 'registro').catch(() => undefined);
    throw new ErrorApp(409, 'REGISTRO_INCOMPLETO', 'Tu registro no está completo. Te enviamos un código para continuarlo.', {
      correo: u.correo,
    });
  }

  await registrarAcceso({ usuarioId: u.id, correo: u.correo, tipo: 'login', exito: true, req });
  if (!u.dosfa_activo) {
    // 2FA desactivado por el usuario: la sesión se abre con la contraseña
    emitir(res, { sub: u.id, rol: u.rol, tv: u.token_version }, 'app');
    await query('UPDATE usuarios SET ultimo_acceso = NOW() WHERE id = $1', [u.id]);
    return res.json({ ok: true, requiere2fa: false, rol: u.rol, redirigir: destinoInicial(u) });
  }
  await emitirCodigo(u.id, u.correo, 'login');
  emitir(res, { sub: u.id, rol: u.rol, tv: u.token_version }, 'mfa');
  res.json({ ok: true, requiere2fa: true, mensaje: 'Te enviamos un código de acceso a tu correo.' });
});

function destinoInicial(u: FilaUsuario): string {
  if (u.rol === 'administrador') return '/admin';
  return u.tutorial_visto ? '/app' : '/app/tutorial';
}

rutasAuth.post('/login/verificar', limiteAuth, async (req, res) => {
  const { codigo: cod } = z.object({ codigo }).parse(req.body);
  const p = leer(req.cookies, 'mfa');
  if (!p) throw noAutorizado('La verificación expiró. Inicia sesión de nuevo.');
  const u = await uno<FilaUsuario>('SELECT * FROM usuarios WHERE id = $1', [p.sub]);
  if (!u || !u.activo || u.token_version !== p.tv) throw noAutorizado();

  const ok = await verificarCodigo(u.id, 'login', cod);
  await registrarAcceso({ usuarioId: u.id, correo: u.correo, tipo: '2fa', exito: ok, req });
  if (!ok) throw new ErrorApp(400, 'CODIGO_INVALIDO', 'Código incorrecto o expirado');

  limpiar(res, 'mfa');
  emitir(res, { sub: u.id, rol: u.rol, tv: u.token_version }, 'app');
  await query('UPDATE usuarios SET ultimo_acceso = NOW() WHERE id = $1', [u.id]);
  res.json({ ok: true, rol: u.rol, redirigir: destinoInicial(u) });
});

rutasAuth.post('/login/reenviar', limiteAuth, async (req, res) => {
  const p = leer(req.cookies, 'mfa');
  if (!p) throw noAutorizado('La verificación expiró. Inicia sesión de nuevo.');
  const u = await uno<FilaUsuario>('SELECT * FROM usuarios WHERE id = $1', [p.sub]);
  if (!u) throw noAutorizado();
  await emitirCodigo(u.id, u.correo, 'login');
  emitir(res, { sub: u.id, rol: u.rol, tv: u.token_version }, 'mfa');
  res.json({ ok: true });
});

rutasAuth.post('/logout', async (req, res) => {
  const p = leer(req.cookies, 'app');
  if (p) await registrarAcceso({ usuarioId: p.sub, tipo: 'logout', exito: true, req });
  limpiar(res, 'app', 'reauth', 'mfa', 'registro');
  res.json({ ok: true });
});

/** Cierra todas las sesiones abiertas del usuario (revoca JWT emitidos). */
rutasAuth.post('/logout-todo', requiereSesion, async (req, res) => {
  await query('UPDATE usuarios SET token_version = token_version + 1 WHERE id = $1', [req.usuario!.id]);
  limpiar(res, 'app', 'reauth');
  await registrarEvento({ usuarioId: req.usuario!.id, tipo: 'seguridad', operacion: 'REVOCAR_SESIONES', descripcion: 'Cierre de todas las sesiones', req });
  res.json({ ok: true });
});

/** Confirmación de contraseña para operaciones sensibles (reautenticación). */
rutasAuth.post('/confirmar-password', limiteAuth, requiereSesion, async (req, res) => {
  const { password: pass } = z.object({ password: z.string().min(1).max(128) }).parse(req.body);
  const u = await uno<FilaUsuario>('SELECT * FROM usuarios WHERE id = $1', [req.usuario!.id]);
  if (!u || !(await bcrypt.compare(pass, u.password_hash))) {
    await registrarEvento({ usuarioId: req.usuario!.id, tipo: 'seguridad', operacion: 'REAUTH', descripcion: 'Contraseña incorrecta en operación sensible', req });
    throw new ErrorApp(401, 'CONTRASENA_INCORRECTA', 'Contraseña incorrecta');
  }
  emitir(res, { sub: u.id, rol: u.rol, tv: u.token_version }, 'reauth');
  res.json({ ok: true, minutos: config.REAUTH_MINUTOS });
});

rutasAuth.get('/yo', requiereSesion, async (req, res) => {
  const u = await uno(
    `SELECT u.id, u.correo, u.rol, u.creado_en, u.ultimo_acceso, u.alertas_vigencia, u.dosfa_activo, u.tutorial_visto,
            p.nombre, p.apellido_paterno, p.apellido_materno, p.verificado
       FROM usuarios u LEFT JOIN perfil_usuario p ON p.usuario_id = u.id WHERE u.id = $1`,
    [req.usuario!.id],
  );
  res.json(u);
});
