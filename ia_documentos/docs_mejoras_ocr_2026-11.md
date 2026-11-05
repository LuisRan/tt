# Mejoras OCR 0.3.1 — 3 de noviembre de 2026

## Alcance

Esta entrega parte de `tt-integracion.zip` y conserva el frontend, registro con INE,
backend, la extensión, las reglas de Maiki y `gen-ia`. Se modificó principalmente
`ia_documentos/`. No se ha entrenado un nuevo modelo ni se han cambiado los datos
almacenados en PostgreSQL o MongoDB.

## Cambios implementados

1. **Rescate visual acotado de Tesseract**: primera pasada con autocontraste y
   modo PSM 3 (o PSM 11 para texto disperso). Si la lectura es débil, se prueba
   PSM 11 y, opcionalmente, CLAHE con unsharp mask y umbral adaptativo, con PSM 6.
   La variante se elige con una **heurística de legibilidad**, no por su número
   bruto de caracteres. No se hacen filtros adicionales cuando el texto inicial
   ya es legible. Cada variante se limita a 12 millones de píxeles y no se guarda.
2. **OCR complementario sin pérdida de la lectura anterior**: se conserva el
   texto extraído primero y se agregan únicamente líneas nuevas del rescate
   cuando los motores generan lecturas diferentes. La desduplicación ignora
   espacios y tildes, pero no modifica identificadores ni los reconstruye.
3. **INE anverso y reverso**: si el PDF tiene exactamente dos páginas y el
   clasificador detecta INE, también se hace OCR del reverso completo. Para
   documentos con ambas caras en una página se mantienen las zonas originales.
4. **Anti-alucinación de fechas**: `fecha_expedicion`, `fecha_caducidad` y
   `fecha_registro` exigen evidencia de **día, mes y año**. Ya no basta con
   coincidir en día y mes. Se admiten fechas ISO, numéricas o con el nombre
   del mes en español. Si falta el año se pide revisión humana.
5. **Tolerancia a fallos**: si la generación de variantes con OpenCV falla,
   se conserva la lectura base; no se cancela el documento por ese filtro.
6. **Versionado**: pipeline `0.3.1` para distinguir sus resultados en métricas.

### Algoritmos

- Normalización léxica Unicode (NFKD) y búsqueda de tokens relevantes.
- CLAHE (ecualización adaptativa de histograma limitada en contraste).
- Unsharp mask y umbral adaptativo gaussiano.
- PSM 3 / 11 / 6 de Tesseract como selección de segmentación alternativa.
- Fusión estable de líneas OCR mediante un conjunto de huellas normalizadas.
- Validación de fecha calendario completa para controlar alucinaciones del LLM.

No se ha demostrado todavía aumento de precisión estadística: debe medirse
contra documentos de referencia revisados manualmente, antes y después,
y reportarse por tipo, campo y dificultad de imagen.

## Configuración

`docker-compose.yml` pasa estas variables al servicio `ia`:

```dotenv
OCR_RESCATE_ADAPTATIVO=true
OCR_RESCATE_MIN_CHARS=100
```

La segunda variable está acotada entre 20 y 500 caracteres. Para recuperar el
comportamiento anterior de Tesseract cuando los filtros aumenten demasiado la
latencia, utiliza `OCR_RESCATE_ADAPTATIVO=false` en tu `.env` local. Mantén
`ANTI_HALLUCINATION=true`, que ya está fijado en Compose.

**Dependencia**: `opencv-python-headless` se incluye en los requisitos Linux y
Windows. Si se utiliza el adaptador fuera de Docker sin OpenCV, usa el rescate
reducido de Pillow. El resto de las dependencias del proyecto no cambia.

## Instrucciones para tu entorno de prueba

No subas tu `.env`, certificados, documentos o backups a Git. Conserva las
claves antiguas si necesitas recuperar documentos ya cifrados.

```powershell
# Desde la rama nueva de trabajo, y con tu .env privado ya presente
docker compose config --quiet
docker compose --progress plain build ia
docker compose up -d --no-deps ia
docker compose logs --tail=100 ia
```

Si utilizas proyecto Docker aislado (`-p tt-eden-pruebas`), incluye el mismo
`-p` en todos los comandos. No uses `down -v` ni sobrescribas tu base original.

### Regresiones ejecutadas en el entorno de elaboración

```bash
cd ia_documentos
PYTHONPATH=. python -m pytest tests/unit \
  --ignore=tests/unit/test_flask_app.py \
  --ignore=tests/unit/test_seguridad_api.py \
  -q --override-ini addopts='' \
  -k 'not test_osd_tesseract_ignora_orientacion_de_baja_confianza and not test_osd_tesseract_sin_umbral_no_toca_docling and not test_prompt_respeta_limite_y_registra_metricas'
```

Se excluyeron las pruebas que requieren dependencias no instaladas en el
entorno local: Flask, Docling y el cliente Ollama. Falta E2E con Docker, carga
real del backend y medición de WER/CER/exactitud por campo.

## Evaluación recomendada antes de producción

1. Guardar por separado ejemplos sintéticos o autorizados (sin PII en Git).
2. Etiquetar manualmente tipo documental, anverso/reverso y campos esperados.
3. Ejecutar con `OCR_RESCATE_ADAPTATIVO=false` y luego `true` sobre el MISMO
   conjunto, sin modificarlo entre corridas.
4. Medir CER/WER del texto, exactitud por campo, documentos desconocidos,
   valores descartados y duración p50/p95. Reportar resultados por tipo.
5. Si la lectura correcta no aparece en ninguna variante, evaluar mejoras
   del OCR o modelos especializados; fine-tuning de Qwen solo tiene sentido si
   el texto correcto sí existe pero la extracción posterior falla.
