/** Herramienta administrativa por stdin: no imprime valores, contraseñas ni URI. */
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const require = createRequire(path.resolve('package.json'));
const pg = require('pg');
const { MongoClient } = require('mongodb');
import { readFileSync } from 'node:fs';

const accion = process.argv[2] || 'audit';
if (!['audit', 'counts', 'provision', 'rotate', 'migrate-check', 'migrate-apply', 'migrate-rollback'].includes(accion)) {
  throw new Error('Operación no reconocida');
}
const entrada = ['provision', 'grants'].includes(accion)
  ? JSON.parse(readFileSync(0, 'utf8')) : null;
const sql = new pg.Client({ connectionString: process.env.DATABASE_URL, connectionTimeoutMillis: 8000 });
const mongo = new MongoClient(process.env.MONGO_URL, { serverSelectionTimeoutMS: 8000 });
let pgAbierto = false;

function ident(n) {
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,62}$/.test(n)) throw new Error('Identificador no admitido');
  return '"' + n + '"';
}
function password(p) {
  if (!/^[a-f0-9]{64}$/.test(p)) throw new Error('Clave generada inválida');
  return "'" + p + "'";
}
function contador() {
  return { autenticados: 0, legados: 0, invalidos: 0, vacios: 0 };
}

try {
  if (accion !== 'audit-mongo') {
    await sql.connect();
    pgAbierto = true;
    await sql.query("SET statement_timeout = '60s'");
  }
  if (accion === 'provision') {
    const db = ident(entrada.pg_database);
    for (const nombre of ['runtime', 'backup']) {
      const rol = ident(entrada[nombre].pg_user);
      const existe = (await sql.query('SELECT 1 FROM pg_roles WHERE rolname=$1', [entrada[nombre].pg_user])).rowCount;
      if (!existe) {
        await sql.query('CREATE ROLE ' + rol + ' LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD ' + password(entrada[nombre].pg_password));
      } else {
        const r = (await sql.query('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=$1', [entrada[nombre].pg_user])).rows[0];
        if (r.rolsuper || r.rolcreatedb || r.rolcreaterole) throw new Error('Rol existente con privilegios inesperados');
      }
    }
    await sql.query('REVOKE CREATE ON SCHEMA public FROM PUBLIC');
    await sql.query('REVOKE CONNECT,TEMP ON DATABASE ' + db + ' FROM PUBLIC');
    const runtime = ident(entrada.runtime.pg_user), backup = ident(entrada.backup.pg_user);
    const owner = ident((await sql.query('SELECT current_user AS nombre')).rows[0].nombre);
    await sql.query('GRANT CONNECT ON DATABASE ' + db + ' TO ' + runtime + ',' + backup);
    await sql.query('GRANT USAGE ON SCHEMA public TO ' + runtime + ',' + backup);
    await sql.query('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO ' + runtime);
    await sql.query('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO ' + runtime);
    await sql.query('GRANT SELECT ON ALL TABLES IN SCHEMA public TO ' + backup);
    await sql.query('GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO ' + backup);
    await sql.query('GRANT INSERT ON respaldos TO ' + backup);
    await sql.query('GRANT USAGE ON SEQUENCE respaldos_id_seq TO ' + backup);
    await sql.query('ALTER DEFAULT PRIVILEGES FOR ROLE ' + owner + ' IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO ' + runtime);
    await sql.query('ALTER DEFAULT PRIVILEGES FOR ROLE ' + owner + ' IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO ' + runtime);
    await sql.query('ALTER DEFAULT PRIVILEGES FOR ROLE ' + owner + ' IN SCHEMA public GRANT SELECT ON TABLES TO ' + backup);
    await sql.query('ALTER DEFAULT PRIVILEGES FOR ROLE ' + owner + ' IN SCHEMA public GRANT SELECT ON SEQUENCES TO ' + backup);
    await mongo.connect();
    const mdb = mongo.db(entrada.mongo_database);
    for (const nombre of ['runtime', 'backup']) {
      const usuario = entrada[nombre].mongo_user;
      ident(usuario);
      const roles = [{ role: nombre === 'runtime' ? 'readWrite' : 'read', db: entrada.mongo_database }];
      const info = await mdb.command({ usersInfo: usuario });
      if (!info.users.length) {
        await mdb.command({ createUser: usuario, pwd: entrada[nombre].mongo_password, roles });
      } else {
        // Solo roles de la base de aplicación; nunca conserva root por accidente.
        await mdb.command({ updateUser: usuario, roles });
      }
    }
    console.log(JSON.stringify({ provision: 'completada', roles_runtime: 'limitados', claves: 'conservadas' }));
  } else if (accion === 'rotate') {
    await sql.query('SELECT pg_reload_conf()');
    await mongo.connect();
    await mongo.db('admin').command({ rotateCertificates: 1 });
    console.log('Certificados de bases recargados');
  } else if (accion === 'counts') {
    const tablas = ['usuarios', 'perfil_usuario', 'documentos', 'documento_versiones', 'validaciones_dato', 'consentimientos_privacidad'];
    const cuentas = {};
    for (const tabla of tablas) cuentas[tabla] = Number((await sql.query('SELECT count(*) n FROM ' + ident(tabla))).rows[0].n);
    await mongo.connect();
    cuentas.resultados_ocr = await mongo.db(process.env.MONGO_DB || 'tt_ocr').collection('resultados_ocr').countDocuments();
    console.log(JSON.stringify(cuentas));
  } else {
    const crypto = await import(pathToFileURL(path.resolve('dist/lib/crypto.js')).href);
    const { camposDeValidacion } = await import(pathToFileURL(path.resolve('dist/lib/campos-validacion.js')).href);
    const aplicar = accion === 'migrate-apply';
    const revertir = accion === 'migrate-rollback';
    const migrar = accion === 'migrate-check' || aplicar || revertir;
    function versionUno(v) {
      // El lector anterior descifraba únicamente los textos del primer nivel.
      return Object.fromEntries(Object.entries(v ?? {}).map(([k, x]) => [k, typeof x === 'string' ? crypto.cifrar(x) : x]));
    }
    const resultado = { grupos: {}, enlaces: { huerfanos: 0, propietarios: 0, referencias: 0 }, postgres: null, mongo_roles: [] };
    const ids = new Set();
    let documentos = new Map();
    if (pgAbierto) {
      await sql.query(migrar && (aplicar || revertir) ? 'BEGIN' : 'BEGIN READ ONLY');
      const r = (await sql.query('SELECT r.rolname,r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolbypassrls,s.ssl FROM pg_roles r LEFT JOIN pg_stat_ssl s ON s.pid=pg_backend_pid() WHERE r.rolname=current_user')).rows[0];
      resultado.postgres = r;
      documentos = new Map((await sql.query('SELECT id,usuario_id,mongo_resultado_id FROM documentos')).rows.map(d => [d.id, d]));
    }
    function verificar(v, c) {
      if (v == null) { c.vacios++; return; }
      if (Array.isArray(v)) { for (const x of v) verificar(x, c); return; }
      if (typeof v === 'object' && v.constructor === Object) { for (const x of Object.values(v)) verificar(x, c); return; }
      if (typeof v !== 'string' || !/^enc:v[12]:/.test(v)) { c.legados++; return; }
      try {
        const raw = v.slice(7), b = Buffer.from(raw, 'base64');
        if (b.length < 28 || b.toString('base64') !== raw) throw new Error();
        crypto.descifrarBuffer(b);
        if (v.startsWith('enc:v2:')) crypto.descifrarObjeto({ dato: v });
        c.autenticados++;
      } catch { c.invalidos++; }
    }
    await mongo.connect();
    const db = mongo.db(process.env.MONGO_DB || 'tt_ocr'), col = db.collection('resultados_ocr');
    const auth = await db.command({ connectionStatus: 1, showPrivileges: false });
    resultado.mongo_roles = auth.authInfo.authenticatedUserRoles;
    const todos = await col.find({}).maxTimeMS(60000).toArray();
    const cambios = [];
    for (const d of todos) {
      ids.add(String(d._id));
      if (pgAbierto) {
        const p = documentos.get(Number(d.documento_id));
        if (!p) resultado.enlaces.huerfanos++;
        else {
          if (p.usuario_id !== Number(d.usuario_id)) resultado.enlaces.propietarios++;
          if (p.mongo_resultado_id !== String(d._id)) resultado.enlaces.referencias++;
        }
      }
      for (const campo of ['datos_extraidos', 'datos_confirmados', 'texto_crudo']) {
        resultado.grupos[campo] ??= contador();
        verificar(d[campo], resultado.grupos[campo]);
      }
      resultado.grupos.campos_detectados ??= contador();
      for (const c of d.campos_detectados || []) verificar(c.valor, resultado.grupos.campos_detectados);
      if (migrar) {
        const nuevo = {
          datos_extraidos: (revertir ? versionUno : crypto.cifrarObjeto)(crypto.descifrarObjeto(d.datos_extraidos)),
          campos_detectados: (d.campos_detectados || []).map(c => ({ campo: c.campo, valor: crypto.cifrar(crypto.descifrar(c.valor)) })),
          texto_crudo: crypto.cifrar(crypto.descifrar(d.texto_crudo))
        };
        if (d.datos_confirmados) nuevo.datos_confirmados = (revertir ? versionUno : crypto.cifrarObjeto)(crypto.descifrarObjeto(d.datos_confirmados));
        if (aplicar && d.metadatos?.errores_validacion) {
          nuevo['metadatos.errores_validacion'] = camposDeValidacion(d.metadatos.errores_validacion, d.datos_extraidos ?? {});
        }
        cambios.push({ _id: d._id, nuevo });
      }
    }
    if (pgAbierto) {
      for (const d of documentos.values()) if (d.mongo_resultado_id && !ids.has(d.mongo_resultado_id)) resultado.enlaces.referencias++;
      for (const [tabla, columnas] of [['perfil_usuario', ['curp', 'direccion']], ['validaciones_dato', ['valor_detectado', 'valor_confirmado']]]) {
        const filas = (await sql.query('SELECT id,' + columnas.map(ident).join(',') + ' FROM ' + ident(tabla))).rows;
        for (const f of filas) for (const campo of columnas) {
          const grupo = tabla + '.' + campo;
          resultado.grupos[grupo] ??= contador();
          verificar(f[campo], resultado.grupos[grupo]);
          if (aplicar && f[campo] != null) await sql.query('UPDATE ' + ident(tabla) + ' SET ' + ident(campo) + '=$1 WHERE id=$2', [crypto.cifrar(crypto.descifrar(f[campo])), f.id]);
        }
      }
    }
    if (Object.values(resultado.grupos).some(c => c.invalidos) || Object.values(resultado.enlaces).some(n => n)) {
      throw new Error('Datos incoherentes o sin autenticidad; no continuar');
    }
    if (aplicar || revertir) {
      for (const c of cambios) await col.updateOne({ _id: c._id }, { $set: c.nuevo });
      if (pgAbierto) await sql.query('COMMIT');
    } else if (pgAbierto) await sql.query('ROLLBACK');
    resultado.resultados_ocr = todos.length;
    console.log(JSON.stringify(resultado));
    if (accion === 'audit' && (['rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls'].some(k => resultado.postgres?.[k]) ||
        resultado.postgres?.ssl !== true || resultado.mongo_roles.length !== 1 ||
        resultado.mongo_roles.some(r => r.role !== 'readWrite' || r.db !== (process.env.MONGO_DB || 'tt_ocr')) ||
        Object.values(resultado.grupos).some(c => c.legados))) {
      process.exitCode = 2;
    }
  }
} catch (e) {
  console.error(JSON.stringify({ operacion: accion, error: e.name, estado: 'detenido' }));
  process.exitCode = 1;
} finally {
  if (pgAbierto) await sql.end().catch(() => {});
  await mongo.close().catch(() => {});
}
