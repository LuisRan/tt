/** Utilidades de interfaz: toasts, modales, formato y escape de HTML. */

export const esc = (v: unknown): string =>
  String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

export function toast(mensaje: string, tipo: 'info' | 'ok' | 'error' = 'info', ms = 4500): void {
  let cont = document.getElementById('toasts');
  if (!cont) {
    cont = document.createElement('div');
    cont.id = 'toasts';
    document.body.appendChild(cont);
  }
  const t = document.createElement('div');
  t.className = `toast ${tipo}`;
  t.setAttribute('role', 'status');
  t.textContent = mensaje;
  cont.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

export function mostrarError(contenedor: HTMLElement | null, err: any): void {
  if (!contenedor) return toast(err?.message ?? 'Error', 'error');
  const campos = err?.detalles?.campos as Record<string, string> | undefined;
  const lista = campos ? `<ul>${Object.entries(campos).map(([k, v]) => `<li><b>${esc(etiqueta(k))}</b>: ${esc(v)}</li>`).join('')}</ul>` : '';
  contenedor.innerHTML = `<div class="alerta alerta-error">${esc(err?.message ?? 'Ocurrió un error')}${lista}</div>`;
}

export function limpiar(contenedor: HTMLElement | null): void {
  if (contenedor) contenedor.innerHTML = '';
}

export function abrirModal(html: string, ancho = false): { cerrar: () => void; el: HTMLElement } {
  const fondo = document.createElement('div');
  fondo.className = 'modal-fondo';
  fondo.innerHTML = `<div class="card modal ${ancho ? 'modal-ancho' : ''}" role="dialog" aria-modal="true">${html}</div>`;
  const cerrar = () => fondo.remove();
  fondo.addEventListener('click', (e) => e.target === fondo && cerrar());
  document.addEventListener('keydown', function esc(e) {
    if (e.key === 'Escape') {
      cerrar();
      document.removeEventListener('keydown', esc);
    }
  });
  document.body.appendChild(fondo);
  return { cerrar, el: fondo.querySelector('.modal') as HTMLElement };
}

export function confirmar(titulo: string, mensaje: string, textoOk = 'Confirmar', peligro = false): Promise<boolean> {
  return new Promise((resolve) => {
    const m = abrirModal(`
      <h2>${esc(titulo)}</h2><p class="muted">${esc(mensaje)}</p>
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:20px">
        <button class="btn btn-borde" data-no>Cancelar</button>
        <button class="btn ${peligro ? 'btn-peligro' : 'btn-primario'}" data-si>${esc(textoOk)}</button>
      </div>`);
    m.el.querySelector('[data-no]')!.addEventListener('click', () => (m.cerrar(), resolve(false)));
    m.el.querySelector('[data-si]')!.addEventListener('click', () => (m.cerrar(), resolve(true)));
  });
}

/** Reautenticación para operaciones sensibles (CU-02, último paso). */
export function pedirPassword(): Promise<boolean> {
  return new Promise((resolve) => {
    const m = abrirModal(`
      <h2>Confirma tu contraseña</h2>
      <p class="muted">Por seguridad, las operaciones con documentos oficiales requieren confirmar tu contraseña.</p>
      <form data-form>
        <div data-error></div>
        <div class="campo"><label for="reauth-pass">Contraseña</label>
          <input id="reauth-pass" type="password" autocomplete="current-password" required></div>
        <div style="display:flex;gap:10px;justify-content:flex-end">
          <button type="button" class="btn btn-borde" data-no>Cancelar</button>
          <button class="btn btn-primario">Confirmar</button>
        </div>
      </form>`);
    const input = m.el.querySelector('input') as HTMLInputElement;
    input.focus();
    let resuelto = false;
    m.el.querySelector('[data-no]')!.addEventListener('click', () => {
      resuelto = true;
      m.cerrar();
      resolve(false);
    });
    m.el.querySelector('[data-form]')!.addEventListener('submit', async (e) => {
      e.preventDefault();
      const r = await fetch('/api/auth/confirmar-password', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: input.value }),
      });
      if (r.ok) {
        resuelto = true;
        m.cerrar();
        resolve(true);
      } else {
        const d = await r.json().catch(() => ({}));
        (m.el.querySelector('[data-error]') as HTMLElement).innerHTML = `<div class="alerta alerta-error">${esc(d.error ?? 'Contraseña incorrecta')}</div>`;
      }
    });
    const obs = new MutationObserver(() => {
      if (!document.body.contains(m.el) && !resuelto) {
        obs.disconnect();
        resolve(false);
      }
    });
    obs.observe(document.body, { childList: true });
  });
}

