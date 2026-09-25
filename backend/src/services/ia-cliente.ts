/**
 * Cliente HTTP del módulo de IA (Flask) — interfaz IServicio_IA de la Fig. 25.
 * Implementa el contrato de la Tabla 23 del documento.
 */
import { config } from '../config.js';
import { logger } from '../lib/logger.js';

export interface EnvelopeIA {
  doc_id: string;
  user_id: string;
  tipo_documento: 'ine' | 'curp' | 'acta_nacimiento' | 'pasaporte';
  datos: Record<string, string | null>;
  metadatos: {
    adaptador_usado?: string;
    pipeline_version?: string;
    timestamp_procesamiento?: string;
    hash_sha256?: string;
    tiempo_procesamiento_ms?: number;
    errores_validacion?: string[];
    campos_descartados_por_alucinacion?: string[];
    confianza?: number;
    campos_detectados?: number;
    modelo_llm?: string | null;
    [k: string]: unknown;
  };
  datos_adicionales?: Record<string, unknown>;
}

export class ErrorIA extends Error {
  constructor(
    public readonly status: number,
    public readonly codigo: string,
    mensaje: string,
    public readonly reintentable: boolean,
  ) {
    super(mensaje);
  }
}

function cabeceras(): Record<string, string> {
  return config.IA_API_KEY ? { 'X-API-Key': config.IA_API_KEY } : {};
}

export async function procesarEnIA(pdf: Buffer, userId: string, docId: string): Promise<EnvelopeIA> {
  const form = new FormData();
  form.append('archivo', new Blob([new Uint8Array(pdf)], { type: 'application/pdf' }), `${docId}.pdf`);
  form.append('user_id', userId);
  form.append('doc_id', docId);

  let respuesta: Response;
  try {
    respuesta = await fetch(`${config.IA_URL}/api/v1/documentos/procesa`, {
      method: 'POST',
      headers: cabeceras(),
      body: form,
      signal: AbortSignal.timeout(config.IA_TIMEOUT_MS),
    });
  } catch (err: any) {
    logger.error({ err: err?.message }, 'módulo de IA inalcanzable');
    throw new ErrorIA(503, 'IA_NO_DISPONIBLE', 'El módulo de IA no respondió. Se reintentará.', true);
  }

  const cuerpo = (await respuesta.json().catch(() => ({}))) as any;
  if (respuesta.ok) return cuerpo as EnvelopeIA;

  const mensaje = cuerpo?.error ?? `Error ${respuesta.status} del módulo de IA`;
  const codigo = cuerpo?.codigo ?? 'IA_ERROR';
  // 415 no soportado, 422 PDF inválido, 400/413: errores del documento -> no reintentar
  const reintentable = respuesta.status >= 500 || respuesta.status === 429;
  throw new ErrorIA(respuesta.status, codigo, mensaje, reintentable);
}

export async function textoCrudoIA(docId: string): Promise<string | null> {
  let r: Response;
  try {
    r = await fetch(`${config.IA_URL}/api/v1/documentos/${encodeURIComponent(docId)}/texto`, {
      headers: { ...cabeceras(), Accept: 'application/json' },
      signal: AbortSignal.timeout(15_000),
    });
  } catch (err: any) {
    throw new ErrorIA(503, 'IA_NO_DISPONIBLE', `Módulo de IA inalcanzable: ${err?.message ?? err}`, true);
  }
  if (r.status === 404) return null;
  if (!r.ok) {
    const cuerpo = await r.text().catch(() => '');
    logger.warn({ status: r.status, cuerpo: cuerpo.slice(0, 200) }, 'el módulo de IA no devolvió el texto');
    throw new ErrorIA(r.status, 'IA_ERROR', 'No se pudo recuperar el texto', r.status >= 500);
  }
  const j = (await r.json()) as { texto: string };
  return j.texto;
}

export async function saludIA(): Promise<Record<string, any> | null> {
  try {
    const r = await fetch(`${config.IA_URL}/health`, { signal: AbortSignal.timeout(8_000) });
    return (await r.json()) as Record<string, any>;
  } catch {
    return null;
  }
}
