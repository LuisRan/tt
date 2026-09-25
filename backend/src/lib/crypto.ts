/**
 * Utilidades criptográficas (sección 2.7.2.7 del documento: cifrado AES).
 *
 * - cifrar/descifrar: AES-256-GCM (confidencialidad + integridad) para datos
 *   personales en BD (CURP, dirección, valores extraídos) y archivos en disco.
 * - hmac: huella determinista para búsquedas sin descifrar y para guardar los
 *   códigos 2FA (nunca se guarda el código en claro).
 * - generarCodigo: código numérico de 6 dígitos con CSPRNG (crypto.randomInt).
 * - URLs firmadas temporales para ver archivos (Fig. 19 del documento).
 */
import crypto from 'node:crypto';
import { config } from '../config.js';

const ALGORITMO = 'aes-256-gcm';
const PREFIJO = 'enc:v1:';

function claveDerivada(proposito: string): Buffer {
  return crypto.createHmac('sha256', config.claveDatos).update(`tt:${proposito}`).digest();
}
const CLAVE_CIFRADO = claveDerivada('cifrado');
const CLAVE_HMAC = claveDerivada('hmac');
const CLAVE_URL = claveDerivada('url-firmada');

/** Cifra un buffer: [iv 12][tag 16][datos]. */
export function cifrarBuffer(datos: Buffer): Buffer {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv(ALGORITMO, CLAVE_CIFRADO, iv);
  const cifrado = Buffer.concat([cipher.update(datos), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), cifrado]);
}

export function descifrarBuffer(paquete: Buffer): Buffer {
  const iv = paquete.subarray(0, 12);
  const tag = paquete.subarray(12, 28);
  const datos = paquete.subarray(28);
  const decipher = crypto.createDecipheriv(ALGORITMO, CLAVE_CIFRADO, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(datos), decipher.final()]);
}

/** Cifra un texto. null/undefined/'' se conservan como null. */
export function cifrar(texto: string | null | undefined): string | null {
  if (texto === null || texto === undefined || texto === '') return null;
  return PREFIJO + cifrarBuffer(Buffer.from(String(texto), 'utf8')).toString('base64');
}

export function descifrar(valor: string | null | undefined): string | null {
  if (valor === null || valor === undefined) return null;
  if (!valor.startsWith(PREFIJO)) return valor; // tolerante a datos antiguos en claro
  try {
    return descifrarBuffer(Buffer.from(valor.slice(PREFIJO.length), 'base64')).toString('utf8');
  } catch {
    return null;
  }
}

/** Cifra todos los valores string de un objeto plano (para Mongo). */
export function cifrarObjeto(obj: Record<string, unknown>): Record<string, unknown> {
  const salida: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj ?? {})) {
    salida[k] = typeof v === 'string' ? cifrar(v) : v;
  }
  return salida;
}

export function descifrarObjeto(obj: Record<string, unknown> | null | undefined): Record<string, unknown> {
  const salida: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj ?? {})) {
    salida[k] = typeof v === 'string' ? descifrar(v) : v;
  }
  return salida;
}

export function hmac(valor: string): string {
  return crypto.createHmac('sha256', CLAVE_HMAC).update(valor).digest('hex');
}

export function sha256(datos: Buffer): string {
  return crypto.createHash('sha256').update(datos).digest('hex');
}

/** Comparación en tiempo constante de dos strings hex/utf8. */
export function igualSeguro(a: string, b: string): boolean {
  const ba = Buffer.from(a);
  const bb = Buffer.from(b);
  return ba.length === bb.length && crypto.timingSafeEqual(ba, bb);
}

/** Código numérico de N dígitos generado con CSPRNG (sin sesgo de módulo). */
export function generarCodigo(digitos = 6): string {
  return crypto.randomInt(0, 10 ** digitos).toString().padStart(digitos, '0');
}

export function idAleatorio(bytes = 12): string {
  return crypto.randomBytes(bytes).toString('hex');
}

// ── URLs firmadas ────────────────────────────────────────────────────────────

export function firmarToken(payload: Record<string, unknown>, segundos: number): string {
  const cuerpo = Buffer.from(JSON.stringify({ ...payload, exp: Date.now() + segundos * 1000 })).toString('base64url');
  const firma = crypto.createHmac('sha256', CLAVE_URL).update(cuerpo).digest('base64url');
  return `${cuerpo}.${firma}`;
}

export function verificarToken<T extends Record<string, unknown>>(token: string): T | null {
  const [cuerpo, firma] = token.split('.');
  if (!cuerpo || !firma) return null;
  const esperada = crypto.createHmac('sha256', CLAVE_URL).update(cuerpo).digest('base64url');
  if (!igualSeguro(firma, esperada)) return null;
  try {
    const datos = JSON.parse(Buffer.from(cuerpo, 'base64url').toString('utf8')) as T & { exp: number };
    if (typeof datos.exp !== 'number' || datos.exp < Date.now()) return null;
    return datos;
  } catch {
    return null;
  }
}
