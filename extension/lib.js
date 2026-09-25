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

  async function leer() {
    const r = await chrome.storage.local.get(CLAVE);
    return r[CLAVE] ?? {};
  }

  async function guardar(parcial) {
    const estado = { ...(await leer()), ...parcial };
    await chrome.storage.local.set({ [CLAVE]: estado });
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

  async function vincular(codigo, url) {
    const base = String(url || urlBase(await leer())).replace(/\/+$/, '');
    if (!base) throw new Error('Falta la dirección de la plataforma.');
    const r = await peticion('/extension/vincular', { metodo: 'POST', cuerpo: { codigo, nombre: nombreNavegador() }, url: base });
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
  }

  return { leer, guardar, urlBase, vincular, sincronizar, desvincular };
})();

globalThis.Nucleo = Nucleo;
