/**
 * Autenticación: registro (CUP-01/CUA-01) e inicio de sesión con 2FA (CUP/CUA-02).
 */
import bcrypt from 'bcryptjs';
import { Router } from 'express';
import rateLimit from 'express-rate-limit';
import { z } from 'zod';
import { config } from '../config.js';
import { query, uno } from '../db/pg.js';
import { ErrorApp, noAutorizado } from '../lib/errores.js';
import { emitir, leer, limpiar } from '../lib/sesion.js';
import { requiereSesion } from '../middleware/auth.js';
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
      `UPDATE usuarios SET password_hash = $2, correo_verificado = FALSE, terminos_aceptados_en = NULL,
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
  });
});

rutasAuth.post('/registro/terminos', async (req, res) => {
  const p = leer(req.cookies, 'registro');
  if (!p) throw noAutorizado('Tu sesión de registro expiró.');
  const { acepta } = z.object({ acepta: z.boolean() }).parse(req.body);
  if (!acepta) {
    // CU-01 alterno: el usuario rechaza los términos -> se cancela el registro sin guardar datos
    await query('DELETE FROM usuarios WHERE id = $1 AND NOT registro_completo', [p.sub]);
    limpiar(res, 'registro');
    await registrarEvento({ tipo: 'auditoria', tabla: 'usuarios', operacion: 'DELETE', descripcion: 'Registro cancelado: términos rechazados', req });
    return res.json({ ok: true, cancelado: true });
  }
  // Al aceptar los términos el registro queda completo y se abre la sesión.
  // La primera vez se muestra el tutorial (tutorial_visto = FALSE); la carga
  // de documentos ya no es obligatoria para registrarse.
  const u = await uno<FilaUsuario>(
    `UPDATE usuarios SET terminos_aceptados_en = NOW(), registro_completo = TRUE, ultimo_acceso = NOW(),
                         actualizado_en = NOW()
      WHERE id = $1 RETURNING *`,
    [p.sub],
  );
  if (!u || u.token_version !== p.tv) throw noAutorizado();
  limpiar(res, 'registro');
  emitir(res, { sub: u.id, rol: u.rol, tv: u.token_version }, 'app');
  await registrarEvento({ usuarioId: u.id, tipo: 'auditoria', tabla: 'usuarios', operacion: 'REGISTRO', descripcion: 'Registro completado', req });
  res.json({ ok: true, rol: u.rol, redirigir: '/app/tutorial' });
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
