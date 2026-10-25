# Endurecimiento de seguridad del módulo de IA (rama sec/ia-hardening)

## Correcciones aplicadas

| Id | Qué se corrigió | Dónde |
|----|-----------------|-------|
| R1 | Pixel-bomb: antes de rasterizar se lee el MediaBox (pypdf) de cada página; se rechaza (PdfValidationError, HTTP 422 `PDF_INVALIDO`) si ancho×alto (in) × OCR_DPI² > `PDF_MAX_PIXELS` o si una dimensión > `PDF_MAX_DIM_PT`. `PIL.Image.MAX_IMAGE_PIXELS` se fija a `PDF_MAX_PIXELS`. | `infrastructure/ocr/pdf_validator.py`, `docling_adapter.py`, `platform_config.py` |
| R2 | Se rechazan PDFs cifrados y los que contienen /JS, /JavaScript, /Launch, /EmbeddedFile(s) (en Root, páginas, acciones y anotaciones, incl. /OpenAction con JS). | `pdf_validator.py` |
| R5 | La copia con orientación corregida usa `tempfile.mkstemp` en `DATA_DIR/tmp` (no `/tmp/{doc_id}_corregido.pdf`) y se limpia siempre, incluso con error. | `application/pipeline.py` |
| R6 | JSON y MD se escriben con `os.open(..., 0o600)` y reemplazo atómico (`os.replace`); el índice por usuario se protege con lock (hilos + `fcntl.flock`; no-op en Windows). Contenido y ubicación sin cambios. | `infrastructure/storage/file_adapters.py` |
| R8 | `IA_REQUIRE_API_KEY` (default false): si es true y `IA_API_KEY` está vacía, `create_app` lanza RuntimeError. En modo abierto se emite un WARNING `api_sin_clave_modo_abierto` al crear la app. | `integration/flask_app.py`, `config/settings.py` |
| R9 | El 500 ya no devuelve `str(e)`: `detalle` = `id_correlacion=<12 hex>`; la excepción completa se registra con ese id. Claves del cuerpo intactas. | `integration/flask_app.py` |

## Variables nuevas

- `PDF_MAX_PIXELS` (default 50000000): tope de píxeles por página al rasterizar.
- `PDF_MAX_DIM_PT` (default 5000): dimensión máxima de página en puntos.
- `IA_REQUIRE_API_KEY` (default false).

Nota: el directorio `DATA_DIR/tmp` se crea bajo demanda.

## Pruebas

`tests/unit/test_seguridad_pdf.py` (R1, R2), `test_seguridad_almacenamiento.py` (R5, R6),
`test_seguridad_api.py` (R8, R9). Todas sin red, sin LLM y sin Docling real.
Ejecutar: `python -m pytest -q tests` desde `ia_documentos/`.

## Riesgos pendientes (no abordados aquí)

- R3, R4: pendientes (ver matriz de la revisión estática).
- R7: IDOR por `doc_id`/`user_id`; requiere un cambio coordinado con el backend Node (autorización por propietario).
- R10 a R15: pendientes.
- R12: prompt injection hacia el LLM (el texto OCR es no confiable); sin mitigación adicional a la anti-alucinación existente.
- Límites de recursos (CPU/memoria/pids) en docker-compose: pendientes; fuera del alcance de este módulo.
- La detección de contenido activo es por inspección de claves del objeto PDF; no sustituye un sandbox ni un antivirus.
