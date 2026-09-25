/**
 * Tareas programadas (módulo de notificaciones, gestión de tareas asíncronas):
 *  - Alertas de vigencia por correo (RN-11, RN-12).
 *  - Limpieza de códigos 2FA vencidos y registros abandonados.
 *  - Volcado del contador de tráfico.
 */
import cron from 'node-cron';
import { config } from '../config.js';
import { resultados } from '../db/mongo.js';
import { query } from '../db/pg.js';
import { logger } from '../lib/logger.js';
import { volcarTrafico } from '../middleware/trafico.js';
import { almacenamiento } from './almacenamiento.js';
import { registrarEvento } from './auditoria.js';
import { enviarCorreo } from './correo.js';
import { correoAlertaVigencia } from './plantillas-correo.js';

export async function enviarAlertasVigencia(): Promise<number> {
  let enviadas = 0;
  for (const dias of config.alertasDias) {
    // Documentos vigentes cuya fecha de vigencia cae dentro del umbral y que
    // aún no recibieron la alerta de ese umbral.
    const r = await query<{ id: number; usuario_id: number; correo: string; tipo: string; fecha_vigencia: string; restantes: number }>(
      `SELECT d.id, d.usuario_id, u.correo, t.nombre AS tipo, d.fecha_vigencia,
              (d.fecha_vigencia - CURRENT_DATE)::int AS restantes
         FROM documentos d
         JOIN usuarios u ON u.id = d.usuario_id AND u.activo AND u.alertas_vigencia
         JOIN tipos_documento t ON t.id = d.tipo_id
        WHERE d.vigente AND d.estado = 'validado' AND d.fecha_vigencia IS NOT NULL
          AND d.fecha_vigencia - CURRENT_DATE <= $1
          AND NOT EXISTS (SELECT 1 FROM alertas_vigencia a WHERE a.documento_id = d.id AND a.dias_antes <= $1)`,
      [dias],
    );
    for (const d of r.rows) {
      try {
        const fecha = new Date(`${d.fecha_vigencia}T12:00:00Z`).toLocaleDateString('es-MX', { dateStyle: 'long', timeZone: 'UTC' });
        await enviarCorreo({ para: d.correo, ...correoAlertaVigencia({ documento: d.tipo, fecha, dias: d.restantes }) });
        await query(
          'INSERT INTO alertas_vigencia (documento_id, usuario_id, dias_antes) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING',
          [d.id, d.usuario_id, dias],
        );
        await registrarEvento({ usuarioId: d.usuario_id, tipo: 'info', operacion: 'ALERTA_VIGENCIA', descripcion: `Alerta (${dias} días) enviada para documento ${d.id}` });
        enviadas++;
      } catch (err) {
        logger.error({ err, documentoId: d.id }, 'no se pudo enviar alerta de vigencia');
      }
    }
  }
  if (enviadas) logger.info({ enviadas }, 'alertas de vigencia enviadas');
  return enviadas;
}

export async function limpieza(): Promise<void> {
  await query(`DELETE FROM codigos_2fa WHERE expiracion < NOW() - INTERVAL '1 day'`);
  // Registros abandonados (> 24 h sin completar): se eliminan con sus archivos
  const abandonados = await query<{ id: number }>(
    `SELECT id FROM usuarios WHERE NOT registro_completo AND creado_en < NOW() - INTERVAL '24 hours'`,
  );
  for (const u of abandonados.rows) {
    const docs = await query<{ id: number; ruta_archivo: string }>('SELECT id, ruta_archivo FROM documentos WHERE usuario_id = $1', [u.id]);
    for (const d of docs.rows) {
      await almacenamiento.eliminar(d.ruta_archivo).catch(() => undefined);
      await resultados().deleteOne({ documento_id: d.id }).catch(() => undefined);
    }
    await query('DELETE FROM usuarios WHERE id = $1', [u.id]);
  }
  if (abandonados.rowCount) logger.info({ n: abandonados.rowCount }, 'registros incompletos eliminados');
}

export function programarTareas(): void {
  cron.schedule(config.ALERTAS_CRON, () => void enviarAlertasVigencia().catch((err) => logger.error({ err }, 'alertas')), {
    timezone: config.TZ,
  });
  cron.schedule('17 * * * *', () => void limpieza().catch((err) => logger.error({ err }, 'limpieza')));
  cron.schedule('* * * * *', () => void volcarTrafico());
  logger.info({ alertas: config.ALERTAS_CRON, tz: config.TZ }, 'tareas programadas');
}
