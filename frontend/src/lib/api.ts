/**
 * Cliente HTTP del frontend. Todas las llamadas van a /api (mismo origen vía
 * Nginx), con cookies httpOnly. Maneja de forma centralizada:
 *  - 401: sesión expirada -> redirige al login
 *  - 428: operación sensible -> pide la contraseña y reintenta (CU-02)
 */
import { pedirPassword } from './ui';

export class ErrorApi extends Error {
  constructor(
    public status: number,
    public codigo: string,
    mensaje: string,
    public detalles?: any,
  ) {
    super(mensaje);
  }
}

interface Opciones {
  metodo?: string;
  cuerpo?: unknown;
  form?: FormData;
  redirigir401?: boolean;
}

/** Detiene el script de la página mientras el navegador redirige (sin error en consola). */
const detener = () => new Promise<never>(() => undefined);

export async function api<T = any>(ruta: string, op: Opciones = {}): Promise<T> {
  const init: RequestInit = {
    method: op.metodo ?? (op.cuerpo || op.form ? 'POST' : 'GET'),
    credentials: 'same-origin',
    headers: op.form ? {} : { 'Content-Type': 'application/json' },
    body: op.form ?? (op.cuerpo !== undefined ? JSON.stringify(op.cuerpo) : undefined),
  };
  let r: Response;
  try {
    r = await fetch(`/api${ruta}`, init);
  } catch {
    throw new ErrorApi(0, 'SIN_CONEXION', 'No hay conexión con el servidor. Verifica que el sistema esté en ejecución.');
  }

  if (r.status === 428) {
    const ok = await pedirPassword();
    if (ok) return api<T>(ruta, op);
    throw new ErrorApi(428, 'CANCELADO', 'Operación cancelada: no se confirmó la contraseña.');
  }

  const tipo = r.headers.get('content-type') ?? '';
  const datos = tipo.includes('application/json') ? await r.json().catch(() => ({})) : await r.text();
  if (!r.ok) {
    if (r.status === 401 && op.redirigir401 !== false && !location.pathname.startsWith('/login')) {
      location.href = `/login?expirada=1&volver=${encodeURIComponent(location.pathname + location.search)}`;
      return detener();
    }
    const d = typeof datos === 'object' ? datos : {};
    throw new ErrorApi(r.status, d.codigo ?? 'ERROR', d.error ?? `Error ${r.status}`, d.detalles);
  }
  return datos as T;
}

export interface Yo {
  id: number;
  correo: string;
  rol: 'usuario' | 'administrador';
  nombre: string | null;
  apellido_paterno: string | null;
  verificado: boolean | null;
  dosfa_activo: boolean;
  tutorial_visto: boolean;
}

/** Guarda de página: exige sesión (y rol opcional). */
export async function requerirSesion(rol?: 'administrador' | 'usuario'): Promise<Yo> {
  const yo = await api<Yo>('/auth/yo');
  if (rol === 'administrador' && yo.rol !== 'administrador') {
    location.href = '/app';
    return detener();
  }
  if (rol === 'usuario' && yo.rol === 'administrador') {
    location.href = '/admin';
    return detener();
  }
  // Tutorial obligatorio la primera vez que un usuario entra a la plataforma
  if (yo.rol === 'usuario' && !yo.tutorial_visto && !location.pathname.startsWith('/app/tutorial')) {
    location.href = '/app/tutorial';
    return detener();
  }
  const nombre = document.querySelector('[data-usuario-nombre]');
  const correo = document.querySelector('[data-usuario-correo]');
  if (nombre) nombre.textContent = yo.nombre ? `${yo.nombre} ${yo.apellido_paterno ?? ''}` : yo.rol === 'administrador' ? 'Administrador' : 'Usuario';
  if (correo) correo.textContent = yo.correo;
  document.body.classList.remove('cargando-sesion');
  return yo;
}

export async function cerrarSesion(): Promise<void> {
  await api('/auth/logout', { cuerpo: {}, redirigir401: false }).catch(() => undefined);
  location.href = '/login';
}
