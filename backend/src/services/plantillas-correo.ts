/**
 * Plantillas HTML con branding para los correos (2FA y alertas de vigencia).
 * Estilos en línea porque los clientes de correo ignoran <style>.
 */
import { config } from '../config.js';

const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

function marco(titulo: string, cuerpo: string): string {
  const app = esc(config.APP_NAME);
  return `<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(titulo)}</title></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:Segoe UI,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;padding:32px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#1e293b;border-radius:16px;overflow:hidden;">
<tr><td style="background:linear-gradient(90deg,#6366f1,#8b5cf6);padding:22px 28px;">
<span style="color:#fff;font-size:20px;font-weight:700;letter-spacing:.3px;">${app}</span>
<span style="color:#e0e7ff;font-size:13px;display:block;margin-top:2px;">Bóveda de identidad · OCR + IA</span>
</td></tr>
<tr><td style="padding:28px;color:#e2e8f0;font-size:15px;line-height:1.6;">${cuerpo}</td></tr>
<tr><td style="padding:18px 28px;border-top:1px solid #334155;color:#94a3b8;font-size:12px;line-height:1.5;">
Recibes este correo porque tienes una cuenta en ${app}. Nunca te pediremos tu contraseña ni este código por teléfono o chat.<br>
Si no reconoces esta actividad, cambia tu contraseña de inmediato.
</td></tr>
</table></td></tr></table></body></html>`;
}

const PROPOSITOS: Record<string, { asunto: string; accion: string }> = {
  registro: { asunto: 'Verifica tu correo', accion: 'confirmar tu correo y continuar con tu registro' },
  login: { asunto: 'Tu código de acceso', accion: 'iniciar sesión' },
  sensible: { asunto: 'Confirma la operación', accion: 'confirmar una operación sensible' },
};

export function correoCodigo(codigo: string, proposito: string, minutos: number) {
  const p = PROPOSITOS[proposito] ?? PROPOSITOS.login;
  const digitos = codigo
    .split('')
    .map(
      (d) =>
        `<span style="display:inline-block;width:40px;height:52px;line-height:52px;margin:0 3px;background:#0f172a;border:1px solid #6366f1;border-radius:10px;color:#fff;font-size:26px;font-weight:700;text-align:center;">${d}</span>`,
    )
    .join('');
  const html = marco(
    p.asunto,
    `<h1 style="margin:0 0 12px;font-size:22px;color:#fff;">${esc(p.asunto)}</h1>
     <p style="margin:0 0 20px;">Usa este código para ${esc(p.accion)}:</p>
     <div style="text-align:center;margin:24px 0;">${digitos}</div>
     <p style="margin:0;color:#94a3b8;">El código vence en <b style="color:#e2e8f0;">${minutos} minutos</b> y solo puede usarse una vez.</p>`,
  );
  const texto = `${config.APP_NAME}\n\n${p.asunto}\nTu código para ${p.accion} es: ${codigo}\nVence en ${minutos} minutos.`;
  return { asunto: `${p.asunto} · ${config.APP_NAME}`, html, texto };
}

export function correoAlertaVigencia(d: { documento: string; fecha: string; dias: number }) {
  const vencido = d.dias <= 0;
  const titulo = vencido ? `Tu ${d.documento} ha vencido` : `Tu ${d.documento} vence en ${d.dias} días`;
  const color = vencido ? '#f87171' : d.dias <= 30 ? '#fbbf24' : '#a5b4fc';
  const html = marco(
    titulo,
    `<h1 style="margin:0 0 12px;font-size:22px;color:#fff;">${esc(titulo)}</h1>
     <p style="margin:0 0 16px;">Registramos que tu <b>${esc(d.documento)}</b> tiene vigencia hasta el
     <b style="color:${color};">${esc(d.fecha)}</b>.</p>
     <p style="margin:0 0 24px;">${vencido
       ? 'Un documento vencido puede ser rechazado en trámites. Te recomendamos renovarlo y cargar la nueva versión.'
       : 'Te recomendamos iniciar su renovación con anticipación y, cuando la tengas, cargar la nueva versión en la plataforma.'}</p>
     <a href="${esc(config.PUBLIC_URL)}/app/documentos" style="display:inline-block;background:linear-gradient(90deg,#6366f1,#8b5cf6);color:#fff;text-decoration:none;padding:12px 22px;border-radius:10px;font-weight:600;">Ver mis documentos</a>`,
  );
  const texto = `${titulo}. Vigencia: ${d.fecha}. Consulta tus documentos en ${config.PUBLIC_URL}/app/documentos`;
  return { asunto: `${titulo} · ${config.APP_NAME}`, html, texto };
}
