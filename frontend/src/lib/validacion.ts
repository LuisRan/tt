/**
 * Formulario de validación (CU-05): muestra los datos detectados por OCR + IA
 * para que el usuario los revise, corrija y confirme antes de guardarlos (RN-08).
 * Se usa tanto en el registro (INE) como en la sección de documentos.
 */
import { api } from './api';
import { icono } from './iconos';
import { esc, etiqueta, mostrarError, ORDEN_CAMPOS, toast } from './ui';

const CAMPOS_OCULTOS = new Set(['nombre_completo', 'curps_asociadas', 'mrz_linea1', 'mrz_linea2', 'domicilio_calle', 'domicilio_colonia', 'domicilio_municipio_ciudad']);
const FECHAS = new Set(['fecha_nacimiento', 'fecha_emision', 'fecha_caducidad', 'fecha_expedicion', 'fecha_registro', 'fecha_inscripcion']);

/**
 * modo 'validar': primera confirmación (POST /validar, cuenta para la métrica de precisión)
 * modo 'editar' : corrección posterior de un documento ya validado (PUT /datos, no cuenta)
 */
export function renderValidacion(
  contenedor: HTMLElement,
  doc: any,
  alConfirmar: (r: any) => void,
  textoBoton = 'Confirmar y guardar',
  modo: 'validar' | 'editar' = 'validar',
): void {
  const detectados: Record<string, string | null> =
    (modo === 'editar' ? doc.resultado?.datos_confirmados : null) ?? doc.resultado?.datos_extraidos ?? {};
  const requeridos: string[] = doc.campos_requeridos ?? [];
  const validaciones: Record<string, boolean> = doc.resultado?.validaciones ?? {};
  const campos = [...new Set([...requeridos, ...Object.keys(detectados)])]
    .filter((c) => !CAMPOS_OCULTOS.has(c))
    .sort((a, b) => {
      const ra = requeridos.includes(a) ? 0 : 1;
      const rb = requeridos.includes(b) ? 0 : 1;
      return ra - rb || ORDEN_CAMPOS.indexOf(a) - ORDEN_CAMPOS.indexOf(b);
    });

  const confianza = modo === 'validar' ? doc.resultado?.confianza_ocr : null;
  const advertencias: string[] = modo === 'validar' ? doc.advertencias ?? [] : [];
  const descartados: string[] = modo === 'validar' ? doc.resultado?.metadatos?.campos_descartados_por_alucinacion ?? [] : [];
  const corregidosOcr: string[] = modo === 'validar' ? doc.resultado?.metadatos?.campos_corregidos_ocr ?? [] : [];

  contenedor.innerHTML = `
    ${confianza != null ? `<div style="margin-bottom:18px"><div class="small muted" style="display:flex;justify-content:space-between"><span>Confianza de la extracción</span><b>${Math.round(confianza * 100)}%</b></div><div class="barra"><span style="width:${Math.round(confianza * 100)}%"></span></div></div>` : ''}
    ${advertencias.length ? `<div class="alerta alerta-aviso"><b>Revisa:</b><ul>${advertencias.map((a) => `<li>${esc(a)}</li>`).join('')}</ul></div>` : ''}
    ${descartados.length ? `<div class="alerta alerta-info">Por seguridad (anti-alucinación) se dejaron vacíos datos que no se pudieron verificar en el documento: ${descartados.map((c) => esc(etiqueta(c))).join(', ')}.</div>` : ''}
    <p class="muted small">${modo === 'editar'
      ? 'Modifica los datos que necesites. Estos cambios no afectan la medición de precisión de la IA.'
      : 'Los campos marcados con * son obligatorios. Corrige cualquier dato incorrecto antes de confirmar.'}</p>
    <form data-form-validacion novalidate>
      <div data-error></div>
      <div class="form-validacion">
        ${campos
          .map((c) => {
            const v = detectados[c] ?? '';
            const invalido = (c === 'curp' && validaciones.curp_valido === false) || (c === 'clave_elector' && validaciones.clave_elector_valida === false);
            return `<div class="campo">
              <label for="v-${esc(c)}">${esc(etiqueta(c))}${requeridos.includes(c) ? ' *' : ''}</label>
              <input id="v-${esc(c)}" name="${esc(c)}" value="${esc(v)}" data-original="${esc(v)}" ${FECHAS.has(c) ? 'placeholder="dd/mm/aaaa" inputmode="numeric"' : ''} ${invalido ? 'class="invalido"' : ''} autocomplete="off">
              ${!v ? '<span class="ayuda">No detectado: complétalo si aparece en tu documento</span>' : ''}
              ${v && corregidosOcr.includes(c) ? '<span class="ayuda" style="color:var(--aviso)">Corregido automáticamente (validación cruzada): verifícalo</span>' : ''}
              ${c === 'fecha_emision' ? '<span class="ayuda">Fecha que aparece como “Ciudad de México, a __ de ____ de ____”</span>' : ''}
            </div>`;
          })
          .join('')}
      </div>
      <div style="display:flex;gap:12px;justify-content:flex-end;flex-wrap:wrap;margin-top:8px">
        <button class="btn btn-primario btn-grande" type="submit">${esc(textoBoton)}</button>
      </div>
    </form>`;

  const form = contenedor.querySelector('[data-form-validacion]') as HTMLFormElement;
  form.querySelectorAll('input').forEach((i) =>
    i.addEventListener('input', () => {
      i.classList.toggle('corregido', i.value !== i.dataset.original);
      i.classList.remove('invalido');
    }),
  );
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const boton = form.querySelector('button[type=submit]') as HTMLButtonElement;
    const datos: Record<string, string | null> = {};
    form.querySelectorAll('input').forEach((i) => (datos[i.name] = i.value.trim() || null));
    boton.disabled = true;
    boton.textContent = 'Guardando…';
    try {
      const r = modo === 'editar'
        ? await api(`/documentos/${doc.id}/datos`, { metodo: 'PUT', cuerpo: { datos } })
        : await api(`/documentos/${doc.id}/validar`, { cuerpo: { datos } });
      toast(modo === 'editar' ? 'Cambios guardados' : 'Información validada y guardada', 'ok');
      alConfirmar(r);
    } catch (err: any) {
      mostrarError(form.querySelector('[data-error]'), err);
      const campos = err?.detalles?.campos ?? {};
      Object.keys(campos).forEach((c) => form.querySelector(`[name="${CSS.escape(c)}"]`)?.classList.add('invalido'));
      form.querySelector('[data-error]')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } finally {
      boton.disabled = false;
      boton.textContent = textoBoton;
    }
  });
}