export const fecha = (v: string | null | undefined, conHora = false) => {
  if (!v) return '—';
  const d = /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(`${v}T12:00:00`) : new Date(v);
  return conHora
    ? d.toLocaleString('es-MX', { dateStyle: 'short', timeStyle: 'short' })
    : d.toLocaleDateString('es-MX', { day: '2-digit', month: '2-digit', year: 'numeric' });
};

export const haceTiempo = (v: string) => {
  const s = Math.round((Date.now() - new Date(v).getTime()) / 1000);
  if (s < 60) return 'Hace un momento';
  if (s < 3600) return `Hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `Hace ${Math.round(s / 3600)} h`;
  return `Hace ${Math.round(s / 86400)} días`;
};

export const ESTADOS: Record<string, string> = {
  cargado: 'En cola',
  procesando: 'En proceso',
  procesado: 'Por validar',
  validado: 'Validado',
  rechazado: 'Rechazado',
  error: 'Error',
};

export const estadoHtml = (e: string) => `<span class="estado estado-${esc(e)}">${esc(ESTADOS[e] ?? e)}</span>`;

const ETIQUETAS: Record<string, string> = {
  nombre_completo: 'Nombre completo',
  nombres: 'Nombre(s)',
  primer_apellido: 'Primer apellido',
  segundo_apellido: 'Segundo apellido',
  curp: 'CURP',
  fecha_nacimiento: 'Fecha de nacimiento',
  sexo: 'Sexo (H/M)',
  clave_elector: 'Clave de elector',
  domicilio: 'Domicilio',
  domicilio_calle: 'Calle y número',
  domicilio_colonia: 'Colonia y C.P.',
  domicilio_municipio_ciudad: 'Municipio / ciudad',
  estado: 'Estado (código)',
  municipio: 'Municipio (código)',
  localidad: 'Localidad',
  seccion: 'Sección',
  año_registro: 'Año de registro',
  año_emision: 'Año de emisión',
  año_vigencia: 'Año de vigencia',
  folio_impresion: 'Folio de impresión',
  identificador_electronico: 'Identificador electrónico',
  numero_certificado_nac: 'No. certificado de nacimiento',
  numero_acta: 'Número de acta',
  entidad_registro: 'Entidad de registro',
  municipio_registro: 'Municipio de registro',
  fecha_registro: 'Fecha de registro',
  oficialia: 'Oficialía',
  libro: 'Libro',
  lugar_nacimiento: 'Lugar de nacimiento',
  nombre_padre: 'Nombre del padre',
  primer_apellido_padre: 'Primer apellido del padre',
  segundo_apellido_padre: 'Segundo apellido del padre',
  nacionalidad_padre: 'Nacionalidad del padre',
  curp_padre: 'CURP del padre',
  nombre_madre: 'Nombre de la madre',
  primer_apellido_madre: 'Primer apellido de la madre',
  segundo_apellido_madre: 'Segundo apellido de la madre',
  nacionalidad_madre: 'Nacionalidad de la madre',
  curp_madre: 'CURP de la madre',
  numero_pasaporte: 'Número de pasaporte',
  tipo_pasaporte: 'Tipo',
  pais_expedicion: 'País de expedición',
  nacionalidad: 'Nacionalidad',
  fecha_expedicion: 'Fecha de expedición',
  fecha_caducidad: 'Fecha de caducidad',
  mrz_linea1: 'MRZ línea 1',
  mrz_linea2: 'MRZ línea 2',
  observaciones: 'Observaciones',
  fecha_inscripcion: 'Fecha de inscripción',
  folio: 'Folio',
  curps_asociadas: 'CURPs asociadas',
  codigo_verificacion: 'Código de verificación',
  fecha_emision: 'Fecha de emisión de la constancia',
};

export const etiqueta = (campo: string) => ETIQUETAS[campo] ?? campo.replace(/_/g, ' ');
export const ORDEN_CAMPOS = Object.keys(ETIQUETAS);
