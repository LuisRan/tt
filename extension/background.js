/** Service worker: API + origen autorizado + inserción de content scripts en mundo aislado. */
/* global chrome, importScripts, Nucleo, AutofillCore */
importScripts('config.js', 'lib.js', 'autofill-core.js');
const CONFIG = globalThis.CONFIG_PLATAFORMA || {};

chrome.runtime.onInstalled.addListener(async () => {
  const estado = await Nucleo.leer();
  if (!estado.url && CONFIG.url) await Nucleo.guardar({ url: CONFIG.url });
  // Permite vinculación de descarga personalizada con código temporal, pero no lo guarda en el paquete compartido.
  const vigente = CONFIG.expira_en && Date.parse(CONFIG.expira_en) > Date.now();
  if (!estado.token && CONFIG.codigo && vigente) {
    try { await Nucleo.vincular(CONFIG.codigo, CONFIG.url); }
    catch { await Nucleo.guardar({ aviso:'La vinculación automática falló. Introduce un código nuevo.' }); }
  }
});

function origen(url) { try { return new URL(url).origin; } catch { return null; } }
function paginaApta(url) {
  try { const u = new URL(url); return ['https:','http:'].includes(u.protocol) && (u.protocol === 'https:' || ['localhost','127.0.0.1'].includes(u.hostname)); }
  catch { return false; }
}
function origenPermitido(estado) { return origen(Nucleo.urlBase(estado) || CONFIG.url); }

chrome.runtime.onMessageExternal.addListener((mensaje, remitente, responder) => {
  (async () => {
    const estado = await Nucleo.leer();
    const permitido = origenPermitido(estado);
    if (!permitido || remitente.origin !== permitido) return responder({ ok:false, error:'Origen no permitido' });
    switch (mensaje?.tipo) {
      case 'estado': return responder({ ok:true, instalada:true, version:chrome.runtime.getManifest().version,
        vinculada:!!estado.token, correo:estado.token ? estado.correo ?? null:null,
        documentos:estado.token ? estado.documentos?.length ?? 0:0,
        sincronizado_en:estado.token ? estado.sincronizado_en ?? null:null });
      case 'vincular': {
        if (estado.token) await Nucleo.desvincular();
        const r = await Nucleo.vincular(String(mensaje.codigo || ''), permitido);
        return responder({ ok:true, correo:r.correo, documentos:r.documentos?.length ?? 0 });
      }
      case 'sincronizar': {
        const r = await Nucleo.sincronizar();
        return responder({ ok:true, documentos:r.documentos?.length ?? 0, sincronizado_en:r.sincronizado_en });
      }
      default: return responder({ ok:false, error:'Mensaje no reconocido' });
    }
  })().catch(err => responder({ ok:false,error:err.message }));
  return true;
});

async function insertar(tabId, datos, sesion, urlEsperada) {
  const resultados = await chrome.scripting.executeScript({ target:{tabId, allFrames:false}, files:['autofill-core.js','autofill.js'] });
  const principal = resultados.find(r => r.frameId === 0);
  if (!principal?.documentId) throw new Error('No se pudo identificar el documento activo. Recarga la página.');
  return chrome.tabs.sendMessage(tabId, { tipo:'autofill:aplicar', datos, sesion, urlEsperada }, { documentId:principal.documentId });
}
async function cancelarTab(tabId) {
  try { await chrome.tabs.sendMessage(tabId,{tipo:'autofill:cancelar'},{frameId:0}); } catch { /* Puede no existir un script inyectado. */ }
}
function sitioAutorizado(estado, url) { return Boolean(estado?.sitios?.includes(origen(url))); }
async function rellenarTab(tabId, { documentoId = null, automatico = false } = {}) {
  const tab = await chrome.tabs.get(tabId);
  if (!paginaApta(tab.url)) throw new Error('Solo puedes autocompletar páginas web HTTPS (o un entorno local).');
  const estado = await Nucleo.leer();
  if (!estado.token) throw new Error('Vincula primero la extensión.');
  if (automatico && !sitioAutorizado(estado, tab.url)) throw new Error('Sitio no autorizado.');
  let datos;
  if (documentoId != null && !automatico) {
    const doc = await Nucleo.obtenerDocumento(documentoId);
    datos = AutofillCore.datosDesdeDocumento(doc);
  } else {
    datos = AutofillCore.normalizarPerfil(await Nucleo.obtenerPerfil());
  }
  if (!Object.keys(datos).length) throw new Error('No hay datos confirmados que puedan autocompletarse.');
  // Evitar TOCTOU: pestaña podría navegar durante la consulta a API.
  const actual = await chrome.tabs.get(tabId);
  const vigente = await Nucleo.leer();
  if (!vigente.token || vigente.token !== estado.token || Nucleo.urlBase(vigente) !== Nucleo.urlBase(estado)) throw new Error('La vinculación cambió. Vuelve a intentarlo.');
  if (actual.url !== tab.url || (automatico && !sitioAutorizado(vigente,actual.url))) throw new Error('La página o su autorización cambiaron durante la operación.');
  return insertar(tabId, datos, `${tabId}-${Date.now()}`, tab.url);
}

