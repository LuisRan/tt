/** Motor local y determinista. Nunca consulta servicios externos ni inventa identidad. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.AutofillCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const CLAVES = ['nombre','apellido_paterno','apellido_materno','apellidos','nombre_completo','curp',
    'fecha_nacimiento','dia_nacimiento','mes_nacimiento','anio_nacimiento','estado_nacimiento','sexo',
    'calle','numero_exterior','numero_interior','colonia','codigo_postal','municipio','estado',
    'domicilio_completo','direccion_linea1','correo'];
  function normalizar(v) {
    return String(v == null ? '' : v).replace(/([a-záéíóúñ])([A-ZÁÉÍÓÚÑ])/g, '$1 $2')
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .replace(/([a-zA-Z])(\d)/g, '$1 $2').replace(/(\d)([a-zA-Z])/g, '$1 $2')
      .toLowerCase().replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
  }
  const negativo = /\b(contrasena|password|passwd|pwd|otp|one time|verification code|codigo de verificacion|codigo de seguridad|security code|cvv|cvc|tarjeta|credit card|debit card|card number|captcha|token|nip|pin|bank|bancaria|iban|clabe|account number|cuenta bancaria|confirm|confirmar|confirmacion|repetir|repeat|usuario|username|search|busqueda|mensaje|message|empresa|company|referencia|reference|destinatario|recipient|beneficiario|padre|madre|father|mother|conyuge|spouse|tutor|emergencia|emergency|estado civil|marital|nacionalidad|nationality|nacimiento registro)\b/;
  const autoExacto = {
    'given-name':'nombre', 'family-name':'apellidos', name:'nombre_completo', email:'correo',
    bday:'fecha_nacimiento', 'bday-day':'dia_nacimiento', 'bday-month':'mes_nacimiento', 'bday-year':'anio_nacimiento',
    sex:'sexo', 'street-address':'domicilio_completo', 'address-line1':'direccion_linea1',
    'address-level2':'municipio', 'address-level1':'estado', 'postal-code':'codigo_postal'
  };
  function tokenAutocomplete(texto) {
    const tokens = String(texto || '').toLowerCase().trim().split(/\s+/);
    // Ignorar section-*, shipping/billing y webauthn; conservar el token de campo real.
    return tokens.find(t => Object.hasOwn(autoExacto,t) || /^(?:cc-|tel|organization|additional-name|country|username|nickname|impp|photo|url|language|transaction-|new-password|current-password|one-time-code)/.test(t)) || '';
  }
  function autocompleteSensible(texto) {
    const tokens=String(texto || '').toLowerCase().trim().split(/\s+/);
    const campos=tokens.filter(t=>Object.hasOwn(autoExacto,t));
    return new Set(campos).size>1 || tokens.some(t=>{const k=tokenAutocomplete(t);return !!k && !Object.hasOwn(autoExacto,k);});
  }
  const patrones = [
    ['curp', /\bcurp\b|\bclave unica de registro de poblacion\b/],
    ['correo', /\b(email|e mail|correo(?: electronico)?|mail address)\b/],
    ['estado_nacimiento', /\b(?:estado|entidad(?: federativa)?|provincia|state|province) (?:de |del |of )?(?:nacimiento|birth)\b|\b(?:birth|nacimiento) (?:state|estado|entidad)\b/],
    ['dia_nacimiento', /\b(?:dia|day) (?:de |del |of )?(?:nacimiento|birth|bday)\b|\b(?:birth|bday|nacimiento) (?:day|dia)\b/],
    ['mes_nacimiento', /\b(?:mes|month) (?:de |del |of )?(?:nacimiento|birth|bday)\b|\b(?:birth|bday|nacimiento) (?:month|mes)\b/],
    ['anio_nacimiento', /\b(?:ano|anio|year) (?:de |del |of )?(?:nacimiento|birth|bday)\b|\b(?:birth|bday|nacimiento) (?:year|ano|anio)\b/],
    ['fecha_nacimiento', /\b(fecha de nacimiento|fecha nacimiento|birth date|date of birth|dob|birthday|nacimiento fecha|birthdate|fec nac|fecha nac|f nacimiento|f nac)\b/],
    ['apellido_paterno', /\b(apellido paterno|primer apellido|first surname|last name 1|paternal surname|surname 1|apellido 1|ape paterno|ap paterno|paterno)\b/],
    ['apellido_materno', /\b(apellido materno|segundo apellido|second surname|maternal surname|surname 2|apellido 2|last name 2|ape materno|ap materno|materno)\b/],
    ['nombre_completo', /\b(nombre completo|full name|complete name|nombre y apellidos|titular completo)\b/],
    ['apellidos', /\b(apellidos|surnames|family names?|last name|lastname|surname)\b/],
    ['nombre', /\b(nombres|nombre|first names?|given names?|forename|nom)\b/],
    ['codigo_postal', /\b(codigo postal|cod postal|postal code|zip code|zipcode|zip|cp|c p)\b/],
    ['numero_exterior', /\b(numero exterior|num exterior|no exterior|external number|street number|numero de casa|num ext|no ext)\b/],
    ['numero_interior', /\b(numero interior|num interior|no interior|apartment number|apto|interior|num int|no int)\b/],
    ['colonia', /\b(colonia|neighborhood|neighbourhood|suburb|barrio)\b/],
    ['municipio', /\b(municipio|alcaldia|delegacion|city|ciudad|localidad|town)\b/],
    ['estado', /\b(estado|state|province|provincia|entidad federativa|entidad)\b/],
    ['direccion_linea1', /\b(calle y numero|address line 1)\b/],
    ['calle', /\b(calle|street name|street|vialidad)\b/],
    ['domicilio_completo', /\b(domicilio|direccion completa|full address|street address|direccion|address)\b/],
    ['sexo', /\b(sexo|genero|gender|sex)\b/]
  ];
  function clasificar(texto, { autocomplete = false } = {}) {
    if (autocomplete) return autoExacto[tokenAutocomplete(texto)] || null;
    const s = normalizar(texto);
    if (!s || negativo.test(s)) return null;
    for (const [clave, re] of patrones) if (re.test(s)) return clave;
    return null;
  }
  function campoDesdeSenales(senales) {
    if (autocompleteSensible(senales.autocomplete)) return null;
    // Las señales del propio control pueden vetar el relleno; el texto de todo un formulario no.
    if (['label','aria','name','id','placeholder','data'].some(k => negativo.test(normalizar(senales[k])))) return null;
    const evidencia = [];
    const pesos = {autocomplete:100,label:90,aria:88,data:76,name:66,id:62,placeholder:55,contexto:30};
    for (const [k,peso] of Object.entries(pesos)) {
      const texto = senales[k];
      let campo = clasificar(texto,{autocomplete:k==='autocomplete'});
      const nacimiento = /\b(nacimiento|birth|bday|dob)\b/.test(normalizar(senales.grupo));
      if (!campo && nacimiento) {
        const t = normalizar(texto).replace(/^(txt|cmb|ddl|sel|input) /,'');
        if (/^(dia|day|dd)$/.test(t)) campo='dia_nacimiento';
        if (/^(mes|month|mm)$/.test(t)) campo='mes_nacimiento';
        if (/^(ano|anio|year|yyyy|aaaa)$/.test(t)) campo='anio_nacimiento';
      }
      if (campo) evidencia.push({campo,peso});
    }
    if (!evidencia.length) return null;
    evidencia.sort((a,b)=>b.peso-a.peso);
    let campo = evidencia[0].campo;
    // family-name describe apellidos, pero una etiqueta explícita puede indicar cuál de los dos.
    if (campo === 'apellidos') {
      const especifico = evidencia.find(x=>x.peso>=88 && /^apellido_/.test(x.campo));
      if (especifico) campo=especifico.campo;
    }
    const fuertes = evidencia.filter(x=>x.peso>=88);
    const compatible = x => x.campo===campo || (x.campo==='apellidos' && /^apellido_/.test(campo));
    if (fuertes.some(x=>!compatible(x))) return null;
    const grupo = normalizar(senales.grupo);
    const nacimiento = /\b(nacimiento|birth|bday|dob)\b/.test(grupo);
    const domicilio = /\b(domicilio|direccion|address|codigo postal|postal code)\b/.test(grupo);
    if (campo==='estado' && nacimiento && !tokenAutocomplete(senales.autocomplete)) {
      const propio=normalizar([senales.label,senales.aria,senales.name,senales.id,senales.contexto].filter(Boolean).join(' '));
      if (/\b(domicilio|residencia|residence|address)\b/.test(propio)) return 'estado';
      if (domicilio) return null; // Un formulario mixto sin etiqueta precisa requiere revisión humana.
      campo='estado_nacimiento';
    }
    return campo;
  }
  const alias = {
    nombre:['nombre','nombres','given_name','first_name','givenName','firstName'],
    apellido_paterno:['apellido_paterno','primer_apellido','paternal_surname','first_surname','apellidoPaterno'],
    apellido_materno:['apellido_materno','segundo_apellido','maternal_surname','second_surname','apellidoMaterno'],
    nombre_completo:['nombre_completo','full_name','fullName'], apellidos:['apellidos','last_name','family_name','surnames'],
    curp:['curp'],correo:['correo','email','correo_electronico'],
    fecha_nacimiento:['fecha_nacimiento','birth_date','date_of_birth','dateOfBirth','fechaNacimiento'],
    dia_nacimiento:['dia_nacimiento','birth_day'],mes_nacimiento:['mes_nacimiento','birth_month'],anio_nacimiento:['anio_nacimiento','año_nacimiento','birth_year'],
    estado_nacimiento:['estado_nacimiento','entidad_nacimiento','entidad_federativa_nacimiento','birth_state','state_of_birth'],
    sexo:['sexo','genero','gender','sex'],calle:['calle','street'],colonia:['colonia','neighborhood'],
    numero_exterior:['numero_exterior','num_exterior'],numero_interior:['numero_interior','num_interior'],
    codigo_postal:['codigo_postal','cp','postal_code','zip'],municipio:['municipio','ciudad','city','alcaldia'],
    estado:['estado','entidad_federativa','state'],direccion_linea1:['direccion_linea1','domicilio_calle','address_line1'],
    domicilio_completo:['domicilio_completo','domicilio','direccion_completa','full_address']
  };
  const claveCanonica = new Map(Object.entries(alias).flatMap(([clave,nombres])=>nombres.map(n=>[normalizar(n),clave])));
  function fechaPartes(v) {
    const s=String(v||'').trim();
    let m=s.match(/^(\d{4})-(\d{2})-(\d{2})$/), anio,mes,dia;
    if (m) [,anio,mes,dia]=m;
    else { m=s.match(/^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$/); if (!m) return null; [,dia,mes,anio]=m; }
    const a=Number(anio),b=Number(mes),c=Number(dia);
    const d=new Date(Date.UTC(a,b-1,c));
    if (a<1000 || a>9999 || d.getUTCFullYear()!==a || d.getUTCMonth()!==b-1 || d.getUTCDate()!==c) return null;
    return {anio:String(a),mes:String(b).padStart(2,'0'),dia:String(c).padStart(2,'0'),iso:`${anio}-${String(b).padStart(2,'0')}-${String(c).padStart(2,'0')}`};
  }
  function completar(salida) {
    if (!salida.apellidos) salida.apellidos=[salida.apellido_paterno,salida.apellido_materno].filter(Boolean).join(' ') || undefined;
    if (!salida.nombre_completo && salida.nombre) salida.nombre_completo=[salida.nombre,salida.apellido_paterno,salida.apellido_materno].filter(Boolean).join(' ');
    if (!salida.direccion_linea1 && salida.domicilio_completo) salida.direccion_linea1=salida.domicilio_completo;
    const f=fechaPartes(salida.fecha_nacimiento);
    if (f) { salida.fecha_nacimiento=f.iso; salida.dia_nacimiento=f.dia; salida.mes_nacimiento=f.mes; salida.anio_nacimiento=f.anio; }
    for (const k of Object.keys(salida)) if (!salida[k]) delete salida[k];
    return salida;
  }
  function datosDesdeDocumento(doc) {
    const salida={};
    if (!doc || !Array.isArray(doc.campos)) return salida;
    for (const c of doc.campos) {
      if (!c || typeof c.valor!=='string' || !c.valor.trim()) continue;
      // La clave del contrato API tiene prioridad absoluta: no interpretar campos de padres/registro como del titular.
      const tipado=typeof c.campo==='string' && c.campo.trim()!=='';
      let clave=tipado ? claveCanonica.get(normalizar(c.campo)) : clasificar(c.etiqueta);
      if (!tipado && clave==='nombre' && /^(nombre|name)$/.test(normalizar(c.etiqueta))) clave='nombre_completo';
      if (!clave) continue;
      // INE: estos campos son códigos electorales; no convertirlos en domicilio o nacimiento.
      if (String(doc.tipo).toLowerCase()==='ine' && ['estado','municipio'].includes(clave) && /^\d+$/.test(c.valor.trim())) continue;
      if (!salida[clave]) salida[clave]=c.valor.trim().slice(0,300);
    }
    return completar(salida);
  }
  function normalizarPerfil(obj) {
    const salida={};
    if (!obj || typeof obj!=='object' || Array.isArray(obj)) return salida;
    if (Array.isArray(obj.campos)) Object.assign(salida,datosDesdeDocumento(obj));
    for (const clave of CLAVES) for (const k of alias[clave] || [clave]) {
      if (Object.hasOwn(obj,k) && ['string','number'].includes(typeof obj[k])) {
        const v=String(obj[k]).trim(); if (v && !salida[clave]) salida[clave]=v.slice(0,300);
      }
    }
    return completar(salida);
  }
  function valorPara(clave,datos) {
    if (!CLAVES.includes(clave)) return null;
    let v=datos?.[clave];
    if (['dia_nacimiento','mes_nacimiento','anio_nacimiento'].includes(clave)) {
      const f=fechaPartes(datos?.fecha_nacimiento);
      if (f) v=f[{dia_nacimiento:'dia',mes_nacimiento:'mes',anio_nacimiento:'anio'}[clave]];
      else if (datos?.fecha_nacimiento) return null;
    }
    if (clave==='fecha_nacimiento') return fechaPartes(v)?.iso || null;
    return typeof v==='string' && v.trim() ? v.trim() : null;
  }
  const meses=['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'];
  const months=['january','february','march','april','may','june','july','august','september','october','november','december'];
  const entidades=[
    ['AS','AGS','Aguascalientes'],['BC','Baja California'],['BS','BCS','Baja California Sur'],['CC','CAMP','Campeche'],
    ['CL','COAH','Coahuila','Coahuila de Zaragoza'],['CM','COL','Colima'],['CS','CHIS','Chiapas'],['CH','CHIH','Chihuahua'],
    ['DF','CMX','CDMX','Ciudad de México','Distrito Federal'],['DG','DGO','Durango'],['GT','GTO','Guanajuato'],
    ['GR','GRO','Guerrero'],['HG','HGO','Hidalgo'],['JC','JAL','Jalisco'],['MC','MEX','México','Estado de México'],
    ['MN','MICH','Michoacán','Michoacán de Ocampo'],['MS','MOR','Morelos'],['NT','NAY','Nayarit'],['NL','Nuevo León'],
    ['OC','OAX','Oaxaca'],['PL','PUE','Puebla'],['QT','QRO','Querétaro','Querétaro de Arteaga'],['QR','ROO','Quintana Roo'],
    ['SP','SLP','San Luis Potosí'],['SL','SIN','Sinaloa'],['SR','SON','Sonora'],['TC','TAB','Tabasco'],
    ['TS','TAMPS','Tamaulipas'],['TL','TLAX','Tlaxcala'],['VZ','VER','Veracruz','Veracruz de Ignacio de la Llave'],
    ['YN','YUC','Yucatán'],['ZS','ZAC','Zacatecas'],['NE','Nacido en el extranjero','Nacidos en el extranjero','Extranjero']
  ];
  function entidad(v) { const s=normalizar(v); return entidades.findIndex(xs=>xs.some(x=>normalizar(x)===s)); }
  // En el contrato mexicano del backend: H=hombre, M=mujer. Nunca interpretar M como masculino por defecto.
  function sexo(v) {
    const s=normalizar(v);
    if (/^(h|hombre|masculino|male|man)$/.test(s)) return 'hombre';
    if (/^(m|f|mujer|femenino|female|woman)$/.test(s)) return 'mujer';
    return null;
  }
  function esPlaceholder(o) { return !o || o.disabled || !String(o.value??'').trim() || /^[–—.\s-]+$/.test(String(o.text||'')) || /^(seleccion\w*|select\w*|choose|elige|elija|escoja|sin seleccionar|no seleccionado)(\b|$)|^[-–—.\s]+$/.test(normalizar(o.text)); }
  function numeroMes(v) {
    const s=normalizar(v); if (/^\d{1,2}$/.test(s)) return Number(s);
    let i=meses.findIndex((m,n)=>s===m || s===months[n] || s===m.slice(0,3) || s===months[n].slice(0,3));
    if (s==='setiembre') i=8;
    if (i>=0) return i+1;
    const m=s.match(/^(\d{1,2}) (.+)$/); return m && numeroMes(m[2])===Number(m[1]) ? Number(m[1]) : null;
  }
  function elegirOpcion(clave,valor,opciones) {
    const opts=opciones.filter(o=>!esPlaceholder(o));
    const n=normalizar(valor);
    let candidatas=[];
    if (clave==='sexo') {
      const s=sexo(valor); if (!s) return null;
      const codigos=new Set(opts.map(o=>normalizar(o.value)));
      const letras=codigos.has('h') && codigos.has('m') ? {h:'hombre',m:'mujer'} : codigos.has('m') && codigos.has('f') ? {m:'hombre',f:'mujer'} : {h:'hombre',f:'mujer'};
      candidatas=opts.filter(o=> {
        const t=normalizar(o.text);
        const explicito=/^(hombre|masculino|male|man|mujer|femenino|female|woman)$/.test(t) ? sexo(t) : null;
        return (explicito || letras[normalizar(o.value)] || (/^(h|f)$/.test(t)?sexo(t):null))===s;
      });
    } else if (['dia_nacimiento','mes_nacimiento','anio_nacimiento'].includes(clave)) {
      const target=Number(valor), max=clave==='dia_nacimiento'?31:clave==='mes_nacimiento'?12:9999;
      if (!/^\d+$/.test(String(valor)) || target<1 || target>max || (clave==='anio_nacimiento' && target<1000)) return null;
      candidatas=opts.filter(o=>{
        const text=normalizar(o.text);
        const num=clave==='mes_nacimiento'?numeroMes(text):/^\d+$/.test(text)?Number(text):null;
        // Etiquetas numéricas/meses prevalecen: soporta valores 0..11 y códigos opacos sin confundir meses.
        if (num!==null) return num===target;
        return /^\d+$/.test(o.value) && Number(o.value)===target;
      });
    } else if (['estado','estado_nacimiento'].includes(clave)) {
      const e=entidad(valor);
      candidatas=opts.filter(o=>normalizar(o.text)===n || normalizar(o.value)===n || (e>=0 && (entidad(o.text)===e || entidad(o.value)===e)));
    } else {
      candidatas=opts.filter(o=>normalizar(o.text)===n || normalizar(o.value)===n);
    }
    return candidatas.length===1 ? String(candidatas[0].value) : null;
  }
  return {normalizar,clasificar,campoDesdeSenales,normalizarPerfil,datosDesdeDocumento,valorPara,fechaPartes,
    esPlaceholder,elegirOpcion,sexo,CLAVES};
});
