/**
 * Gestión documental: carga (CU-03), procesamiento con el módulo de IA (CU-04),
 * validación por el usuario (CU-05), consulta (CU-06), vigencia de CURP (CU-07)
 * y versionado (RN-07).
 *
 * Ciclo de vida de un documento:
 *   cargado -> procesando -> procesado -> validado
 *                         \-> rechazado  (no soportado, tipo distinto, CURP > 3 meses...)
 *                         \-> error      (módulo de IA caído; se puede reintentar)
 */
import { ObjectId } from 'mongodb';
import { PDFDocument } from 'pdf-lib';
import { z } from 'zod';
import { config } from '../config.js';
import { resultados, type ResultadoOcrDoc } from '../db/mongo.js';
import { query, transaccion, uno } from '../db/pg.js';
import { cifrar, cifrarObjeto, descifrar, descifrarObjeto, hmac, idAleatorio, sha256 } from '../lib/crypto.js';
import { ErrorApp, noEncontrado, solicitudInvalida } from '../lib/errores.js';
import { logger } from '../lib/logger.js';
import { almacenamiento } from './almacenamiento.js';
import { registrarEvento } from './auditoria.js';
import { ErrorIA, procesarEnIA, textoCrudoIA, type EnvelopeIA } from './ia-cliente.js';

// ── Catálogo ─────────────────────────────────────────────────────────────────

export const TIPOS = ['ine', 'curp', 'acta_nacimiento', 'pasaporte'] as const;
export type ClaveTipo = (typeof TIPOS)[number];

export const NOMBRE_TIPO: Record<ClaveTipo, string> = {
  ine: 'INE',
  curp: 'CURP',
  acta_nacimiento: 'Acta de nacimiento',
  pasaporte: 'Pasaporte',
};

let cacheTipos: Map<string, number> | null = null;
export async function idTipo(clave: string): Promise<number | null> {
  if (!cacheTipos) {
    const r = await query<{ id: number; clave: string }>('SELECT id, clave FROM tipos_documento');
    cacheTipos = new Map(r.rows.map((t) => [t.clave, t.id]));
  }
  return cacheTipos.get(clave) ?? null;
}

// ── Formatos y reglas ────────────────────────────────────────────────────────

