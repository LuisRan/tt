/** Perfil del usuario (Configuración): datos, seguridad (2FA), tutorial y autocompletado. */
import bcrypt from 'bcryptjs';
import { Router } from 'express';
import { z } from 'zod';
import { query, uno } from '../db/pg.js';
import { ErrorApp } from '../lib/errores.js';
import { limpiar } from '../lib/sesion.js';
import { requiereReautenticacion, requiereSesion } from '../middleware/auth.js';
import { registrarEvento } from '../services/auditoria.js';
import { actualizarPerfil, esquemaPerfil, leerPerfil } from '../services/perfil.js';

export const rutasPerfil = Router();
rutasPerfil.use(requiereSesion);

rutasPerfil.get('/', async (req, res) => {
  res.json(await leerPerfil(req.usuario!.id));
});

/** El usuario edita su propio perfil (requiere confirmar contraseña). */
rutasPerfil.put('/', requiereReautenticacion, async (req, res) => {
  const datos = esquemaPerfil.parse(req.body ?? {});
  await actualizarPerfil(req.usuario!.id, datos);
  await registrarEvento({
    usuarioId: req.usuario!.id,
    tipo: 'auditoria',
    tabla: 'perfil_usuario',
    operacion: 'UPDATE',
    descripcion: `Perfil editado por el usuario (campos: ${Object.keys(datos).filter((k) => (datos as any)[k] !== undefined).join(', ')})`,
    req,
  });
  res.json(await leerPerfil(req.usuario!.id));
});

rutasPerfil.put('/preferencias', async (req, res) => {
  const { alertas_vigencia } = z.object({ alertas_vigencia: z.boolean() }).parse(req.body);
  await query('UPDATE usuarios SET alertas_vigencia = $2, actualizado_en = NOW() WHERE id = $1', [req.usuario!.id, alertas_vigencia]);
  res.json({ ok: true });
});

/** Activar/desactivar la verificación en dos pasos (requiere confirmar contraseña). */
rutasPerfil.put('/seguridad', requiereReautenticacion, async (req, res) => {
  const { dosfa_activo } = z.object({ dosfa_activo: z.boolean() }).parse(req.body);
  await query('UPDATE usuarios SET dosfa_activo = $2, actualizado_en = NOW() WHERE id = $1', [req.usuario!.id, dosfa_activo]);
  await registrarEvento({
    usuarioId: req.usuario!.id,
    tipo: 'seguridad',
    tabla: 'usuarios',
    operacion: dosfa_activo ? '2FA_ACTIVADO' : '2FA_DESACTIVADO',
    descripcion: `Verificación en dos pasos ${dosfa_activo ? 'activada' : 'desactivada'} por el usuario`,
    req,
  });
  res.json({ ok: true, dosfa_activo });
});

rutasPerfil.post('/tutorial', async (req, res) => {
  await query('UPDATE usuarios SET tutorial_visto = TRUE WHERE id = $1', [req.usuario!.id]);
  res.json({ ok: true });
});

rutasPerfil.post('/password', async (req, res) => {
  const body = z
    .object({
      actual: z.string().min(1).max(128),
      nueva: z
        .string()
        .min(10, 'Mínimo 10 caracteres')
        .max(128)
        .regex(/[a-z]/, 'Debe incluir una minúscula')
        .regex(/[A-Z]/, 'Debe incluir una mayúscula')
        .regex(/\d/, 'Debe incluir un número'),
    })
    .parse(req.body);
  const u = await uno<{ password_hash: string }>('SELECT password_hash FROM usuarios WHERE id = $1', [req.usuario!.id]);
  if (!u || !(await bcrypt.compare(body.actual, u.password_hash))) {
    throw new ErrorApp(401, 'CONTRASENA_INCORRECTA', 'La contraseña actual es incorrecta');
  }
  // Cambiar la contraseña revoca todas las sesiones (token_version + 1)
  await query(
    'UPDATE usuarios SET password_hash = $2, token_version = token_version + 1, actualizado_en = NOW() WHERE id = $1',
    [req.usuario!.id, await bcrypt.hash(body.nueva, 12)],
  );
  limpiar(res, 'app', 'reauth');
  await registrarEvento({ usuarioId: req.usuario!.id, tipo: 'seguridad', tabla: 'usuarios', operacion: 'PASSWORD', descripcion: 'Cambio de contraseña', req });
  res.json({ ok: true, mensaje: 'Contraseña actualizada. Inicia sesión de nuevo.' });
});

/**
 * Datos listos para autocompletar formularios externos (uso interno / futura
 * extensión de navegador). No se muestra en la interfaz del usuario.
 */
rutasPerfil.get('/autofill', async (req, res) => {
  const p = await leerPerfil(req.usuario!.id);
  const plantillas = await query<{ campo_web: string; campo_bd: string }>('SELECT campo_web, campo_bd FROM plantillas_autofill');
  const fuente: Record<string, unknown> = {
    'perfil.nombre': p?.nombre,
    'perfil.apellido_paterno': p?.apellido_paterno,
    'perfil.apellido_materno': p?.apellido_materno,
    'perfil.nombre_completo': [p?.nombre, p?.apellido_paterno, p?.apellido_materno].filter(Boolean).join(' ') || null,
    'perfil.fecha_nacimiento': p?.fecha_nacimiento,
    'perfil.sexo': p?.sexo,
    'perfil.direccion': p?.direccion,
    'perfil.curp': p?.curp,
    'usuario.correo': p?.correo,
  };
  const campos = Object.fromEntries(plantillas.rows.map((t) => [t.campo_web, fuente[t.campo_bd] ?? null]));
  await registrarEvento({ usuarioId: req.usuario!.id, tipo: 'auditoria', operacion: 'AUTOFILL', descripcion: 'Consulta de datos para autocompletado', req });
  res.json({ verificado: !!p?.verificado, campos });
});
