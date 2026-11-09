/** Integración real en Chromium; todas las páginas/datos son sintéticos y no salen a red. */
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('playwright');
let browser;
before(async()=>{browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE || undefined,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});});
after(async()=>{await browser?.close();});
const root=path.resolve(__dirname,'../../extension');
const datos={nombre:'LUISA MARÍA',apellido_paterno:'PRUEBA',apellido_materno:'SINTÉTICA',fecha_nacimiento:'2000-02-09',sexo:'M',estado_nacimiento:'Ciudad de México',estado:'Jalisco',correo:'persona@example.invalid',codigo_postal:'01234',domicilio_completo:'CALLE FICTICIA 10'};
const curpForm=`<form id="consulta"><label for="n">Nombre(s)*:</label><input id="n" name="nombre">
<label for="p">Primer apellido*:</label><input id="p" name="primerApellido">
<label for="a">Segundo apellido:</label><input id="a" name="segundoApellido">
<label for="d">Día de nacimiento*:</label><select id="d"><option value="0">Seleccionar el día</option><option value="9">09</option><option value="10">10</option></select>
<label for="m">Mes de nacimiento*:</label><select id="m"><option value="0">Seleccionar el mes</option><option value="1">Enero</option><option value="2">Febrero</option><option value="3">Marzo</option></select>
<label for="y">Año de nacimiento*:</label><input id="y" type="number" placeholder="Ingresa el año Ej. 1943" min="1900" max="2026">
<label for="s">Sexo*:</label><select id="s"><option value="0">Selecciona el sexo</option><option value="H">Hombre</option><option value="M">Mujer</option></select>
<label for="e">Estado*:</label><select id="e"><option value="0">Selecciona el estado</option><option value="DF">Distrito Federal</option><option value="JC">Jalisco</option></select><button>Buscar</button></form>`;
async function pagina(html) {
  const p=await browser.newPage();
  await p.route('**/*',r=>r.request().resourceType()==='document'?r.fulfill({contentType:'text/html; charset=utf-8',body:html}):r.abort());
  await p.goto('https://formularios.example.test/registro');
  await p.evaluate(()=>{
    window.__envios=0;window.__eventos=[];
    document.addEventListener('submit',e=>{e.preventDefault();window.__envios++;});
    document.addEventListener('input',e=>window.__eventos.push(['input',e.target.id]));
    document.addEventListener('change',e=>window.__eventos.push(['change',e.target.id]));
    window.chrome={runtime:{id:'tt-test',onMessage:{addListener(fn){window.__handler=fn;}}}};
  });
  await p.addScriptTag({path:path.join(root,'autofill-core.js')});
  await p.addScriptTag({path:path.join(root,'autofill.js')});
  return p;
}
async function aplicar(p,perfil=datos,id='uno',extra={}) {
  return p.evaluate(({perfil,id,extra})=>{
    let result;window.__handler({tipo:'autofill:aplicar',datos:perfil,sesion:id,urlEsperada:location.href,...extra},{id:'tt-test'},r=>result=r);return result;
  },{perfil,id,extra});
}
async function valores(p,ids) {return p.evaluate(ids=>Object.fromEntries(ids.map(id=>[id,document.getElementById(id).value])),ids);}
async function status(p,id='uno') {return p.evaluate(id=>{let r;window.__handler({tipo:'autofill:estado',sesion:id},{id:'tt-test'},x=>r=x);return r;},id);}
test('Reproducción CURP: ocho controles nativos completos, sin enviar formulario',async()=>{
  const p=await pagina(curpForm);try {
    const r=await aplicar(p);assert.equal(r.completados,8);assert.equal(r.compatibles,8);
    assert.deepEqual(await valores(p,['n','p','a','d','m','y','s','e']),{n:'LUISA MARÍA',p:'PRUEBA',a:'SINTÉTICA',d:'9',m:'2',y:'2000',s:'M',e:'DF'});
    assert.equal(await p.evaluate(()=>window.__envios),0);
    assert.ok((await p.evaluate(()=>window.__eventos)).some(([t,id])=>t==='input' && id==='n'));
  } finally {await p.close();}
});
test('Documento API con nombres tipados: siete campos y explicación del estado faltante',async()=>{
  const p=await pagina(curpForm);try {
    const doc={tipo:'ine',campos:[['nombres','Nombre(s)','LUISA MARÍA'],['primer_apellido','Primer apellido','PRUEBA'],['segundo_apellido','Segundo apellido','SINTÉTICA'],['fecha_nacimiento','Fecha de nacimiento','09/02/2000'],['sexo','Sexo (H/M)','M'],['estado','Estado (código)','14']].map(([campo,etiqueta,valor])=>({campo,etiqueta,valor}))};
    const perfil=await p.evaluate(doc=>AutofillCore.datosDesdeDocumento(doc),doc);
    const r=await aplicar(p,perfil);assert.equal(r.completados,7);assert.deepEqual(r.sin_datos,['estado_nacimiento']);assert.equal((await valores(p,['e'])).e,'0');
  } finally {await p.close();}
});
test('No confundir estado del domicilio con nacimiento aunque exista otro valor confirmado',async()=>{
  const p=await pagina(curpForm);try {const {estado_nacimiento,...perfil}=datos;const r=await aplicar(p,perfil);assert.equal(r.completados,7);assert.equal((await valores(p,['e'])).e,'0');}finally{await p.close();}
});
test('Ocultos por CSS, ancestro transparente, hidden, disabled y readonly no se rellenan',async()=>{
  const p=await pagina(`<style>.cerrado{display:none}.invisible{opacity:0}</style><div class="cerrado"><input id="oculto" autocomplete="given-name"></div><div class="invisible"><input id="transparente" autocomplete="email"></div><div hidden><input id="hidden" autocomplete="email"></div><fieldset disabled><input id="disabled" autocomplete="email"></fieldset><input readonly id="ro" autocomplete="email"><input id="visible" autocomplete="email">`);
  try {const r=await aplicar(p);assert.equal(r.completados,1);assert.equal(r.compatibles,1);const v=await valores(p,['oculto','transparente','hidden','disabled','ro','visible']);assert.equal(v.visible,datos.correo);for(const id of ['oculto','transparente','hidden','disabled','ro'])assert.equal(v[id],'');}finally{await p.close();}
});
test('No sobrescribe entradas, selecciones, campos ambiguos, contraseñas, OTP, tarjetas ni datos parentales',async()=>{
  const p=await pagina(`<label for="n">Nombre</label><input id="n" value="USUARIO LO ESCRIBIÓ"><input id="cc" autocomplete="cc-name" aria-label="Nombre"><input id="otp" name="nombre" aria-label="Código de verificación"><input id="pw" type="password" autocomplete="given-name"><label for="padre">Nombre del padre</label><input id="padre" name="nombre"><label for="s">Sexo</label><select id="s"><option value="H" selected>Hombre</option><option value="M">Mujer</option></select><input id="amb" autocomplete="given-name" aria-label="Correo electrónico"><input id="cuenta" name="nombre" aria-label="Referencia bancaria">`);
  try {await aplicar(p);assert.deepEqual(await valores(p,['n','cc','otp','pw','padre','s','amb','cuenta']),{n:'USUARIO LO ESCRIBIÓ',cc:'',otp:'',pw:'',padre:'',s:'H',amb:'',cuenta:''});assert.equal(await p.evaluate(()=>window.__envios),0);}finally{await p.close();}
});
test('Fechas con formato, número con límites, maxlength y pattern se respetan',async()=>{
  const p=await pagina(`<input id="fecha" type="date" autocomplete="bday"><input id="latina" name="fechaNacimiento" placeholder="dd/mm/aaaa"><input id="us" autocomplete="bday" placeholder="mm/dd/yyyy"><input id="corto" autocomplete="given-name" maxlength="2"><input id="pat" autocomplete="given-name" pattern="[0-9]+"><input id="rango" type="number" autocomplete="bday-year" min="2010"><input id="dia" type="number" autocomplete="bday-day" min="1" max="31">`);
  try {await aplicar(p);assert.deepEqual(await valores(p,['fecha','latina','us','corto','pat','rango','dia']),{fecha:'2000-02-09',latina:'09/02/2000',us:'02/09/2000',corto:'',pat:'',rango:'',dia:'09'});}finally{await p.close();}
});
test('Texto próximo sin label, ARIA con múltiples IDs y Shadow DOM abierto',async()=>{
  const p=await pagina(`<div class="form-group"><strong>Nombre(s)</strong><input id="x1"></div><span id="t1">Primer</span><span id="t2">apellido</span><input id="x2" aria-labelledby="t1 t2"><div id="host"></div>`);
  try {
    await p.evaluate(()=>{document.getElementById('host').attachShadow({mode:'open'}).innerHTML='<span id="t">Correo electrónico</span><input id="x3" aria-labelledby="t">';});
    const r=await aplicar(p);assert.equal(r.completados,3);assert.equal((await valores(p,['x1'])).x1,datos.nombre);assert.equal((await valores(p,['x2'])).x2,datos.apellido_paterno);assert.equal(await p.evaluate(()=>document.getElementById('host').shadowRoot.querySelector('input').value),datos.correo);
  }finally{await p.close();}
});
test('Listas y campos dinámicos se rellenan, el contador se actualiza y no se repite input',async()=>{
  const p=await pagina(`<label for="mes">Mes de nacimiento</label><select id="mes"><option value="0">Selecciona el mes</option></select><div id="nuevos"></div>`);
  try {
    const r=await aplicar(p);assert.equal(r.completados,0);
    await p.evaluate(()=>{document.getElementById('mes').insertAdjacentHTML('beforeend','<option value="X">Febrero</option>');document.getElementById('nuevos').innerHTML='<input id="correo" autocomplete="email">';});
    await p.waitForFunction(()=>document.getElementById('mes').value==='X' && document.getElementById('correo').value!=='');
    assert.equal((await status(p)).completados,2);
    await p.evaluate(()=>document.getElementById('nuevos').append(document.createElement('span')));
    await p.waitForTimeout(250);
    assert.equal((await p.evaluate(()=>window.__eventos)).filter(([t,id])=>t==='input' && id==='correo').length,1);
  }finally{await p.close();}
});
test('Setter nativo evita una propiedad value redefinida y notifica input/change',async()=>{
  const p=await pagina('<input id="n" autocomplete="given-name">');try {
    await p.evaluate(()=>{const n=document.getElementById('n');Object.defineProperty(n,'value',{get(){return Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').get.call(this)},set(){throw new Error('setter controlado')}});});
    const r=await aplicar(p);assert.equal(r.completados,1);assert.equal((await valores(p,['n'])).n,datos.nombre);assert.deepEqual(await p.evaluate(()=>window.__eventos),[['input','n'],['change','n']]);
  }finally{await p.close();}
});
test('Mensajes ajenos y URL inesperada no acceden a valores',async()=>{
  const p=await pagina('<input id="n" autocomplete="given-name">');try {
    await p.evaluate(datos=>window.__handler({tipo:'autofill:aplicar',datos},{id:'otro'},()=>{}),datos);
    assert.equal((await valores(p,['n'])).n,'');const r=await aplicar(p,datos,'uno',{urlEsperada:'https://formularios.example.test/otra'});assert.equal(r.ok,false);assert.equal((await valores(p,['n'])).n,'');
  }finally{await p.close();}
});
test('Cancelar corta el temporizador pendiente y no rellena controles posteriores',async()=>{
  const p=await pagina('<div id="nuevos"></div>');try {
    await aplicar(p);await p.evaluate(()=>{document.getElementById('nuevos').innerHTML='<input id="n" autocomplete="given-name">';window.__handler({tipo:'autofill:cancelar'},{id:'tt-test'},()=>{});});await p.waitForTimeout(300);assert.equal((await valores(p,['n'])).n,'');assert.equal((await status(p)).finalizado,true);
  }finally{await p.close();}
});
test('Navegación SPA y vencimiento de sesión impiden rellenar formularios nuevos',async()=>{
  const p=await pagina('<div id="nuevos"></div>');try {
    await aplicar(p);await p.evaluate(()=>{history.pushState({},'','/otra');document.getElementById('nuevos').innerHTML='<input id="n" autocomplete="given-name">';});await p.waitForTimeout(300);assert.equal((await valores(p,['n'])).n,'');assert.equal((await status(p)).finalizado,true);
    await aplicar(p,datos,'dos');await p.evaluate(()=>{const ahora=Date.now();Date.now=()=>ahora+19000;document.getElementById('nuevos').innerHTML='<input id="correo" autocomplete="email">';});await p.waitForTimeout(250);assert.equal((await valores(p,['correo'])).correo,'');assert.equal((await status(p,'dos')).finalizado,true);
  }finally{await p.close();}
});
test('Un formulario controlado que rechaza el valor no cuenta como completado',async()=>{
  const p=await pagina('<input id="n" autocomplete="given-name">');try {
    await p.evaluate(()=>document.getElementById('n').addEventListener('change',e=>{e.target.value='';}));
    const r=await aplicar(p);assert.equal(r.completados,0);assert.equal(r.no_admitidos,1);assert.equal((await valores(p,['n'])).n,'');
  }finally{await p.close();}
});
test('Radios de sexo con códigos y etiquetas: rellena una opción y conserva la decisión existente',async()=>{
  const p=await pagina('<fieldset><legend>Sexo</legend><label><input id="h" type="radio" name="sexo" value="M">Masculino</label><label><input id="f" type="radio" name="sexo" value="F">Femenino</label></fieldset>');
  try {const r=await aplicar(p);assert.equal(r.completados,1);assert.deepEqual(await p.evaluate(()=>[document.getElementById('h').checked,document.getElementById('f').checked]),[false,true]);await aplicar(p,{...datos,sexo:'H'},'dos');assert.equal(await p.locator('#f').isChecked(),true);}finally{await p.close();}
});
test('Si el usuario borra un valor durante el periodo dinámico, se respeta su edición',async()=>{
  const p=await pagina('<input id="n" autocomplete="given-name"><div id="caja"></div>');
  try {await aplicar(p);await p.locator('#n').fill('');await p.evaluate(()=>document.getElementById('caja').append(document.createElement('span')));await p.waitForTimeout(300);assert.equal((await valores(p,['n'])).n,'');}finally{await p.close();}
});
test('Navegación síncrona disparada por input detiene el relleno restante',async()=>{
  const p=await pagina('<input id="n" autocomplete="given-name"><input id="correo" autocomplete="email">');try {
    await p.evaluate(()=>document.getElementById('n').addEventListener('input',()=>history.pushState({},'','/cambio')));
    const r=await aplicar(p);assert.equal(r.ok,false);assert.equal((await valores(p,['correo'])).correo,'');assert.equal((await status(p)).finalizado,true);
  }finally{await p.close();}
});
test('Fechas respetan separadores explícitos de texto y pattern',async()=>{
  const p=await pagina('<input id="f" autocomplete="bday" placeholder="dd-mm-aaaa" pattern="[0-9]{2}-[0-9]{2}-[0-9]{4}"><input id="y" autocomplete="bday" data-format="yyyy/mm/dd">');try {
    const r=await aplicar(p);assert.equal(r.completados,2);assert.deepEqual(await valores(p,['f','y']),{f:'09-02-2000',y:'2000/02/09'});
  }finally{await p.close();}
});