const RE_CURP = /^[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$/;
const RE_FECHA = /^(\d{2})\/(\d{2})\/(\d{4})$/;
const RE_CLAVE_ELECTOR = /^[A-Z]{6}\d{8}[A-Z]\d{3}$/;
const RE_PASAPORTE = /^[A-Z]\d{8}$/;
const RE_ANIO = /^\d{4}$/;

/** 'dd/mm/yyyy' -> Date (UTC) o null si no es fecha real. */
export function parsearFecha(valor: unknown): Date | null {
  const m = RE_FECHA.exec(String(valor ?? '').trim());
  if (!m) return null;
  const [, dd, mm, yyyy] = m;
  const f = new Date(Date.UTC(+yyyy, +mm - 1, +dd));
  return f.getUTCDate() === +dd && f.getUTCMonth() === +mm - 1 ? f : null;
}
const aISO = (f: Date | null) => (f ? f.toISOString().slice(0, 10) : null);
const hoyUTC = () => {
  const h = new Date();
  return new Date(Date.UTC(h.getFullYear(), h.getMonth(), h.getDate()));
};
const diasEntre = (a: Date, b: Date) => Math.round((b.getTime() - a.getTime()) / 86_400_000);

/** Campos obligatorios que el usuario debe confirmar por tipo (CU-05). */
export const CAMPOS_REQUERIDOS: Record<ClaveTipo, string[]> = {
  ine: ['nombres', 'primer_apellido', 'curp', 'fecha_nacimiento', 'año_vigencia'],
  curp: ['curp', 'nombres', 'primer_apellido', 'fecha_emision'],
  acta_nacimiento: ['nombres', 'primer_apellido', 'fecha_nacimiento'],
  pasaporte: ['numero_pasaporte', 'nombres', 'primer_apellido', 'fecha_nacimiento', 'fecha_caducidad'],
};

/** Verificaciones de formato que se guardan en Mongo como "validaciones" (Fig. 32). */
export function calcularValidaciones(tipo: ClaveTipo, d: Record<string, any>): Record<string, boolean> {
  const v: Record<string, boolean> = {};
  const fechaOk = (x: unknown) => parsearFecha(x) !== null;
  if (d.curp != null) v.curp_valido = RE_CURP.test(String(d.curp));
  if (d.fecha_nacimiento != null) v.fecha_nacimiento_valida = fechaOk(d.fecha_nacimiento);
  v.nombre_valido = Boolean((d.nombres || d.nombre_completo) && (d.primer_apellido || d.nombre_completo));
  if (tipo === 'ine') {
    if (d.clave_elector != null) v.clave_elector_valida = RE_CLAVE_ELECTOR.test(String(d.clave_elector));
    v.direccion_valida = Boolean(d.domicilio);
    if (d.año_vigencia != null) v.vigente = RE_ANIO.test(String(d.año_vigencia)) && +d.año_vigencia >= new Date().getFullYear();
  }
  if (tipo === 'pasaporte') {
    if (d.numero_pasaporte != null) v.numero_pasaporte_valido = RE_PASAPORTE.test(String(d.numero_pasaporte));
    const cad = parsearFecha(d.fecha_caducidad);
    if (cad) v.vigente = cad >= hoyUTC();
  }
  if (tipo === 'curp') {
    const em = parsearFecha(d.fecha_emision);
    if (em) v.antiguedad_valida = diasEntre(em, hoyUTC()) <= config.CURP_MAX_DIAS;
  }
  return v;
}

export interface EvaluacionReglas {
  rechazo: string | null;
  advertencias: string[];
  fechaVigencia: string | null;
}

/**
 * Reglas de negocio sobre los datos (RN-04, RN-06, RN-11).
 * `definitivo` = true cuando se evalúan datos ya confirmados por el usuario.
 */
export function evaluarReglas(tipo: ClaveTipo, d: Record<string, any>, definitivo = false): EvaluacionReglas {
  const advertencias: string[] = [];
  let rechazo: string | null = null;
  let fechaVigencia: string | null = null;
  const hoy = hoyUTC();

  if (tipo === 'ine') {
    if (RE_ANIO.test(String(d.año_vigencia ?? ''))) {
      fechaVigencia = `${d.año_vigencia}-12-31`;
      if (+d.año_vigencia < hoy.getUTCFullYear()) advertencias.push(`La INE venció en ${d.año_vigencia} (RN-11).`);
    }
    if (RE_ANIO.test(String(d.año_emision ?? '')) && +d.año_emision < 2016) {
      advertencias.push(`La INE fue emitida en ${d.año_emision}; el formato soportado es el vigente desde 2016 (RN-04).`);
    }
  }

  if (tipo === 'pasaporte') {
    const cad = parsearFecha(d.fecha_caducidad);
    if (cad) {
      fechaVigencia = aISO(cad);
      if (cad < hoy) advertencias.push(`El pasaporte venció el ${d.fecha_caducidad} (RN-11).`);
    }
    const exp = parsearFecha(d.fecha_expedicion);
    if (exp && exp.getUTCFullYear() < 2021) {
      advertencias.push('El pasaporte es anterior al formato electrónico tipo "E" de 2021 (RN-04).');
    }
  }

  if (tipo === 'curp') {
    // RN-06 / CU-07: constancia digital con antigüedad máxima de 3 meses
    const em = parsearFecha(d.fecha_emision);
    if (em) {
      const dias = diasEntre(em, hoy);
      const limite = new Date(em.getTime() + config.CURP_MAX_DIAS * 86_400_000);
      fechaVigencia = aISO(limite);
      if (dias > config.CURP_MAX_DIAS) {
        rechazo =
          `La constancia CURP fue emitida el ${d.fecha_emision} (hace ${dias} días). ` +
          `Solo se aceptan constancias con antigüedad máxima de 3 meses: descarga una nueva en gob.mx/curp (RN-06).`;
      } else if (dias < -1) {
        rechazo = 'La fecha de emisión de la CURP está en el futuro; revisa el dato.';
      }
    } else if (definitivo) {
      rechazo = 'Indica la fecha de emisión de la constancia CURP para validar su vigencia (RN-06).';
    } else {
      advertencias.push(
        'No se detectó la fecha de emisión de la constancia; confírmala en la validación para verificar que tenga máximo 3 meses.',
      );
    }
  }

  return { rechazo, advertencias, fechaVigencia };
}

// ── Carga ────────────────────────────────────────────────────────────────────

const MIME_PERMITIDOS: Record<string, string> = {
  'application/pdf': '.pdf',
  'image/jpeg': '.jpg',
  'image/png': '.png',
};

function tipoPorFirma(buf: Buffer): string | null {
  if (buf.subarray(0, 5).toString('latin1') === '%PDF-') return 'application/pdf';
  if (buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) return 'image/jpeg';
  if (buf.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return 'image/png';
  return null;
}

/** El módulo de IA procesa PDF: las fotos (JPG/PNG) se envuelven en un PDF de una página. */
export async function normalizarAPdf(buf: Buffer): Promise<{ pdf: Buffer; mime: string }> {
  const mime = tipoPorFirma(buf);
  if (!mime) {
    throw new ErrorApp(415, 'FORMATO_NO_PERMITIDO', 'Solo se aceptan archivos PDF, JPG o PNG.');
  }
  if (mime === 'application/pdf') {
    try {
      const doc = await PDFDocument.load(buf, { ignoreEncryption: false, updateMetadata: false });
      if (doc.getPageCount() > 4) throw new ErrorApp(422, 'DEMASIADAS_PAGINAS', 'El PDF debe tener máximo 4 páginas.');
    } catch (e) {
      if (e instanceof ErrorApp) throw e;
      throw new ErrorApp(422, 'PDF_DANADO', 'El PDF está dañado o protegido con contraseña.');
    }
    return { pdf: buf, mime };
  }
  try {
    const doc = await PDFDocument.create();
    const img = mime === 'image/png' ? await doc.embedPng(buf) : await doc.embedJpg(buf);
    // Página A4 con la imagen centrada y escalada
    const A4 = { w: 595.28, h: 841.89 };
    const escala = Math.min((A4.w - 40) / img.width, (A4.h - 40) / img.height, 1);
    const w = img.width * escala;
    const h = img.height * escala;
    const pagina = doc.addPage([A4.w, A4.h]);
    pagina.drawImage(img, { x: (A4.w - w) / 2, y: (A4.h - h) / 2, width: w, height: h });
    return { pdf: Buffer.from(await doc.save()), mime };
  } catch {
    throw new ErrorApp(422, 'IMAGEN_DANADA', 'La imagen está dañada o no se pudo leer.');
  }
}

export async function crearDocumento(p: {
  usuarioId: number;
  tipo: ClaveTipo;
  archivo: Buffer;
  nombreOriginal: string;
}): Promise<{ id: number; estado: string }> {
  if (p.archivo.length > config.MAX_UPLOAD_MB * 1024 * 1024) {
    throw new ErrorApp(413, 'ARCHIVO_GRANDE', `El archivo excede ${config.MAX_UPLOAD_MB} MB.`);
  }
  const { pdf, mime } = await normalizarAPdf(p.archivo);
  const hash = sha256(pdf);

  // Evita procesar dos veces exactamente el mismo archivo pendiente
  const duplicado = await uno<{ id: number }>(
    `SELECT id FROM documentos WHERE usuario_id = $1 AND hash_sha256 = $2 AND estado IN ('cargado','procesando','procesado')`,
    [p.usuarioId, hash],
  );
  if (duplicado) {
    throw new ErrorApp(409, 'DOCUMENTO_DUPLICADO', 'Ya cargaste este mismo archivo y está pendiente de validar.', {
      id: duplicado.id,
    });
  }

  const ruta = await almacenamiento.guardar(p.usuarioId, pdf, '.pdf');
  const tipoId = await idTipo(p.tipo);
  const fila = await uno<{ id: number; estado: string }>(
    `INSERT INTO documentos (usuario_id, tipo_id, ruta_archivo, nombre_original, mime_original, tamano_bytes, hash_sha256, estado)
     VALUES ($1, $2, $3, $4, $5, $6, $7, 'cargado') RETURNING id, estado`,
    [p.usuarioId, tipoId, ruta, p.nombreOriginal.slice(0, 255), mime, pdf.length, hash],
  );
  await registrarEvento({
    usuarioId: p.usuarioId,
    tipo: 'auditoria',
    tabla: 'documentos',
    operacion: 'INSERT',
    descripcion: `Documento ${fila!.id} (${NOMBRE_TIPO[p.tipo]}) cargado: ${p.nombreOriginal}`,
  });
  encolar(fila!.id);
  return fila!;
}

// ── Cola de procesamiento (asíncrona, desacoplada del request HTTP) ──────────

const cola: number[] = [];
const enCurso = new Set<number>();
let trabajadores = 0;

export function encolar(documentoId: number, retrasoMs = 0): void {
  const agregar = () => {
    if (!cola.includes(documentoId) && !enCurso.has(documentoId)) cola.push(documentoId);
    despachar();
  };
  if (retrasoMs > 0) setTimeout(agregar, retrasoMs).unref();
  else agregar();
}

function despachar(): void {
  while (trabajadores < config.IA_CONCURRENCIA && cola.length > 0) {
    const id = cola.shift()!;
    enCurso.add(id);
    trabajadores++;
    procesarDocumento(id)
      .catch((err) => logger.error({ err, documentoId: id }, 'fallo inesperado procesando documento'))
      .finally(() => {
        enCurso.delete(id);
        trabajadores--;
        despachar();
      });
  }
}

export function estadoCola() {
  return { pendientes: cola.length, en_curso: enCurso.size };
}

/** Al arrancar: reencola documentos que quedaron a medias (reinicio del servidor). */
export async function recuperarPendientes(): Promise<void> {
  const r = await query<{ id: number }>(
    `UPDATE documentos SET estado = 'cargado' WHERE estado = 'procesando' RETURNING id`,
  );
  const pendientes = await query<{ id: number }>(`SELECT id FROM documentos WHERE estado = 'cargado' ORDER BY id`);
  pendientes.rows.forEach((d) => encolar(d.id));
  if (pendientes.rowCount) logger.info({ n: pendientes.rowCount, reiniciados: r.rowCount }, 'documentos reencolados');
}

const CLAVE_MONGO: Record<ClaveTipo, string> = {
  ine: 'INE',
  curp: 'CURP',
  acta_nacimiento: 'ACTA_NACIMIENTO',
  pasaporte: 'PASAPORTE',
};

export async function procesarDocumento(documentoId: number): Promise<void> {
  const doc = await uno<{
    id: number;
    usuario_id: number;
    ruta_archivo: string;
    estado: string;
    intentos: number;
    tipo_clave: ClaveTipo | null;
  }>(
    `UPDATE documentos d SET estado = 'procesando', intentos = intentos + 1, actualizado_en = NOW()
       FROM (SELECT id FROM documentos WHERE id = $1 AND estado IN ('cargado','error') FOR UPDATE) s
      WHERE d.id = s.id
      RETURNING d.id, d.usuario_id, d.ruta_archivo, d.estado, d.intentos,
                (SELECT clave FROM tipos_documento WHERE id = d.tipo_id) AS tipo_clave`,
    [documentoId],
  );
  if (!doc) return; // ya lo tomó otro proceso o cambió de estado

  const inicio = Date.now();
  const iaDocId = `d${doc.id}_${idAleatorio(6)}`;
  let envelope: EnvelopeIA;
  try {
    const pdf = await almacenamiento.leer(doc.ruta_archivo);
    envelope = await procesarEnIA(pdf, `u${doc.usuario_id}`, iaDocId);
  } catch (err) {
    const e =
      err instanceof ErrorIA ? err : new ErrorIA(500, 'ERROR_INTERNO', (err as Error)?.message ?? 'Error', true);
    await query(
      `INSERT INTO metricas_ia (documento_id, tiempo_total, exito, codigo_http, error) VALUES ($1, $2, FALSE, $3, $4)`,
      [doc.id, Date.now() - inicio, e.status, `${e.codigo}: ${e.message}`.slice(0, 1000)],
    );
    if (e.reintentable && doc.intentos <= config.IA_REINTENTOS) {
      await query(`UPDATE documentos SET estado = 'cargado', motivo_rechazo = $2 WHERE id = $1`, [
        doc.id,
        'El módulo de IA no está disponible; reintentando…',
      ]);
      encolar(doc.id, 30_000 * doc.intentos);
      logger.warn({ documentoId: doc.id, intento: doc.intentos }, 'IA no disponible, se reintentará');
      return;
    }
    const estado = e.reintentable ? 'error' : 'rechazado';
    const motivo =
      e.status === 415
        ? 'El archivo no fue reconocido como INE, CURP, acta de nacimiento o pasaporte, o no es legible. Carga un documento válido.'
        : e.message;
    await query(`UPDATE documentos SET estado = $2, motivo_rechazo = $3, actualizado_en = NOW() WHERE id = $1`, [
      doc.id,
      estado,
      motivo,
    ]);
    await registrarEvento({
      usuarioId: doc.usuario_id,
      tipo: estado === 'error' ? 'error' : 'advertencia',
      tabla: 'documentos',
      operacion: 'PROCESAR',
      descripcion: `Documento ${doc.id}: ${e.codigo} (${e.status}) ${e.message}`.slice(0, 1000),
    });
    return;
  }

  const tipo = envelope.tipo_documento as ClaveTipo;
  const datos = envelope.datos ?? {};
  const meta = envelope.metadatos ?? {};
  const reglas = evaluarReglas(tipo, datos);
  const advertencias = [...reglas.advertencias];
  let rechazo = reglas.rechazo;

  if (doc.tipo_clave && doc.tipo_clave !== tipo) {
    rechazo = `Seleccionaste ${NOMBRE_TIPO[doc.tipo_clave]} pero el archivo parece ser ${NOMBRE_TIPO[tipo] ?? tipo}. Verifica el documento.`;
  }
  if ((meta.errores_validacion?.length ?? 0) > 0) {
    advertencias.push('Algunos campos no tienen el formato esperado y quedaron vacíos: revísalos.');
  }
  if ((meta.campos_descartados_por_alucinacion?.length ?? 0) > 0) {
    advertencias.push('Algunos datos no pudieron verificarse contra el documento y se dejaron vacíos.');
  }
  const corregidosOcr = (meta.campos_corregidos_ocr as string[] | undefined) ?? [];
  if (corregidosOcr.length) {
    const nombres: Record<string, string> = { curp: 'CURP', clave_elector: 'clave de elector' };
    advertencias.push(
      `Se corrigieron automáticamente errores de lectura en ${corregidosOcr.map((c) => nombres[c] ?? c).join(', ')} ` +
        'mediante validación cruzada (dígito verificador y consistencia con nombre, fecha y sexo). Verifícalos.',
    );
  }

  const camposDetectados = Object.entries(datos)
    .filter(([, v]) => v !== null && v !== '')
    .map(([campo, valor]) => ({ campo, valor: cifrar(String(valor)) }));

  const mongoDoc: ResultadoOcrDoc = {
    documento_id: doc.id,
    usuario_id: doc.usuario_id,
    tipo_documento: CLAVE_MONGO[tipo] ?? tipo,
    datos_extraidos: cifrarObjeto(datos),
    campos_detectados: camposDetectados,
    validaciones: calcularValidaciones(tipo, datos),
    confianza_ocr: typeof meta.confianza === 'number' ? meta.confianza : null,
    modelo_ia: (meta.modelo_llm as string) ?? null,
    adaptador: meta.adaptador_usado ?? null,
    metadatos: {
      pipeline_version: meta.pipeline_version,
      hash_sha256: meta.hash_sha256,
      tiempo_procesamiento_ms: meta.tiempo_procesamiento_ms,
      errores_validacion: meta.errores_validacion ?? [],
      campos_descartados_por_alucinacion: (meta.campos_descartados_por_alucinacion ?? []).map((c) => c.split('=')[0]),
      campos_corregidos_ocr: corregidosOcr,
      tiempos_etapas_ms: meta.tiempos_etapas_ms,
      plataforma: meta.plataforma,
    },
    ia_doc_id: iaDocId,
    fecha_procesamiento: new Date(),
    texto_crudo: await textoCrudoIA(iaDocId)
      .then((t) => cifrar(t))
      .catch(() => null),
  };
  const guardado = await resultados().findOneAndReplace({ documento_id: doc.id }, mongoDoc, {
    upsert: true,
    returnDocument: 'after',
  });

  const estado = rechazo ? 'rechazado' : 'procesado';
  await query(
    `UPDATE documentos
        SET estado = $2, tipo_id = COALESCE($3, tipo_id), mongo_resultado_id = $4, ia_doc_id = $5,
            fecha_vigencia = $6, motivo_rechazo = $7, advertencias = $8::jsonb, actualizado_en = NOW()
      WHERE id = $1`,
    [
      doc.id,
      estado,
      await idTipo(tipo),
      guardado?._id?.toString() ?? null,
      iaDocId,
      reglas.fechaVigencia,
      rechazo,
      JSON.stringify(advertencias),
    ],
  );
  await query(
    `INSERT INTO metricas_ia (documento_id, tiempo_procesamiento, tiempo_total, modelo_usado, adaptador, confianza,
                              campos_detectados, alucinaciones, exito, codigo_http)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE, 200)`,
    [
      doc.id,
      meta.tiempo_procesamiento_ms ?? null,
      Date.now() - inicio,
      meta.modelo_llm ?? null,
      meta.adaptador_usado ?? null,
      meta.confianza ?? null,
      meta.campos_detectados ?? camposDetectados.length,
      meta.campos_descartados_por_alucinacion?.length ?? 0,
    ],
  );
  await registrarEvento({
    usuarioId: doc.usuario_id,
    tipo: rechazo ? 'advertencia' : 'info',
    tabla: 'documentos',
    operacion: 'PROCESAR',
    descripcion: rechazo
      ? `Documento ${doc.id} rechazado: ${rechazo}`
      : `Documento ${doc.id} procesado como ${NOMBRE_TIPO[tipo]} en ${Date.now() - inicio} ms`,
  });
}

// ── Identificador visible: folio + iniciales del titular (0001_CSC) ──────────

const PARTICULAS = new Set(['DE', 'DEL', 'LA', 'LAS', 'LOS', 'Y', 'MC', 'MAC', 'VAN', 'VON', 'DA', 'DI', 'DAS', 'DOS']);

function inicial(texto: unknown): string {
  const palabras = String(texto ?? '')
    .toUpperCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^A-Z ]/g, ' ')
    .split(/\s+/)
    .filter(Boolean);
  const util = palabras.find((p) => !PARTICULAS.has(p)) ?? palabras[0];
  return util ? util[0] : '';
}

