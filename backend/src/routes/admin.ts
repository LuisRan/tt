/** Módulo administrativo: RF-ADM-01..05 (CUA-03..06). */
import bcrypt from 'bcryptjs';
import { Router } from 'express';
import { z } from 'zod';
import { mongoSano } from '../db/mongo.js';
import { query, uno } from '../db/pg.js';
import { descifrar } from '../lib/crypto.js';
import { ErrorApp, noEncontrado } from '../lib/errores.js';
import { requiereRol, requiereSesion } from '../middleware/auth.js';
import { registrarEvento } from '../services/auditoria.js';
import { almacenamiento } from '../services/almacenamiento.js';
import { actualizarPerfil, esquemaPerfil } from '../services/perfil.js';
import { resultados } from '../db/mongo.js';
import { estadoCola } from '../services/documentos.js';
import { saludIA } from '../services/ia-cliente.js';

export const rutasAdmin = Router();
rutasAdmin.use(requiereSesion, requiereRol('administrador'));

const enmascarar = (v: string | null) => (v ? `${v.slice(0, 4)}${'•'.repeat(Math.max(0, v.length - 6))}${v.slice(-2)}` : null);

async function estadoSistema() {
  const [pg, mongo, ia] = await Promise.all([
    query('SELECT 1').then(() => true).catch(() => false),
    mongoSano(),
    saludIA(),
  ]);
  return {
    servidor_api: true,
    base_datos: pg,
    mongo,
    ocr: ia ? !!ia.ocr_disponible : false,
    llm: ia ? !!ia.llm_disponible : false,
    ia_estado: ia?.status ?? 'sin_respuesta',
    modelo_llm: ia?.modelo_llm ?? null,
    plataforma_ia: ia?.plataforma ? { sistema: ia.plataforma.sistema, motor: ia.plataforma.motor_ocr_docling, docker: ia.plataforma.en_docker } : null,
    cola: estadoCola(),
  };
}

// Dashboard (Fig. 29)
rutasAdmin.get('/resumen', async (_req, res) => {
  const kpis = await uno(
    `SELECT (SELECT COUNT(*) FROM usuarios WHERE activo AND registro_completo AND rol = 'usuario')::int AS usuarios_activos,
            (SELECT COUNT(*) FROM documentos WHERE estado IN ('procesado','validado'))::int AS documentos_procesados,
            (SELECT COUNT(*) FROM metricas_ia)::int AS solicitudes_ocr,
            ((SELECT COUNT(*) FROM metricas_ia WHERE NOT exito AND creado_en > NOW() - INTERVAL '30 days') +
             (SELECT COUNT(*) FROM eventos_sistema WHERE tipo_evento = 'error' AND fecha > NOW() - INTERVAL '30 days'))::int AS errores_detectados`,
  );
  const actividad = await query(
    `SELECT e.id, e.tipo_evento, e.operacion, e.descripcion, e.fecha, u.correo
       FROM eventos_sistema e LEFT JOIN usuarios u ON u.id = e.usuario_id
      ORDER BY e.fecha DESC LIMIT 8`,
  );
  res.json({ ...kpis, actividad_reciente: actividad.rows, estado_sistema: await estadoSistema() });
});

rutasAdmin.get('/estado', async (_req, res) => {
  res.json(await estadoSistema());
});

// CUA-03 Consultar usuarios (paginado)
rutasAdmin.get('/usuarios', async (req, res) => {
  const q = z
    .object({
      pagina: z.coerce.number().int().min(1).default(1),
      por_pagina: z.coerce.number().int().min(1).max(100).default(20),
      buscar: z.string().trim().max(100).optional(),
      estado: z.enum(['todos', 'activos', 'inactivos', 'pendientes']).default('todos'),
    })
    .parse(req.query);
  const filtros: string[] = [];
  const params: unknown[] = [];
  if (q.buscar) {
    params.push(`%${q.buscar.toLowerCase()}%`);
    filtros.push(`(LOWER(u.correo) LIKE $${params.length} OR LOWER(COALESCE(p.nombre,'') || ' ' || COALESCE(p.apellido_paterno,'')) LIKE $${params.length})`);
  }
  if (q.estado === 'activos') filtros.push('u.activo AND u.registro_completo');
  if (q.estado === 'inactivos') filtros.push('NOT u.activo');
  if (q.estado === 'pendientes') filtros.push('NOT u.registro_completo');
  const where = filtros.length ? `WHERE ${filtros.join(' AND ')}` : '';

  const total = await uno<{ n: number }>(
    `SELECT COUNT(*)::int AS n FROM usuarios u LEFT JOIN perfil_usuario p ON p.usuario_id = u.id ${where}`,
    params,
  );
  params.push(q.por_pagina, (q.pagina - 1) * q.por_pagina);
  const filas = await query(
    `SELECT u.id, u.correo, u.rol, u.activo, u.registro_completo, u.creado_en, u.ultimo_acceso,
            p.nombre, p.apellido_paterno, p.verificado,
            (SELECT COUNT(*) FROM documentos d WHERE d.usuario_id = u.id)::int AS documentos
       FROM usuarios u LEFT JOIN perfil_usuario p ON p.usuario_id = u.id
       ${where}
      ORDER BY u.creado_en DESC
      LIMIT $${params.length - 1} OFFSET $${params.length}`,
    params,
  );
  res.json({ total: total?.n ?? 0, pagina: q.pagina, por_pagina: q.por_pagina, usuarios: filas.rows });
});

