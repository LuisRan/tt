/**
 * Extensión de navegador: vinculación, empaquetado personalizado y datos.
 *
 * Flujo de vinculación (sin contraseñas dentro de la extensión):
 *  1. El usuario, con sesión y contraseña reconfirmada, genera un CÓDIGO de un
 *     solo uso (15 min). Se guarda solo su HMAC.
 *  2. La extensión canjea el código por un TOKEN de dispositivo (Bearer). Se
 *     guarda solo su HMAC junto con el token_version de la cuenta, de modo que
 *     cambiar la contraseña o "cerrar todas las sesiones" revoca la extensión.
 *  3. Con el token la extensión solo puede LEER los documentos validados del
 *     usuario (GET /api/extension/documentos) y desvincularse.
 *
 * El paquete .zip que se descarga desde la plataforma lleva un config.js con la
 * URL de la plataforma y un código recién generado: al cargar la extensión se
 * vincula sola. Si el código ya expiró, la extensión pide uno nuevo.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type { NextFunction, Request, Response } from 'express';
import { strToU8, zipSync, type Zippable } from 'fflate';
import { config } from '../config.js';
import { query, transaccion, uno } from '../db/pg.js';
import { resultados } from '../db/mongo.js';
import { descifrar, descifrarObjeto, hmac } from '../lib/crypto.js';
import { ErrorApp, noAutorizado, noEncontrado } from '../lib/errores.js';
import { etiqueta, ORDEN_CAMPOS } from '../lib/etiquetas.js';
import { ipDe, registrarEvento } from './auditoria.js';
import { NOMBRE_TIPO, textoOcr, type ClaveTipo } from './documentos.js';

const ALFABETO = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // sin 0/O/1/I para dictarlo sin errores
const CARPETA_ZIP = 'ocr-platform-extension';

declare module 'express-serve-static-core' {
  interface Request {
    dispositivoId?: number;
  }
}

// ── Manifest e identificador de la extensión ────────────────────────────────

function leerManifest(): Record<string, any> {
  const ruta = path.join(config.EXTENSION_DIR, 'manifest.json');
  if (!fs.existsSync(ruta)) throw new ErrorApp(503, 'EXTENSION_NO_DISPONIBLE', 'El código de la extensión no está disponible en el servidor.');
  return JSON.parse(fs.readFileSync(ruta, 'utf8'));
}

/** ID de Chrome derivado de la clave pública del manifest (fijo para todas las instalaciones). */
export function idExtension(): string | null {
  try {
    const key = leerManifest().key as string | undefined;
    if (!key) return null;
    const hash = crypto.createHash('sha256').update(Buffer.from(key, 'base64')).digest('hex').slice(0, 32);
    return [...hash].map((c) => String.fromCharCode(97 + parseInt(c, 16))).join('');
  } catch {
    return null;
  }
}

/** Patrón de coincidencia de Chrome para la URL pública (sin puerto: cubre cualquiera). */
function patronOrigen(url: string): string {
  const u = new URL(url);
  return `${u.protocol}//${u.hostname}/*`;
}

export function infoExtension() {
  let version: string | null = null;
  try {
    version = leerManifest().version ?? null;
  } catch {
    /* sin extensión */
  }
  return { extension_id: idExtension(), version, url: new URL(config.PUBLIC_URL).origin };
}

// ── Códigos de vinculación ──────────────────────────────────────────────────

const normalizarCodigo = (c: string) => c.toUpperCase().replace(/[^A-Z0-9]/g, '');

export async function generarCodigoVinculacion(usuarioId: number) {
  const bytes = crypto.randomBytes(8);
  const crudo = [...bytes].map((b) => ALFABETO[b % ALFABETO.length]).join('');
  const codigo = `${crudo.slice(0, 4)}-${crudo.slice(4)}`;
  const expira = new Date(Date.now() + config.EXTENSION_CODIGO_MINUTOS * 60_000);
  await query('INSERT INTO extension_codigos (usuario_id, codigo_hash, expira_en) VALUES ($1, $2, $3)', [
    usuarioId,
    hmac(`ext-codigo:${crudo}`),
    expira,
  ]);
  return { codigo, expira_en: expira.toISOString(), url: new URL(config.PUBLIC_URL).origin };
}

