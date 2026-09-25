import type { NextFunction, Request, Response } from 'express';
import { uno } from '../db/pg.js';
import { ErrorApp, noAutorizado, prohibido } from '../lib/errores.js';
import { leer, type PayloadSesion } from '../lib/sesion.js';

export interface UsuarioSesion {
  id: number;
  correo: string;
  rol: 'usuario' | 'administrador';
  registroCompleto: boolean;
  alcance: 'app' | 'registro';
}

declare module 'express-serve-static-core' {
  interface Request {
    usuario?: UsuarioSesion;
  }
}

async function cargarUsuario(p: PayloadSesion, alcance: 'app' | 'registro'): Promise<UsuarioSesion | null> {
  // Se consulta la BD en cada request: una cuenta deshabilitada o con sesiones
  // revocadas (token_version) pierde el acceso de inmediato (R10).
  const u = await uno<{ id: number; correo: string; rol: 'usuario' | 'administrador'; activo: boolean; token_version: number; registro_completo: boolean }>(
    'SELECT id, correo, rol, activo, token_version, registro_completo FROM usuarios WHERE id = $1',
    [p.sub],
  );
  if (!u || !u.activo || u.token_version !== p.tv) return null;
  return { id: u.id, correo: u.correo, rol: u.rol, registroCompleto: u.registro_completo, alcance };
}

/** Exige sesión completa (tras 2FA). RNF-02. */
export async function requiereSesion(req: Request, _res: Response, next: NextFunction) {
  const p = leer(req.cookies, 'app');
  const u = p ? await cargarUsuario(p, 'app') : null;
  if (!u) return next(noAutorizado());
  req.usuario = u;
  next();
}

/** Acepta sesión completa o la sesión limitada del registro (subir/validar INE). */
export async function requiereSesionORegistro(req: Request, _res: Response, next: NextFunction) {
  const app = leer(req.cookies, 'app');
  if (app) {
    const u = await cargarUsuario(app, 'app');
    if (u) {
      req.usuario = u;
      return next();
    }
  }
  const reg = leer(req.cookies, 'registro');
  const u = reg ? await cargarUsuario(reg, 'registro') : null;
  if (!u) return next(noAutorizado());
  req.usuario = u;
  next();
}

export function requiereRol(rol: 'administrador') {
  return (req: Request, _res: Response, next: NextFunction) => {
    if (!req.usuario) return next(noAutorizado());
    if (req.usuario.rol !== rol) return next(prohibido('Solo administradores'));
    next();
  };
}

/**
 * Operaciones sensibles (cargar/modificar documentos oficiales): exige haber
 * confirmado la contraseña en los últimos minutos (CU-02, último paso).
 * Durante el registro no aplica: el usuario acaba de crear su contraseña.
 */
export function requiereReautenticacion(req: Request, _res: Response, next: NextFunction) {
  if (req.usuario?.alcance === 'registro') return next();
  const p = leer(req.cookies, 'reauth');
  if (!p || p.sub !== req.usuario?.id) {
    return next(new ErrorApp(428, 'REQUIERE_CONTRASENA', 'Confirma tu contraseña para continuar'));
  }
  next();
}
