/**
 * Service worker de la extensión.
 *  - Al instalarse, se vincula sola con el código incluido en config.js
 *    (generado por la plataforma al descargar el paquete).
 *  - Atiende mensajes de la página de la plataforma (externally_connectable):
 *    consultar estado, vincular con un código nuevo y sincronizar. Solo se
 *    aceptan mensajes del origen exacto de la plataforma configurada.
 */
/* global chrome, importScripts, Nucleo */
importScripts('config.js', 'lib.js');

const CONFIG = globalThis.CONFIG_PLATAFORMA || {};

chrome.runtime.onInstalled.addListener(async () => {
  const estado = await Nucleo.leer();
  if (!estado.url && CONFIG.url) await Nucleo.guardar({ url: CONFIG.url });
  const vigente = !CONFIG.expira_en || new Date(CONFIG.expira_en) > new Date();
  if (!estado.token && CONFIG.codigo && vigente) {
    try {
      await Nucleo.vincular(CONFIG.codigo, CONFIG.url);
    } catch (err) {
      await Nucleo.guardar({ aviso: `No se pudo vincular automáticamente: ${err.message}` });
    }
  }
});

function origenPermitido(estado) {
  try {
    return new URL(Nucleo.urlBase(estado) || CONFIG.url).origin;
  } catch {
    return null;
  }
}

chrome.runtime.onMessageExternal.addListener((mensaje, remitente, responder) => {
  (async () => {
    const estado = await Nucleo.leer();
    const permitido = origenPermitido(estado);
    if (!permitido || remitente.origin !== permitido) {
      return responder({ ok: false, error: 'Origen no permitido' });
    }
    switch (mensaje?.tipo) {
      case 'estado':
        return responder({
          ok: true,
          instalada: true,
          version: chrome.runtime.getManifest().version,
          vinculada: !!estado.token,
          correo: estado.token ? estado.correo ?? null : null,
          documentos: estado.token ? estado.documentos?.length ?? 0 : 0,
          sincronizado_en: estado.token ? estado.sincronizado_en ?? null : null,
        });
      case 'vincular': {
        if (estado.token) await Nucleo.desvincular();
        const r = await Nucleo.vincular(String(mensaje.codigo || ''), permitido);
        return responder({ ok: true, correo: r.correo, documentos: r.documentos?.length ?? 0 });
      }
      case 'sincronizar': {
        const r = await Nucleo.sincronizar();
        return responder({ ok: true, documentos: r.documentos?.length ?? 0, sincronizado_en: r.sincronizado_en });
      }
      default:
        return responder({ ok: false, error: 'Mensaje no reconocido' });
    }
  })().catch((err) => responder({ ok: false, error: err.message }));
  return true; // respuesta asíncrona
});
