/** Trazabilidad: tabla eventos_sistema (auditoría) y accesos (logins). */
import type { Request } from 'express';
import { query } from '../db/pg.js';
import { logger } from '../lib/logger.js';

export type TipoEvento = 'info' | 'advertencia' | 'error' | 'seguridad' | 'auditoria';

export function ipDe(req?: Request): string | null {
  if (!req) return null;
  return (req.ip || req.socket?.remoteAddress || '').slice(0, 64) || null;
}

export async function registrarEvento(e: {
  usuarioId?: number | null;
  tipo: TipoEvento;
  tabla?: string;
  operacion?: string;
  descripcion: string;
  req?: Request;
}): Promise<void> {
  try {
    await query(
      `INSERT INTO eventos_sistema (usuario_id, tipo_evento, tabla_afectada, operacion, descripcion, ip)
       VALUES ($1, $2, $3, $4, $5, $6)`,
      [e.usuarioId ?? null, e.tipo, e.tabla ?? null, e.operacion ?? null, e.descripcion.slice(0, 2000), ipDe(e.req)],
    );
  } catch (err) {
    logger.error({ err }, 'no se pudo registrar evento de auditoría');
  }
}

export async function registrarAcceso(a: {
  usuarioId?: number | null;
  correo?: string | null;
  tipo: 'login' | '2fa' | 'logout' | 'intento' | 'registro';
  exito: boolean;
  req?: Request;
}): Promise<void> {
  try {
    await query(
      `INSERT INTO accesos (usuario_id, correo_intentado, ip, user_agent, tipo, exito)
       VALUES ($1, $2, $3, $4, $5, $6)`,
      [
        a.usuarioId ?? null,
        a.correo?.slice(0, 254) ?? null,
        ipDe(a.req),
        a.req?.get('user-agent')?.slice(0, 500) ?? null,
        a.tipo,
        a.exito,
      ],
    );
  } catch (err) {
    logger.error({ err }, 'no se pudo registrar acceso');
  }
}
