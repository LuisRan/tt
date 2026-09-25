/** Descarga de archivos con URL firmada + sesión del propietario (defensa en profundidad). */
import { Router } from 'express';
import { uno } from '../db/pg.js';
import { verificarToken } from '../lib/crypto.js';
import { ErrorApp, noEncontrado } from '../lib/errores.js';
import { requiereSesion } from '../middleware/auth.js';
import { almacenamiento } from '../services/almacenamiento.js';

export const rutasArchivos = Router();

rutasArchivos.get('/:token', requiereSesion, async (req, res) => {
  const datos = verificarToken<{ d: number; u: number }>(String(req.params.token));
  if (!datos) throw new ErrorApp(410, 'URL_EXPIRADA', 'El enlace expiró. Vuelve a abrir el documento.');
  if (datos.u !== req.usuario!.id) throw noEncontrado();
  const d = await uno<{ ruta_archivo: string; nombre_original: string }>(
    'SELECT ruta_archivo, nombre_original FROM documentos WHERE id = $1 AND usuario_id = $2',
    [datos.d, datos.u],
  );
  if (!d) throw noEncontrado();
  const pdf = await almacenamiento.leer(d.ruta_archivo);
  const nombre = (d.nombre_original || 'documento').replace(/[^\w.\- ]/g, '_').replace(/\.(jpe?g|png)$/i, '.pdf');
  res.setHeader('Content-Type', 'application/pdf');
  res.setHeader('Content-Disposition', `inline; filename="${nombre}"`);
  res.setHeader('Cache-Control', 'private, no-store');
  res.setHeader('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'");
  res.send(pdf);
});
