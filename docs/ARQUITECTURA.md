# Arquitectura

La plataforma es un monorepo con cinco servicios que se ejecutan en local con Docker Compose.
Solo Nginx publica puertos; el resto vive en la red interna `interna` de Docker.

```
 Navegador ──HTTPS :8443──> web (Nginx)
                              ├── /            archivos estáticos del frontend (Astro)
                              └── /api/*  ───> backend (Node.js 22 + TypeScript, Express 5) :3000
                                                 ├──> PostgreSQL 16   datos relacionales, auditoría
                                                 ├──> MongoDB 7       resultados OCR (semiestructurados)
                                                 ├──> Resend (HTTPS)  correos 2FA y alertas (opcional)
                                                 └──> ia (Python 3.11, Flask) :5001   X-API-Key
                                                         ├── Docling + Tesseract / Apple Vision (OCR)
                                                         ├── regex + validación cruzada (CURP, clave de elector)
                                                         └──> Ollama :11434  qwen3:4b-instruct-2507-q4_K_M
 Extensión del navegador ──HTTPS (Bearer)──> /api/extension/*   (solo lectura de documentos validados)
 backup ──> pg_dump + mongodump cifrados cada BACKUP_INTERVALO_HORAS
```

## Servicios (`docker-compose.yml`)

| Servicio | Imagen / origen | Función | Perfil |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | Base relacional (usuarios, documentos, auditoría). Volumen `pgdata`. | siempre |
| `mongo` | `mongo:7` | Colección `resultados_ocr`. Volumen `mongodata`. | siempre |
| `backend` | `backend/Dockerfile` | API REST, sesiones, reglas de negocio, cola de procesamiento, tareas programadas. Volumen `storage` (archivos cifrados). | siempre |
| `web` | `nginx/Dockerfile` (compila el frontend) | TLS, reverse proxy, CSP, rate limit, sirve el sitio estático. | siempre |
| `backup` | `backup/Dockerfile` | Respaldos cifrados periódicos. Volumen `./backups`. | siempre |
| `ia` | `ia_documentos/Dockerfile` | Módulo de IA en contenedor Linux (Tesseract, CPU). | `ia` |
| `ollama` / `ollama-pull` | `ollama/ollama` | LLM local en contenedor (si no hay Ollama nativo). | `ollama` |

`COMPOSE_PROFILES` en `.env` decide qué perfiles se levantan. Con `./scripts/start.sh --ia-nativa`
(recomendado en Mac) el perfil `ia` se omite: el módulo corre nativo en macOS para usar Apple
Vision y la GPU Metal, y el backend lo alcanza en `http://host.docker.internal:5001`.

## Capas del backend

```
routes/        HTTP: validación de entrada (zod), guardas de sesión/rol/reautenticación
  └─ services/ reglas de negocio (documentos, perfil, 2FA, correo, alertas, cliente IA)
       └─ db/  PostgreSQL (pg, consultas parametrizadas) y MongoDB (driver oficial)
lib/           cifrado AES-256-GCM, JWT de sesión, logger, errores tipados
middleware/    autenticación, conteo de tráfico, manejador de errores
```

## Capas del módulo de IA (arquitectura hexagonal)

```
integration/flask_app.py   API REST (Tabla 23 del documento)
application/pipeline.py    orquesta: OCR -> clasificación -> extracción -> validación -> anti-alucinación
domain/                    entidades, esquemas Pydantic por tipo de documento, puertos
infrastructure/            adaptadores: Docling/Tesseract/Apple Vision, Ollama, regex, almacenamiento,
                           detección de SO (platform_config.py)
```

## Flujo de un documento

1. El usuario sube el archivo (`POST /api/documentos`, requiere reconfirmar contraseña).
2. El backend valida tipo MIME real, tamaño y formato; cifra el archivo con AES-256-GCM y lo guarda
   en `storage/`; crea el registro con estado `cargado` y lo encola.
3. La cola (`IA_CONCURRENCIA` trabajos simultáneos) descifra en memoria y lo envía al módulo de IA
   (`POST /api/v1/documentos/procesa`, multipart, cabecera `X-API-Key`).
4. El módulo ejecuta OCR, clasifica, extrae con LLM + regex, corrige errores de lectura con
   validación cruzada (dígito verificador de la CURP, fecha, sexo, entidad) y descarta valores
   que no aparecen en el texto (anti-alucinación). Devuelve JSON con campos, confianza y tiempos.
5. El backend aplica las reglas de negocio (tipo esperado, antigüedad de CURP, vigencia de INE,
   formato), guarda el resultado cifrado en MongoDB y cambia el estado a `procesado` o `rechazado`.
6. El usuario revisa y confirma (`POST /api/documentos/:id/validar`). Se registra cada campo en
   `validaciones_dato` (detectado vs. confirmado), el documento pasa a `validado` y a versión
   vigente, y si pertenece al titular de la cuenta se completa su perfil.
7. Después puede editar la información (`PUT /api/documentos/:id/datos`) sin afectar la métrica
   de precisión de la IA.

## Decisiones relevantes

- **Sitio estático + API**: el frontend es HTML/JS compilado por Astro; toda la lógica de acceso
  está en el backend. Esto permite una CSP estricta (`script-src 'self'`).
- **Procesamiento asíncrono**: el OCR puede tardar; la interfaz consulta
  `GET /api/documentos/:id/estado` cada pocos segundos y muestra el avance por etapa.
- **Datos semiestructurados en MongoDB**: cada tipo de documento tiene campos distintos; los
  valores se guardan cifrados junto con metadatos del pipeline (tiempos, modelo, correcciones).
- **Todo local**: ningún documento sale del equipo; el único servicio externo opcional es Resend
  para enviar correos.