/** Espera a que el documento termine de procesarse (polling). */
export async function esperarProcesamiento(id: number, alCambiar?: (estado: string) => void): Promise<any> {
  const finales = ['procesado', 'rechazado', 'error', 'validado'];
  let anterior = '';
  for (let i = 0; i < 600; i++) {
    const e = await api(`/documentos/${id}/estado`);
    if (e.estado !== anterior) {
      anterior = e.estado;
      alCambiar?.(e.estado);
    }
    if (finales.includes(e.estado)) return e;
    await new Promise((r) => setTimeout(r, i < 20 ? 1500 : 3000));
  }
  throw new Error('El procesamiento está tardando demasiado. Revisa más tarde en "Documentos".');
}

export function pasosProceso(estado: string): string {
  const pasos = [
    ['cargado', 'Archivo recibido y cifrado'],
    ['procesando', 'OCR adaptativo (Docling / Tesseract) y detección del tipo'],
    ['procesando2', 'Extracción híbrida regex + LLM local y validación anti-alucinación'],
    ['procesado', 'Listo para tu validación'],
  ];
  const idx = estado === 'cargado' ? 0 : estado === 'procesando' ? 1 : 3;
  return `<div class="pasos-proceso">${pasos
    .map(([, t], i) => {
      const clase = i < idx ? 'hecho' : i === idx || (idx === 1 && i === 2) ? 'actual' : '';
      const ic = i < idx ? icono('check', 16) : clase === 'actual' ? icono('punto', 16) : icono('circulo', 16);
      return `<div class="${clase}"><span class="icono">${ic}</span>${esc(t)}</div>`;
    })
    .join('')}</div>`;
}