/** Iniciales del titular del documento: nombre + primer apellido + segundo apellido. */
export function inicialesTitular(d: Record<string, unknown>): string {
  let r = inicial(d.nombres) + inicial(d.primer_apellido) + inicial(d.segundo_apellido);
  if (!r && d.nombre_completo) {
    r = String(d.nombre_completo).split(/\s+/).map(inicial).join('').slice(0, 3);
  }
  return r || 'XXX';
}

/** Clave del titular: HMAC de la CURP o, si no hay, del nombre normalizado. */
export function claveTitular(d: Record<string, unknown>): string | null {
  const curp = String(d.curp ?? '').toUpperCase().replace(/\s/g, '');
  if (curp) return hmac(`titular:curp:${curp}`);
  const nombre = [d.nombres, d.primer_apellido, d.segundo_apellido].filter(Boolean).join(' ') || String(d.nombre_completo ?? '');
  const n = nombre.toUpperCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^A-Z ]/g, ' ').replace(/\s+/g, ' ').trim();
  return n ? hmac(`titular:nombre:${n}`) : null;
}

export function identificadorDocumento(folio: number | null | undefined, datos: Record<string, unknown>): string | null {
  if (!folio) return null;
  return `${String(folio).padStart(4, '0')}_${inicialesTitular(datos)}`;
}

