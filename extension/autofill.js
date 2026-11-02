/** Content script MV3, aislado del DOM; solo recibe perfil al pulsar Rellenar o al autorizar origen. */
/* global chrome, AutofillCore */
(() => {
  'use strict';
  if (globalThis.__ttAutoFillLoaded) return;
  globalThis.__ttAutoFillLoaded = true;
  let observador = null;
  let tiempo = null;
  let ultimaClave = '';
  const editable = node => {
    if (!(node instanceof HTMLElement) || !node.isConnected) return false;
    if (node.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
    const cs = getComputedStyle(node);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
    if (node.disabled || node.readOnly || node.hidden || node.type === 'hidden') return false;
    if (node instanceof HTMLInputElement) {
      const tipo = (node.type || 'text').toLowerCase();
      if (!['text','email','search','date','number'].includes(tipo)) return false;
      if (tipo === 'search') return false;
      if (/^(password|tel|file|radio|checkbox|submit|button|reset)$/i.test(tipo)) return false;
    }
    return node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement || node instanceof HTMLSelectElement;
  };
  function etiqueta(node) {
    const listado = [];
    if (node.labels) for (const label of node.labels) listado.push(label.innerText || label.textContent || '');
    if (node.id) {
      for (const label of document.querySelectorAll('label[for]')) if (label.htmlFor === node.id) listado.push(label.textContent || '');
    }
    const padre = node.closest('label');
    if (padre) listado.push(padre.textContent || '');
    return listado.join(' ').slice(0, 260);
  }
  function detectar(node) {
    const senales = {
      autocomplete: node.getAttribute('autocomplete') || '', label: etiqueta(node),
      aria: node.getAttribute('aria-label') || (document.getElementById(node.getAttribute('aria-labelledby') || '')?.textContent || ''),
      data: [node.getAttribute('data-field'),node.getAttribute('data-name'),node.getAttribute('data-testid')].filter(Boolean).join(' '),
      name: node.getAttribute('name') || '', id: node.id || '', placeholder: node.getAttribute('placeholder') || ''
    };
    return AutofillCore.campoDesdeSenales(senales);
  }
  function fechaISO(v) {
    const s = String(v).trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
    const m = s.match(/^(\d{2})[\/.-](\d{2})[\/.-](\d{4})$/);
    return m ? `${m[3]}-${m[2]}-${m[1]}` : null;
  }
  function preparar(node, clave, valor) {
    if (node instanceof HTMLSelectElement) {
      if (node.value && !node.selectedOptions[0]?.disabled && AutofillCore.normalizar(node.selectedOptions[0]?.textContent) !== 'selecciona' && AutofillCore.normalizar(node.selectedOptions[0]?.textContent) !== 'seleccione') return null;
      if (clave !== 'sexo') {
        if (!['estado','municipio','colonia'].includes(clave)) return null;
        const nombre = AutofillCore.normalizar(valor);
        const opciones = Array.from(node.options).filter(o => !o.disabled && (AutofillCore.normalizar(o.textContent) === nombre || AutofillCore.normalizar(o.value) === nombre));
        return opciones.length === 1 ? opciones[0].value : null;
      }
      const s = AutofillCore.normalizar(valor);
      const x = /^(m|masculino|hombre|male)$/.test(s) ? ['masculino','hombre','male','m'] : /^(f|femenino|mujer|female)$/.test(s) ? ['femenino','mujer','female','f'] : [];
      if (!x.length) return null;
      const opciones = Array.from(node.options).filter(o => x.includes(AutofillCore.normalizar(o.value)) || x.includes(AutofillCore.normalizar(o.text)));
      return opciones.length === 1 ? opciones[0].value : null;
    }
    if (String(node.value || '').trim() !== '') return null;
    if (node.type === 'number' && !['codigo_postal','numero_exterior','numero_interior'].includes(clave)) return null;
    if (node instanceof HTMLInputElement && node.type === 'date') return fechaISO(valor);
    if (clave === 'fecha_nacimiento' && /^\d{4}-\d{2}-\d{2}$/.test(valor)) {
      return /dd.mm.yyyy|dia.mes.ano|dd\/mm\/aaaa/i.test(node.placeholder || '') ? valor.split('-').reverse().join('/') : valor;
    }
    if (clave === 'curp' && !/^[A-ZÑ&]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$/i.test(valor)) return null;
    if (clave === 'codigo_postal' && !/^\d{5}$/.test(valor)) return null;
    return valor;
  }
  function establecer(node, valor) {
    const proto = node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(node, valor);
    else node.value = valor;
    node.dispatchEvent(new Event('input', { bubbles:true, composed:true }));
    node.dispatchEvent(new Event('change', { bubbles:true, composed:true }));
  }
  function campos(root, destino = [], nivel = 0) {
    if (nivel > 4 || !root.querySelectorAll) return destino;
    for (const n of root.querySelectorAll('input,select,textarea')) destino.push(n);
    for (const n of root.querySelectorAll('*')) if (n.shadowRoot) campos(n.shadowRoot,destino,nivel+1);
    return destino;
  }
  function rellenar(datos) {
    let completados = 0, compatibles = 0;
    for (const node of campos(document)) {
      if (!editable(node)) continue;
      const clave = detectar(node);
      if (!clave) continue;
      const valor = AutofillCore.valorPara(clave,datos);
      if (!valor) continue;
      compatibles++;
      const v = preparar(node, clave, valor);
      if (v == null) continue;
      try { establecer(node, v); completados++; } catch { /* formulario controlado o no editable */ }
    }
    return { completados, compatibles };
  }
  chrome.runtime.onMessage.addListener((mensaje, sender, responder) => {
    if (sender.id !== chrome.runtime.id || mensaje?.tipo !== 'autofill:aplicar') return false;
    const perfil = AutofillCore.normalizarPerfil(mensaje.datos);
    if (!Object.keys(perfil).length) { responder({ok:false,error:'El perfil no tiene datos compatibles.'}); return false; }
    // Reaplicar solo en esta página, durante un período corto, para formularios SPA que aparecen después.
    const clave = String(mensaje.sesion || 'manual');
    if (observador) observador.disconnect();
    if (tiempo) clearTimeout(tiempo);
    ultimaClave = clave;
    const resultado = rellenar(perfil);
    let vueltas = 0;
    let pendientes = false;
    observador = new MutationObserver(() => {
      if (pendientes || vueltas > 40 || ultimaClave !== clave) return;
      pendientes = true;
      setTimeout(() => { pendientes = false; vueltas++; rellenar(perfil); }, 180);
    });
    observador.observe(document.documentElement, { childList:true, subtree:true });
    tiempo = setTimeout(() => { observador?.disconnect(); observador = null; }, 18000);
    responder({ ok:true, ...resultado });
    return false;
  });
})();
