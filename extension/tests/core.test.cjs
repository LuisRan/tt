const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../autofill-core.js');
const ej = [
  ['given-name','nombre',true],['family-name','apellidos',true],['postal-code','codigo_postal',true],
  ['fechaNacimiento','fecha_nacimiento'],['apellidoPaterno','apellido_paterno'],['apellidoMaterno','apellido_materno'],
  ['Nombre completo','nombre_completo'],['CURP','curp'],['C.P.','codigo_postal'],['Correo electrónico','correo'],
  ['street-address','domicilio_completo',true],['código de verificación',null],['password',null],
  ['captcha',null],['numExterior','numero_exterior'],['municipio','municipio'],['gender','sexo'],
  ['nombre de usuario',null],['tarjeta de crédito',null],['confirmar email',null],['dateOfBirth','fecha_nacimiento']
];
for (const [s,expected,autocomplete] of ej) test(`Clasifica ${s}`,()=>assert.equal(core.clasificar(s,{autocomplete}),expected));
test('No adivina nombre de pila por nombre completo',()=>assert.equal(core.valorPara('nombre',{nombre_completo:'PERSONA PRUEBA'}),null));
test('No infiere código postal faltante',()=>assert.equal(core.valorPara('codigo_postal',{domicilio_completo:'Calle Ejemplo 3'}),null));
test('Combina apellidos confirmados',()=>assert.equal(core.normalizarPerfil({apellido_paterno:'GARCÍA',apellido_materno:'PÉREZ'}).apellidos,'GARCÍA PÉREZ'));
test('Etiqueta NOMBRE de INE se trata como nombre completo',()=>assert.deepEqual(core.datosDesdeDocumento({campos:[{etiqueta:'NOMBRE',valor:'PERSONA SINTÉTICA'}]}),{nombre_completo:'PERSONA SINTÉTICA'}));
test('Etiquetas con valores en blanco no se procesan',()=>assert.deepEqual(core.datosDesdeDocumento({campos:[{etiqueta:'CURP',valor:''}]}),{}));
test('Perfil solo admite claves conocidas',()=>assert.deepEqual(core.normalizarPerfil({password:'secret',nombre:'Ana'}),{nombre:'Ana',nombre_completo:'Ana'}));