rutasAdmin.get('/usuarios/:id', async (req, res) => {
  const id = Number(req.params.id);
  const u = await uno(
    `SELECT u.id, u.correo, u.rol, u.activo, u.registro_completo, u.creado_en, u.ultimo_acceso, u.terminos_aceptados_en,
            u.dosfa_activo, u.alertas_vigencia,
            p.nombre, p.apellido_paterno, p.apellido_materno, p.curp, p.verificado, p.fecha_nacimiento, p.sexo,
            p.direccion, p.editado_manual
       FROM usuarios u LEFT JOIN perfil_usuario p ON p.usuario_id = u.id WHERE u.id = $1`,
    [id],
  );
  if (!u) throw noEncontrado('Usuario no encontrado');
  const docs = await query(
    `SELECT d.id, t.nombre AS tipo, d.estado, d.version, d.vigente, d.fecha_vigencia, d.creado_en
       FROM documentos d LEFT JOIN tipos_documento t ON t.id = d.tipo_id
      WHERE d.usuario_id = $1 ORDER BY d.creado_en DESC`,
    [id],
  );
  const accesos = await query(
    `SELECT tipo, exito, ip, fecha FROM accesos WHERE usuario_id = $1 ORDER BY fecha DESC LIMIT 10`,
    [id],
  );
  // Minimización de datos: el administrador ve la CURP enmascarada
  res.json({
    ...u,
    curp: enmascarar(descifrar(u.curp)),
    direccion: descifrar(u.direccion),
    documentos: docs.rows,
    accesos: accesos.rows,
  });
});


// ── CRUD de usuarios (administrador) ────────────────────────────────────────
const politicaPassword = z
  .string()
  .min(10, 'Mínimo 10 caracteres')
  .max(128)
  .regex(/[a-z]/, 'Debe incluir una minúscula')
  .regex(/[A-Z]/, 'Debe incluir una mayúscula')
  .regex(/\d/, 'Debe incluir un número');

const esquemaCuenta = z.object({
  correo: z.string().trim().toLowerCase().email('Correo inválido').max(254).optional(),
  rol: z.enum(['usuario', 'administrador']).optional(),
  activo: z.boolean().optional(),
  dosfa_activo: z.boolean().optional(),
  alertas_vigencia: z.boolean().optional(),
});

rutasAdmin.post('/usuarios', async (req, res) => {
  const body = esquemaCuenta
    .extend({ correo: z.string().trim().toLowerCase().email('Correo inválido').max(254), password: politicaPassword })
    .merge(esquemaPerfil)
    .parse(req.body);
  const existe = await uno('SELECT 1 FROM usuarios WHERE correo = $1', [body.correo]);
  if (existe) throw new ErrorApp(409, 'CORREO_REGISTRADO', 'Ya existe una cuenta con ese correo');
  const u = await uno<{ id: number }>(
    `INSERT INTO usuarios (correo, password_hash, rol, activo, dosfa_activo, correo_verificado, registro_completo,
                           terminos_aceptados_en, tutorial_visto)
     VALUES ($1, $2, $3, $4, $5, TRUE, TRUE, NOW(), $6) RETURNING id`,
    [
      body.correo,
      await bcrypt.hash(body.password, 12),
      body.rol ?? 'usuario',
      body.activo ?? true,
      body.dosfa_activo ?? false,
      (body.rol ?? 'usuario') === 'administrador',
    ],
  );
  await actualizarPerfil(u!.id, body);
  await registrarEvento({ usuarioId: req.usuario!.id, tipo: 'auditoria', tabla: 'usuarios', operacion: 'INSERT', descripcion: `Cuenta ${u!.id} (${body.correo}) creada por administrador`, req });
  res.status(201).json({ id: u!.id });
});

