/**
 * Ventana emergente de la extensión.
 *   Inicio      -> 4 categorías (INE, CURP, acta, pasaporte) + sincronizar
 *   Categoría   -> documentos validados identificados como 0001_CSC
 *   Documento   -> datos con opción de copiar cada uno, copiar todo y descargar .txt
 * Todo se pinta con textContent (sin innerHTML con datos) para evitar inyección.
 */
/* global chrome, Nucleo */

const ICONOS = {
  atras: '<path d="M15 18l-6-6 6-6"/>',
  sync: '<path d="M21 12a9 9 0 0 1-15.4 6.4L3 16"/><path d="M3 12a9 9 0 0 1 15.4-6.4L21 8"/><path d="M21 3v5h-5"/><path d="M3 21v-5h5"/>',
  copiar: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  descargar: '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  credencial: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2.2"/><path d="M5.8 16c.6-1.5 1.8-2.3 3.2-2.3s2.6.8 3.2 2.3"/><path d="M14.5 10h4M14.5 13.5h3"/>',
  documento: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/>',
  acta: '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4"/><circle cx="12" cy="14" r="3"/><path d="M10.5 16.5 9.5 20l2.5-1.2 2.5 1.2-1-3.5"/>',
  pasaporte: '<rect x="5" y="3" width="14" height="18" rx="2"/><circle cx="12" cy="10.5" r="3.2"/><path d="M8.8 10.5h6.4M12 7.3c1 1 1.4 2 1.4 3.2s-.4 2.2-1.4 3.2c-1-1-1.4-2-1.4-3.2s.4-2.2 1.4-3.2"/><path d="M9 17h6"/>',
  enlace: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
};
const svg = (nombre, tam = 18) =>
  `<svg width="${tam}" height="${tam}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONOS[nombre]}</svg>`;

const CATEGORIAS = [
  { tipo: 'ine', nombre: 'INE', icono: 'credencial', desc: 'Credencial para votar' },
  { tipo: 'curp', nombre: 'CURP', icono: 'documento', desc: 'Constancia de la CURP' },
  { tipo: 'acta_nacimiento', nombre: 'Acta de nacimiento', icono: 'acta', desc: 'Formato único' },
  { tipo: 'pasaporte', nombre: 'Pasaporte', icono: 'pasaporte', desc: 'Pasaporte mexicano' },
];

const $ = (s) => document.querySelector(s);
const vista = $('[data-vista]');
let estado = {};
let pila = []; // navegación: [{vista:'inicio'}, {vista:'categoria', tipo}, {vista:'documento', id}]

// ── Utilidades ───────────────────────────────────────────────────────────────
function el(tag, attrs = {}, ...hijos) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v; // solo para íconos SVG constantes
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const h of hijos.flat()) if (h !== null && h !== undefined && h !== false) n.append(h instanceof Node ? h : String(h));
  return n;
}

function toast(texto) {
  const t = $('[data-toast]');
  t.textContent = texto;
  t.classList.remove('oculto');
  clearTimeout(toast.t);
  toast.t = setTimeout(() => t.classList.add('oculto'), 1600);
}

