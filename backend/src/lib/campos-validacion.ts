/** Conserva solo nombres de campos conocidos. Los errores del extractor pueden incluir valores de identidad. */
export function camposDeValidacion(errores: unknown, datos: Record<string, unknown>): string[] {
  if (!Array.isArray(errores)) return [];
  return errores.map(error => {
    const campo = typeof error === 'string' ? error.split(/[:=]/, 1)[0].trim() : '';
    return /^[a-z][a-z0-9_]{0,63}$/.test(campo) && Object.hasOwn(datos, campo) ? campo : 'campo';
  });
}
