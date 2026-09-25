# Seguridad

Controles implementados según la sección 2.7.2 del documento del TT.

## Transporte y perímetro
- **HTTPS** en Nginx (TLS 1.2/1.3, HSTS). Certificado autofirmado automático o de confianza con `mkcert`.
- **Reverse proxy**: solo Nginx expone puertos (`HTTP_PORT` redirige a `HTTPS_PORT`). Backend, IA,
  PostgreSQL y MongoDB solo están en la red interna de Docker.
- **CSP estricta** (`script-src 'self'`, sin scripts en línea), `X-Frame-Options`, `nosniff`,
  `Referrer-Policy`, `Permissions-Policy`.
- **Límite de peticiones**: Nginx (10/min por IP en login y registro, 20/s en la API) y
  `express-rate-limit` en el backend.

## Autenticación
- Contraseñas con **bcrypt** (coste 12) y política mínima (10 caracteres, mayúscula, minúscula, número).
- Verificación del correo al registrarse con código de 6 dígitos.
- **Verificación en dos pasos opcional** por usuario (`usuarios.dosfa_activo`), desactivada por
  defecto; se activa en Configuración o la activa el administrador.
- Algoritmo del código (`services/dosfa.ts`): `crypto.randomInt` (CSPRNG) → se guarda solo su
  **HMAC-SHA256** ligado a usuario y propósito → vence en 10 min → máximo 5 intentos → un solo uso →
  comparación en tiempo constante (`timingSafeEqual`).

## Sesiones
- JWT firmado (`JWT_SECRET`) en cookies `httpOnly`, `Secure`, `SameSite=Strict`:
  `sesion` (app), `registro` (registro en curso), `mfa` (entre contraseña y código), `reauth`.
- **Revocación**: cada token lleva `token_version`; se incrementa al cambiar la contraseña, cerrar
  todas las sesiones, o cuando el administrador cambia rol/correo, restablece la contraseña o
  deshabilita la cuenta.
- **Reautenticación** (respuesta 428): subir, validar o editar documentos, editar el perfil y
  cambiar la verificación en dos pasos exigen haber confirmado la contraseña en los últimos
  `REAUTH_MINUTOS`.

## Datos
- **Cifrado AES-256-GCM** de: archivos en disco, CURP y domicilio del perfil, valores detectados y
  confirmados (`validaciones_dato`), y valores y texto OCR en MongoDB.
- Búsqueda de CURP por `curp_hash` (HMAC) sin descifrar.
- **Consultas parametrizadas** en todo el backend (sin concatenar SQL) y validación de entradas con **Zod**.
- Archivos: se verifica el tipo real por *magic bytes* (no por extensión), tamaño máximo
  `MAX_UPLOAD_MB`, y se sirven solo con URL firmada de 5 minutos.
- **RN-10**: toda consulta de documentos filtra por `usuario_id`; un documento ajeno responde 404.
- El administrador ve la CURP **enmascarada** y no puede abrir los documentos de los usuarios.

## Extensión de navegador
- Nunca recibe la contraseña: se vincula con un **código de un solo uso** (15 min, HMAC) que se
  canjea por un **token de dispositivo** (Bearer, HMAC en la BD).
- El token solo permite **leer** documentos validados; se revoca desde la plataforma, al cambiar la
  contraseña, al cerrar todas las sesiones o si la cuenta se deshabilita. Al recibir 401 la extensión
  borra los datos guardados en el navegador.
- El service worker solo acepta mensajes del origen exacto de la plataforma.
- CSP propia de la extensión (`script-src 'self'`) y pintado con `textContent`.

## IA local
- Los documentos se procesan en el propio equipo (Docling/Tesseract/Apple Vision + Ollama).
- El módulo de IA solo acepta peticiones con `X-API-Key` (`IA_API_KEY`) desde la red interna.
- Anti-alucinación: los valores del LLM que no aparecen en el texto OCR se descartan; la CURP se
  valida con su dígito verificador y se contrasta con nombre, fecha y sexo.

## Auditoría y respaldos
- `accesos`: cada inicio de sesión, intento fallido, 2FA y registro con IP y navegador.
- `eventos_sistema`: operaciones de usuarios y administradores (altas, cambios, bajas, reportes).
- Respaldos cifrados (AES-256, PBKDF2) de PostgreSQL, MongoDB y archivos; retención configurable.

## Secretos (`.env`)
| Variable | Uso | Si se pierde |
|---|---|---|
| `DATA_ENCRYPTION_KEY` | Cifrado de archivos y columnas. | **Datos irrecuperables.** Respáldala fuera del equipo. |
| `BACKUP_ENCRYPTION_KEY` | Cifrado de respaldos. | Respaldos irrecuperables. |
| `JWT_SECRET` | Firma de sesiones. | Se cierran todas las sesiones al cambiarla. |
| `IA_API_KEY` | Backend → módulo de IA. | Regenerar en ambos lados. |
| `RESEND_API_KEY` | Envío de correos. | Rotarla en el panel de Resend si se expone. |

El archivo `.env` no debe subirse a Git (está en `.gitignore`).