// ── Consulta ─────────────────────────────────────────────────────────────────

export async function listarDocumentos(usuarioId: number) {
  const r = await query(
    `SELECT d.id, t.clave AS tipo, t.nombre AS tipo_nombre, d.nombre_original, d.estado, d.version, d.vigente,
            d.fecha_vigencia, d.motivo_rechazo, d.advertencias, d.creado_en, d.actualizado_en,
            d.folio, d.titular_iniciales, m.confianza
       FROM documentos d
       LEFT JOIN tipos_documento t ON t.id = d.tipo_id
       LEFT JOIN LATERAL (SELECT confianza FROM metricas_ia WHERE documento_id = d.id AND exito
                          ORDER BY id DESC LIMIT 1) m ON TRUE
      WHERE d.usuario_id = $1
      ORDER BY d.creado_en DESC`,
    [usuarioId],
  );
  return r.rows.map(({ titular_iniciales, ...d }: any) => ({
    ...d,
    identificador: d.folio ? `${String(d.folio).padStart(4, '0')}_${titular_iniciales ?? 'XXX'}` : null,
  }));
}

/** Completa las iniciales de documentos validados antes de existir la columna (se ejecuta al arrancar). */
export async function completarIdentificadores(): Promise<number> {
  const r = await query<{ id: number }>(
    `SELECT id FROM documentos WHERE estado = 'validado' AND (titular_iniciales IS NULL OR titular_hash IS NULL)`,
  );
  for (const { id } of r.rows) {
    const m = await resultados().findOne({ documento_id: id });
    const datos = descifrarObjeto(m?.datos_confirmados ?? m?.datos_extraidos);
    if (!Object.keys(datos).length) continue; // sin resultado en MongoDB: no se inventan iniciales
    const titular = claveTitular(datos);
    await query(
      'UPDATE documentos SET titular_iniciales = COALESCE(titular_iniciales, $2), titular_hash = $3 WHERE id = $1',
      [id, inicialesTitular(datos), titular],
    );
    await query('UPDATE documento_versiones SET titular_hash = $2 WHERE documento_id = $1', [id, titular]);
  }
  if (r.rowCount) {
    // Recalcula la versión vigente por titular (datos creados antes de esta regla)
    await query(
      `UPDATE documentos d SET vigente = (d.id = (
          SELECT d2.id FROM documentos d2
           WHERE d2.usuario_id = d.usuario_id AND d2.tipo_id = d.tipo_id AND d2.estado = 'validado'
             AND d2.titular_hash IS NOT DISTINCT FROM d.titular_hash
           ORDER BY d2.version DESC, d2.id DESC LIMIT 1))
        WHERE d.estado = 'validado'`,
    );
  }
  return r.rowCount ?? 0;
}

