/** Texto de términos y aviso de privacidad (tratamiento de datos personales y documentos oficiales). */
function textoPublico(valor: unknown) {
  return String(valor ?? '').trim().replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] ?? ''));
}
const responsable = textoPublico(import.meta.env.PUBLIC_PRIVACIDAD_RESPONSABLE);
const domicilio = textoPublico(import.meta.env.PUBLIC_PRIVACIDAD_DOMICILIO);
const contacto = textoPublico(import.meta.env.PUBLIC_PRIVACIDAD_CONTACTO_ARCO);
const conservacion = textoPublico(import.meta.env.PUBLIC_PRIVACIDAD_CONSERVACION);
export const TERMINOS_HTML = `
<p>Al registrarte en <b>OCR Platform</b> aceptas que:</p>
<ol>
  ${responsable ? `<li><b>Responsable.</b> ${responsable}${domicilio ? ' · ' + domicilio : ''}</li>` : ''}
  <li><b>Finalidad.</b> Tus documentos de identidad (INE, CURP, acta de nacimiento y pasaporte) y los datos extraídos se usan
  únicamente para resguardarlos en tu bóveda personal, permitirte consultarlos, reutilizarlos en trámites y avisarte de su vigencia.</li>
  <li><b>Procesamiento local.</b> El OCR y la inteligencia artificial se ejecutan en la infraestructura contratada y administrada por el proyecto; tus documentos
  no se envían a servicios de IA de terceros.</li>
  <li><b>Seguridad.</b> Los archivos y los valores protegidos (CURP, domicilio y datos extraídos) se cifran con AES-256-GCM;
  su lectura autorizada y procesamiento requieren descifrarlos en memoria. Otros datos de cuenta y metadatos se conservan con acceso restringido.
  Las contraseñas se guardan con hash (bcrypt) y puedes activar un segundo factor enviado a tu correo desde Configuración.</li>
  <li><b>Validación.</b> La información extraída automáticamente no se considera definitiva hasta que tú la revisas y confirmas.</li>
  <li><b>Alcance.</b> Solo se aceptan documentos impresos o digitales legibles en los formatos vigentes; no se procesan documentos manuscritos.
  La constancia CURP debe tener una antigüedad máxima de 3 meses.</li>
  <li><b>Titularidad.</b> Tu cuenta controla el acceso a tus documentos. El panel administrativo limita los datos mostrados y protege CURP y domicilio;
  el personal con acceso técnico a la infraestructura puede acceder al sistema para su operación y soporte, bajo controles y deber de confidencialidad.</li>
  <li><b>Derechos ARCO.</b> Puedes solicitar el acceso, rectificación, cancelación u oposición al tratamiento de tus datos personales.
  ${contacto ? 'Dirige la solicitud a ' + contacto + '. Indica el derecho que deseas ejercer. El responsable te indicará el medio para acreditar tu identidad.' : ''}</li>
  ${conservacion ? `<li><b>Conservación.</b> ${conservacion}</li>` : ''}
  <li><b>Extensión y proveedores.</b> Al usar el autocompletado, el sitio abierto recibe los campos que autorizas.
  El proveedor del VPS aloja los servicios. El proveedor de correo recibe la dirección y el contenido necesario para entregar los mensajes de la plataforma.</li>
  <li><b>Prototipo académico.</b> Este sistema es un Trabajo Terminal de la ESCOM-IPN y no sustituye la validación oficial de las
  autoridades emisoras de los documentos.</li>
</ol>`;
