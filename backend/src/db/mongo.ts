/**
 * MongoDB: modelo documental NoSQL (sección 7.4, Fig. 32) para los resultados
 * semiestructurados del OCR/IA, cuyos campos varían por tipo de documento.
 */
import { Collection, Db, MongoClient, ObjectId } from 'mongodb';
import { config } from '../config.js';
import { logger } from '../lib/logger.js';
import { ColeccionMemoria } from './mongo-memoria.js';

export interface ResultadoOcrDoc {
  _id?: ObjectId;
  documento_id: number;
  usuario_id: number;
  tipo_documento: string;            // "INE" | "CURP" | "ACTA_NACIMIENTO" | "PASAPORTE"
  datos_extraidos: Record<string, unknown>;   // valores cifrados
  campos_detectados: { campo: string; valor: string | null }[]; // valores cifrados
  validaciones: Record<string, boolean>;
  datos_confirmados?: Record<string, unknown>; // valores cifrados (tras RN-08)
  confianza_ocr: number | null;
  modelo_ia: string | null;
  adaptador: string | null;
  metadatos: Record<string, unknown>;
  ia_doc_id: string;
  fecha_procesamiento: Date;
  fecha_validacion?: Date;
  fecha_edicion?: Date;
  texto_crudo?: string | null; // texto OCR cifrado (copia para consulta sin depender del módulo de IA)
}

let cliente: MongoClient | null = null;
let db: Db | null = null;
let memoria: ColeccionMemoria | null = null;

export async function conectarMongo(reintentos = 30): Promise<Db | null> {
  if (config.MONGO_URL.startsWith('memory://')) {
    memoria ??= new ColeccionMemoria();
    logger.warn('MongoDB en MEMORIA (MONGO_URL=memory://): los resultados OCR se pierden al reiniciar');
    return null;
  }
  if (db) return db;
  for (let intento = 1; ; intento++) {
    try {
      cliente = new MongoClient(config.MONGO_URL, { serverSelectionTimeoutMS: 5000 });
      await cliente.connect();
      db = cliente.db(config.MONGO_DB);
      await resultados().createIndex({ documento_id: 1 }, { unique: true });
      await resultados().createIndex({ usuario_id: 1, fecha_procesamiento: -1 });
      logger.info('conectado a MongoDB');
      return db;
    } catch (err) {
      await cliente?.close().catch(() => undefined);
      cliente = null;
      if (intento >= reintentos) throw err;
      logger.warn({ intento }, 'MongoDB aún no está listo, reintentando...');
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
}

export function resultados(): Collection<ResultadoOcrDoc> {
  if (memoria) return memoria as unknown as Collection<ResultadoOcrDoc>;
  if (!db) throw new Error('MongoDB no inicializado');
  return db.collection<ResultadoOcrDoc>('resultados_ocr');
}

export async function mongoSano(): Promise<boolean> {
  if (memoria) return true;
  try {
    await db?.command({ ping: 1 });
    return !!db;
  } catch {
    return false;
  }
}

export async function cerrarMongo(): Promise<void> {
  await cliente?.close();
  cliente = null;
  db = null;
}
