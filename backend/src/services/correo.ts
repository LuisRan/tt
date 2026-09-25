/**
 * Envío de correo transaccional.
 *  - resend: API HTTP de Resend (https://resend.com) con dominio verificado
 *            (DNS SPF/DKIM configurados en Resend) -> llega a cualquier dirección.
 *  - console: desarrollo sin credenciales; el correo se imprime en el log.
 */
import { config } from '../config.js';
import { logger } from '../lib/logger.js';

export interface Correo {
  para: string;
  asunto: string;
  html: string;
  texto: string;
}

export async function enviarCorreo(c: Correo): Promise<{ id?: string; proveedor: string }> {
  if (config.proveedorCorreo === 'resend') {
    const respuesta = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${config.RESEND_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from: config.MAIL_FROM,
        to: [c.para],
        subject: c.asunto,
        html: c.html,
        text: c.texto,
        ...(config.MAIL_REPLY_TO ? { reply_to: config.MAIL_REPLY_TO } : {}),
      }),
      signal: AbortSignal.timeout(15_000),
    });
    const cuerpo = (await respuesta.json().catch(() => ({}))) as { id?: string; message?: string };
    if (!respuesta.ok) {
      logger.error({ status: respuesta.status, error: cuerpo.message }, 'Resend rechazó el correo');
      throw new Error(`No se pudo enviar el correo (${respuesta.status}): ${cuerpo.message ?? 'error'}`);
    }
    logger.info({ id: cuerpo.id }, 'correo enviado con Resend');
    return { id: cuerpo.id, proveedor: 'resend' };
  }

  // Modo consola (desarrollo): muestra el texto plano para poder copiar el código
  logger.warn(
    { para: c.para, asunto: c.asunto },
    `[CORREO SIMULADO — configura RESEND_API_KEY para enviarlo de verdad]\n${c.texto}`,
  );
  if (!config.esTest) {
    // eslint-disable-next-line no-console
    console.log(`\n========== CORREO (modo consola) ==========\nPara: ${c.para}\n${c.texto}\n===========================================\n`);
  }
  return { proveedor: 'console' };
}
