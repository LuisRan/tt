/**
 * Motor determinista de campos: sin conexión a red y sin inferir datos personales.
 * Scoring: señales HTML autocomplete > label/aria > name/id > placeholder.
 * Criterios estrictos y palabras negativas para minimizar falsos positivos.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.AutofillCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const CLAVES = ['nombre','apellido_paterno','apellido_materno','apellidos','nombre_completo','curp','fecha_nacimiento','sexo','calle','numero_exterior','numero_interior','colonia','codigo_postal','municipio','estado','domicilio_completo','correo'];
  function normalizar(v) {
    return String(v == null ? '' : v).replace(/([a-záéíóúñ])([A-ZÁÉÍÓÚÑ])/g, '$1 $2')
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .replace(/([a-zA-Z])(\d)/g, '$1 $2').replace(/(\d)([a-zA-Z])/g, '$1 $2')
      .toLowerCase().replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
  }
  const negative = /\b(contrasena|password|passwd|pwd|otp|one time|verification code|codigo de verificacion|codigo de seguridad|security code|cvv|cvc|tarjeta|credit card|debit card|card number|captcha|token|nip|pin|bank|bancaria|iban|clabe|account number|cuenta bancaria|confirm|confirmar|confirmacion|repetir|repeat|usuario|username|search|busqueda|mensaje|message|empresa|company|referencia|reference|destinatario|recipient|beneficiario)\b/;
  const patrones = [
    ['curp', /\bcurp\b|\bclave unica de registro de poblacion\b/],
    ['correo', /\b(email|e mail|correo electronico|mail address|email address)\b/],
    ['fecha_nacimiento', /\b(fecha de nacimiento|fecha nacimiento|birth date|date of birth|dob|birthday|nacimiento fecha|birthdate)\b/],
    ['apellido_paterno', /\b(apellido paterno|primer apellido|first surname|last name 1|paternal surname|surname1|apellido 1)\b/],
    ['apellido_materno', /\b(apellido materno|segundo apellido|second surname|maternal surname|surname2|apellido 2|last name 2)\b/],
    ['nombre_completo', /\b(nombre completo|full name|complete name|nombre y apellidos|titular completo)\b/],
    ['apellidos', /\b(apellidos|surnames|family name|family names|last name|lastname|surname)\b/],
    ['nombre', /\b(nombres|nombre|first name|first names|given name|given names|forename)\b/],
    ['codigo_postal', /\b(codigo postal|cod postal|postal code|zip code|zipcode|zip|cp|c p)\b/],
    ['numero_exterior', /\b(numero exterior|num exterior|no exterior|external number|street number|numero de casa)\b/],
    ['numero_interior', /\b(numero interior|num interior|no interior|apartment number|apto|interior)\b/],
    ['colonia', /\b(colonia|neighborhood|neighbourhood|suburb|barrio)\b/],
    ['municipio', /\b(municipio|alcaldia|delegacion|city|ciudad|localidad|town)\b/],
    ['estado', /\b(estado|state|province|provincia|entidad federativa)\b/],
    ['calle', /\b(calle|street name|street|vialidad)\b/],
    ['domicilio_completo', /\b(domicilio|direccion completa|full address|street address|address line 1|direccion|address)\b/],
    ['sexo', /\b(sexo|genero|gender|sex)\b/],
  ];
  const autoExacto = {
    'given name': 'nombre', 'additional name': null, 'family name': 'apellidos', 'name': 'nombre_completo',
    'email': 'correo', 'bday': 'fecha_nacimiento', 'sex': 'sexo', 'street address': 'domicilio_completo',
    'address line1': 'domicilio_completo', 'address level2': 'municipio', 'address level1': 'estado',
    'postal code': 'codigo_postal', 'address line2': 'numero_interior'
  };
  function clasificar(texto, { autocomplete = false } = {}) {
    const s = normalizar(texto);
    if (!s || negative.test(s)) return null;
    if (autocomplete) {
      const tokens = s.split(' ').filter(t => !/^section\w*$/.test(t) && !['shipping','billing'].includes(t));
      const val = tokens.join(' ');
      if (Object.prototype.hasOwnProperty.call(autoExacto, val)) return autoExacto[val];
      if (['off','on','new password','current password','one time code','cc number','tel','organization'].includes(val)) return null;
    }
    for (const [clave, re] of patrones) if (re.test(s)) return clave;
    return null;
  }
  function campoDesdeSenales(senales) {
    // etiqueta y autocomplete tienen prioridad; señales discordantes son ignoradas si ambiguas.
    const prioridades = [
      ['autocomplete', 100], ['label', 90], ['aria', 85], ['data', 75],
      ['name', 65], ['id', 60], ['placeholder', 50], ['contexto', 25]
    ];
    const ver = prioridades.map(([k,score]) => ({ key: clasificar(senales[k], { autocomplete:k === 'autocomplete' }), score, texto: normalizar(senales[k]) })).filter(x => x.key);
    if (!ver.length) return null;
    const top = ver[0];
    // Evitar un campo con etiquetas que describen credenciales o confirmaciones, incluso si name=id=nombre.
    if ([senales.label,senales.aria,senales.placeholder].some(s => negative.test(normalizar(s)))) return null;
    return top.key;
  }
  const sinAcento = normalizar;
  function datosDesdeDocumento(doc) {
    const salida = {};
    if (!doc || !Array.isArray(doc.campos)) return salida;
    for (const c of doc.campos) {
      if (!c || typeof c.valor !== 'string' || !c.valor.trim()) continue;
      const clave = clasificar(c.etiqueta);
      if (!clave) continue;
      // En INE/actas, un campo genérico 'NOMBRE' suele contener el nombre completo.
      const destino = clave === 'nombre' ? 'nombre_completo' : clave;
      if (!salida[destino]) salida[destino] = c.valor.trim().slice(0, 300);
    }
    return salida;
  }
  const alias = {
    nombre: ['nombre','nombres','given_name','first_name'],
    apellido_paterno: ['apellido_paterno','primer_apellido','paternal_surname','first_surname'],
    apellido_materno: ['apellido_materno','segundo_apellido','maternal_surname','second_surname'],
    nombre_completo: ['nombre_completo','full_name'],
    apellidos: ['apellidos','last_name','family_name','surnames'],
    curp: ['curp'], correo: ['correo','email','correo_electronico'],
    fecha_nacimiento: ['fecha_nacimiento','birth_date','date_of_birth'], sexo: ['sexo','genero','gender'],
    calle: ['calle','street'], colonia: ['colonia','neighborhood'],
    numero_exterior: ['numero_exterior','num_exterior'], numero_interior: ['numero_interior','num_interior'],
    codigo_postal: ['codigo_postal','cp','postal_code','zip'],
    municipio: ['municipio','ciudad','city','alcaldia'], estado: ['estado','entidad_federativa','state'],
    domicilio_completo: ['domicilio_completo','domicilio','direccion_completa','full_address']
  };
  function normalizarPerfil(obj) {
    const salida = {};
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return salida;
    const campos = Array.isArray(obj.campos) ? obj.campos : null;
    if (campos) Object.assign(salida, datosDesdeDocumento({ campos }));
    for (const clave of CLAVES) {
      for (const k of (alias[clave] || [clave])) {
        if (Object.prototype.hasOwnProperty.call(obj,k) && (typeof obj[k] === 'string' || typeof obj[k] === 'number')) {
          const v = String(obj[k]).trim();
          if (v && !salida[clave]) salida[clave] = v.slice(0, 300);
        }
      }
    }
    if (!salida.apellidos && salida.apellido_paterno && salida.apellido_materno) salida.apellidos = `${salida.apellido_paterno} ${salida.apellido_materno}`;
    if (!salida.nombre_completo && salida.nombre) salida.nombre_completo = [salida.nombre,salida.apellido_paterno,salida.apellido_materno].filter(Boolean).join(' ');
    return salida;
  }
  function valorPara(clave, datos) {
    if (clave === 'nombre' && !datos.nombre) return null; // No dividir nombre completo por conjeturas.
    if (clave === 'apellido_paterno' && !datos.apellido_paterno) return null;
    if (clave === 'apellido_materno' && !datos.apellido_materno) return null;
    if (clave === 'apellidos' && !datos.apellidos) return null;
    const v = datos?.[clave];
    return typeof v === 'string' && v.trim() ? v.trim() : null;
  }
  return { normalizar:sinAcento, clasificar, campoDesdeSenales, normalizarPerfil, datosDesdeDocumento, valorPara, CLAVES };
});
