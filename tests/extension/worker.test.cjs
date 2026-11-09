const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const core=require('../../extension/autofill-core.js');
const extension=path.resolve(__dirname,'../../extension');
function entorno(op={}) {
  const listeners={},llamadas={inyecciones:[],mensajes:[],perfil:0,documento:0};
  const estado={url:'https://plataforma.example.invalid',token:'token-ficticio',sitios:['https://formulario.example.invalid'],documentos:[{id:7,campos:[{campo:'nombres',valor:'VIEJO'}]}]};
  let lecturas=0,consultas=0;
  const evento=n=>({addListener(fn){listeners[n]=fn;}});
  const chrome={runtime:{id:'tt-test',onInstalled:evento('installed'),onMessageExternal:evento('externo'),onMessage:evento('interno'),getURL:p=>'chrome-extension://tt-test/'+p,getManifest:()=>({version:'1.2.0'})},
    scripting:{executeScript:async x=>{llamadas.inyecciones.push(x);return [{frameId:0,documentId:'documento-prueba'}];}},
    tabs:{get:async()=>({id:3,url:op.navega && consultas++>0?'https://formulario.example.invalid/otra':'https://formulario.example.invalid/formulario'}),query:async()=>[{id:3,url:'https://formulario.example.invalid/formulario'}],sendMessage:async(...x)=>{llamadas.mensajes.push(x);return {ok:true,sesion:'simulada',completados:1};},onUpdated:evento('updated')},
    storage:{onChanged:evento('storage')},permissions:{contains:async()=>true,onRemoved:evento('permiso')}};
  const Nucleo={leer:async()=>{lecturas++;return {...estado,token:op.revocado && lecturas>1?null:estado.token};},
    urlBase:x=>x.url,guardar:async x=>Object.assign(estado,x),obtenerPerfil:async()=>{llamadas.perfil++;return {nombre:'DUEÑA',correo:'duena@example.invalid'};},
    obtenerDocumento:async id=>{llamadas.documento++;if(op.sinDocumento)throw new Error('Documento ya no confirmado');return {id,campos:[{campo:'nombres',etiqueta:'Nombre(s)',valor:'OTRA PERSONA'}]};}};
  const c=vm.createContext({chrome,Nucleo,AutofillCore:core,URL,Date,importScripts:()=>{},CONFIG_PLATAFORMA:{}});
  vm.runInContext(fs.readFileSync(path.join(extension,'background.js'),'utf8'),c);
  const enviar=(mensaje,sender={id:'tt-test',url:'chrome-extension://tt-test/popup.html'})=>new Promise(resolve=>{let llamado=false;const seguir=listeners.interno(mensaje,sender,r=>{llamado=true;resolve(r);});if(!seguir&&!llamado)resolve({ignorado:true});});
  return {listeners,llamadas,estado,enviar};
}
test('Documento seleccionado se consulta fresco y jamás mezcla perfil del dueño',async()=>{
  const e=entorno();const r=await e.enviar({tipo:'autofill:manual',tabId:3,documentoId:7});assert.equal(r.ok,true);assert.equal(e.llamadas.documento,1);assert.equal(e.llamadas.perfil,0);
  const [,m,op]=e.llamadas.mensajes.at(-1);assert.equal(m.datos.nombre,'OTRA PERSONA');assert.equal(m.datos.correo,undefined);assert.equal(m.urlEsperada,'https://formulario.example.invalid/formulario');assert.equal(op.documentId,'documento-prueba');
  assert.equal(e.llamadas.inyecciones[0].target.allFrames,false);
});
test('Sin documento autorizado en API no se usa la copia vieja de sesión',async()=>{
  const e=entorno({sinDocumento:true});const r=await e.enviar({tipo:'autofill:manual',tabId:3,documentoId:7});assert.equal(r.ok,false);assert.equal(e.llamadas.inyecciones.length,0);
});
test('Perfil confirmado se obtiene solo si se elige esa fuente',async()=>{
  const e=entorno();assert.equal((await e.enviar({tipo:'autofill:manual',tabId:3})).ok,true);assert.equal(e.llamadas.perfil,1);assert.equal(e.llamadas.documento,0);
});
test('Navegación dentro del mismo origen durante API bloquea la inyección',async()=>{
  const e=entorno({navega:true});assert.equal((await e.enviar({tipo:'autofill:manual',tabId:3})).ok,false);assert.equal(e.llamadas.inyecciones.length,0);
});
test('Desvinculación durante la consulta bloquea la inyección',async()=>{
  const e=entorno({revocado:true});assert.equal((await e.enviar({tipo:'autofill:manual',tabId:3})).ok,false);assert.equal(e.llamadas.inyecciones.length,0);
});
test('Content scripts y remitentes externos no pueden solicitar el perfil',async()=>{
  const e=entorno();for(const s of [{id:'tt-test',url:'https://formulario.example.invalid'},{id:'ajeno',url:'chrome-extension://tt-test/popup.html'}])assert.equal((await e.enviar({tipo:'autofill:manual',tabId:3},s)).ignorado,true);assert.equal(e.llamadas.perfil,0);
});
test('Retirar un sitio cancela pasadas pendientes en la pestaña',async()=>{
  const e=entorno();const r=await e.enviar({tipo:'autofill:origen',tabId:3,origen:'https://formulario.example.invalid',activar:false});assert.equal(r.ok,true);assert.equal(e.llamadas.mensajes.at(-1)[1].tipo,'autofill:cancelar');assert.equal(e.estado.sitios.length,0);
});
test('Cambiar token o revocar permiso cancela los scripts activos',async()=>{
  const e=entorno();e.listeners.storage({ocr_platform:{oldValue:{token:'uno'},newValue:{}}},'local');await new Promise(setImmediate);assert.equal(e.llamadas.mensajes.at(-1)[1].tipo,'autofill:cancelar');e.listeners.permiso({});await new Promise(setImmediate);assert.equal(e.llamadas.mensajes.length,2);
});
test('Mensaje de estado transmite únicamente id de sesión al frame principal',async()=>{
  const e=entorno();await e.enviar({tipo:'autofill:estado',tabId:3,sesion:'prueba'});const [,m,o]=e.llamadas.mensajes.at(-1);assert.equal(m.tipo,'autofill:estado');assert.equal(m.datos,undefined);assert.equal(o.frameId,0);
});
function api(respuesta,status=200) {
  const persistente={ocr_platform:{url:'https://plataforma.example.invalid',token:'token-sintetico'}};
  const docs={ocr_platform_documentos:[{id:1}]};const peticiones=[];
  const c=vm.createContext({URL,Date,navigator:{userAgent:'test'},CONFIG_PLATAFORMA:{},chrome:{storage:{
    local:{get:async()=>persistente,set:async x=>Object.assign(persistente,x)},
    session:{get:async()=>docs,set:async x=>Object.assign(docs,x),remove:async k=>delete docs[k]}}},
    fetch:async(url,opciones)=>{peticiones.push({url,opciones});return {ok:status<400,status,json:async()=>respuesta};}});
  vm.runInContext(fs.readFileSync(path.join(extension,'lib.js'),'utf8'),c);
  return {Nucleo:c.Nucleo,persistente,docs,peticiones};
}
test('API de documentos: usa bearer solo hacia la plataforma y no cachea datos nuevos',async()=>{
  const e=api({documentos:[{id:7,campos:[{campo:'nombres',valor:'PERSONA SINTÉTICA'}]}]});const d=await e.Nucleo.obtenerDocumento(7);assert.equal(d.id,7);assert.equal(e.docs.ocr_platform_documentos[0].id,1);assert.equal(e.peticiones[0].url,'https://plataforma.example.invalid/api/extension/documentos');assert.equal(e.peticiones[0].opciones.credentials,'omit');assert.equal(e.peticiones[0].opciones.cache,'no-store');
});
test('API 401 elimina token y documentos; no puede reutilizarlos',async()=>{
  const e=api({error:'Token revocado'},401);await assert.rejects(e.Nucleo.obtenerDocumento(7),/Token revocado/);assert.equal(e.persistente.ocr_platform.token,undefined);assert.equal(e.docs.ocr_platform_documentos,undefined);
});
test('API no devuelve documento pedido: no acepta otro id o cache',async()=>{
  const e=api({documentos:[{id:8,campos:[]}]});await assert.rejects(e.Nucleo.obtenerDocumento(7),/disponible|confirmado/);
});
test('Cambiar fuente desde popup cancela el documento anterior sin consultar API',async()=>{
  const e=entorno();assert.equal((await e.enviar({tipo:'autofill:cancelar',tabId:3})).ok,true);assert.equal(e.llamadas.mensajes.at(-1)[1].tipo,'autofill:cancelar');assert.equal(e.llamadas.perfil+e.llamadas.documento,0);
});
