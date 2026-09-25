# TT · Plataforma de validación y procesamiento de documentos de identidad (OCR + IA)

Aplicación web para administrar el ciclo de vida de documentos de identidad mexicanos
(**INE, CURP, acta de nacimiento y pasaporte**): carga, clasificación automática, extracción
de datos con **OCR + LLM local**, validación por el usuario, resguardo cifrado, consulta,
versionado y alertas de vigencia. Implementa lo descrito en el documento del Trabajo
Terminal (ESCOM-IPN), capítulos 3 a 7.

```
 Navegador ──HTTPS──-> web (Nginx: reverse proxy + TLS + frontend Astro)
                          │  /api
                          |
                     backend (Node.js/TypeScript)  ──-> PostgreSQL  (usuarios, documentos, auditoría…)
                          │                        ──-> MongoDB     (resultados OCR semiestructurados)
                          │                        ──-> Resend      (correos 2FA y alertas)
                          |  X-API-Key (red interna)
                     ia (Python/Flask: Docling + Tesseract + regex + anti-alucinación)
                          |
                     Ollama (qwen3:4b-instruct-2507-q4_K_M, local)
       backup (pg_dump + mongodump cifrados, periódico)
```

| Carpeta | Contenido |
|---|---|
| `ia_documentos/` | Módulo de IA (tu repo). Se agregó la API REST Flask, la detección de SO y el Dockerfile. |
| `backend/` | Backend principal Node.js + TypeScript (Express 5). |
| `frontend/` | Cliente web Astro (sitio estático). |
| `nginx/` | Reverse proxy HTTPS, cabeceras de seguridad y Dockerfile de la imagen `web`. |
| `backup/` | Servicio de respaldos cifrados y script de restauración. |
| `scripts/` | Arranque/paro para macOS, Linux y Windows. |
| `extension/` | Extensión de navegador (Chrome/Edge): documentos validados con copiar y descargar .txt. |
| `docs/` | Documentación técnica: arquitectura, flujos, API, base de datos, seguridad y operación. |
| `.env` | Configuración y secretos **de este equipo** (ya generado, no lo subas a Git). |

---

## 0. Clonar el proyecto (equipo)

```bash
git clone https://github.com/LuisRan/tt.git && cd tt
./scripts/start.sh --ia-nativa        # Mac  (Windows: .\scripts\start.ps1)
```

- **No se comparte el `.env`**: no está en el repositorio. La primera vez, `start.sh` / `start.ps1`
  lo crea a partir de `.env.example` con contraseñas y llaves **aleatorias propias de cada equipo**
  (cada quien tiene su propia base de datos, su propio administrador y sus propias llaves de cifrado).
- La contraseña del administrador queda en tu `.env` (`ADMIN_PASSWORD`); cambia `ADMIN_EMAIL` si quieres.
- Correos: por defecto `MAIL_PROVIDER=console` y los códigos salen en `docker compose logs -f backend`.
  Para enviar correos reales, pide la llave de Resend por un canal privado (no por el repo ni el
  chat grupal) y ponla en `RESEND_API_KEY` con `MAIL_PROVIDER=resend`.
- Los PDF de prueba (documentos reales) **no se suben** al repositorio: colócalos en
  `ia_documentos/data/input/` (los necesita `npm test` del backend).

## 1. Arranque rápido

### Requisitos
- **Docker Desktop** (Mac / Windows) o Docker Engine (Linux), con **≥ 8 GB de RAM** asignados.
- Opcional: `mkcert` para HTTPS sin advertencias del navegador.

### macOS (Apple Silicon)
```bash
cd ~/Documents/tt
chmod +x scripts/*.sh
./scripts/start.sh --ia-nativa     # recomendado en Mac
# o todo en Docker:
./scripts/start.sh
```
`--ia-nativa` ejecuta el módulo de IA directamente en macOS para aprovechar **ocrmac (Vision de
Apple) y la GPU Metal (MPS)**, como lo tenías configurado; el resto corre en Docker. El script
instala con Homebrew lo que falte (Ollama, Tesseract + español, Poppler, Python 3.12), crea
`ia_documentos/.venv` y descarga el modelo `qwen3:4b-instruct-2507-q4_K_M` la primera vez (si no lo tienes).

