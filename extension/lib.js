/**
 * Núcleo compartido por el service worker (background.js) y la ventana
 * emergente (popup.js): almacenamiento local, vinculación y sincronización.
 *
 * Seguridad:
 *  - La extensión nunca conoce la contraseña del usuario. Se vincula con un
 *    código de un solo uso y recibe un token de dispositivo revocable.
 *  - Solo puede LEER los documentos ya validados del usuario.
 *  - Si la plataforma responde 401 (token revocado, contraseña cambiada o
 *    cuenta deshabilitada) se borran los datos guardados en el navegador.
 */
/* global chrome */
const Nucleo = (() => {
  const CLAVE = 'ocr_platform';
  const SESION_DOCS = 'ocr_platform_documentos';

  async function leer() {
    const [l, sesion] = await Promise.all([
      chrome.storage.local.get(CLAVE), chrome.storage.session.get(SESION_DOCS),
    ]);
    const estado = l[CLAVE] ?? {};
    // Migración desde la versión anterior: eliminar las copias permanentes de documentos.
    if (Object.prototype.hasOwnProperty.call(estado, 'documentos')) {
      if (!sesion[SESION_DOCS]) await chrome.storage.session.set({ [SESION_DOCS]: estado.documentos });
      const documentos = sesion[SESION_DOCS] || estado.documentos;
      delete estado.documentos;
      await chrome.storage.local.set({ [CLAVE]: estado });
      return { ...estado, documentos };
    }
    return { ...estado, documentos: sesion[SESION_DOCS] || [] };
  }

  async function guardar(parcial) {
    const estado = { ...(await leer()), ...parcial };
    const { documentos, ...persistente } = estado;
    // Los datos OCR no deben quedar almacenados permanentemente en chrome.storage.local.
    await Promise.all([
      chrome.storage.local.set({ [CLAVE]: persistente }),
      chrome.storage.session.set({ [SESION_DOCS]: documentos || [] }),
    ]);
    return estado;
  }

  function urlBase(estado) {
    return String(estado?.url || globalThis.CONFIG_PLATAFORMA?.url || '').replace(/\/+$/, '');
  }

  async function peticion(ruta, { metodo = 'GET', cuerpo, token, url }) {
    const cabeceras = { Accept: 'application/json' };
    if (cuerpo !== undefined) cabeceras['Content-Type'] = 'application/json';
    if (token) cabeceras.Authorization = `Bearer ${token}`;
    let r;
    try {
      r = await fetch(`${url}/api${ruta}`, {
        method: metodo,
        headers: cabeceras,
        body: cuerpo !== undefined ? JSON.stringify(cuerpo) : undefined,
        credentials: 'omit',
        cache: 'no-store',
      });
    } catch {
      const e = new Error(
        `No se pudo conectar con la plataforma (${url}). Verifica que esté en ejecución y ábrela una vez en este navegador para aceptar su certificado.`,
      );
      e.status = 0;
      throw e;
    }
    const datos = await r.json().catch(() => ({}));
    if (!r.ok) {
      const e = new Error(datos.error || `Error ${r.status}`);
      e.status = r.status;
      throw e;
    }
    return datos;
  }

  function nombreNavegador() {
    const ua = navigator.userAgent;
    const nav = /Edg\//.test(ua) ? 'Microsoft Edge' : /OPR\//.test(ua) ? 'Opera' : navigator.brave ? 'Brave' : 'Chrome';
    const so = /Mac OS X/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : 'equipo';
    return `${nav} en ${so}`;
  }

  function comprobarBase(url) {
    let parsed;
    try { parsed = new URL(url); } catch { throw new Error('La URL de la plataforma no es válida.'); }
    const local = ['localhost', '127.0.0.1'].includes(parsed.hostname);
    if (parsed.protocol !== 'https:' && !(local && parsed.protocol === 'http:')) throw new Error('Para vincular la extensión se necesita HTTPS.');
    if (parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== '/') throw new Error('Introduce solo el origen de la plataforma, por ejemplo https://app.ejemplo.mx');
    return parsed.origin;
  }

  async function obtenerPerfil() {
    const estado = await leer();
    if (!estado.token) throw new Error('Primero vincula la extensión.');
    try {
      const r = await peticion('/extension/autofill', { token: estado.token, url: urlBase(estado) });
      if (r.validado === false || r.confirmado === false) throw new Error('El perfil aún no está confirmado.');
      const perfil = r.perfil || r.datos || r.datos_confirmados || r.autofill || r.campos;
      if (!perfil || typeof perfil !== 'object' || Array.isArray(perfil)) throw new Error('La API no devolvió un perfil de autocompletado.');
      return perfil;
    } catch (err) {
      if (err.status === 401) {
        await chrome.storage.local.set({ [CLAVE]: { url: urlBase(estado), aviso: err.message } });
        await chrome.storage.session.remove(SESION_DOCS);
      }
      throw err;
    }
  }

  async function vincular(codigo, url) {
    const base = comprobarBase(String(url || urlBase(await leer())).replace(/\/+$/, ''));
    if (!base) throw new Error('Falta la dirección de la plataforma.');
    const r = await peticion('/extension/vincular', { metodo: 'POST', cuerpo: { codigo, nombre: nombreNavegador() }, url: base });
    await chrome.storage.session.remove(SESION_DOCS);
    await chrome.storage.local.set({
      [CLAVE]: { url: base, token: r.token, correo: r.correo, vinculada_en: new Date().toISOString() },
    });
    return sincronizar();
  }

  async function sincronizar() {
    const estado = await leer();
    if (!estado.token) throw new Error('La extensión no está vinculada.');
    try {
      const r = await peticion('/extension/documentos', { token: estado.token, url: urlBase(estado) });
      return guardar({ documentos: r.documentos, correo: r.correo, sincronizado_en: r.generado_en, aviso: null });
    } catch (err) {
      if (err.status === 401) {
        // Token revocado: se eliminan los datos personales guardados en el navegador
        await chrome.storage.local.set({ [CLAVE]: { url: urlBase(estado), aviso: err.message } });
        await chrome.storage.session.remove(SESION_DOCS);
      }
      throw err;
    }
  }

  async function desvincular() {
    const estado = await leer();
    if (estado.token) {
      await peticion('/extension/desvincular', { metodo: 'POST', cuerpo: {}, token: estado.token, url: urlBase(estado) }).catch(() => undefined);
    }
    await chrome.storage.local.set({ [CLAVE]: { url: urlBase(estado) } });
    await chrome.storage.session.remove(SESION_DOCS);
    // Se revocan permisos adicionales otorgados a sitios recordados.
    for (const sitio of estado.sitios || []) {
      try { const u = new URL(sitio); await chrome.permissions.remove({ origins: [`${u.protocol}//${u.hostname}/*`] }); } catch {}
    }
  }

  return { leer, guardar, urlBase, comprobarBase, obtenerPerfil, vincular, sincronizar, desvincular };
})();

globalThis.Nucleo = Nucleo;
