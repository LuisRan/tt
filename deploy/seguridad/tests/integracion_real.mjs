import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import https from 'node:https';
const require = createRequire(path.resolve('package.json'));
const pg = require('pg'), { MongoClient } = require('mongodb'), bcrypt = require('bcryptjs');
const root = path.resolve('..');
const folder = process.env.TEST_FOLDER, ca = process.env.TEST_CA;
const dbname = 'tt_security_fixture';
const pgUrl = 'postgresql://postgres@127.0.0.1:' + process.env.TEST_PG_PORT + '/';
const sslPg = '?sslmode=verify-full&sslrootcert=' + encodeURIComponent(ca);
const mongoBase = 'mongodb://127.0.0.1:' + process.env.TEST_MONGO_PORT;
const sslMongo = '?tls=true&tlsCAFile=' + encodeURIComponent(ca);
const admin = new pg.Client({ connectionString: pgUrl + 'postgres' + sslPg });
const bootstrap = new MongoClient(mongoBase + '/admin' + sslMongo);
await admin.connect();
assert.equal((await admin.query("SELECT current_setting('data_directory') d")).rows[0].d, process.env.TEST_PG_DATA_DIR);
await admin.query('CREATE DATABASE ' + dbname);
await bootstrap.connect();
await bootstrap.db('admin').command({ createUser: 'fixture_admin', pwd: process.env.TEST_MONGO_PASSWORD, roles: [{ role: 'root', db: 'admin' }] });
await bootstrap.close();
const mongoAdmin = 'mongodb://fixture_admin:' + process.env.TEST_MONGO_PASSWORD + '@127.0.0.1:' + process.env.TEST_MONGO_PORT + '/' + dbname + sslMongo + '&authSource=admin';
const common = { ...process.env, DATABASE_URL: pgUrl + dbname + sslPg, MONGO_URL: mongoAdmin, MONGO_DB: dbname };
function child(args, input, extra = {}) {
  const r = spawnSync('node', args, { cwd: process.cwd(), env: { ...common, ...extra }, input, encoding: 'utf8', timeout: 60000 });
  assert.equal(r.status, 0, (r.stderr || r.stdout));
  return r.stdout;
}
const tool = path.join(root, 'deploy/seguridad/backend-tools.mjs');
const cred = {
  pg_database: dbname, mongo_database: dbname,
  runtime: { pg_user: 'fixture_runtime', pg_password: 'a'.repeat(64), mongo_user: 'fixture_runtime', mongo_password: 'b'.repeat(64) },
  backup: { pg_user: 'fixture_backup', pg_password: 'c'.repeat(64), mongo_user: 'fixture_backup', mongo_password: 'd'.repeat(64) }
};
let backend;
const conexiones = [];
try {
  child(['dist/db/cli.js']);
  child([tool, 'provision'], JSON.stringify(cred));
  // Reaplicar no cambia contraseñas, ni concede roles superiores.
  child([tool, 'provision'], JSON.stringify(cred));
  const owner = new pg.Client({ connectionString: common.DATABASE_URL });
  await owner.connect(); conexiones.push(owner);
  const pw = await bcrypt.hash('SyntheticPassword123', 12);
  const usuarios = (await owner.query("INSERT INTO usuarios(correo,password_hash,registro_completo,correo_verificado,rol) VALUES ('a@example.invalid',$1,true,true,'usuario'),('b@example.invalid',$1,true,true,'usuario'),('admin@example.invalid',$1,true,true,'administrador') RETURNING id", [pw])).rows.map(x => x.id);
  const [a, b, adm] = usuarios;
  await owner.query("INSERT INTO perfil_usuario(usuario_id,nombre,curp,direccion) VALUES ($1,'PERSONA SINTETICA','DATO-SINTETICO','DOMICILIO-SINTETICO')", [a]);
  const doc = (await owner.query("INSERT INTO documentos(usuario_id,tipo_id,ruta_archivo,estado,folio,vigente) SELECT $1,id,'fixture.enc','validado',1,true FROM tipos_documento WHERE clave='ine' RETURNING id", [a])).rows[0].id;
  const m = new MongoClient(mongoAdmin); await m.connect(); conexiones.push(m);
  const original = { nombres: 'PERSONA SINTETICA', primer_apellido: 'PRUEBA', segundo_apellido: 'PRUEBA', anidado: { texto: 'SINTETICO', numero: 42, activo: true } };
  const id = (await m.db(dbname).collection('resultados_ocr').insertOne({
    documento_id: doc, usuario_id: a, tipo_documento: 'INE',
    datos_extraidos: original, datos_confirmados: original,
    campos_detectados: [{ campo: 'nombres', valor: 'PERSONA SINTETICA' }],
    texto_crudo: 'OCR SINTETICO', metadatos: {errores_validacion: ['nombres: valor SINTETICO']}, validaciones: {}
  })).insertedId;
  await owner.query('UPDATE documentos SET mongo_resultado_id=$1 WHERE id=$2', [String(id), doc]);
  child([tool, 'migrate-check']);
  child([tool, 'migrate-apply']);
  const rolesEnv = {};
  for (const nombre of ['runtime', 'backup']) {
    const c = cred[nombre];
    rolesEnv[nombre] = {
      DATABASE_URL: 'postgresql://' + c.pg_user + ':' + c.pg_password + '@127.0.0.1:' + process.env.TEST_PG_PORT + '/' + dbname + sslPg,
      MONGO_URL: 'mongodb://' + c.mongo_user + ':' + c.mongo_password + '@127.0.0.1:' + process.env.TEST_MONGO_PORT + '/' + dbname + sslMongo + '&authSource=' + dbname
    };
  }
  const reporte = JSON.parse(child([tool, 'audit'], undefined, { ...rolesEnv.runtime, ALLOW_LEGACY_PLAINTEXT: 'false' }).trim());
  assert.equal(reporte.postgres.rolsuper, false);
  assert.equal(reporte.postgres.ssl, true);
  for (const c of Object.values(reporte.grupos)) { assert.equal(c.legados, 0); assert.equal(c.invalidos, 0); }
  const runtime = new pg.Client({ connectionString: rolesEnv.runtime.DATABASE_URL });
  await runtime.connect(); conexiones.push(runtime);
  await assert.rejects(runtime.query('CREATE TABLE public.no_debe_crearse(id int)'), e => e.code === '42501');
  await assert.rejects(runtime.query('CREATE ROLE no_debe_crearse'), e => e.code === '42501');
  const backup = new pg.Client({ connectionString: rolesEnv.backup.DATABASE_URL });
  await backup.connect(); conexiones.push(backup);
  assert.equal(Number((await backup.query('SELECT count(*) n FROM usuarios')).rows[0].n), 3);
  await assert.rejects(backup.query('UPDATE usuarios SET activo=false'), e => e.code === '42501');
  await backup.query("INSERT INTO respaldos(tipo,ubicacion,estado) VALUES ('fixture','fixture','completado')");
  const sinTLS = new pg.Client({ host: '127.0.0.1', port: Number(process.env.TEST_PG_PORT), database: dbname, user: cred.runtime.pg_user, password: cred.runtime.pg_password, ssl: false });
  await assert.rejects(sinTLS.connect()); await sinTLS.end().catch(() => {});
  const mb = new MongoClient(rolesEnv.backup.MONGO_URL);
  await mb.connect(); conexiones.push(mb);
  assert.equal(await mb.db(dbname).collection('resultados_ocr').countDocuments(), 1);
  await assert.rejects(mb.db(dbname).collection('resultados_ocr').insertOne({ dato: 1 }), e => e.code === 13);
  const mr = new MongoClient(rolesEnv.runtime.MONGO_URL);
  await mr.connect(); conexiones.push(mr);
  await assert.rejects(mr.db('admin').collection('system.users').findOne({}), e => e.code === 13);
  if (process.env.TEST_BACKUP_TOOLS === '1') {
    const restaurada = 'tt_restore_fixture';
    await admin.query('CREATE DATABASE ' + restaurada);
    const res = spawnSync(process.env.TEST_PYTHON, [path.join(root, 'deploy/seguridad/tests/respaldo_real.py')], {
      env: { ...common, ...rolesEnv.backup, RESTORE_OWNER_MONGO: mongoAdmin, RESTORE_DATABASE: restaurada },
      cwd: folder, encoding: 'utf8', timeout: 60000
    });
    if (res.status !== 0) throw new Error('Falló el respaldo sintético: ' + (res.stderr || res.error?.name));
    const clon = new pg.Client({ connectionString: pgUrl + restaurada + sslPg });
    await clon.connect(); conexiones.push(clon);
    assert.equal(Number((await clon.query('SELECT count(*) n FROM usuarios')).rows[0].n), 3);
    assert.equal((await clon.query('SELECT mongo_resultado_id FROM documentos')).rows[0].mongo_resultado_id, String(id));
    assert.deepEqual((await m.db(restaurada).collection('resultados_ocr').findOne({ _id: id })).datos_confirmados,
                     (await mr.db(dbname).collection('resultados_ocr').findOne({ _id: id })).datos_confirmados);
    console.log('Respaldos reales con el rol limitado y recuperación en bases nuevas: correctos.');
  }
  const insecure = new MongoClient(mongoBase + '/admin', { serverSelectionTimeoutMS: 1000 });
  await assert.rejects(insecure.connect()); await insecure.close();
  const envBackend = { ...common, ...rolesEnv.runtime, RUN_DB_MIGRATIONS: 'false', ALLOW_LEGACY_PLAINTEXT: 'false',
    PORT: process.env.TEST_BACKEND_PORT, PUBLIC_URL: 'https://localhost:' + process.env.TEST_BACKEND_PORT,
    BACKEND_TLS_CERT_FILE: folder + '/backend.crt', BACKEND_TLS_KEY_FILE: folder + '/backend.key',
    STORAGE_DIR: folder + '/storage', IA_URL: 'https://localhost:1' };
  backend = spawn('node', ['dist/index.js'], { env: envBackend, cwd: process.cwd(), stdio: ['ignore', 'ignore', 'pipe'] });
  let logs = ''; backend.stderr.on('data', x => { logs += x.toString(); });
  const agent = new https.Agent({ ca: readFileSync(ca), rejectUnauthorized: true });
  function request(p, options = {}) {
    return new Promise((resolve, reject) => {
      const req = https.request('https://localhost:' + process.env.TEST_BACKEND_PORT + p, { agent, ...options }, res => {
        let text = ''; res.on('data', x => { text += x; }); res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: text ? JSON.parse(text) : null }));
      });
      req.on('error', reject); if (options.body) req.write(options.body); req.end();
    });
  }
  let salud;
  for (let i = 0; i < 80; i++) {
    try { salud = await request('/api/salud'); if (salud.status === 200) break; } catch {}
    if (backend.exitCode !== null) throw new Error('Backend terminó: ' + logs);
    await new Promise(r => setTimeout(r, 100));
  }
  assert.equal(salud.body.postgres, true); assert.equal(salud.body.mongo, true);
  assert.equal(salud.headers['cache-control'], 'no-store');
  async function login(email) {
    const r = await request('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ correo: email, password: 'SyntheticPassword123' }) });
    assert.equal(r.status, 200);
    return r.headers['set-cookie'].map(c => c.split(';')[0]).join('; ');
  }
  const adminCookie = await login('admin@example.invalid');
  const perfil = await request('/api/admin/usuarios/' + a, { headers: { Cookie: adminCookie } });
  assert.equal(perfil.status, 200); assert.equal(perfil.body.direccion, null);
  assert.equal(perfil.body.direccion_protegida, true);
  const cookieB = await login('b@example.invalid');
  const ajeno = await request('/api/documentos/' + doc, { headers: { Cookie: cookieB } });
  assert.ok([403, 404].includes(ajeno.status));
  // Reversión lógica compatible con el lector v1, sin restaurar ni borrar las bases.
  backend.kill('SIGTERM'); await new Promise(resolve => backend.once('exit', resolve)); backend = null;
  child([tool, 'migrate-rollback']);
  const revertido = await m.db(dbname).collection('resultados_ocr').findOne({ _id: id });
  assert.deepEqual(revertido.datos_confirmados.anidado, original.anidado);
  assert.deepEqual(revertido.metadatos.errores_validacion, ['nombres']);
  Object.assign(process.env, common);
  const crypto = await import(pathToFileURL(path.resolve('dist/lib/crypto.js')).href);
  assert.equal(crypto.descifrar(revertido.datos_confirmados.nombres), original.nombres);
  console.log(JSON.stringify({ integracion_real: 'correcta', postgres: '16', mongo: '7',
    verificaciones: ['TLS obligatorio', 'roles limitados', 'migración y reversión', 'cifrado anidado',
      'respaldo sin UPDATE', 'HTTPS backend con CA verificada', 'domicilio protegido', 'acceso cruzado rechazado'] }));
} finally {
  if (backend) backend.kill('SIGTERM');
  for (const c of conexiones.reverse()) {
    if (c instanceof MongoClient) await c.close(); else await c.end();
  }
  await admin.end();
}
