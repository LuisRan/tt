const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const core=require(process.env.TT_CORE_PATH || path.resolve(__dirname,'../../extension/autofill-core.js'));
const opciones=xs=>xs.map(([value,text,disabled=false])=>({value,text,disabled}));
const ejemplo={tipo:'ine',campos:[
  {campo:'nombres',etiqueta:'Nombre(s)',valor:'LUISA MARÍA'},
  {campo:'primer_apellido',etiqueta:'Primer apellido',valor:'PRUEBA'},
  {campo:'segundo_apellido',etiqueta:'Segundo apellido',valor:'SINTÉTICA'},
  {campo:'fecha_nacimiento',etiqueta:'Fecha de nacimiento',valor:'09/02/2000'},
  {campo:'sexo',etiqueta:'Sexo (H/M)',valor:'M'},
  {campo:'estado',etiqueta:'Estado (código)',valor:'09'},
  {campo:'municipio',etiqueta:'Municipio (código)',valor:'015'}
]};
test('Contrato real: campo=nombres conserva nombres de pila y compone nombre completo',()=>{
  const p=core.datosDesdeDocumento(ejemplo);
  assert.equal(p.nombre,'LUISA MARÍA');assert.equal(p.nombre_completo,'LUISA MARÍA PRUEBA SINTÉTICA');
  assert.equal(p.apellido_paterno,'PRUEBA');assert.equal(p.apellido_materno,'SINTÉTICA');
});
test('Clave tipada desconocida o parental no se reinterpreta por etiqueta genérica',()=>{
  const p=core.datosDesdeDocumento({campos:[{campo:'nombre_padre',etiqueta:'Nombre',valor:'AJENO'},{campo:'fecha_registro',etiqueta:'Fecha de nacimiento',valor:'01/01/1990'}]});
  assert.deepEqual(p,{});
});
test('INE: códigos electorales no se convierten en nacimiento/domicilio',()=>{
  const p=core.datosDesdeDocumento(ejemplo); assert.equal(p.estado,undefined);assert.equal(p.municipio,undefined);assert.equal(p.estado_nacimiento,undefined);
});
test('Fecha confirmada se descompone sin consultar CURP',()=>{
  const p=core.datosDesdeDocumento(ejemplo);
  assert.equal(core.valorPara('dia_nacimiento',p),'09');assert.equal(core.valorPara('mes_nacimiento',p),'02');assert.equal(core.valorPara('anio_nacimiento',p),'2000');
});
test('No infiere cumpleaños, sexo ni estado de nacimiento de CURP ni del domicilio',()=>{
  const p=core.normalizarPerfil({curp:'XXXX000209MDFXXX00',estado:'Jalisco',domicilio:'CALLE FICTICIA'});
  for (const k of ['fecha_nacimiento','dia_nacimiento','sexo','estado_nacimiento']) assert.equal(core.valorPara(k,p),null);
});
for(const [v,iso] of [['2000-02-29','2000-02-29'],['9/2/2000','2000-02-09'],['09.02.2000','2000-02-09'],['1900-02-29',null],['2001-02-29',null],['2000-13-01',null],['31/04/2000',null],['01/02/00',null]])
  test(`Calendario real ${v}`,()=>assert.equal(core.fechaPartes(v)?.iso || null,iso));
for(const [s,key] of [['section-registro shipping given-name','nombre'],['section-paciente billing address-line1','direccion_linea1'],['bday-day','dia_nacimiento'],['bday-month','mes_nacimiento'],['bday-year','anio_nacimiento'],['section-a address-level1','estado']])
  test(`Token HTML ${s}`,()=>assert.equal(core.clasificar(s,{autocomplete:true}),key));
for(const [s,key] of [['Día de nacimiento','dia_nacimiento'],['mesNacimiento','mes_nacimiento'],['anioNacimiento','anio_nacimiento'],['birthState','estado_nacimiento'],['Estado de nacimiento','estado_nacimiento'],['fec_nac','fecha_nacimiento'],['surname1','apellido_paterno'],['surname2','apellido_materno'],['Nombre(s)','nombre']])
  test(`Vocabulario ${s}`,()=>assert.equal(core.clasificar(s),key));
for(const s of ['Nombre del padre','Primer apellido madre','Estado civil','nombre de usuario','Correo de emergencia','nacionalidad'])
  test(`Evita otra identidad o dato distinto: ${s}`,()=>assert.equal(core.clasificar(s),null));