/** RN-10: el filtro por usuario_id garantiza que solo se consulten documentos propios. */
export async function obtenerDocumento(usuarioId: number, id: number) {
  const d = await uno(
    `SELECT d.*, t.clave AS tipo, t.nombre AS tipo_nombre
       FROM documentos d LEFT JOIN tipos_documento t ON t.id = d.tipo_id
      WHERE d.id = $1 AND d.usuario_id = $2`,
    [id, usuarioId],
  );
  if (!d) throw noEncontrado('Documento no encontrado');

  const mongo = d.mongo_resultado_id
    ? await resultados().findOne({ _id: new ObjectId(d.mongo_resultado_id as string) })
    : await resultados().findOne({ documento_id: id });

  const versiones = d.tipo_id
    ? await query(
        `SELECT v.version, v.documento_id, v.fecha FROM documento_versiones v
          WHERE v.usuario_id = $1 AND v.tipo_id = $2 AND v.titular_hash IS NOT DISTINCT FROM $3
          ORDER BY v.version DESC`,
        [usuarioId, d.tipo_id, d.titular_hash ?? null],
      )
    : { rows: [] };
  const metrica = await uno(
    `SELECT tiempo_procesamiento, tiempo_total, modelo_usado, adaptador, confianza, campos_detectados, alucinaciones
       FROM metricas_ia WHERE documento_id = $1 AND exito ORDER BY id DESC LIMIT 1`,
    [id],
  );
  const validaciones = await query(
    `SELECT campo, valor_detectado, valor_confirmado, corregido, origen, creado_en FROM validaciones_dato
      WHERE documento_id = $1 ORDER BY id`,
    [id],
  );

  const { ruta_archivo: _r, hash_sha256: _h, ia_doc_id: _i, titular_iniciales: ini, titular_hash: _t, ...publico } = d;
  return {
    ...publico,
    identificador: d.folio ? `${String(d.folio).padStart(4, '0')}_${ini ?? 'XXX'}` : null,
    campos_requeridos: d.tipo ? CAMPOS_REQUERIDOS[d.tipo as ClaveTipo] : [],
    resultado: mongo
      ? {
          tipo_documento: mongo.tipo_documento,
          datos_extraidos: descifrarObjeto(mongo.datos_extraidos),
          datos_confirmados: mongo.datos_confirmados ? descifrarObjeto(mongo.datos_confirmados) : null,
          validaciones: mongo.validaciones,
          confianza_ocr: mongo.confianza_ocr,
          modelo_ia: mongo.modelo_ia,
          adaptador: mongo.adaptador,
          metadatos: mongo.metadatos,
          fecha_procesamiento: mongo.fecha_procesamiento,
          fecha_validacion: mongo.fecha_validacion ?? null,
        }
      : null,
    metricas: metrica,
    versiones: versiones.rows,
    historial_validacion: validaciones.rows.map((v: any) => ({
      ...v,
      valor_detectado: descifrar(v.valor_detectado),
      valor_confirmado: descifrar(v.valor_confirmado),
    })),
  };
}

