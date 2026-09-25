/**
 * Sesiones con JWT en cookies httpOnly (no accesibles desde JavaScript → mitiga XSS).
 *  - sesion : sesión completa tras 2FA (alcance "app").
 *  - registro: sesión limitada durante el alta (alcance "registro"): solo permite
 *              aceptar términos, subir la INE y validarla.
 *  - mfa    : desafío pendiente de 2FA tras validar contraseña (10 min).
 *  - reauth : confirmación reciente de contraseña para operaciones sensibles.
 */
import type { CookieOptions, Response } from 'express';
import jwt from 'jsonwebtoken';
import { config } from '../config.js';

export type Alcance = 'app' | 'registro' | 'mfa' | 'reauth';

export interface PayloadSesion {
  sub: number;
  rol: 'usuario' | 'administrador';
  alc: Alcance;
  tv: number; // token_version
}

const NOMBRES: Record<Alcance, string> = { app: 'sesion', registro: 'registro', mfa: 'mfa', reauth: 'reauth' };
const DURACION_MIN: Record<Alcance, () => number> = {
  app: () => config.JWT_EXPIRA_MIN,
  registro: () => 60,
  mfa: () => config.CODIGO_2FA_MINUTOS,
  reauth: () => config.REAUTH_MINUTOS,
};

function opciones(minutos: number): CookieOptions {
  return {
    httpOnly: true,
    secure: config.COOKIE_SECURE,
    sameSite: 'strict',
    path: '/api',
    maxAge: minutos * 60_000,
  };
}

export function emitir(res: Response, payload: Omit<PayloadSesion, 'alc'>, alcance: Alcance): void {
  const minutos = DURACION_MIN[alcance]();
  const token = jwt.sign({ ...payload, alc: alcance }, config.JWT_SECRET, {
    expiresIn: `${minutos}m`,
    issuer: 'tt-backend',
    audience: alcance,
  });
  res.cookie(NOMBRES[alcance], token, opciones(minutos));
}

export function leer(cookies: Record<string, string> | undefined, alcance: Alcance): PayloadSesion | null {
  const token = cookies?.[NOMBRES[alcance]];
  if (!token) return null;
  try {
    const p = jwt.verify(token, config.JWT_SECRET, { issuer: 'tt-backend', audience: alcance }) as unknown as PayloadSesion;
    return p.alc === alcance ? p : null;
  } catch {
    return null;
  }
}

export function limpiar(res: Response, ...alcances: Alcance[]): void {
  for (const a of alcances) res.clearCookie(NOMBRES[a], { ...opciones(0), maxAge: undefined });
}
