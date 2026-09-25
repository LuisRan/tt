# Documentación técnica

Índice de la documentación del proyecto. Cada carpeta del monorepo tiene además su propio
`README.md` con el detalle de su código.

| Documento | Contenido |
|---|---|
| [ARQUITECTURA.md](ARQUITECTURA.md) | Componentes, contenedores, comunicación entre servicios y flujo de un documento. |
| [FLUJOS.md](FLUJOS.md) | Casos de uso implementados: registro, guía de uso, carga, validación, edición, 2FA, administración. |
| [API.md](API.md) | Referencia de los endpoints REST del backend (`/api/...`) y del módulo de IA. |
| [BASE_DE_DATOS.md](BASE_DE_DATOS.md) | Modelo relacional (PostgreSQL), colección de MongoDB, migraciones y métrica de precisión. |
| [SEGURIDAD.md](SEGURIDAD.md) | Controles de seguridad: TLS, sesiones, 2FA, reautenticación, cifrado, auditoría. |
| [OPERACION.md](OPERACION.md) | Comandos para arrancar, detener, reiniciar, reconstruir, ver logs y respaldar. |

| Carpeta | README |
|---|---|
| `backend/` | [backend/README.md](../backend/README.md) |
| `frontend/` | [frontend/README.md](../frontend/README.md) |
| `ia_documentos/` | [ia_documentos/README.md](../ia_documentos/README.md) y [OCR_STRATEGY.md](../ia_documentos/OCR_STRATEGY.md) |
| `nginx/` | [nginx/README.md](../nginx/README.md) |
| `backup/` | [backup/README.md](../backup/README.md) |
| `scripts/` | [scripts/README.md](../scripts/README.md) |
| `extension/` | [extension/README.md](../extension/README.md) |
