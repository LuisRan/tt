/** MV3, mundo aislado: valores confirmados solo por orden interna, sin envío de formularios. */
/* global chrome, AutofillCore */
(() => {
  'use strict';
  if (globalThis.__ttAutoFillLoaded) return;
  globalThis.__ttAutoFillLoaded = true;
  let sesion = null;
  const MAX_CAMPOS=500, DURACION=18000, MAX_PASADAS=40;
  const padreCompuesto=n=>n.parentElement || n.getRootNode()?.host || null;
  function visible(node) {
    if (!(node instanceof HTMLElement) || !node.isConnected || !node.getClientRects().length) return false;
    for (let p=node;p;p=padreCompuesto(p)) {
      if (p.hidden || p.inert || p.getAttribute('aria-hidden')==='true') return false;
      const css=getComputedStyle(p);
      if (css.display==='none' || ['hidden','collapse'].includes(css.visibility) || css.opacity==='0') return false;
    }
    return true;
  }
  function editable(node) {
    if (!visible(node) || node.matches(':disabled') || node.readOnly || node.getAttribute('aria-disabled')==='true') return false;
    if (node instanceof HTMLInputElement && !['text','email','date','number','radio'].includes(node.type)) return false;
    return node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement || (node instanceof HTMLSelectElement && !node.multiple);
  }
  function texto(node) {
    if (!node) return '';
    const copia=node.cloneNode(true);
    for (const x of copia.querySelectorAll('input,select,textarea,button,script,style,[aria-hidden="true"]')) x.remove();
    return (copia.textContent || '').replace(/\s+/g,' ').trim().slice(0,240);
  }
  function etiqueta(node) { return Array.from(node.labels || []).map(texto).join(' ').slice(0,300); }
  function aria(node) {
    const root=node.getRootNode();
    const ids=(node.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    return [node.getAttribute('aria-label'),...ids.map(id=>texto(root.getElementById?.(id)))].filter(Boolean).join(' ').slice(0,300);
  }
  function cercano(node) {
    const caja=node.closest('.form-group,.field,.form-field,[data-field-container],td');
    if (caja && caja.querySelectorAll('input,select,textarea').length===1) return texto(caja);
    const anterior=node.previousElementSibling;
    if (anterior && !anterior.querySelector('input,select,textarea') && /^(LABEL|SPAN|STRONG|P|DIV)$/.test(anterior.tagName)) {
      const t=texto(anterior); if (t.length<=120) return t;
    }
    const p=node.parentElement;
    if (p && p.querySelectorAll('input,select,textarea').length===1) return texto(p);
    return '';
  }
  function campos(root,destino=[],nivel=0,roots=[]) {
    if (nivel>6 || !root.querySelectorAll || destino.length>=MAX_CAMPOS || roots.length>=32) return destino;
    roots.push(root);
    for (const n of root.querySelectorAll('input,select,textarea')) {
      destino.push(n); if (destino.length>=MAX_CAMPOS) break;
    }
    // Solo Shadow DOM abierto; no leer iframes ni pedir permisos adicionales.
    let examinados=0;
    for (const n of root.querySelectorAll('*')) {
      if (++examinados>10000 || roots.length>=32 || destino.length>=MAX_CAMPOS) break;
      if (n.shadowRoot) campos(n.shadowRoot,destino,nivel+1,roots);
    }
    return destino;
  }
  function grupo(node,cache) {
    const caja=node.closest('fieldset,.tab-pane,[role="form"],section') || node.form || node.getRootNode();
    if (cache.has(caja)) return cache.get(caja);
    const controles=Array.from(caja.querySelectorAll('input,select,textarea')).slice(0,MAX_CAMPOS).filter(visible);
    const t=[texto(caja.querySelector('legend')), ...controles.map(n=>[etiqueta(n),aria(n),n.name,n.id,n.getAttribute('autocomplete')].filter(Boolean).join(' '))].join(' ').slice(0,6000);
    cache.set(caja,t); return t;
  }
  function detectar(node,cache) {
    return AutofillCore.campoDesdeSenales({
      autocomplete:node.autocomplete || '',label:etiqueta(node),aria:aria(node),
      data:[node.getAttribute('data-field'),node.getAttribute('data-name'),node.getAttribute('data-testid')].filter(Boolean).join(' '),
      name:node.name || '',id:node.id || '',placeholder:node.getAttribute('placeholder') || '',
      contexto:cercano(node) || texto(node.closest('fieldset')?.querySelector('legend')), grupo:grupo(node,cache)
    });
  }
  function opciones(node) { return Array.from(node.options).map(o=>({value:o.value,text:o.text,disabled:o.disabled || o.parentElement?.disabled})); }
  function vacio(node) {
    if (node instanceof HTMLSelectElement) return AutofillCore.esPlaceholder(node.selectedOptions[0] && {value:node.value,text:node.selectedOptions[0].text,disabled:node.selectedOptions[0].disabled});
    return String(node.value || '').trim()==='';
  }
  function preparar(node,clave,valor) {
    if (!vacio(node)) return null;
    if (node instanceof HTMLSelectElement) return AutofillCore.elegirOpcion(clave,valor,opciones(node));
    if (node.type==='number' && !['codigo_postal','numero_exterior','numero_interior','dia_nacimiento','mes_nacimiento','anio_nacimiento'].includes(clave)) return null;
    if (clave==='fecha_nacimiento') {
      const f=AutofillCore.fechaPartes(valor); if (!f) return null;
      const raw=[node.placeholder,node.getAttribute('data-format'),node.getAttribute('pattern')].filter(Boolean).join(' ');
      const formato=raw.toLowerCase().match(/(dd|mm|yyyy|aaaa)([/.\-])(dd|mm|yyyy|aaaa)\2(dd|mm|yyyy|aaaa)/);
      valor=f.iso;
      if (node.type!=='date' && formato) {
        const tokens=[formato[1],formato[3],formato[4]].map(t=>t==='aaaa'?'yyyy':t);
        if (new Set(tokens).size===3) valor=tokens.map(t=>({dd:f.dia,mm:f.mes,yyyy:f.anio})[t]).join(formato[2]);
      } else if (node.type!=='date' && /dia mes ano/.test(AutofillCore.normalizar(raw))) valor=`${f.dia}/${f.mes}/${f.anio}`;
    }
    if (['dia_nacimiento','mes_nacimiento','anio_nacimiento'].includes(clave)) {
      const max=clave==='dia_nacimiento'?31:clave==='mes_nacimiento'?12:9999;
      if (!/^\d+$/.test(valor) || Number(valor)<1 || Number(valor)>max || (clave==='anio_nacimiento' && Number(valor)<1000)) return null;
    }
    if (clave==='sexo') { const s=AutofillCore.sexo(valor); if (!s) return null; valor=s==='hombre'?'H':'M'; }
    if (clave==='curp') { if (!/^[A-ZÑ&]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$/i.test(valor)) return null; valor=valor.toUpperCase(); }
    if (clave==='codigo_postal' && !/^\d{5}$/.test(valor)) return null;
    if (node.maxLength>=0 && valor.length>node.maxLength) return null;
    if (node.type==='number' || node.type==='date') {
      if (node.min && valor < (node.type==='number'?Number(node.min):node.min)) return null;
      if (node.max && valor > (node.type==='number'?Number(node.max):node.max)) return null;
    }
    // Probar formato/pattern/min/max en una copia desconectada, sin modificar campos ajenos.
    const copia=node.cloneNode(false); copia.removeAttribute('required'); copia.value=valor;
    if (copia.value!==valor || (copia.validity && !copia.validity.valid)) return null;
    return valor;
  }
  function establecer(node,valor) {
    const proto=node instanceof HTMLSelectElement?HTMLSelectElement.prototype:node instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    const setter=Object.getOwnPropertyDescriptor(proto,'value')?.set;
    if (setter) setter.call(node,valor); else node.value=valor;
    node.dispatchEvent(new Event('input',{bubbles:true,composed:true}));
    node.dispatchEvent(new Event('change',{bubbles:true,composed:true}));
    return node.value===valor;
  }
  function vigente(s) { return sesion===s && location.href===s.url && Date.now()<s.hasta; }
  function detener() {
    if (!sesion) return;
    const s=sesion; sesion=null;
    s.observer?.disconnect(); clearTimeout(s.timer); clearTimeout(s.debounce);
    s.datos={}; s.llenados.clear();
  }
  function rellenar(s) {
    if (!vigente(s)) { if (sesion===s) detener(); return; }
    const roots=[], nodos=campos(document,[],0,roots), cache=new WeakMap();
    const sinDato=new Set(), radiosProcesados=new Set(); let compatibles=0,conValor=0,noAdmitidos=0;
    for (const node of nodos) {
      if (!vigente(s)) break;
      if (!editable(node)) continue;
      if (s.editados.has(node)) continue;
      if (node.type==='radio') {
        if (!node.name || radiosProcesados.has(node)) continue;
        const radios=nodos.filter(n=>n.type==='radio' && n.name===node.name && n.form===node.form && n.getRootNode()===node.getRootNode());
        radios.forEach(n=>radiosProcesados.add(n));
        if (!radios.some(n=>detectar(n,cache)==='sexo')) continue;
        compatibles++;
        if (radios.some(n=>n.checked || s.editados.has(n))) { conValor++; continue; }
        const valor=AutofillCore.valorPara('sexo',s.datos);
        if (!valor) { sinDato.add('sexo'); continue; }
        const aptos=radios.filter(editable);
        const v=AutofillCore.elegirOpcion('sexo',valor,aptos.map(n=>({value:n.value,text:etiqueta(n),disabled:false})));
        const coincidencias=aptos.filter(n=>n.value===v);
        if (v==null || coincidencias.length!==1) { noAdmitidos++; continue; }
        const elegido=coincidencias[0];
        try {
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'checked').set.call(elegido,true);
          elegido.dispatchEvent(new Event('input',{bubbles:true,composed:true}));
          elegido.dispatchEvent(new Event('change',{bubbles:true,composed:true}));
          if (elegido.checked) s.llenados.set(elegido,v); else noAdmitidos++;
        } catch { noAdmitidos++; }
        continue;
      }
      const clave=detectar(node,cache); if (!clave) continue;
      compatibles++;
      if (!vacio(node)) { conValor++; continue; }
      const valor=AutofillCore.valorPara(clave,s.datos);
      if (!valor) { sinDato.add(clave); continue; }
      const v=preparar(node,clave,valor);
      if (v==null) { noAdmitidos++; continue; }
      try { if (establecer(node,v)) s.llenados.set(node,v); else noAdmitidos++; } catch { noAdmitidos++; }
    }
    if (!vigente(s)) { if (sesion===s) detener(); return; }
    const completados=Array.from(s.llenados).filter(([n,v])=>n.isConnected && n.value===v && (n.type!=='radio' || n.checked)).length;
    s.resultado={ok:true,sesion:s.id,completados,compatibles,sin_datos:[...sinDato],con_valor:conValor,no_admitidos:noAdmitidos};
    for (const root of roots) s.observer?.observe(root===document?document.documentElement:root,{childList:true,subtree:true,attributes:true,attributeFilter:['class','style','hidden','disabled','aria-hidden','aria-disabled']});
  }
  function planificar(s) {
    if (!vigente(s)) { if (sesion===s) detener(); return; }
    if (s.debounce || s.pasadas>=MAX_PASADAS) return;
    s.debounce=setTimeout(()=>{s.debounce=null;s.pasadas++;rellenar(s);},180);
  }
  // Una navegación, envío o cambio de vista no debe reutilizar silenciosamente los datos anteriores.
  addEventListener('pagehide',detener);
  addEventListener('popstate',detener);
  addEventListener('hashchange',detener);
  document.addEventListener('submit',detener,true);
  for (const tipo of ['input','change']) document.addEventListener(tipo,e=>{
    if (sesion && e.isTrusted) { const n=e.composedPath()[0]; if (n instanceof HTMLElement) sesion.editados.add(n); }
  },true);
  chrome.runtime.onMessage.addListener((mensaje,sender,responder)=>{
    if (sender.id!==chrome.runtime.id) return false;
    if (mensaje?.tipo==='autofill:cancelar') { detener(); responder({ok:true}); return false; }
    if (mensaje?.tipo==='autofill:estado') {
      if (sesion && !vigente(sesion)) detener();
      responder(sesion?.id===mensaje.sesion?sesion.resultado:{ok:false,finalizado:true}); return false;
    }
    if (mensaje?.tipo!=='autofill:aplicar') return false;
    detener();
    if (mensaje.urlEsperada && location.href!==mensaje.urlEsperada) { responder({ok:false,error:'La página cambió. Vuelve a pulsar Autocompletar.'}); return false; }
    const datos=AutofillCore.normalizarPerfil(mensaje.datos);
    if (!Object.keys(datos).length) { responder({ok:false,error:'No hay datos confirmados compatibles.'}); return false; }
    const s={id:String(mensaje.sesion || Date.now()),url:location.href,hasta:Date.now()+DURACION,datos,llenados:new Map(),editados:new WeakSet(),pasadas:0};
    sesion=s;
    s.observer=new MutationObserver(()=>planificar(s));
    rellenar(s);
    if (!vigente(s)) { responder({ok:false,error:'La página cambió durante el relleno. Revisa los campos y vuelve a intentarlo.'}); return false; }
    s.timer=setTimeout(()=>{if(sesion===s)detener();},DURACION);
    // Una pasada adicional cubre listeners de frameworks que actualizan listas tras input/change.
    planificar(s);
    responder(s.resultado); return false;
  });
})();