function fecha(iso) {
  if (!iso) return '';
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  return d.toLocaleDateString('es-MX', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function hace(iso) {
  if (!iso) return 'nunca';
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'hace un momento';
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return `el ${fecha(iso)}`;
}

async function copiar(texto, boton) {
  try {
    await navigator.clipboard.writeText(texto);
  } catch {
    const area = el('textarea', {}, texto);
    document.body.append(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
  toast('Copiado');
  if (boton) {
    const previo = boton.innerHTML;
    boton.classList.add('hecho');
    boton.innerHTML = svg('check', 16);
    setTimeout(() => {
      boton.classList.remove('hecho');
      boton.innerHTML = previo;
    }, 1200);
  }
}

function descargarTxt(doc) {
  const blob = new Blob([doc.texto], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  el('a', { href: url, download: `${doc.identificador}.txt` }).click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

const documentos = () => estado.documentos ?? [];
const vencido = (d) => d.fecha_vigencia && new Date(`${d.fecha_vigencia}T23:59:59`) < new Date();

// ── Cabecera / navegación ───────────────────────────────────────────────────
function cabecera(titulo, subtitulo, conAtras) {
  $('[data-titulo]').textContent = titulo;
  $('[data-subtitulo]').textContent = subtitulo ?? '';
  $('[data-atras]').classList.toggle('oculto', !conAtras);
}

function ir(paso) {
  pila.push(paso);
  pintar();
}
function atras() {
  pila.pop();
  if (!pila.length) pila = [{ vista: 'inicio' }];
  pintar();
}

function pintar() {
  const vinculada = !!estado.token;
  $('[data-pie]').classList.toggle('oculto', !vinculada);
  $('[data-sincronizar]').classList.toggle('oculto', !vinculada);
  vista.replaceChildren();
  if (!vinculada) return pintarVincular();
  const actual = pila[pila.length - 1] ?? { vista: 'inicio' };
  if (actual.vista === 'categoria') return pintarCategoria(actual.tipo);
  if (actual.vista === 'documento') return pintarDocumento(actual.id);
  return pintarInicio();
}

// ── Vistas ───────────────────────────────────────────────────────────────────
function pintarInicio() {
  cabecera('Mis documentos', estado.correo ?? '', false);
  const total = documentos().length;
  vista.append(
    el(
      'div',
      { class: 'sync' },
      el('div', {}, el('b', {}, `${total} documento${total === 1 ? '' : 's'} validado${total === 1 ? '' : 's'}`), el('span', { class: 'sub' }, `Sincronizado ${hace(estado.sincronizado_en)}`)),
      el('button', { class: 'btn btn-primario', style: 'flex:none', onclick: sincronizar, html: `${svg('sync', 16)} Sincronizar` }),
    ),
  );
  if (estado.aviso) vista.append(el('div', { class: 'alerta aviso' }, estado.aviso));
  vista.append(
    el(
      'div',
      { class: 'categorias' },
      CATEGORIAS.map((c) => {
        const n = documentos().filter((d) => d.tipo === c.tipo).length;
        return el(
          'button',
          { class: 'categoria', onclick: () => ir({ vista: 'categoria', tipo: c.tipo }) },
          el('span', { class: 'ico', html: svg(c.icono, 20) }),
          el('span', { class: 'nombre' }, c.nombre),
          el('span', { class: 'cuenta' }, n ? `${n} documento${n === 1 ? '' : 's'}` : c.desc),
        );
      }),
    ),
  );
  if (!total) {
    vista.append(
      el('p', { class: 'vacio' }, 'Aún no hay documentos validados. Sube y confirma un documento en la plataforma y pulsa Sincronizar.'),
    );
  }
}

function pintarCategoria(tipo) {
  const cat = CATEGORIAS.find((c) => c.tipo === tipo);
  const docs = documentos().filter((d) => d.tipo === tipo);
  cabecera(cat?.nombre ?? tipo, `${docs.length} documento${docs.length === 1 ? '' : 's'}`, true);
  if (!docs.length) {
    vista.append(el('p', { class: 'vacio' }, `No tienes documentos de tipo ${cat?.nombre ?? tipo} validados.`));
    return;
  }
  vista.append(
    el(
      'div',
      { class: 'lista' },
      docs.map((d) =>
        el(
          'button',
          { class: 'item', onclick: () => ir({ vista: 'documento', id: d.id }) },
          el(
            'span',
            { class: 'detalle' },
            el('span', { class: 'folio mono' }, d.identificador),
            d.vigente ? el('span', { class: 'pill ok' }, 'vigente') : el('span', { class: 'pill' }, 'versión anterior'),
            vencido(d) ? el('span', { class: 'pill error' }, 'vencido') : null,
            el('span', { class: 'titular' }, `${d.titular} · validado ${fecha(d.validado_en)}`),
          ),
          el('span', { class: 'chev', html: svg('chev', 18) }),
        ),
      ),
    ),
  );
}

function pintarDocumento(id) {
  const d = documentos().find((x) => x.id === id);
  if (!d) return atras();
  cabecera(d.identificador, d.tipo_nombre, true);
  vista.append(
    el(
      'div',
      { class: 'ficha' },
      el('div', { class: 'folio mono' }, d.identificador),
      el('div', {}, d.titular),
      el(
        'div',
        { class: 'sub' },
        `${d.tipo_nombre} · validado ${fecha(d.validado_en)}${d.fecha_vigencia ? ` · vence ${fecha(d.fecha_vigencia)}` : ''}`,
      ),
    ),
    el(
      'div',
      { class: 'acciones' },
      el('button', { class: 'btn btn-primario', onclick: () => copiar(d.texto), html: `${svg('copiar', 16)} Copiar todo` }),
      el('button', { class: 'btn', onclick: () => descargarTxt(d), html: `${svg('descargar', 16)} Descargar .txt` }),
    ),
    el(
      'div',
      { class: 'campos' },
      d.campos.map((c) => {
        const boton = el('button', { class: 'copiar', title: `Copiar ${c.etiqueta}`, 'aria-label': `Copiar ${c.etiqueta}`, html: svg('copiar', 16) });
        boton.addEventListener('click', () => copiar(c.valor, boton));
        return el('div', { class: 'campo' }, el('span', { class: 'txt' }, el('span', { class: 'etq' }, c.etiqueta), el('span', { class: 'val' }, c.valor)), boton);
      }),
    ),
  );
  if (d.texto_escaneado) {
    const pre = el('pre', { class: 'mono' }, d.texto_escaneado);
    vista.append(
      el('div', { style: 'height:12px' }),
      el(
        'details',
        {},
        el('summary', {}, 'Texto escaneado del documento'),
        pre,
        el('div', { class: 'acciones' }, el('button', { class: 'btn', onclick: () => copiar(d.texto_escaneado), html: `${svg('copiar', 16)} Copiar texto escaneado` })),
      ),
    );
  }
}

function pintarVincular() {
  cabecera('Vincular extensión', 'Conecta la extensión con tu cuenta', false);
  const config = globalThis.CONFIG_PLATAFORMA || {};
  const url = Nucleo.urlBase(estado) || config.url || '';
  const error = el('div');
  const codigo = el('input', { class: 'codigo', maxlength: '9', placeholder: 'XXXX-XXXX', autocomplete: 'off', spellcheck: 'false' });
  const direccion = el('input', { value: url, placeholder: 'https://localhost:8443' });
  const boton = el('button', { class: 'btn btn-primario', type: 'submit', html: `${svg('enlace', 16)} Vincular` });
  const form = el(
    'form',
    { class: 'vincular' },
    el('h2', {}, 'Tus documentos a la mano'),
    el('p', { class: 'sub' }, 'Vincula la extensión para consultar y copiar los datos de tus documentos validados.'),
    estado.aviso ? el('div', { class: 'alerta aviso' }, estado.aviso) : null,
    el(
      'ol',
      {},
      el('li', {}, 'Abre la plataforma e inicia sesión.'),
      el('li', {}, 'Entra a "Extensión" en el menú y pulsa "Vincular ahora" (se vincula sola), o genera un código.'),
      el('li', {}, 'Si generaste un código, escríbelo aquí.'),
    ),
    error,
    el('label', {}, 'Código de vinculación'),
    codigo,
    el('label', {}, 'Dirección de la plataforma'),
    direccion,
    el('div', { class: 'acciones' }, boton),
    el('button', { type: 'button', class: 'enlace', onclick: () => abrirPlataforma(direccion.value, '/app/extension') }, 'Abrir la plataforma'),
  );
  codigo.addEventListener('input', () => {
    const v = codigo.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
    codigo.value = v.length > 4 ? `${v.slice(0, 4)}-${v.slice(4)}` : v;
  });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    error.replaceChildren();
    boton.disabled = true;
    try {
      estado = await Nucleo.vincular(codigo.value, direccion.value.trim());
      toast('Extensión vinculada');
      pila = [{ vista: 'inicio' }];
      pintar();
    } catch (err) {
      error.replaceChildren(el('div', { class: 'alerta error' }, err.message));
    } finally {
      boton.disabled = false;
    }
  });
  vista.append(form);
  codigo.focus();
}

// ── Acciones ────────────────────────────────────────────────────────────────
async function sincronizar() {
  const b = $('[data-sincronizar]');
  b.classList.add('girando');
  try {
    estado = await Nucleo.sincronizar();
    toast('Datos sincronizados');
  } catch (err) {
    estado = await Nucleo.leer();
    toast(err.status === 401 ? 'La extensión fue desvinculada' : 'No se pudo sincronizar');
    if (estado.token) estado = await Nucleo.guardar({ aviso: err.message });
  } finally {
    b.classList.remove('girando');
    pintar();
  }
}

function abrirPlataforma(url, ruta = '/app') {
  const base = String(url || Nucleo.urlBase(estado)).replace(/\/+$/, '');
  if (base) chrome.tabs.create({ url: `${base}${ruta}` });
}

$('[data-atras]').innerHTML = svg('atras', 18);
$('[data-atras]').addEventListener('click', atras);
$('[data-sincronizar]').innerHTML = svg('sync', 18);
$('[data-sincronizar]').addEventListener('click', sincronizar);
$('[data-abrir]').addEventListener('click', () => abrirPlataforma());
$('[data-desvincular]').addEventListener('click', async () => {
  if (!confirm('Se borrarán de este navegador los datos guardados. ¿Desvincular la extensión?')) return;
  await Nucleo.desvincular();
  estado = await Nucleo.leer();
  pila = [{ vista: 'inicio' }];
  pintar();
});

// Refresca si el service worker vincula/sincroniza mientras la ventana está abierta
chrome.storage.onChanged.addListener(async () => {
  estado = await Nucleo.leer();
  pintar();
});

(async () => {
  estado = await Nucleo.leer();
  pila = [{ vista: 'inicio' }];
  pintar();
})();
