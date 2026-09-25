# Integración Flask — IMPLEMENTADA

La integración REST que se planeó aquí ya está hecha, sin tocar el dominio:

- `integration/flask_app.py` — adaptador de entrada (FlaskRestAdapter) con los endpoints de la
  Tabla 23, mapeo de errores (400/401/413/415/422/503), validación de `doc_id`/`user_id`
  (evita path traversal), autenticación con `X-API-Key` y procesamiento serializado
  (un documento a la vez para no agotar memoria con Docling + LLM).
- `serve.py` — arranca con gunicorn (macOS/Linux/Docker) o waitress (Windows).
- `Dockerfile` — imagen Linux con Tesseract (spa+eng), Poppler, torch CPU y modelos de
  Docling precargados.
- `tests/unit/test_flask_app.py` — pruebas del contrato HTTP con un pipeline falso.

El backend Node.js la consume desde `backend/src/services/ia-cliente.ts`.
