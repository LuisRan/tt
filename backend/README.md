# Backend (Node.js 22 + TypeScript + Express 5)

API REST de la plataforma: autenticación, documentos, perfil, administración, cola de
procesamiento con el módulo de IA y tareas programadas. Referencia de endpoints en
[`docs/API.md`](../docs/API.md).

## Estructura

```
src/
├── index.ts              arranque: migraciones, admin inicial, MongoDB, cola, cron, servidor
├── app.ts                Express: helmet, CORS, cookies, rate limit, rutas y manejador de errores
├── config.ts             variables de entorno validadas con zod (falla al arrancar si falta algo)
├── db/
│   ├── schema.sql        esquema + migraciones idempotentes (ver docs/BASE_DE_DATOS.md)
│   ├── migrar.ts         aplica schema.sql y crea el administrador inicial
│   ├── pg.ts             pool de PostgreSQL y helpers query / uno / transaccion
│   ├── mongo.ts          colección resultados_ocr (y modo memory:// para pruebas)
│   └── mongo-memoria.ts  colección en memoria compatible con la API usada
├── lib/
│   ├── crypto.ts         AES-256-GCM (texto y archivos), HMAC, comparación en tiempo constante
│   ├── sesion.ts         JWT en cookies (sesion, registro, mfa, reauth)
│   ├── errores.ts        ErrorApp y fábricas (noAutorizado, noEncontrado, ...)
│   └── logger.ts         pino
├── middleware/
│   ├── auth.ts           requiereSesion, requiereRol, requiereReautenticacion (428)
│   ├── trafico.ts        conteo de peticiones por hora -> trafico_api
│   └── errores.ts        traduce Zod / ErrorApp / ErrorIA / multer a JSON uniforme
├── routes/               auth, documentos, archivos, perfil, admin, salud
└── services/
    ├── documentos.ts     ciclo de vida del documento: carga, cola, IA, reglas, validación, edición
    ├── perfil.ts         lectura/edición del perfil (cifrado de CURP y domicilio)
    ├── ia-cliente.ts     cliente HTTP del módulo de IA (timeouts, reintentos, ErrorIA)
    ├── extension.ts      extensión de navegador: códigos, tokens de dispositivo, paquete .zip y datos
    ├── dosfa.ts          códigos de verificación (HMAC, expiración, intentos)
    ├── correo.ts         envío por Resend o consola
    ├── plantillas-correo.ts  HTML de los correos
    ├── tareas.ts         alertas de vigencia (cron), limpieza de códigos vencidos y registros abandonados (>24 h)
    ├── almacenamiento.ts interfaz IAlmacenamiento (disco local cifrado; reemplazable por Blob Storage)
    └── auditoria.ts      eventos_sistema y accesos
test/
├── reglas.test.ts        reglas de negocio y cifrado (unitarias)
└── flujo.test.ts         flujo completo contra PostgreSQL real y el módulo de IA real
```

## Reglas de negocio principales (`services/documentos.ts`)

| Regla | Dónde |
|---|---|
| Tipo detectado debe coincidir con el elegido | `procesarDocumento` |
| RN-04 INE formato 2016+ / RN-11 vigencia | `evaluarReglas` (advertencias) |
| RN-06 CURP con antigüedad ≤ `CURP_MAX_DIAS` | `evaluarReglas` (rechazo) |
| RN-07 una versión vigente por tipo | `validarDocumento` |
| RN-08/09 detectado vs. confirmado | `validaciones_dato` con `origen` |
| Documento de otra persona no sobrescribe el perfil | `validarDocumento` (compara CURP) |
| Edición posterior sin afectar la precisión | `editarDocumentoValidado` (`origen = 'edicion'`) |

JPG/PNG se convierten a PDF con `pdf-lib` (`normalizarAPdf`) porque el módulo de IA solo acepta PDF.
La cola procesa `IA_CONCURRENCIA` documentos a la vez y al arrancar recupera los que quedaron pendientes.

## Variables de entorno principales

| Variable | Por defecto | Descripción |
|---|---|---|
| `DATABASE_URL`, `MONGO_URL`, `MONGO_DB` | | Conexiones. `MONGO_URL=memory://` para pruebas. |
| `JWT_SECRET`, `DATA_ENCRYPTION_KEY` | | Obligatorias. |
| `COOKIE_SECURE` | `true` | `false` solo en desarrollo por HTTP. |
| `REAUTH_MINUTOS` | 10 | Vigencia de la reconfirmación de contraseña. |
| `REGISTRO_VERIFICAR_CORREO` | `true` | Pedir código al registrarse. |
| `MAIL_PROVIDER` | `console` | `resend` para enviar correos reales. |
| `IA_URL`, `IA_API_KEY`, `IA_TIMEOUT_MS`, `IA_CONCURRENCIA`, `IA_REINTENTOS` | | Módulo de IA. |
| `STORAGE_DIR`, `MAX_UPLOAD_MB` | `data/storage`, 10 | Archivos cifrados. |
| `CURP_MAX_DIAS`, `ALERTAS_DIAS`, `ALERTAS_CRON`, `TZ` | 90, `90,30,7,0`, `0 9 * * *` | Reglas y alertas. |

## Comandos

```bash
npm install
npm run dev          # tsx watch (http://localhost:3000/api/salud)
npm run typecheck
npm run build        # dist/ + copia schema.sql
npm test             # vitest
```

Variables para `npm test`: `DATABASE_URL`, `IA_URL`, `IA_API_KEY`, `MONGO_URL=memory://`,
`JWT_SECRET`, `DATA_ENCRYPTION_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `NODE_ENV=test`.
La prueba de flujo **borra y recrea** el esquema de esa base: usa una base exclusiva para pruebas.