export async function resumenUsuario(usuarioId: number) {
  const r = await uno(
    `SELECT COUNT(*)::int AS documentos,
            COUNT(*) FILTER (WHERE estado IN ('procesado','validado'))::int AS procesados,
            COUNT(*) FILTER (WHERE estado = 'validado')::int AS validados,
            COUNT(*) FILTER (WHERE estado IN ('cargado','procesando','procesado'))::int AS pendientes,
            COUNT(*) FILTER (WHERE estado IN ('rechazado','error'))::int AS con_problemas
       FROM documentos WHERE usuario_id = $1`,
    [usuarioId],
  );
  const alertas = await query(
    `SELECT d.id, t.nombre AS tipo_nombre, d.fecha_vigencia,
            (d.fecha_vigencia - CURRENT_DATE)::int AS dias_restantes
       FROM documentos d JOIN tipos_documento t ON t.id = d.tipo_id
      WHERE d.usuario_id = $1 AND d.vigente AND d.fecha_vigencia IS NOT NULL
        AND d.fecha_vigencia <= CURRENT_DATE + INTERVAL '90 days'
      ORDER BY d.fecha_vigencia`,
    [usuarioId],
  );
  return { ...r, alertas_vigencia: alertas.rows };
}

// ── Validación por el usuario (CU-05, RN-07, RN-08, RN-09) ──────────────────

const valorCampo = z
  .union([z.string().max(300), z.null()])
  .transform((v) => (typeof v === 'string' ? v.trim().replace(/\s+/g, ' ') : null))
  .transform((v) => (v === '' ? null : v));

export const esquemaValidacion = z.object({
  datos: z.record(z.string().regex(/^[a-zA-Z0-9_ñÑ]{1,60}$/), valorCampo),
});

function normalizarConfirmados(tipo: ClaveTipo, datos: Record<string, string | null>) {
  const d: Record<string, string | null> = { ...datos };
  for (const k of ['curp', 'curp_padre', 'curp_madre', 'clave_elector', 'numero_pasaporte']) {
    if (d[k]) d[k] = d[k]!.toUpperCase().replace(/\s/g, '');
  }
  for (const k of ['nombres', 'primer_apellido', 'segundo_apellido', 'nombre_completo']) {
    if (d[k]) d[k] = d[k]!.toUpperCase();
  }
  if (!d.nombre_completo && (d.nombres || d.primer_apellido)) {
    d.nombre_completo = [d.nombres, d.primer_apellido, d.segundo_apellido].filter(Boolean).join(' ');
  }
  const errores: Record<string, string> = {};
  for (const campo of CAMPOS_REQUERIDOS[tipo]) if (!d[campo]) errores[campo] = 'Campo obligatorio';
  if (d.curp && !RE_CURP.test(d.curp)) errores.curp = 'CURP con formato inválido (18 caracteres)';
  for (const k of ['fecha_nacimiento', 'fecha_emision', 'fecha_caducidad', 'fecha_expedicion', 'fecha_registro']) {
    if (d[k] && !parsearFecha(d[k])) errores[k] = 'Usa el formato dd/mm/aaaa';
  }
  if (d.año_vigencia && !RE_ANIO.test(d.año_vigencia)) errores['año_vigencia'] = 'Año de 4 dígitos';
  if (d.clave_elector && !RE_CLAVE_ELECTOR.test(d.clave_elector)) errores.clave_elector = 'Clave de elector inválida (18 caracteres)';
  if (d.numero_pasaporte && !RE_PASAPORTE.test(d.numero_pasaporte)) errores.numero_pasaporte = 'Letra seguida de 8 dígitos';
  if (d.sexo && !['H', 'M'].includes(d.sexo.toUpperCase())) errores.sexo = 'H o M';
  else if (d.sexo) d.sexo = d.sexo.toUpperCase();
  return { datos: d, errores };
}