### Windows 10/11
```powershell
cd C:\ruta\a\tt
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # solo la primera vez
.\scripts\start.ps1              # todo en Docker Desktop
.\scripts\start.ps1 -IaNativa    # opcional: IA nativa en Windows (Tesseract + CUDA si hay GPU NVIDIA)
```
Si tienes **Ollama para Windows** abierto se usa ese; si no, Ollama corre en un contenedor
(con GPU NVIDIA automáticamente si existe `nvidia-smi`).

### Linux
```bash
./scripts/start.sh
```

Al terminar abre **https://localhost:8443**.
- Administrador: el correo `ADMIN_EMAIL` del `.env` con la contraseña `ADMIN_PASSWORD` del `.env`.
- Detener: `./scripts/stop.sh` (o `.\scripts\stop.ps1`). Los datos se conservan en volúmenes de Docker.
- Borrar todo (BD, archivos): `./scripts/stop.sh --borrar`.

### Reiniciar tras actualizar el código
```bash
cd ~/Documents/tt
./scripts/stop.sh
./scripts/start.sh --ia-nativa            # reconstruye lo que cambió y reinicia la IA nativa
```
Si algo no refleja los cambios, reconstruye sin caché:
```bash
./scripts/stop.sh
docker compose build --no-cache backend web
./scripts/start.sh --ia-nativa
```
Más comandos (logs, un solo servicio, respaldos) en [docs/OPERACION.md](docs/OPERACION.md).

### Sin scripts
```bash
docker compose up -d --build                 # usa COMPOSE_PROFILES del .env (por defecto: ia)
docker compose --profile ollama up -d        # + Ollama en contenedor
docker compose logs -f backend               # logs (aquí salen los códigos 2FA en modo consola)
```

---

## 2. Detección del sistema operativo (módulo de IA)

`ia_documentos/infrastructure/platform_config.py` detecta el SO al arrancar y configura Docling,
Tesseract y Poppler en consecuencia:

| Plataforma | Motor OCR de Docling | Dispositivo | Tesseract / Poppler | TableFormer |
|---|---|---|---|---|
| macOS (Apple Silicon) | `ocrmac` (Vision) | MPS (Metal) | `/opt/homebrew/bin` | desactivado si RAM ≤ 8 GB (evita el OOM del M1) |
| Windows | Tesseract CLI (o RapidOCR si no hay Tesseract) | CUDA si hay GPU, si no CPU | `C:\Program Files\Tesseract-OCR`, Poppler en rutas típicas o **pypdfium2** si no está | activado |
| Linux / Docker | Tesseract CLI | CPU (CUDA si existe) | `/usr/bin` | activado |

- Si Poppler no existe (común en Windows) las páginas se renderizan con `pypdfium2`.
- Todo se puede forzar en `.env`: `OCR_ENGINE`, `DOCLING_DEVICE`, `DOCLING_TABLES`, `TESSERACT_CMD`, `POPPLER_PATH`.
- `serve.py` elige el servidor WSGI según el SO: **gunicorn** (macOS/Linux/Docker) o **waitress** (Windows).
- `GET /health` del módulo reporta la plataforma detectada, y el panel de administración la muestra.

---

## 3. Correo con Resend (códigos y alertas)

Se envían correos para: verificar el correo al registrarse, el código de inicio de sesión de los
usuarios que **activaron** la verificación en dos pasos (desactivada por defecto, se activa en
Configuración) y las alertas de vigencia.

Por defecto `MAIL_PROVIDER=console`: los correos (con el código de 6 dígitos) se imprimen en
`docker compose logs -f backend`. Para enviar correos reales con tu dominio y branding:

1. En https://resend.com crea una API key y verifica tu dominio (registros DNS SPF/DKIM).
2. En `.env`:
   ```
   MAIL_PROVIDER=resend
   RESEND_API_KEY=re_xxxxxxxx
   MAIL_FROM=OCR Platform <no-reply@tudominio.com>
   ```
3. `docker compose up -d backend`

> Con el remitente de prueba `onboarding@resend.dev` Resend solo entrega al correo dueño de la cuenta.

**Algoritmo 2FA** (`backend/src/services/dosfa.ts`): código de 6 dígitos con CSPRNG
(`crypto.randomInt`), se guarda solo su **HMAC-SHA256** ligado al usuario y propósito, vence en
10 min, máximo 5 intentos, un solo uso, y se compara en tiempo constante.

---

## 4. Trazabilidad con el documento

| Requisito | Implementación |
|---|---|
| RF-AW-01 / CUP-01 Registro (correo, código, términos) + guía de uso obligatoria la primera vez | `backend/src/routes/auth.ts`, `frontend/src/pages/registro.astro`, `app/tutorial.astro` |
| RF-AW-02 / CUP-02 Login + 2FA opcional por usuario + JWT + reconfirmar contraseña en operaciones sensibles | `routes/auth.ts`, `routes/perfil.ts`, `lib/sesion.ts`, `middleware/auth.ts` |
| RF-AW-03..06 / CUP-03..06 Subir, procesar (asíncrono), validar y consultar | `services/documentos.ts`, `routes/documentos.ts`, páginas `app/*` |
| CUP-07 / RN-06 CURP con antigüedad ≤ 3 meses | extracción de `fecha_emision` (regex) + `evaluarReglas()` |
| RN-07 Versión vigente / RN-09 trazabilidad detectado→confirmado / edición posterior | tablas `documento_versiones`, `validaciones_dato` (`origen`) |
| RN-10 Solo documentos propios | todas las consultas filtran por `usuario_id` |
| RN-11 / RN-12 Vigencia y alertas | `fecha_vigencia` + tarea diaria `services/tareas.ts` (90/30/7/0 días) |
| RF-IA-01..09 Endpoints Tabla 23 | `ia_documentos/integration/flask_app.py` |
| RF-ADM-01..05 / CUA-03..06 Admin: CRUD de usuarios, baja lógica, tráfico, precisión de la IA, logs, reporte CSV | `routes/admin.ts`, páginas `admin/*` |
| 2.7.2.x Seguridad | HTTPS/TLS + reverse proxy Nginx, bcrypt, AES-256-GCM, consultas parametrizadas, cookies httpOnly+SameSite, CSP, rate limiting, auditoría |
| 2.8 / 4.5 Respaldos | servicio `backup` (cifrado AES-256, retención 7) |
| 7.4 Modelo NoSQL (Fig. 32) | colección `resultados_ocr` en MongoDB (valores cifrados) |
| Fig. 27–29 Interfaces | `frontend/` (tema oscuro de los mockups) |

---

## 5. Seguridad implementada

- **HTTPS** en Nginx (TLS 1.2/1.3, HSTS). Certificado autofirmado automático o de confianza con `mkcert`.
- **Reverse proxy**: solo Nginx publica puertos; backend, IA, PostgreSQL y MongoDB quedan en la red interna.
- **Contraseñas** con bcrypt (coste 12) y política mínima (10 caracteres, mayúscula, minúscula, número).
- **2FA** por correo opcional (cada usuario la activa en Configuración) y verificación del correo al registrarse.
- **Sesiones** JWT en cookies `httpOnly` + `SameSite=Strict` + `Secure`; revocables (`token_version`),
  reconfirmación de contraseña para cargar, validar o editar documentos y editar el perfil.
