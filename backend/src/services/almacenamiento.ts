/**
 * Almacenamiento de archivos (puerto + adaptador local cifrado).
 * En la nube (Azure Storage, sección 4.6) basta con implementar
 * IAlmacenamiento con @azure/storage-blob sin tocar el resto del backend.
 */
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { config } from '../config.js';
import { cifrarBuffer, descifrarBuffer, idAleatorio } from '../lib/crypto.js';

export interface IAlmacenamiento {
  guardar(usuarioId: number, datos: Buffer, extension: string): Promise<string>;
  leer(ruta: string): Promise<Buffer>;
  eliminar(ruta: string): Promise<void>;
}

class AlmacenamientoLocalCifrado implements IAlmacenamiento {
  constructor(private readonly raiz: string) {}

  private absoluta(ruta: string): string {
    const abs = path.resolve(this.raiz, ruta);
    if (!abs.startsWith(path.resolve(this.raiz) + path.sep)) throw new Error('Ruta fuera del almacenamiento');
    return abs;
  }

  async guardar(usuarioId: number, datos: Buffer, extension: string): Promise<string> {
    const relativa = path.posix.join(String(usuarioId), `${Date.now()}-${idAleatorio(8)}${extension}.enc`);
    const abs = this.absoluta(relativa);
    await mkdir(path.dirname(abs), { recursive: true, mode: 0o700 });
    await writeFile(abs, cifrarBuffer(datos), { mode: 0o600 });
    return relativa;
  }

  async leer(ruta: string): Promise<Buffer> {
    return descifrarBuffer(await readFile(this.absoluta(ruta)));
  }

  async eliminar(ruta: string): Promise<void> {
    await rm(this.absoluta(ruta), { force: true });
  }
}

export const almacenamiento: IAlmacenamiento = new AlmacenamientoLocalCifrado(config.STORAGE_DIR);