export async function validarDocumento(usuarioId: number, id: number, datosEntrada: Record<string, string | null>) {
  const doc = await uno<{ id: number; estado: string; tipo: ClaveTipo; tipo_id: number; ruta_archivo: string }>(
    `SELECT d.id, d.estado, d.tipo_id, d.ruta_archivo, t.clave AS tipo
       FROM documentos d JOIN tipos_documento t ON t.id = d.tipo_id
      WHERE d.id = $1 AND d.usuario_id = $2`,
    [id, usuarioId],
  );
  if (!doc) throw noEncontrado('Documento no encontrado');
  if (doc.estado !== 'procesado') {
    throw new ErrorApp(409, 'ESTADO_INVALIDO', `El documento está "${doc.estado}" y no puede validarse.`);
  }

  const mongo = await resultados().findOne({ documento_id: id });
  const detectados = descifrarObjeto(mongo?.datos_extraidos) as Record<string, string | null>;
  // Solo se aceptan campos conocidos del esquema del tipo (evita inyectar campos arbitrarios)
  const permitidos = new Set([...Object.keys(detectados), ...CAMPOS_REQUERIDOS[doc.tipo], 'nombre_completo']);
  const filtrados = Object.fromEntries(Object.entries(datosEntrada).filter(([k]) => permitidos.has(k)));

  const { datos, errores } = normalizarConfirmados(doc.tipo, filtrados);
  if (Object.keys(errores).length) throw solicitudInvalida('Revisa los campos marcados', { campos: errores });

  const reglas = evaluarReglas(doc.tipo, datos, true);
  if (reglas.rechazo) {
    throw new ErrorApp(422, 'REGLA_NEGOCIO', reglas.rechazo);
  }

  const resultado = await transaccion(async (c) => {
    // RN-09: trazabilidad valor detectado -> valor confirmado
    for (const [campo, valor] of Object.entries(datos)) {
      const detectado = detectados[campo] ?? null;
      await c.query(
        `INSERT INTO validaciones_dato (documento_id, campo, valor_detectado, valor_confirmado, corregido)
         VALUES ($1, $2, $3, $4, $5)`,
        [id, campo, cifrar(detectado), cifrar(valor), (detectado ?? null) !== (valor ?? null)],
      );
    }

    // RN-07: la nueva versión validada pasa a ser la vigente
    // RN-07 por titular: la versión vigente se lleva por usuario + tipo + titular
    const titular = claveTitular(datos);
    const previa = await c.query<{ max: number | null }>(
      `SELECT MAX(version) AS max FROM documento_versiones
        WHERE usuario_id = $1 AND tipo_id = $2 AND titular_hash IS NOT DISTINCT FROM $3`,
      [usuarioId, doc.tipo_id, titular],
    );
    const version = (previa.rows[0]?.max ?? 0) + 1;
    await c.query(
      `UPDATE documentos SET vigente = FALSE
        WHERE usuario_id = $1 AND tipo_id = $2 AND id <> $3 AND titular_hash IS NOT DISTINCT FROM $4`,
      [usuarioId, doc.tipo_id, id, titular],
    );
    // Folio consecutivo por usuario (orden de confirmación). Se bloquea la fila del
    // usuario para que dos validaciones simultáneas no obtengan el mismo número.
    await c.query('SELECT id FROM usuarios WHERE id = $1 FOR UPDATE', [usuarioId]);
    const sig = await c.query<{ folio: number }>(
      'SELECT COALESCE(MAX(folio), 0) + 1 AS folio FROM documentos WHERE usuario_id = $1',
      [usuarioId],
    );
    const folio = sig.rows[0].folio;
    await c.query(
      `UPDATE documentos SET estado = 'validado', vigente = TRUE, version = $2, fecha_vigencia = $3,
                             advertencias = $4::jsonb, motivo_rechazo = NULL, folio = COALESCE(folio, $5),
                             titular_iniciales = $6, titular_hash = $7, actualizado_en = NOW()
        WHERE id = $1`,
      [id, version, reglas.fechaVigencia, JSON.stringify(reglas.advertencias), folio, inicialesTitular(datos), titular],
    );
    await c.query(
      `INSERT INTO documento_versiones (documento_id, usuario_id, tipo_id, version, ruta_archivo, titular_hash)
       VALUES ($1, $2, $3, $4, $5, $6)`,
      [id, usuarioId, doc.tipo_id, version, doc.ruta_archivo, titular],
    );

    // Perfil: el documento solo COMPLETA el perfil si es de la misma persona
    // (misma CURP) o si el perfil aún no tiene identidad. Así un usuario puede
    // procesar documentos de prueba de otras personas sin sobrescribir su perfil.
    const actual = await c.query<{ curp: string | null }>('SELECT curp FROM perfil_usuario WHERE usuario_id = $1', [usuarioId]);
    const curpPerfil = descifrar(actual.rows[0]?.curp ?? null);
    const mismaPersona = !curpPerfil || !datos.curp || curpPerfil === datos.curp;
    let perfilActualizado = false;
    if (mismaPersona) {
      const fn = parsearFecha(datos.fecha_nacimiento);
      const direccion = doc.tipo === 'ine' ? datos.domicilio ?? null : null;
      const verifica = (doc.tipo === 'ine' || doc.tipo === 'pasaporte') && !!datos.curp;
      await c.query(
        `INSERT INTO perfil_usuario (usuario_id, nombre, apellido_paterno, apellido_materno, curp, curp_hash,
                                     fecha_nacimiento, sexo, direccion, verificado, actualizado_en)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
         ON CONFLICT (usuario_id) DO UPDATE SET
           nombre           = COALESCE(perfil_usuario.nombre, EXCLUDED.nombre),
           apellido_paterno = COALESCE(perfil_usuario.apellido_paterno, EXCLUDED.apellido_paterno),
           apellido_materno = COALESCE(perfil_usuario.apellido_materno, EXCLUDED.apellido_materno),
           curp             = COALESCE(perfil_usuario.curp, EXCLUDED.curp),
           curp_hash        = COALESCE(perfil_usuario.curp_hash, EXCLUDED.curp_hash),
           fecha_nacimiento = COALESCE(perfil_usuario.fecha_nacimiento, EXCLUDED.fecha_nacimiento),
           sexo             = COALESCE(perfil_usuario.sexo, EXCLUDED.sexo),
           direccion        = COALESCE(EXCLUDED.direccion, perfil_usuario.direccion),
           verificado       = perfil_usuario.verificado OR EXCLUDED.verificado,
           actualizado_en   = NOW()`,
        [
          usuarioId,
          datos.nombres ?? null,
          datos.primer_apellido ?? null,
          datos.segundo_apellido ?? null,
          cifrar(datos.curp ?? null),
          datos.curp ? hmac(`curp:${datos.curp}`) : null,
          aISO(fn),
          datos.sexo ?? null,
          cifrar(direccion),
          verifica,
        ],
      );
      perfilActualizado = true;
    }
    return { version, perfilActualizado, folio };
  });

  await resultados().updateOne(
    { documento_id: id },
    {
      $set: {
        datos_confirmados: cifrarObjeto(datos),
        validaciones: { ...calcularValidaciones(doc.tipo, datos), confirmado_por_usuario: true },
        fecha_validacion: new Date(),
      },
    },
  );
  await registrarEvento({
    usuarioId,
    tipo: 'auditoria',
    tabla: 'documentos',
    operacion: 'VALIDAR',
    descripcion: `Documento ${id} validado por el usuario (versión ${resultado.version})`,
  });
  const advertencias = [...reglas.advertencias];
  if (!resultado.perfilActualizado) {
    advertencias.push('El documento pertenece a otra persona (CURP distinta a la de tu perfil): se guardó, pero no se usó para actualizar tu perfil.');
  }
  return {
    id,
    estado: 'validado',
    version: resultado.version,
    folio: resultado.folio,
    identificador: identificadorDocumento(resultado.folio, datos),
    advertencias,
    perfil_actualizado: resultado.perfilActualizado,
  };
}

export async function reintentarProcesamiento(usuarioId: number, id: number) {
  const r = await query(
    `UPDATE documentos SET estado = 'cargado', motivo_rechazo = NULL, intentos = 0, actualizado_en = NOW()
      WHERE id = $1 AND usuario_id = $2 AND estado IN ('error') RETURNING id`,
    [id, usuarioId],
  );
  if (!r.rowCount) throw new ErrorApp(409, 'ESTADO_INVALIDO', 'Solo se pueden reintentar documentos con error.');
  encolar(id);
  return { id, estado: 'cargado' };
}