test('Autocomplete sensible veta incluso una etiqueta nombre',()=>{
  for(const a of ['cc-name','one-time-code','new-password','tel','organization-title','additional-name']) assert.equal(core.campoDesdeSenales({autocomplete:a,label:'Nombre'}),null);
});
test('autocomplete=off permite solicitud manual con etiqueta explícita',()=>assert.equal(core.campoDesdeSenales({autocomplete:'off',label:'Nombre(s)'}),'nombre'));
test('Señales fuertes contradictorias se abstienen',()=>assert.equal(core.campoDesdeSenales({autocomplete:'given-name',label:'Correo electrónico'}),null));
test('family-name se precisa por etiqueta primer apellido',()=>assert.equal(core.campoDesdeSenales({autocomplete:'family-name',label:'Primer apellido'}),'apellido_paterno'));
test('id genérico no desplaza etiqueta explícita',()=>assert.equal(core.campoDesdeSenales({label:'Primer apellido',id:'nombre'}),'apellido_paterno'));
test('Día aislado solo es nacimiento con contexto local',()=>{
  assert.equal(core.campoDesdeSenales({label:'Día',grupo:'Fecha de nacimiento'}),'dia_nacimiento');
  assert.equal(core.campoDesdeSenales({label:'Día',grupo:'Fecha de entrega'}),null);
});
test('Estado en formulario de nacimiento es estado_nacimiento',()=>assert.equal(core.campoDesdeSenales({label:'Estado',grupo:'Nombre Día de nacimiento Mes de nacimiento Sexo'}),'estado_nacimiento'));
test('Estado en domicilio sigue siendo domicilio',()=>assert.equal(core.campoDesdeSenales({label:'Estado',grupo:'Domicilio Código postal'}),'estado'));
test('Placeholder con valor 0 y frase completa no se trata como dato existente',()=>assert.equal(core.esPlaceholder({value:'0',text:'Seleccionar el día'}),true));
test('Selección explícita válida se preserva aunque sea primera opción',()=>assert.equal(core.esPlaceholder({value:'01',text:'01'}),false));
for(const [key,v,opts,expected] of [
 ['dia_nacimiento','09',[['0','Seleccionar el día'],['9','09'],['10','10']],'9'],
 ['mes_nacimiento','02',[['','Seleccione'],['0','Enero'],['1','Febrero'],['2','Marzo']],'1'],
 ['mes_nacimiento','09',[['00','Selecciona'],['SEP','September'],['OCT','October']],'SEP'],
 ['mes_nacimiento','02',[['','Seleccione'],['02','02'],['03','03']],'02'],
 ['anio_nacimiento','2000',[['0','Año'],['2000','2000'],['2001','2001']],'2000'],
 ['sexo','H',[['','Selecciona el sexo'],['1','Hombre'],['2','Mujer']],'1'],
 ['sexo','M',[['','Seleccione'],['M','Masculino'],['F','Femenino']],'F'],
 ['sexo','M',[['','Seleccione'],['H','H'],['M','M']],'M'],
 ['sexo','H',[['','Seleccione'],['M','M'],['F','F']],'M'],
 ['estado_nacimiento','Ciudad de México',[['','Seleccione'],['DF','DISTRITO FEDERAL'],['MC','México']],'DF'],
 ['estado_nacimiento','MC',[['','Seleccione'],['15','México'],['09','Ciudad de México']],'15'],
 ['estado','Michoacán de Ocampo',[['','Seleccione'],['MN','Michoacán']],'MN'],
 ['estado','México',[['15','México'],['09','Ciudad de México']],'15'],
 ['estado','09',[['09','Ciudad de México']], '09'],
 ['sexo','M',[['M','M']],null],
 ['municipio','PRUEBA',[['a','Prueba'],['b','Prueba']],null],
 ['mes_nacimiento','02',[['02','Marzo']],null]
]) test(`Opciones ${key} ${v} ${expected}`,()=>assert.equal(core.elegirOpcion(key,v,opciones(opts)),expected));
test('No usar una opción deshabilitada',()=>assert.equal(core.elegirOpcion('estado','Jalisco',opciones([['JC','Jalisco',true]])),null));
test('No leer propiedades heredadas ni inyectar claves desconocidas',()=>assert.deepEqual(core.normalizarPerfil(Object.create({nombre:'OTRA PERSONA'})),{}));
test('Estado ambiguo en un formulario mixto se deja pendiente',()=>assert.equal(core.campoDesdeSenales({label:'Estado',grupo:'Fecha de nacimiento Dirección Código postal'}),null));
test('Una etiqueta de residencia explícita resuelve el formulario mixto',()=>assert.equal(core.campoDesdeSenales({label:'Estado de residencia',grupo:'Fecha de nacimiento Dirección Código postal'}),'estado'));
test('Guiones de placeholder con valor no vacío',()=>assert.equal(core.esPlaceholder({value:'-1',text:'---'}),true));
test('username y dos tokens de campo incompatibles no se rellenan por nombre',()=>{
  for(const a of ['username','given-name one-time-code','given-name email'])assert.equal(core.campoDesdeSenales({autocomplete:a,label:'Nombre'}),null);
});
