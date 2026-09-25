/** Texto de términos y aviso de privacidad (tratamiento de datos personales y documentos oficiales). */
export const TERMINOS_HTML = `
<p>Al registrarte en <b>OCR Platform</b> aceptas que:</p>
<ol>
  <li><b>Finalidad.</b> Tus documentos de identidad (INE, CURP, acta de nacimiento y pasaporte) y los datos extraídos se usan
  únicamente para resguardarlos en tu bóveda personal, permitirte consultarlos, reutilizarlos en trámites y avisarte de su vigencia.</li>
  <li><b>Procesamiento local.</b> El OCR y la inteligencia artificial se ejecutan en servidores propios del proyecto; tus documentos
  no se envían a servicios de IA de terceros.</li>
  <li><b>Seguridad.</b> Los archivos y los datos sensibles (CURP, domicilio, datos extraídos) se almacenan cifrados con AES-256.
  Las contraseñas se guardan con hash (bcrypt) y el acceso requiere un segundo factor enviado a tu correo.</li>
  <li><b>Validación.</b> La información extraída automáticamente no se considera definitiva hasta que tú la revisas y confirmas.</li>
  <li><b>Alcance.</b> Solo se aceptan documentos impresos o digitales legibles en los formatos vigentes; no se procesan documentos manuscritos.
  La constancia CURP debe tener una antigüedad máxima de 3 meses.</li>
  <li><b>Titularidad.</b> Solo tú puedes consultar tus documentos. El personal administrador únicamente ve información básica de tu cuenta
  (correo, estado y fecha de registro) con datos sensibles enmascarados.</li>
  <li><b>Derechos ARCO.</b> Puedes solicitar el acceso, rectificación, cancelación u oposición al tratamiento de tus datos personales
  conforme a la Ley Federal de Protección de Datos Personales en Posesión de los Particulares.</li>
  <li><b>Prototipo académico.</b> Este sistema es un Trabajo Terminal de la ESCOM-IPN y no sustituye la validación oficial de las
  autoridades emisoras de los documentos.</li>
</ol>`;