export async function canjearCodigo(codigo: string, nombre: string | undefined, req: Request) {
  const limpio = normalizarCodigo(codigo);
  if (limpio.length !== 8) throw new ErrorApp(400, 'CODIGO_INVALIDO', 'El código debe tener 8 caracteres (XXXX-XXXX).');
  const token = `ext_${crypto.randomBytes(32).toString('base64url')}`;

  const r = await transaccion(async (c) => {
    const fila = await c.query<{ id: number; usuario_id: number }>(
      `UPDATE extension_codigos SET usado = TRUE
        WHERE codigo_hash = $1 AND NOT usado AND expira_en > NOW()
        RETURNING id, usuario_id`,
      [hmac(`ext-codigo:${limpio}`)],
    );
    if (!fila.rowCount) return null;
    const u = await c.query<{ id: number; correo: string; activo: boolean; registro_completo: boolean; token_version: number }>(
      'SELECT id, correo, activo, registro_completo, token_version FROM usuarios WHERE id = $1',
      [fila.rows[0].usuario_id],
    );
    const usuario = u.rows[0];
    if (!usuario?.activo || !usuario.registro_completo) return null;
    const disp = await c.query<{ id: number }>(
      `INSERT INTO extension_dispositivos (usuario_id, nombre, token_hash, token_version, ultimo_uso, ultima_ip)
       VALUES ($1, $2, $3, $4, NOW(), $5) RETURNING id`,
      [usuario.id, (nombre ?? 'Navegador').slice(0, 80), hmac(`ext-token:${token}`), usuario.token_version, ipDe(req)],
    );
    return { usuario, dispositivoId: disp.rows[0].id };
  });
  if (!r) throw new ErrorApp(400, 'CODIGO_INVALIDO', 'El código no es válido o ya expiró. Genera uno nuevo desde la plataforma.');

  await registrarEvento({
    usuarioId: r.usuario.id,
    tipo: 'seguridad',
    tabla: 'extension_dispositivos',
    operacion: 'VINCULAR',
    descripcion: `Extensión de navegador vinculada (${nombre ?? 'Navegador'})`,
    req,
  });
  return { token, correo: r.usuario.correo, dispositivo_id: r.dispositivoId };
}

// ── Autenticación de la extensión (Bearer) ──────────────────────────────────

export async function requiereExtension(req: Request, _res: Response, next: NextFunction) {
  const cab = req.headers.authorization ?? '';
  const token = cab.startsWith('Bearer ') ? cab.slice(7).trim() : '';
  if (!token.startsWith('ext_')) return next(noAutorizado('Extensión no vinculada'));
  const d = await uno<{ id: number; usuario_id: number; correo: string; rol: 'usuario' | 'administrador'; activo: boolean; tv_cuenta: number; tv: number }>(
    `SELECT d.id, d.usuario_id, u.correo, u.rol, u.activo, u.token_version AS tv_cuenta, d.token_version AS tv
       FROM extension_dispositivos d JOIN usuarios u ON u.id = d.usuario_id
      WHERE d.token_hash = $1 AND d.revocado_en IS NULL`,
    [hmac(`ext-token:${token}`)],
  );
  if (!d || !d.activo || d.tv !== d.tv_cuenta) return next(noAutorizado('La extensión fue desvinculada. Vincúlala de nuevo desde la plataforma.'));
  await query('UPDATE extension_dispositivos SET ultimo_uso = NOW(), ultima_ip = $2 WHERE id = $1', [d.id, ipDe(req)]);
  req.usuario = { id: d.usuario_id, correo: d.correo, rol: d.rol, registroCompleto: true, alcance: 'app' };
  req.dispositivoId = d.id;
  next();
}

// ── Dispositivos ────────────────────────────────────────────────────────────

export async function listarDispositivos(usuarioId: number) {
  const r = await query(
    `SELECT id, nombre, creado_en, ultimo_uso FROM extension_dispositivos
      WHERE usuario_id = $1 AND revocado_en IS NULL ORDER BY creado_en DESC`,
    [usuarioId],
  );
  return r.rows;
}

export async function revocarDispositivo(usuarioId: number, id: number, req: Request) {
  const r = await query(
    'UPDATE extension_dispositivos SET revocado_en = NOW() WHERE id = $1 AND usuario_id = $2 AND revocado_en IS NULL',
    [id, usuarioId],
  );
  if (!r.rowCount) throw noEncontrado('Dispositivo no encontrado');
  await registrarEvento({
    usuarioId,
    tipo: 'seguridad',
    tabla: 'extension_dispositivos',
    operacion: 'DESVINCULAR',
    descripcion: `Extensión de navegador ${id} desvinculada`,
    req,
  });
}

// ── Datos para la extensión ─────────────────────────────────────────────────

const CAMPOS_INTERNOS = new Set(['nombre_completo']);

function textoPlano(doc: { identificador: string; tipo_nombre: string; titular: string; campos: { etiqueta: string; valor: string }[]; validado_en: string | null }) {
  const lineas = [
    `${doc.identificador} - ${doc.tipo_nombre}`,
    `Titular: ${doc.titular}`,
    doc.validado_en ? `Validado: ${doc.validado_en.slice(0, 10)}` : '',
    '',
    ...doc.campos.map((c) => `${c.etiqueta}: ${c.valor}`),
  ];
  return lineas.filter((l, i) => l !== '' || i === 3).join('\n') + '\n';
}