chrome.runtime.onMessage.addListener((mensaje, sender, responder) => {
  // Solo popup interna; un content script nunca puede activar el acceso al perfil.
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('popup.html')) return false;
  (async () => {
    if (mensaje?.tipo === 'autofill:cancelar') { await cancelarTab(mensaje.tabId); return responder({ok:true}); }
    if (mensaje?.tipo === 'autofill:estado') return responder(await chrome.tabs.sendMessage(mensaje.tabId,{tipo:'autofill:estado',sesion:mensaje.sesion},{frameId:0}));
    if (mensaje?.tipo === 'autofill:manual') return responder(await rellenarTab(mensaje.tabId,{ documentoId:mensaje.documentoId ?? null }));
    if (mensaje?.tipo === 'autofill:origen') {
      const tab = await chrome.tabs.get(mensaje.tabId);
      if (!paginaApta(tab.url) || origen(tab.url) !== mensaje.origen) throw new Error('La pestaña cambió o no es compatible.');
      const estado = await Nucleo.leer();
      if (!estado.token) throw new Error('Extensión sin vinculación.');
      const sitios = new Set(estado.sitios || []);
      if (mensaje.activar) sitios.add(mensaje.origen); else sitios.delete(mensaje.origen);
      await Nucleo.guardar({ sitios:[...sitios] });
      if (!mensaje.activar) await cancelarTab(tab.id);
      return responder({ok:true, sitios:[...sitios]});
    }
    return responder({ok:false,error:'Mensaje desconocido'});
  })().catch(err => responder({ ok:false,error:err.message }));
  return true;
});

chrome.tabs.onUpdated.addListener((tabId, cambio, tab) => {
  if (cambio.status === 'loading' || cambio.url) cancelarTab(tabId);
  if (cambio.status !== 'complete' || !paginaApta(tab.url)) return;
  (async () => {
    const estado = await Nucleo.leer();
    if (!sitioAutorizado(estado,tab.url)) return;
    // Si el permiso fue revocado, retirar el origen de sitios.
    const u = new URL(tab.url);
    const patron = `${u.protocol}//${u.hostname}/*`;
    if (!(await chrome.permissions.contains({ origins:[patron] }))) {
      await Nucleo.guardar({ sitios:(estado.sitios||[]).filter(x=>x!==origen(tab.url)) });
      return;
    }
    await rellenarTab(tabId,{automatico:true});
  })().catch(() => { /* No registrar datos personales ni repetir en bucle */ });
});

// Desvincular o retirar un sitio también corta las pasadas pendientes en páginas abiertas.
chrome.storage.onChanged.addListener((cambios,area) => {
  if (area !== 'local' || !cambios.ocr_platform) return;
  const antes=cambios.ocr_platform.oldValue || {}, despues=cambios.ocr_platform.newValue || {};
  const revocados=(antes.sitios || []).filter(s=>!(despues.sitios || []).includes(s));
  if (antes.token===despues.token && antes.url===despues.url && !revocados.length) return;
  chrome.tabs.query({}).then(tabs=>Promise.all(tabs.filter(t=>t.id && (antes.token!==despues.token || antes.url!==despues.url || revocados.includes(origen(t.url)))).map(t=>cancelarTab(t.id)))).catch(()=>{});
});
chrome.permissions.onRemoved.addListener(() => {
  chrome.tabs.query({}).then(tabs=>Promise.all(tabs.filter(t=>t.id).map(t=>cancelarTab(t.id)))).catch(()=>{});
});
