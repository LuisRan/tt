/** Error de negocio con código HTTP y un código estable que el frontend puede interpretar. */
export class ErrorApp extends Error {
  constructor(
    public readonly status: number,
    public readonly codigo: string,
    mensaje: string,
    public readonly detalles?: unknown,
  ) {
    super(mensaje);
    this.name = 'ErrorApp';
  }
}

export const noAutorizado = (msg = 'Debes iniciar sesión') => new ErrorApp(401, 'NO_AUTENTICADO', msg);
export const prohibido = (msg = 'No tienes permisos para esta acción') => new ErrorApp(403, 'PROHIBIDO', msg);
export const noEncontrado = (msg = 'Recurso no encontrado') => new ErrorApp(404, 'NO_ENCONTRADO', msg);
export const solicitudInvalida = (msg: string, detalles?: unknown) =>
  new ErrorApp(400, 'SOLICITUD_INVALIDA', msg, detalles);
export const conflicto = (msg: string, codigo = 'CONFLICTO') => new ErrorApp(409, codigo, msg);