rutasAdmin.put('/usuarios/:id', async (req, res) => {
  const id = Number(req.params.id);
  const body = esquemaCuenta.merge(esquemaPerfil).parse(req.body ?? {});
  const actual = await uno<{ id: number; rol: string; correo: string }>('SELECT id, rol, correo FROM usuarios WHERE id = $1', [id]);
  if (!actual) throw noEncontrado('Usuario no encontrado');
  if (id === req.usuario!.id && (body.activo === false || body.rol === 'usuario')) {
    throw new ErrorApp(400, 'OPERACION_INVALIDA', 'No puedes deshabilitarte ni quitarte el rol de administrador.');
  }
  if (body.correo && body.correo !== actual.correo) {
    const existe = await uno('SELECT 1 FROM usuarios WHERE correo = $1 AND id <> $2', [body.correo, id]);
    if (existe) throw new ErrorApp(409, 'CORREO_REGISTRADO', 'Ya existe una cuenta con ese correo');
  }
  const cuenta: Record<string, unknown> = {};
  for (const k of ['correo', 'rol', 'activo', 'dosfa_activo', 'alertas_vigencia'] as const) {
    if (body[k] !== undefined) cuenta[k] = body[k];
  }
  if (Object.keys(cuenta).length) {
    const sets = Object.keys(cuenta).map((c, i) => `${c} = $${i + 2}`);
    // Cambiar rol, correo o desactivar revoca las sesiones abiertas del usuario
    const revocar = cuenta.rol !== undefined || cuenta.correo !== undefined || cuenta.activo === false;
    await query(
      `UPDATE usuarios SET ${sets.join(', ')}${revocar ? ', token_version = token_version + 1' : ''}, actualizado_en = NOW() WHERE id = $1`,
      [id, ...Object.values(cuenta)],
    );
  }
  await actualizarPerfil(id, body);
  const campos = Object.keys(body).filter((k) => (body as any)[k] !== undefined);
  await registrarEvento({ usuarioId: req.usuario!.id, tipo: 'auditoria', tabla: 'usuarios', operacion: 'UPDATE', descripcion: `Cuenta ${id} actualizada por administrador (${campos.join(', ')})`, req });
  res.json({ ok: true });
});

rutasAdmin.post('/usuarios/:id/password', async (req, res) => {
  const id = Number(req.params.id);
  const { password } = z.object({ password: politicaPassword }).parse(req.body);
  const r = await uno<{ correo: string }>(
    'UPDATE usuarios SET password_hash = $2, token_version = token_version + 1, actualizado_en = NOW() WHERE id = $1 RETURNING correo',
    [id, await bcrypt.hash(password, 12)],
  );
  if (!r) throw noEncontrado('Usuario no encontrado');
  await registrarEvento({ usuarioId: req.usuario!.id, tipo: 'seguridad', tabla: 'usuarios', operacion: 'RESET_PASSWORD', descripcion: `Contraseña de la cuenta ${id} (${r.correo}) restablecida por administrador`, req });
  res.json({ ok: true });
});

// CUA-04 Eliminar: baja lógica (activo = false) por defecto; ?definitivo=true borra la cuenta
rutasAdmin.delete('/usuarios/:id', async (req, res) => {
  const id = Number(req.params.id);
  if (id === req.usuario!.id) throw new ErrorApp(400, 'OPERACION_INVALIDA', 'No puedes eliminar tu propia cuenta.');
  if (req.query.definitivo === 'true') {
    const u = await uno<{ correo: string }>('SELECT correo FROM usuarios WHERE id = $1', [id]);
    if (!u) throw noEncontrado('Usuario no encontrado');
    const docs = await query<{ id: number; ruta_archivo: string }>('SELECT id, ruta_archivo FROM documentos WHERE usuario_id = $1', [id]);
    for (const d of docs.rows) {
      await almacenamiento.eliminar(d.ruta_archivo).catch(() => undefined);
      await resultados().deleteOne({ documento_id: d.id }).catch(() => undefined);
    }
    await query('DELETE FROM usuarios WHERE id = $1', [id]);
    await registrarEvento({ usuarioId: req.usuario!.id, tipo: 'auditoria', tabla: 'usuarios', operacion: 'DELETE', descripcion: `Cuenta ${id} (${u.correo}) eliminada definitivamente con ${docs.rowCount} documento(s)`, req });
    return res.json({ ok: true, eliminado: true });
  }
  const r = await uno<{ correo: string }>(
    `UPDATE usuarios SET activo = FALSE, token_version = token_version + 1, actualizado_en = NOW()
      WHERE id = $1 RETURNING correo`,
    [id],
  );
  if (!r) throw noEncontrado('Usuario no encontrado');
  await registrarEvento({
    usuarioId: req.usuario!.id,
    tipo: 'auditoria',
    tabla: 'usuarios',
    operacion: 'SOFT_DELETE',
    descripcion: `Cuenta ${id} (${r.correo}) deshabilitada por administrador`,
    req,
  });
  res.json({ ok: true });
});

