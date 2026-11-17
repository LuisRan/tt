import { describe, expect, it } from 'vitest';
import { cifrar, descifrar, cifrarBuffer, descifrarBuffer, cifrarObjeto, descifrarObjeto } from '../src/lib/crypto.js';
import { config } from '../src/config.js';

describe('cifrado autenticado y compatibilidad de documentos', () => {
  it('rechaza texto legado cuando el despliegue lo prohíbe', () => {
    const anterior = config.ALLOW_LEGACY_PLAINTEXT;
    try {
      (config as any).ALLOW_LEGACY_PLAINTEXT = false;
      expect(() => descifrar('texto sin proteger')).toThrow();
      expect(() => descifrarObjeto({ numero: 17 })).toThrow();
    } finally { (config as any).ALLOW_LEGACY_PLAINTEXT = anterior; }
  });
  it('conserva tipos JSON anidados sin guardar sus valores legibles', () => {
    const dato = { nombre: 'Persona sintética', domicilio: { numero: 17, confirmado: true }, arreglo: ['ñ', null, 42], vacio: '' };
    const cifrado = cifrarObjeto(dato);
    expect(JSON.stringify(cifrado)).not.toContain('Persona sintética');
    expect(cifrado.nombre).toMatch(/^enc:v2:/);
    expect(descifrarObjeto(cifrado)).toEqual(dato);
  });
  it('lee los registros v1 existentes', () => {
    const antiguo = { nombre: cifrar('Persona sintética') };
    expect(descifrarObjeto(antiguo)).toEqual({ nombre: 'Persona sintética' });
  });
  it('no convierte un fallo de autenticidad en un campo vacío', () => {
    const paquete = cifrarBuffer(Buffer.from('sintético'));
    paquete[13] ^= 1;
    expect(() => descifrar('enc:v1:' + paquete.toString('base64'))).toThrow();
    expect(() => descifrarBuffer(Buffer.alloc(3))).toThrow();
  });
  it('usa nonces distintos y conserva los valores que parecen prefijos', () => {
    expect(cifrar('prueba')).not.toEqual(cifrar('prueba'));
    expect(descifrarObjeto(cifrarObjeto({ v: 'enc:v2:literal del usuario' }))).toEqual({ v: 'enc:v2:literal del usuario' });
  });
  it('limita profundidad y rechaza tipos ajenos a JSON', () => {
    expect(() => cifrarObjeto({ fecha: new Date() })).toThrow();
    expect(() => cifrarObjeto({ infinito: Infinity })).toThrow();
    let profundo: any = 'dato';
    for (let i = 0; i < 35; i++) profundo = { hijo: profundo };
    expect(() => cifrarObjeto(profundo)).toThrow();
  });
});
