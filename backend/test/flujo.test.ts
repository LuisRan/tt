/**
 * Prueba de integración end-to-end del backend:
 *   PostgreSQL real + módulo de IA (Flask) real + Mongo en memoria.
 * Recorre los casos de uso CUP-01..07 y CUA-03..06.
 *
 * Requiere: TEST_DATABASE_URL, IA_URL (Flask corriendo) y los PDFs de ejemplo
 * en ia_documentos/data/input/.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { strFromU8, unzipSync } from 'fflate';
import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

const correos: { para: string; texto: string }[] = [];

vi.mock('../src/services/correo.js', () => ({
  enviarCorreo: async (c: { para: string; texto: string }) => {
    correos.push(c);
    return { proveedor: 'test' };
  },
}));

const PDFS = path.resolve(__dirname, '../../ia_documentos/data/input');
const ultimoCodigo = (para: string) => {
  const c = [...correos].reverse().find((x) => x.para === para);
  return /(\d{6})/.exec(c?.texto ?? '')?.[1] ?? '';
};

let app: any;
let pool: any;
let coleccion: any;

async function esperarEstado(agente: request.Agent, id: number, finales = ['procesado', 'rechazado', 'error']) {
  for (let i = 0; i < 240; i++) {
    const r = await agente.get(`/api/documentos/${id}/estado`);
    if (finales.includes(r.body.estado)) return r.body;
    await new Promise((res) => setTimeout(res, 1000));
  }
  throw new Error('timeout esperando procesamiento');
}

beforeAll(async () => {
  const { migrar, sembrarAdmin } = await import('../src/db/migrar.js');
  ({ pool } = await import('../src/db/pg.js'));
  await pool.query('DROP SCHEMA public CASCADE; CREATE SCHEMA public;');
  await migrar();
  await sembrarAdmin();
  const mongo = await import('../src/db/mongo.js');
  await mongo.conectarMongo();
  coleccion = mongo.resultados();
  ({ crearApp: app } = await import('../src/app.js'));
  app = app();
});

afterAll(async () => {
  await pool?.end();
});

describe('flujo completo', () => {
  let usuario: request.Agent;
  beforeAll(() => {
    usuario = request.agent(app);
  });
  const correo = 'luis.prueba@example.com';
  const password = 'Contrasena123';
  let ineId = 0;

  const confirmarPassword = (a: request.Agent) => a.post('/api/auth/confirmar-password').send({ password });

  it('rechaza contraseñas débiles', async () => {
    const r = await usuario.post('/api/auth/registro').send({ correo, password: 'corta' });
    expect(r.status).toBe(400);
  });

  it('CUP-01 registro: correo + código + términos abre la sesión sin exigir documentos', async () => {
    const r = await usuario.post('/api/auth/registro').send({ correo, password });
    expect(r.status).toBe(201);
    expect(r.body.requiereCodigo).toBe(true);
    const malo = await usuario.post('/api/auth/registro/verificar').send({ correo, codigo: '000000' });
    expect(malo.status).toBe(400);
    const ok = await usuario.post('/api/auth/registro/verificar').send({ correo, codigo: ultimoCodigo(correo) });
    expect(ok.status).toBe(200);
    const t = await usuario.post('/api/auth/registro/terminos').send({ acepta: true });
    expect(t.body.redirigir).toBe('/app/tutorial');
    const yo = await usuario.get('/api/auth/yo');
    expect(yo.status).toBe(200);
    expect(yo.body.tutorial_visto).toBe(false);
    expect(yo.body.dosfa_activo).toBe(false);
    expect((await usuario.post('/api/perfil/tutorial').send({})).status).toBe(200);
    expect((await usuario.get('/api/auth/yo')).body.tutorial_visto).toBe(true);
  });

  it('CUP-03/04 sube la INE (requiere contraseña) y el módulo de IA la procesa', async () => {
    const sin = await usuario.post('/api/documentos').field('tipo', 'ine').attach('archivo', readFileSync(path.join(PDFS, 'ine2.pdf')), 'ine2.pdf');
    expect(sin.status).toBe(428);
    expect((await confirmarPassword(usuario)).status).toBe(200);
    const r = await usuario.post('/api/documentos').field('tipo', 'ine').attach('archivo', readFileSync(path.join(PDFS, 'ine2.pdf')), 'ine2.pdf');
    expect(r.status).toBe(202);
    ineId = r.body.id;
    const estado = await esperarEstado(usuario, ineId);
    expect(estado.estado).toBe('procesado');
    const det = await usuario.get(`/api/documentos/${ineId}`);
    expect(det.body.resultado.tipo_documento).toBe('INE');
    expect(det.body.resultado.datos_extraidos.clave_elector).toBe('BRCRPL94062222M400');
    expect(det.body.resultado.datos_extraidos.curp).toBe('BACP940622MQTRRL08');
    expect(det.body.ruta_archivo).toBeUndefined();
    const crudo = coleccion.docs.find((d: any) => d.documento_id === ineId);
    expect(String(crudo.datos_extraidos.clave_elector)).toMatch(/^enc:v1:/);
    expect(String(crudo.texto_crudo)).toMatch(/^enc:v1:/);
    // El texto OCR ya no se consulta en la plataforma (solo lo lleva la extensión)
    expect((await usuario.get(`/api/documentos/${ineId}/texto`)).status).toBe(404);
  });

  it('CUP-05 valida y la INE propia completa el perfil', async () => {
    const incompleto = await usuario.post(`/api/documentos/${ineId}/validar`).send({ datos: { nombres: 'PAULINA', curp: null } });
    expect(incompleto.status).toBe(400);
    const ok = await usuario.post(`/api/documentos/${ineId}/validar`).send({
      datos: {
        nombres: 'Paulina', primer_apellido: 'Barajas', segundo_apellido: 'De la Cruz', curp: 'BACP940622MQTRRL08',
        fecha_nacimiento: '22/06/1994', sexo: 'M', clave_elector: 'BRCRPL94062222M400',
        domicilio: 'COL LAZARO CARDENAS 76087 QUERETARO, QRO', año_vigencia: '2034',
      },
    });
    expect(ok.status).toBe(200);
    expect(ok.body.version).toBe(1);
    expect(ok.body.perfil_actualizado).toBe(true);
    // Folio 1 + iniciales del titular (se omiten partículas: "DE LA CRUZ" -> C)
    expect(ok.body.identificador).toBe('0001_PBC');
    const yo = await usuario.get('/api/auth/yo');
    expect(yo.body.nombre).toBe('PAULINA');
    expect(yo.body.verificado).toBe(true);
  });

  it('editar un documento validado no cambia la métrica de precisión de la IA', async () => {
    const antes = await pool.query(`SELECT COUNT(*)::int n, SUM(CASE WHEN corregido THEN 1 ELSE 0 END)::int c FROM validaciones_dato WHERE origen='validacion'`);
    const r = await usuario.put(`/api/documentos/${ineId}/datos`).send({
      datos: {
        nombres: 'PAULINA', primer_apellido: 'BARAJAS', segundo_apellido: 'DE LA CRUZ', curp: 'BACP940622MQTRRL08',
        fecha_nacimiento: '22/06/1994', sexo: 'M', clave_elector: 'BRCRPL94062222M400',
        domicilio: 'CALLE NUEVA 10, QUERETARO, QRO', año_vigencia: '2034',
      },
    });
    expect(r.status).toBe(200);
    expect(r.body.campos_modificados).toContain('domicilio');
    const despues = await pool.query(`SELECT COUNT(*)::int n, SUM(CASE WHEN corregido THEN 1 ELSE 0 END)::int c FROM validaciones_dato WHERE origen='validacion'`);
    expect(despues.rows[0]).toEqual(antes.rows[0]);
    const ed = await pool.query(`SELECT COUNT(*)::int n FROM validaciones_dato WHERE origen='edicion'`);
    expect(ed.rows[0].n).toBeGreaterThan(0);
    const det = await usuario.get(`/api/documentos/${ineId}`);
    expect(det.body.resultado.datos_confirmados.domicilio).toBe('CALLE NUEVA 10, QUERETARO, QRO');
  });

  it('un documento de otra persona se guarda pero no sobrescribe el perfil', async () => {
    const r = await usuario.post('/api/documentos').field('tipo', 'ine').attach('archivo', readFileSync(path.join(PDFS, 'ine.pdf')), 'ine.pdf');
    const e = await esperarEstado(usuario, r.body.id);
    expect(e.estado).toBe('procesado');
    const det = await usuario.get(`/api/documentos/${r.body.id}`);
    expect(det.body.resultado.datos_extraidos.curp).toBe('SACR760818HDFVNL08');
    const datos = { ...det.body.resultado.datos_extraidos };
    const v = await usuario.post(`/api/documentos/${r.body.id}/validar`).send({ datos });
    expect(v.status).toBe(200);
    expect(v.body.perfil_actualizado).toBe(false);
    expect(v.body.identificador).toMatch(/^0002_[A-Z]SC$/);
    // La vigencia se lleva por titular: la INE propia sigue vigente
    expect(v.body.version).toBe(1);
    const propia = await usuario.get(`/api/documentos/${ineId}`);
    expect(propia.body.vigente).toBe(true);
    expect(propia.body.versiones).toHaveLength(1);
    expect((await usuario.get('/api/auth/yo')).body.nombre).toBe('PAULINA');
    const lista = await usuario.get('/api/documentos');
    expect(lista.body.map((d: any) => d.identificador).filter(Boolean).sort()).toEqual(['0001_PBC', v.body.identificador]);
  });

  it('extensión: paquete personalizado, vinculación con código y solo documentos validados', async () => {
    const info = await usuario.get('/api/extension/info');
    expect(info.body.extension_id).toMatch(/^[a-p]{32}$/);

    const zip = await usuario
      .post('/api/extension/paquete')
      .send({})
      .buffer(true)
      .parse((res, cb) => {
        const partes: Buffer[] = [];
        res.on('data', (c: Buffer) => partes.push(c));
        res.on('end', () => cb(null, Buffer.concat(partes)));
      });
    expect(zip.status).toBe(200);
    expect(zip.headers['content-type']).toBe('application/zip');
    const archivos = unzipSync(new Uint8Array(zip.body as Buffer));
    const manifest = JSON.parse(strFromU8(archivos['ocr-platform-extension/manifest.json']));
    expect(manifest.host_permissions).toEqual(['https://localhost/*']);
    expect(manifest.externally_connectable.matches).toEqual(['https://localhost/*']);
    expect(archivos['ocr-platform-extension/popup.html']).toBeDefined();
    expect(archivos['ocr-platform-extension/README.md']).toBeUndefined();
    const configJs = strFromU8(archivos['ocr-platform-extension/config.js']);
    const codigo = /"codigo": "([A-Z2-9]{4}-[A-Z2-9]{4})"/.exec(configJs)![1];

    // La extensión se vincula sin cookies y desde su propio origen
    const origen = `chrome-extension://${info.body.extension_id}`;
    const v = await request(app).post('/api/extension/vincular').set('Origin', origen).send({ codigo, nombre: 'Chrome en macOS' });
    expect(v.status).toBe(200);
    expect(v.body.token).toMatch(/^ext_/);
    expect(v.body.correo).toBe(correo);
    // Código de un solo uso
    expect((await request(app).post('/api/extension/vincular').set('Origin', origen).send({ codigo })).status).toBe(400);
    const guardado = await pool.query('SELECT token_hash FROM extension_dispositivos');
    expect(guardado.rows[0].token_hash).not.toContain(v.body.token);

    expect((await request(app).get('/api/extension/documentos')).status).toBe(401);
    const docs = await request(app).get('/api/extension/documentos').set('Authorization', `Bearer ${v.body.token}`);
    expect(docs.status).toBe(200);
    expect(docs.body.documentos.map((d: any) => d.identificador)).toEqual(['0001_PBC', expect.stringMatching(/^0002_/)]);
    const ine = docs.body.documentos[0];
    expect(ine.tipo).toBe('ine');
    expect(docs.body.documentos.every((d: any) => d.vigente)).toBe(true);
    expect(ine.titular).toBe('PAULINA BARAJAS DE LA CRUZ');
    expect(ine.campos.find((c: any) => c.campo === 'curp')).toMatchObject({ etiqueta: 'CURP', valor: 'BACP940622MQTRRL08' });
    expect(ine.texto).toContain('CURP: BACP940622MQTRRL08');
    expect(ine.texto).toMatch(/^0001_PBC - INE/);
    expect(ine.texto_escaneado).toMatch(/ELECTOR/i);

    // Código manual (requiere contraseña reciente) y listado/desvinculación desde la plataforma
    const cod = await usuario.post('/api/extension/codigo').send({});
    expect(cod.body.codigo).toMatch(/^[A-Z2-9]{4}-[A-Z2-9]{4}$/);
    const disp = await usuario.get('/api/extension/dispositivos');
    expect(disp.body).toHaveLength(1);
    expect((await usuario.delete(`/api/extension/dispositivos/${disp.body[0].id}`)).status).toBe(200);
    expect((await request(app).get('/api/extension/documentos').set('Authorization', `Bearer ${v.body.token}`)).status).toBe(401);
  });

  it('al arrancar se completan titular e iniciales de documentos anteriores y se recalcula la vigencia', async () => {
    // Simula datos creados antes de la regla por titular
    await pool.query(`UPDATE documentos SET titular_hash = NULL, titular_iniciales = NULL, vigente = (id <> $1) WHERE estado = 'validado'`, [ineId]);
    await pool.query('UPDATE documento_versiones SET titular_hash = NULL');
    const { completarIdentificadores } = await import('../src/services/documentos.js');
    expect(await completarIdentificadores()).toBe(2);
    const r = await pool.query(`SELECT id, vigente, titular_iniciales, titular_hash FROM documentos WHERE estado = 'validado' ORDER BY folio`);
    expect(r.rows.map((x: any) => x.vigente)).toEqual([true, true]);
    expect(r.rows[0].titular_iniciales).toBe('PBC');
    expect(r.rows[0].titular_hash).not.toBe(r.rows[1].titular_hash);
  });

  it('el usuario edita su perfil desde Configuración', async () => {
    const r = await usuario.put('/api/perfil').send({ direccion: 'AV. SIEMPRE VIVA 742', fecha_nacimiento: '22/06/1994' });
    expect(r.status).toBe(200);
    expect(r.body.direccion).toBe('AV. SIEMPRE VIVA 742');
    expect(r.body.editado_manual).toBe(true);
    const malo = await usuario.put('/api/perfil').send({ curp: 'NOESCURP' });
    expect(malo.status).toBe(400);
    const fila = await pool.query('SELECT curp, direccion FROM perfil_usuario LIMIT 1');
    expect(fila.rows[0].direccion).toMatch(/^enc:v1:/);
  });

  it('CUP-02 login sin 2FA y luego con 2FA activado por el usuario', async () => {
    const a = request.agent(app);
    expect((await a.post('/api/auth/login').send({ correo, password: 'Incorrecta123' })).status).toBe(401);
    const directo = await a.post('/api/auth/login').send({ correo, password });
    expect(directo.body.requiere2fa).toBe(false);
    expect(directo.body.redirigir).toBe('/app');
    expect((await a.get('/api/auth/yo')).status).toBe(200);

    expect((await confirmarPassword(a)).status).toBe(200);
    expect((await a.put('/api/perfil/seguridad').send({ dosfa_activo: true })).status).toBe(200);

    const b = request.agent(app);
    const r = await b.post('/api/auth/login').send({ correo, password });
    expect(r.body.requiere2fa).toBe(true);
    expect((await b.get('/api/auth/yo')).status).toBe(401);
    const v = await b.post('/api/auth/login/verificar').send({ codigo: ultimoCodigo(correo) });
    expect(v.status).toBe(200);
    expect((await b.get('/api/auth/yo')).status).toBe(200);
  });

  it('CUP-07 CURP con más de 3 meses se rechaza', async () => {
    const r = await usuario.post('/api/documentos').field('tipo', 'curp').attach('archivo', readFileSync(path.join(PDFS, 'curp.pdf')), 'curp.pdf');
    expect(r.status).toBe(202);
    const e = await esperarEstado(usuario, r.body.id);
    expect(e.estado).toBe('rechazado');
    expect(e.motivo_rechazo).toMatch(/3 meses/);
  });

  it('rechaza tipo distinto al seleccionado y formatos no válidos; acepta JPG', async () => {
    const r = await usuario.post('/api/documentos').field('tipo', 'ine').attach('archivo', readFileSync(path.join(PDFS, 'pasap.pdf')), 'pasap.pdf');
    const e = await esperarEstado(usuario, r.body.id);
    expect(e.estado).toBe('rechazado');
    expect(e.motivo_rechazo).toMatch(/Pasaporte/);
    const txt = await usuario.post('/api/documentos').field('tipo', 'ine').attach('archivo', Buffer.from('hola'), 'x.pdf');
    expect(txt.status).toBe(415);
    const jpg = await usuario.post('/api/documentos').field('tipo', 'ine').attach('archivo', readFileSync('/tmp/ine2-1.jpg'), 'foto.jpg');
    expect(jpg.status).toBe(202);
    const e2 = await esperarEstado(usuario, jpg.body.id);
    expect(e2.estado).toBe('procesado');
  });

  it('CUP-06 lista, resumen y archivo con URL firmada', async () => {
    const lista = await usuario.get('/api/documentos');
    expect(lista.body.length).toBeGreaterThanOrEqual(3);
    const u = await usuario.get(`/api/documentos/${ineId}/archivo-url`);
    const pdf = await usuario.get(u.body.url);
    expect(pdf.status).toBe(200);
    expect(pdf.headers['content-type']).toBe('application/pdf');
    expect((await request.agent(app).get(u.body.url)).status).toBe(401);
  });

  it('RN-10 un usuario no puede ver documentos de otro; rechazar términos cancela el registro', async () => {
    const otro = request.agent(app);
    const c2 = 'otro@example.com';
    await otro.post('/api/auth/registro').send({ correo: c2, password });
    await otro.post('/api/auth/registro/verificar').send({ correo: c2, codigo: ultimoCodigo(c2) });
    const t = await otro.post('/api/auth/registro/terminos').send({ acepta: false });
    expect(t.body.cancelado).toBe(true);
    expect((await pool.query('SELECT 1 FROM usuarios WHERE correo = $1', [c2])).rowCount).toBe(0);
    const tercero = request.agent(app);
    const c3 = 'tercero@example.com';
    await tercero.post('/api/auth/registro').send({ correo: c3, password });
    await tercero.post('/api/auth/registro/verificar').send({ correo: c3, codigo: ultimoCodigo(c3) });
    await tercero.post('/api/auth/registro/terminos').send({ acepta: true });
    expect((await tercero.get(`/api/documentos/${ineId}`)).status).toBe(404);
  });

  it('CUA-03..06 administración con CRUD de usuarios', async () => {
    const admin = request.agent(app);
    const r = await admin.post('/api/auth/login').send({ correo: process.env.ADMIN_EMAIL, password: process.env.ADMIN_PASSWORD });
    expect(r.body.redirigir).toBe('/admin');
    expect((await usuario.get('/api/admin/resumen')).status).toBe(403);

    const res = await admin.get('/api/admin/resumen');
    expect(res.body.documentos_procesados).toBeGreaterThanOrEqual(1);
    expect(res.body.estado_sistema.ocr).toBe(true);

    // Crear
    const nuevo = await admin.post('/api/admin/usuarios').send({ correo: 'creado@example.com', password: 'CreadoSeguro1', nombre: 'Ana', apellido_paterno: 'López' });
    expect(nuevo.status).toBe(201);
    const nid = nuevo.body.id;
    // Leer
    const det = await admin.get(`/api/admin/usuarios/${nid}`);
    expect(det.body.nombre).toBe('ANA');
    // Actualizar (datos de cuenta y perfil)
    const upd = await admin.put(`/api/admin/usuarios/${nid}`).send({ rol: 'administrador', dosfa_activo: true, apellido_materno: 'Ruiz', curp: 'LORA900101MDFPZN09' });
    expect(upd.status).toBe(200);
    const det2 = await admin.get(`/api/admin/usuarios/${nid}`);
    expect(det2.body.rol).toBe('administrador');
    expect(det2.body.dosfa_activo).toBe(true);
    expect(det2.body.apellido_materno).toBe('RUIZ');
    expect(det2.body.curp).toContain('•');
    // Restablecer contraseña
    expect((await admin.post(`/api/admin/usuarios/${nid}/password`).send({ password: 'OtraClave2026' })).status).toBe(200);
    // Borrado definitivo
    expect((await admin.delete(`/api/admin/usuarios/${nid}?definitivo=true`)).status).toBe(200);
    expect((await pool.query('SELECT 1 FROM usuarios WHERE id = $1', [nid])).rowCount).toBe(0);

    const traf = await admin.get('/api/admin/trafico?dias=7');
    expect(traf.body.serie.length).toBe(7);
    expect(traf.body.totales.campos_evaluados).toBeGreaterThan(0);
    expect(traf.body.totales.precision_campos).not.toBeNull();
    expect((await admin.get('/api/admin/logs')).body.total).toBeGreaterThan(5);
    expect((await admin.get('/api/admin/reporte')).headers['content-type']).toContain('text/csv');

    // Baja lógica: revoca la sesión del usuario de inmediato
    const us = await admin.get('/api/admin/usuarios?buscar=luis');
    const uid = us.body.usuarios[0].id;
    expect((await admin.delete(`/api/admin/usuarios/${uid}`)).status).toBe(200);
    expect((await usuario.get('/api/auth/yo')).status).toBe(401);
  });
});