rutasAdmin.post('/usuarios/:id/reactivar', async (req, res) => {
  const id = Number(req.params.id);
  const r = await uno<{ correo: string }>('UPDATE usuarios SET activo = TRUE, actualizado_en = NOW() WHERE id = $1 RETURNING correo', [id]);
  if (!r) throw noEncontrado('Usuario no encontrado');
  await registrarEvento({ usuarioId: req.usuario!.id, tipo: 'auditoria', tabla: 'usuarios', operacion: 'REACTIVAR', descripcion: `Cuenta ${id} (${r.correo}) reactivada`, req });
  res.json({ ok: true });
});

// CUA-05 Consultar tráfico del sistema
rutasAdmin.get('/trafico', async (req, res) => {
  const { dias } = z.object({ dias: z.coerce.number().int().min(1).max(365).default(30) }).parse(req.query);
  const serie = await query(
    `WITH dias AS (SELECT generate_series(CURRENT_DATE - ($1::int - 1), CURRENT_DATE, INTERVAL '1 day')::date AS dia)
     SELECT d.dia,
            (SELECT COUNT(*) FROM accesos a WHERE a.fecha::date = d.dia AND a.tipo = '2fa' AND a.exito)::int AS accesos_exitosos,
            (SELECT COUNT(*) FROM accesos a WHERE a.fecha::date = d.dia AND NOT a.exito)::int AS accesos_fallidos,
            (SELECT COUNT(DISTINCT a.usuario_id) FROM accesos a WHERE a.fecha::date = d.dia AND a.exito)::int AS usuarios_activos,
            (SELECT COUNT(*) FROM metricas_ia m WHERE m.creado_en::date = d.dia)::int AS solicitudes_ocr,
            (SELECT COUNT(*) FROM documentos x WHERE x.creado_en::date = d.dia)::int AS documentos_cargados,
            (SELECT COALESCE(SUM(t.total),0) FROM trafico_api t WHERE t.hora::date = d.dia)::int AS solicitudes_api
       FROM dias d ORDER BY d.dia`,
    [dias],
  );
  const totales = await uno(
    `SELECT (SELECT ROUND(AVG(tiempo_procesamiento))::int FROM metricas_ia WHERE exito AND creado_en > NOW() - make_interval(days => $1)) AS tiempo_promedio_ms,
            (SELECT ROUND(AVG(confianza)::numeric, 2) FROM metricas_ia WHERE exito AND creado_en > NOW() - make_interval(days => $1)) AS confianza_promedio,
            (SELECT COUNT(*) FROM metricas_ia WHERE NOT exito AND creado_en > NOW() - make_interval(days => $1))::int AS errores_ia,
            (SELECT COUNT(*) FROM usuarios WHERE creado_en > NOW() - make_interval(days => $1))::int AS registros,
            -- Precisión de la IA: % de campos que el usuario NO tuvo que corregir en la
            -- PRIMERA validación. Las ediciones posteriores (origen='edicion') no cuentan.
            (SELECT ROUND(1 - AVG(CASE WHEN corregido THEN 1 ELSE 0 END)::numeric, 3)
               FROM validaciones_dato
              WHERE origen = 'validacion' AND creado_en > NOW() - make_interval(days => $1)) AS precision_campos,
            (SELECT COUNT(*) FROM validaciones_dato
              WHERE origen = 'validacion' AND creado_en > NOW() - make_interval(days => $1))::int AS campos_evaluados`,
    [dias],
  );
  const porTipo = await query(
    `SELECT t.nombre AS tipo, COUNT(*)::int AS total FROM documentos d JOIN tipos_documento t ON t.id = d.tipo_id
      WHERE d.creado_en > NOW() - make_interval(days => $1) GROUP BY t.nombre ORDER BY total DESC`,
    [dias],
  );
  const rutas = await query(
    `SELECT metodo, ruta, SUM(total)::int AS total FROM trafico_api
      WHERE hora > NOW() - make_interval(days => $1) GROUP BY metodo, ruta ORDER BY total DESC LIMIT 10`,
    [dias],
  );
  res.json({ dias, serie: serie.rows, totales, documentos_por_tipo: porTipo.rows, rutas_mas_usadas: rutas.rows });
});