/** Solo documentos VALIDADOS (confirmados por el usuario), con folio e iniciales. */
export async function documentosParaExtension(usuarioId: number) {
  const r = await query<{
    id: number;
    folio: number;
    titular_iniciales: string | null;
    tipo: ClaveTipo;
    version: number;
    vigente: boolean;
    fecha_vigencia: string | null;
    actualizado_en: Date;
  }>(
    `SELECT d.id, d.folio, d.titular_iniciales, t.clave AS tipo, d.version, d.vigente,
            to_char(d.fecha_vigencia, 'YYYY-MM-DD') AS fecha_vigencia, d.actualizado_en
       FROM documentos d JOIN tipos_documento t ON t.id = d.tipo_id
      WHERE d.usuario_id = $1 AND d.estado = 'validado' AND d.folio IS NOT NULL
      ORDER BY d.folio`,
    [usuarioId],
  );
  const documentos = [];
  for (const d of r.rows) {
    const m = await resultados().findOne({ documento_id: d.id });
    const datos = descifrarObjeto(m?.datos_confirmados) as Record<string, string | null>;
    const campos = Object.entries(datos)
      .filter(([k, v]) => v !== null && v !== '' && !CAMPOS_INTERNOS.has(k))
      .sort(([a], [b]) => {
        const ia = ORDEN_CAMPOS.indexOf(a);
        const ib = ORDEN_CAMPOS.indexOf(b);
        return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
      })
      .map(([campo, valor]) => ({ campo, etiqueta: etiqueta(campo), valor: String(valor) }));
    const titular = datos.nombre_completo ?? [datos.nombres, datos.primer_apellido, datos.segundo_apellido].filter(Boolean).join(' ');
    const identificador = `${String(d.folio).padStart(4, '0')}_${d.titular_iniciales ?? 'XXX'}`;
    const validado_en = (m?.fecha_edicion ?? m?.fecha_validacion ?? d.actualizado_en)?.toISOString?.() ?? null;
    const base = { identificador, tipo_nombre: NOMBRE_TIPO[d.tipo], titular: titular || 'Sin nombre', campos, validado_en };
    documentos.push({
      id: d.id,
      folio: d.folio,
      identificador,
      tipo: d.tipo,
      tipo_nombre: base.tipo_nombre,
      titular: base.titular,
      version: d.version,
      vigente: d.vigente,
      fecha_vigencia: d.fecha_vigencia,
      validado_en,
      campos,
      texto: textoPlano(base),
      texto_escaneado: descifrar(m?.texto_crudo ?? null) ?? (await textoOcr(usuarioId, d.id).catch(() => null)),
    });
  }
  const u = await uno<{ correo: string }>('SELECT correo FROM usuarios WHERE id = $1', [usuarioId]);
  return { correo: u?.correo ?? null, generado_en: new Date().toISOString(), documentos };
}

// ── Paquete .zip personalizado ──────────────────────────────────────────────

function leerCarpeta(dir: string, base = ''): Zippable {
  const salida: Zippable = {};
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name.startsWith('.') || e.name === 'README.md' || e.name === 'config.js') continue;
    const rel = base ? `${base}/${e.name}` : e.name;
    const abs = path.join(dir, e.name);
    if (e.isDirectory()) Object.assign(salida, leerCarpeta(abs, rel));
    else salida[rel] = new Uint8Array(fs.readFileSync(abs));
  }
  return salida;
}

export async function paqueteExtension(usuarioId: number, req: Request): Promise<Buffer> {
  const manifest = leerManifest();
  const url = new URL(config.PUBLIC_URL).origin;
  const patron = patronOrigen(url);
  manifest.host_permissions = [patron];
  manifest.externally_connectable = { matches: [patron] };

  const { codigo, expira_en } = await generarCodigoVinculacion(usuarioId);
  const configJs =
    `// Generado por la plataforma para ${req.usuario?.correo ?? 'el usuario'} el ${new Date().toISOString()}.\n` +
    `// El código es de un solo uso y vence a los ${config.EXTENSION_CODIGO_MINUTOS} minutos.\n` +
    `globalThis.CONFIG_PLATAFORMA = ${JSON.stringify({ url, codigo, expira_en }, null, 2)};\n`;

  const archivos: Zippable = {};
  for (const [rel, datos] of Object.entries(leerCarpeta(config.EXTENSION_DIR))) archivos[`${CARPETA_ZIP}/${rel}`] = datos;
  archivos[`${CARPETA_ZIP}/manifest.json`] = strToU8(JSON.stringify(manifest, null, 2));
  archivos[`${CARPETA_ZIP}/config.js`] = strToU8(configJs);

  await registrarEvento({
    usuarioId,
    tipo: 'seguridad',
    tabla: 'extension_codigos',
    operacion: 'PAQUETE',
    descripcion: 'Descarga del paquete de la extensión de navegador',
    req,
  });
  return Buffer.from(zipSync(archivos, { level: 6 }));
}
