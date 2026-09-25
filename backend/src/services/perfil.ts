/**
 * Perfil del usuario: lectura (descifrada) y actualización manual
 * (por el propio usuario o por un administrador).
 */
import { z } from 'zod';
import { query, uno } from '../db/pg.js';
import { cifrar, descifrar, hmac } from '../lib/crypto.js';

const RE_CURP = /^[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$/;

const texto = (max: number) =>
  z
    .union([z.string().max(max), z.null()])
    .optional()
    .transform((v) => (typeof v === 'string' ? v.trim().replace(/\s+/g, ' ') || null : v));

/** Fecha 'dd/mm/aaaa' o 'aaaa-mm-dd' -> 'aaaa-mm-dd'. */
const fecha = z
  .union([z.string(), z.null()])
  .optional()
  .transform((v, ctx) => {
    if (v === undefined || v === null || v.trim() === '') return v === undefined ? undefined : null;
    const t = v.trim();
    let iso: string | null = null;
    let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t);
    if (m) iso = t;
    m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(t);
    if (m) iso = `${m[3]}-${m[2]}-${m[1]}`;
    const d = iso ? new Date(`${iso}T12:00:00Z`) : null;
    if (!d || Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== iso) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'Fecha inválida (dd/mm/aaaa)' });
      return z.NEVER;
    }
    return iso;
  });

export const esquemaPerfil = z.object({
  nombre: texto(120).transform((v) => (v ? v.toUpperCase() : v)),
  apellido_paterno: texto(80).transform((v) => (v ? v.toUpperCase() : v)),
  apellido_materno: texto(80).transform((v) => (v ? v.toUpperCase() : v)),
  curp: texto(18)
    .transform((v) => (v ? v.toUpperCase().replace(/\s/g, '') : v))
    .refine((v) => !v || RE_CURP.test(v), 'CURP con formato inválido (18 caracteres)'),
  fecha_nacimiento: fecha,
  sexo: z
    .union([z.enum(['H', 'M', 'h', 'm']), z.literal(''), z.null()])
    .optional()
    .transform((v) => (v ? v.toUpperCase() : v === undefined ? undefined : null)),
  direccion: texto(300),
});
export type DatosPerfil = z.infer<typeof esquemaPerfil>;

export async function leerPerfil(usuarioId: number) {
  const p = await uno(
    `SELECT u.correo, u.rol, u.creado_en, u.ultimo_acceso, u.alertas_vigencia, u.dosfa_activo, u.tutorial_visto,
            p.nombre, p.apellido_paterno, p.apellido_materno, p.curp, p.fecha_nacimiento, p.sexo,
            p.direccion, p.verificado, p.editado_manual, p.actualizado_en
       FROM usuarios u LEFT JOIN perfil_usuario p ON p.usuario_id = u.id WHERE u.id = $1`,
    [usuarioId],
  );
  return p ? { ...p, curp: descifrar(p.curp), direccion: descifrar(p.direccion) } : null;
}

/** Aplica solo los campos presentes (undefined = no cambia, null = borrar). */
export async function actualizarPerfil(usuarioId: number, datos: DatosPerfil): Promise<void> {
  const columnas: Record<string, unknown> = {};
  if (datos.nombre !== undefined) columnas.nombre = datos.nombre;
  if (datos.apellido_paterno !== undefined) columnas.apellido_paterno = datos.apellido_paterno;
  if (datos.apellido_materno !== undefined) columnas.apellido_materno = datos.apellido_materno;
  if (datos.curp !== undefined) {
    columnas.curp = cifrar(datos.curp);
    columnas.curp_hash = datos.curp ? hmac(`curp:${datos.curp}`) : null;
  }
  if (datos.fecha_nacimiento !== undefined) columnas.fecha_nacimiento = datos.fecha_nacimiento;
  if (datos.sexo !== undefined) columnas.sexo = datos.sexo;
  if (datos.direccion !== undefined) columnas.direccion = cifrar(datos.direccion);
  if (!Object.keys(columnas).length) return;

  await query('INSERT INTO perfil_usuario (usuario_id) VALUES ($1) ON CONFLICT (usuario_id) DO NOTHING', [usuarioId]);
  const sets = Object.keys(columnas).map((c, i) => `${c} = $${i + 2}`);
  await query(
    `UPDATE perfil_usuario SET ${sets.join(', ')}, editado_manual = TRUE, actualizado_en = NOW() WHERE usuario_id = $1`,
    [usuarioId, ...Object.values(columnas)],
  );
}