// CUA-06 Consultar logs (eventos_sistema + accesos, filtrado por fecha, paginado)
const filtroLogs = z.object({
  desde: z.string().date().optional(),
  hasta: z.string().date().optional(),
  tipo: z.string().trim().max(20).optional(),
  pagina: z.coerce.number().int().min(1).default(1),
  por_pagina: z.coerce.number().int().min(1).max(200).default(50),
});

async function consultarLogs(q: z.infer<typeof filtroLogs>, limite?: number) {
  const desde = q.desde ?? new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10);
  const hasta = q.hasta ?? new Date().toISOString().slice(0, 10);
  const params: unknown[] = [desde, hasta];
  let filtroTipo = '';
  if (q.tipo) {
    params.push(q.tipo);
    filtroTipo = `WHERE tipo = $${params.length}`;
  }
  const base = `
    SELECT * FROM (
      SELECT 'evento' AS origen, e.fecha, e.tipo_evento AS tipo, e.operacion AS accion, e.descripcion, e.ip, u.correo AS usuario
        FROM eventos_sistema e LEFT JOIN usuarios u ON u.id = e.usuario_id
       WHERE e.fecha >= $1::date AND e.fecha < $2::date + 1
      UNION ALL
      SELECT 'acceso', a.fecha, CASE WHEN a.exito THEN 'acceso' ELSE 'acceso_fallido' END, a.tipo,
             CASE a.tipo
               WHEN 'login'    THEN 'Contraseña verificada; código 2FA enviado'
               WHEN '2fa'      THEN CASE WHEN a.exito THEN 'Código 2FA correcto: sesión iniciada' ELSE 'Código 2FA incorrecto' END
               WHEN 'logout'   THEN 'Cierre de sesión'
               WHEN 'intento'  THEN 'Intento de inicio de sesión fallido'
               WHEN 'registro' THEN 'Inicio de registro de cuenta'
               ELSE a.tipo END
             || CASE WHEN a.user_agent ~* 'edg/' THEN ' · Edge' WHEN a.user_agent ~* 'firefox' THEN ' · Firefox'
                     WHEN a.user_agent ~* 'chrome' THEN ' · Chrome' WHEN a.user_agent ~* 'safari' THEN ' · Safari' ELSE '' END,
             a.ip, COALESCE(u.correo, a.correo_intentado)
        FROM accesos a LEFT JOIN usuarios u ON u.id = a.usuario_id
       WHERE a.fecha >= $1::date AND a.fecha < $2::date + 1
    ) l ${filtroTipo}`;
  const total = await uno<{ n: number }>(`SELECT COUNT(*)::int AS n FROM (${base}) c`, params);
  const lim = limite ?? q.por_pagina;
  const off = limite ? 0 : (q.pagina - 1) * q.por_pagina;
  const filas = await query(`${base} ORDER BY fecha DESC LIMIT ${Number(lim)} OFFSET ${Number(off)}`, params);
  return { total: total?.n ?? 0, desde, hasta, registros: filas.rows };
}

rutasAdmin.get('/logs', async (req, res) => {
  const q = filtroLogs.parse(req.query);
  res.json({ pagina: q.pagina, por_pagina: q.por_pagina, ...(await consultarLogs(q)) });
});

// "Generar reporte" (Fig. 29): CSV de logs + resumen
rutasAdmin.get('/reporte', async (req, res) => {
  const q = filtroLogs.parse(req.query);
  const { registros, desde, hasta } = await consultarLogs(q, 10_000);
  const csv = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const lineas = [
    ['fecha', 'origen', 'tipo', 'accion', 'usuario', 'ip', 'descripcion'].join(','),
    ...registros.map((r: any) =>
      [new Date(r.fecha).toISOString(), r.origen, r.tipo, r.accion, r.usuario, r.ip, r.descripcion].map(csv).join(','),
    ),
  ];
  await registrarEvento({ usuarioId: req.usuario!.id, tipo: 'auditoria', operacion: 'REPORTE', descripcion: `Reporte de logs ${desde}..${hasta}`, req });
  res.setHeader('Content-Type', 'text/csv; charset=utf-8');
  res.setHeader('Content-Disposition', `attachment; filename="reporte_${desde}_${hasta}.csv"`);
  res.send('﻿' + lineas.join('\n'));
});

rutasAdmin.get('/respaldos', async (_req, res) => {
  const r = await query('SELECT id, tipo, ubicacion, tamano, estado, creado_en FROM respaldos ORDER BY creado_en DESC LIMIT 50');
  res.json(r.rows);
});