export async function descartarDocumento(usuarioId: number, id: number) {
  const d = await uno<{ ruta_archivo: string; estado: string }>(
    `SELECT ruta_archivo, estado FROM documentos WHERE id = $1 AND usuario_id = $2`,
    [id, usuarioId],
  );
  if (!d) throw noEncontrado();
  if (d.estado === 'validado' || d.estado === 'procesando') {
    throw new ErrorApp(409, 'ESTADO_INVALIDO', 'No se puede descartar un documento validado o en proceso.');
  }
  await query('DELETE FROM documentos WHERE id = $1', [id]);
  await resultados().deleteOne({ documento_id: id });
  await almacenamiento.eliminar(d.ruta_archivo);
  await registrarEvento({
    usuarioId,
    tipo: 'auditoria',
    tabla: 'documentos',
    operacion: 'DELETE',
    descripcion: `Documento ${id} (${d.estado}) descartado por el usuario`,
  });
}


// ── Edición posterior de un documento ya validado ───────────────────────────
/**
 * El usuario puede corregir la información de un documento ya validado.
 * Estas ediciones se registran con origen = 'edicion' y NO afectan la métrica
 * de precisión de la IA (que solo usa la primera validación) ni las métricas
 * de procesamiento (metricas_ia).
 */
export async function editarDocumentoValidado(usuarioId: number, id: number, datosEntrada: Record<string, string | null>) {
  const doc = await uno<{ id: number; estado: string; tipo: ClaveTipo }>(
    `SELECT d.id, d.estado, t.clave AS tipo FROM documentos d JOIN tipos_documento t ON t.id = d.tipo_id
      WHERE d.id = $1 AND d.usuario_id = $2`,
    [id, usuarioId],
  );
  if (!doc) throw noEncontrado('Documento no encontrado');
  if (doc.estado !== 'validado') {
    throw new ErrorApp(409, 'ESTADO_INVALIDO', 'Solo se puede editar un documento ya validado; usa "Validar" primero.');
  }
  const mongo = await resultados().findOne({ documento_id: id });
  const anteriores = descifrarObjeto(mongo?.datos_confirmados ?? mongo?.datos_extraidos) as Record<string, string | null>;
  const detectados = descifrarObjeto(mongo?.datos_extraidos) as Record<string, string | null>;
  const permitidos = new Set([...Object.keys(detectados), ...Object.keys(anteriores), ...CAMPOS_REQUERIDOS[doc.tipo], 'nombre_completo']);
  const filtrados = Object.fromEntries(Object.entries(datosEntrada).filter(([k]) => permitidos.has(k)));

  const { datos, errores } = normalizarConfirmados(doc.tipo, filtrados);
  if (Object.keys(errores).length) throw solicitudInvalida('Revisa los campos marcados', { campos: errores });
  const reglas = evaluarReglas(doc.tipo, datos, true);
  if (reglas.rechazo) throw new ErrorApp(422, 'REGLA_NEGOCIO', reglas.rechazo);

  const cambios = Object.entries(datos).filter(([k, v]) => (anteriores[k] ?? null) !== (v ?? null));
  await transaccion(async (c) => {
    for (const [campo, valor] of cambios) {
      await c.query(
        `INSERT INTO validaciones_dato (documento_id, campo, valor_detectado, valor_confirmado, corregido, origen)
         VALUES ($1, $2, $3, $4, TRUE, 'edicion')`,
        [id, campo, cifrar(anteriores[campo] ?? null), cifrar(valor)],
      );
    }
    await c.query(
      `UPDATE documentos SET fecha_vigencia = $2, advertencias = $3::jsonb, titular_iniciales = $4,
                             titular_hash = $5, editado_en = NOW(), actualizado_en = NOW() WHERE id = $1`,
      [id, reglas.fechaVigencia, JSON.stringify(reglas.advertencias), inicialesTitular(datos), claveTitular(datos)],
    );
  });
  await resultados().updateOne(
    { documento_id: id },
    {
      $set: {
        datos_confirmados: cifrarObjeto(datos),
        validaciones: { ...calcularValidaciones(doc.tipo, datos), confirmado_por_usuario: true },
        fecha_edicion: new Date(),
      },
    },
  );
  await registrarEvento({
    usuarioId,
    tipo: 'auditoria',
    tabla: 'documentos',
    operacion: 'EDITAR',
    descripcion: `Documento ${id} editado por el usuario (${cambios.length} campo(s): ${cambios.map(([k]) => k).join(', ') || 'sin cambios'})`,
  });
  return { id, estado: 'validado', campos_modificados: cambios.map(([k]) => k), advertencias: reglas.advertencias };
}


/** Texto OCR crudo (RF-IA-07): copia cifrada en Mongo; si no existe, se pide al módulo de IA. */
export async function textoOcr(usuarioId: number, id: number): Promise<string> {
  const d = await uno<{ ia_doc_id: string | null }>('SELECT ia_doc_id FROM documentos WHERE id = $1 AND usuario_id = $2', [id, usuarioId]);
  if (!d) throw noEncontrado('Documento no encontrado');
  const mongo = await resultados().findOne({ documento_id: id });
  const local = descifrar(mongo?.texto_crudo ?? null);
  if (local) return local;
  if (!d.ia_doc_id) throw noEncontrado('El documento aún no tiene texto procesado');
  try {
    const t = await textoCrudoIA(d.ia_doc_id);
    if (t === null) throw noEncontrado('El texto OCR ya no está disponible en el módulo de IA');
    await resultados().updateOne({ documento_id: id }, { $set: { texto_crudo: cifrar(t) } });
    return t;
  } catch (e) {
    if (e instanceof ErrorApp) throw e;
    throw new ErrorApp(503, 'IA_NO_DISPONIBLE', 'No se pudo obtener el texto del módulo de IA. Verifica que esté en ejecución.');
  }
}
