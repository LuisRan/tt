/** Pruebas unitarias de reglas de negocio y criptografía (sin BD). */
import { describe, expect, it, vi } from 'vitest';

vi.mock('../src/db/mongo.js', () => ({ resultados: () => ({}) }));

const { evaluarReglas, calcularValidaciones, parsearFecha, identificadorDocumento, inicialesTitular } = await import('../src/services/documentos.js');
const crypto = await import('../src/lib/crypto.js');

const fechaHace = (dias: number) => {
  const d = new Date(Date.now() - dias * 86_400_000);
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}/${d.getFullYear()}`;
};

describe('reglas de negocio', () => {
  it('RN-06: CURP reciente se acepta y fija vigencia a 90 días', () => {
    const r = evaluarReglas('curp', { fecha_emision: fechaHace(10) });
    expect(r.rechazo).toBeNull();
    expect(r.fechaVigencia).not.toBeNull();
  });
  it('RN-06: CURP con más de 3 meses se rechaza', () => {
    expect(evaluarReglas('curp', { fecha_emision: fechaHace(120) }).rechazo).toMatch(/3 meses/);
  });
  it('RN-06: sin fecha de emisión se advierte al procesar y se exige al validar', () => {
    expect(evaluarReglas('curp', {}).advertencias.length).toBe(1);
    expect(evaluarReglas('curp', {}, true).rechazo).toMatch(/fecha de emisión/);
  });
  it('RN-11: INE vencida genera advertencia y fecha de vigencia 31/12', () => {
    const r = evaluarReglas('ine', { año_vigencia: '2020', año_emision: '2010' });
    expect(r.fechaVigencia).toBe('2020-12-31');
    expect(r.advertencias.join(' ')).toMatch(/venció/);
    expect(r.advertencias.join(' ')).toMatch(/2016/);
  });
  it('valida formatos (Fig. 32 "validaciones")', () => {
    const v = calcularValidaciones('ine', { curp: 'SACR760818HDFVNL08', clave_elector: 'MAL', nombres: 'X', primer_apellido: 'Y' });
    expect(v.curp_valido).toBe(true);
    expect(v.clave_elector_valida).toBe(false);
  });
  it('parsea solo fechas reales', () => {
    expect(parsearFecha('31/02/2020')).toBeNull();
    expect(parsearFecha('29/02/2020')).not.toBeNull();
  });
});

describe('criptografía', () => {
  it('AES-256-GCM ida y vuelta, y detecta manipulación', () => {
    const c = crypto.cifrar('SACR760818HDFVNL08')!;
    expect(c.startsWith('enc:v1:')).toBe(true);
    expect(crypto.descifrar(c)).toBe('SACR760818HDFVNL08');
    const alterado = c.slice(0, -4) + (c.endsWith('AAAA') ? 'BBBB' : 'AAAA');
    expect(crypto.descifrar(alterado)).toBeNull();
  });
  it('códigos 2FA de 6 dígitos', () => {
    for (let i = 0; i < 50; i++) expect(crypto.generarCodigo()).toMatch(/^\d{6}$/);
  });
  it('tokens firmados expiran y no se pueden falsificar', async () => {
    const t = crypto.firmarToken({ d: 1 }, 60);
    expect(crypto.verificarToken(t)?.d).toBe(1);
    expect(crypto.verificarToken(t.replace(/.$/, (x) => (x === 'a' ? 'b' : 'a')))).toBeNull();
    expect(crypto.verificarToken(crypto.firmarToken({ d: 1 }, -1))).toBeNull();
  });
});

describe('identificador visible del documento', () => {
  it('folio de 4 dígitos + iniciales de nombre, primer y segundo apellido', () => {
    expect(identificadorDocumento(1, { nombres: 'Carlos', primer_apellido: 'Saavedra', segundo_apellido: 'Cinta' })).toBe('0001_CSC');
    expect(identificadorDocumento(27, { nombres: 'ÁNGEL', primer_apellido: 'ÑUÑEZ' })).toBe('0027_AN'); // ASCII: sirve también como nombre de archivo
  });
  it('omite partículas y usa el nombre completo si faltan los campos', () => {
    expect(inicialesTitular({ nombres: 'MARIA', primer_apellido: 'DE LA TORRE', segundo_apellido: 'DEL VALLE' })).toBe('MTV');
    expect(inicialesTitular({ nombre_completo: 'JUAN PEREZ LOPEZ' })).toBe('JPL');
    expect(inicialesTitular({})).toBe('XXX');
    expect(identificadorDocumento(null, {})).toBeNull();
  });
});
