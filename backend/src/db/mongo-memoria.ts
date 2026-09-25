/**
 * Sustituto mínimo EN MEMORIA de la colección de MongoDB (solo las operaciones
 * que usa el backend). Se activa con MONGO_URL=memory:// para pruebas o para
 * desarrollo sin MongoDB. Los datos se pierden al reiniciar: NO usar en producción.
 */
import { ObjectId } from 'mongodb';

const coincide = (doc: any, filtro: any) =>
  Object.entries(filtro).every(([k, v]) => (v instanceof ObjectId ? doc[k]?.equals?.(v) : doc[k] === v));

export class ColeccionMemoria {
  docs: any[] = [];
  async createIndex() {
    return 'ok';
  }
  async findOne(f: any) {
    return this.docs.find((d) => coincide(d, f)) ?? null;
  }
  async findOneAndReplace(f: any, nuevo: any) {
    const i = this.docs.findIndex((d) => coincide(d, f));
    const doc = { ...nuevo, _id: i >= 0 ? this.docs[i]._id : new ObjectId() };
    if (i >= 0) this.docs[i] = doc;
    else this.docs.push(doc);
    return doc;
  }
  async updateOne(f: any, u: any) {
    const d = this.docs.find((x) => coincide(x, f));
    if (d) Object.assign(d, u.$set ?? {});
    return { matchedCount: d ? 1 : 0 };
  }
  async deleteOne(f: any) {
    this.docs = this.docs.filter((d) => !coincide(d, f));
  }
}

export const coleccion = new ColeccionMemoria();