- **Cifrado AES-256-GCM** de archivos en disco, CURP y domicilio en PostgreSQL, y valores extraídos en MongoDB.
- **Inyección SQL**: todas las consultas son parametrizadas; entradas validadas con Zod.
- **CSP estricta** (`script-src 'self'`), cabeceras de seguridad, límite de peticiones en autenticación.
- **Auditoría**: `eventos_sistema` y `accesos`; el administrador ve la CURP enmascarada.
- **IA local**: los documentos nunca salen del equipo; la API del módulo exige `X-API-Key`.

> Importante: Guarda `DATA_ENCRYPTION_KEY` (en `.env`) en un lugar seguro: sin ella los datos cifrados no se pueden recuperar.

---

## 6. Desarrollo sin Docker (opcional)

```bash
# Módulo de IA
./scripts/ia-nativa.sh                     # http://localhost:5001/health
# Backend (requiere PostgreSQL y MongoDB accesibles; MONGO_URL=memory:// para pruebas rápidas)
cd backend && npm install && npm run dev   # http://localhost:3000/api/salud
# Frontend con recarga en caliente (proxy /api -> :3000)
cd frontend && npm install && npm run dev  # http://localhost:4321
```
Para desarrollo local por HTTP pon `COOKIE_SECURE=false` y `CORS_ORIGINS=http://localhost:4321`.

## 7. Pruebas

```bash
cd ia_documentos && python -m pytest                 # 95 pruebas (dominio, corrección OCR, API Flask, detección de SO)
cd backend && npm test                               # 22 pruebas: reglas/cripto + flujo E2E contra
                                                     # PostgreSQL real y el módulo de IA real
```
Variables para las pruebas del backend: `DATABASE_URL`, `IA_URL`, `IA_API_KEY`, `MONGO_URL=memory://`,
`JWT_SECRET`, `DATA_ENCRYPTION_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `NODE_ENV=test`.

## 8. Solución de problemas

| Síntoma | Solución |
|---|---|
| El navegador advierte del certificado | Normal con el autofirmado. Instala `mkcert`, borra `nginx/certs/*.pem` y vuelve a arrancar. |
| "LLM local: Solo regex" en el panel admin | Ollama no responde o falta el modelo: `ollama pull qwen3:4b-instruct-2507-q4_K_M` (Mac) o revisa `docker compose logs ollama-pull`. |
| El primer documento tarda mucho | Docling descarga/carga modelos la primera vez. En Docker se precargan al construir la imagen. |
| No llega el código 2FA | En modo consola míralo en `docker compose logs -f backend`. Con Resend revisa el dominio verificado. |
| Contenedor `ia` se reinicia (OOM) | Sube la memoria de Docker Desktop (Settings → Resources) a 8 GB o usa `--ia-nativa` en Mac. |
| El procesamiento tarda mucho | Revisa en el detalle del documento el tiempo por etapa (OCR / extracción). Usa `--ia-nativa` en Mac, confirma `OLLAMA_MODEL=qwen3:4b-instruct-2507-q4_K_M` y `OLLAMA_KEEP_ALIVE=30m` en `.env`, y limita Docker Desktop a ~3 GB en Macs de 8 GB. |
| La extensión no sincroniza | Abre la plataforma una vez en ese navegador y acepta el certificado (o usa `mkcert`); si dice que fue desvinculada, entra a Extensión > Vincular ahora. |
| Puerto 8443/8080 ocupado | Cambia `HTTPS_PORT` / `HTTP_PORT` y `PUBLIC_URL` en `.env`. |
| Restaurar un respaldo | `docker compose exec backup restaurar.sh pg_AAAAMMDD_HHMMSS.dump.enc` |

## 9. Hacia Azure (sección 4.6)

- `backend` y `ia` → Azure Container Apps / App Service (mismas imágenes).
- PostgreSQL → Azure Database for PostgreSQL; MongoDB → Azure Cosmos DB for MongoDB.
- Archivos → implementar `IAlmacenamiento` (`backend/src/services/almacenamiento.ts`) con Azure Blob Storage.
- Respaldos → Azure Backup. Certificado TLS real en Nginx o Azure Front Door.
