# Evaluación supervisada: puente generador → OCR

Este paquete (`src/evaluation/`) conecta el generador de documentos sintéticos (`gen-ia`) con el módulo
OCR/IA de `tt/ia_documentos` **sin pasar por la web ni por una sesión**. Genera documentos con verdad de
referencia conocida, los envía al OCR real, compara y emite reportes reproducibles.

## Ejecutar

```
cd tt/gen-ia
./evaluar.sh                              # prueba pequeña (examples/campaign.smoke.json)
./evaluar.sh examples/campaign.baseline100.json   # campaña grande (~7 h)
```

Requisitos: Ollama encendido (`localhost:11434`) y el entorno del OCR en `tt/ia_documentos/.venv`.
Comandos sueltos (sustituye los nombres de carpeta por los reales; `ls out/campaigns`):

```
OLLAMA_HOST=http://localhost:11434 .venv/bin/python -m src.evaluation run --config examples/campaign.smoke.json
.venv/bin/python -m src.evaluation run --config examples/campaign.baseline100.json --resume   # reanudar
.venv/bin/python -m src.evaluation report  --run-dir out/campaigns/NOMBRE_DE_LA_CAMPAÑA
.venv/bin/python -m src.evaluation compare --baseline out/campaigns/CAMPAÑA_A --candidate out/campaigns/CAMPAÑA_B
```

Para campañas largas usa `caffeinate -dimsu` (si el equipo duerme, el proceso muere) y no uses la web ni
lances otra campaña a la vez: ambas comparten Ollama y contaminan la medición de latencia.

## Flujo

1. **Configuración** (`contracts/evaluation.py: CampaignConfig`): semilla, documentos por tipo, presets de
   degradación (`none`, `light`, `default`, `heavy`, `scanner`), política de páginas de la INE.
2. **Generación** con la API Python del generador. Cada documento base se genera una vez por preset con la
   misma semilla e índice: mismos datos, distinta degradación. El `sample_id` enviado al OCR es
   `<id_generador>-<preset>`; `group_id` identifica al documento base.
3. **Reparto dev/val/test por `group_id`** (sha256 de semilla + grupo), de modo que las variantes de un mismo
   documento nunca cruzan conjuntos.
4. **OCR**: `CliOcrClient` lanza una vez `integration.evaluation_cli` de `tt/ia_documentos` (el modelo se
   carga una sola vez); `HttpOcrClient` consume `POST /api/v1/documentos/procesa`. Ambos implementan el puerto
   `OcrClient` y son intercambiables.
5. **Evaluación** contra el ground truth del generador (la salida del OCR nunca es la referencia).
6. **Reportes** en `results/`.

La INE se envía por defecto solo con el anverso (`ine_pages: "front"`, spec 5.4.1); el ground truth conserva
los campos del reverso y el evaluador no los espera.

## Carpeta de una campaña (`out/campaigns/<run_id>/`)

```
config.json         configuración efectiva, hash y versiones (generador, contratos, layout, git)
manifest.jsonl      una fila por muestra: sample_id, group_id, split, preset, rutas, run_id
samples/<preset>/<tipo>/<id>.pdf|.json   documento y ground truth
submitted/          PDFs recortados enviados al OCR (INE anverso)
predictions.jsonl   una predicción del OCR por muestra (contrato OcrPrediction)
ocr_data/           datos aislados del OCR (nunca el data/ real de tt)
results/            report.md, report.json, fields.csv, samples.csv, confusion.csv, performance.json
```

Cada resultado se rastrea: `sample_id` → PDF → GT → `config.json` → `run_id`. Las predicciones incluyen la
versión del pipeline, el modelo LLM y un hash de la configuración efectiva del OCR.

## Qué mide

- **Extracción** por campo y tipo: EXACT, NORMALIZED, PARTIAL, MISMATCH, MISSING, EXTRA, UNINTERPRETABLE,
  NOT_APPLICABLE; CER y WER. Las claves legítimas del esquema del OCR sin equivalente en el GT son
  `EXTRA_SCHEMA` y no cuentan como error.
- **Normalización por campo**: los identificadores críticos (CURP, clave de elector, pasaporte, folios, MRZ)
  solo admiten mayúsculas y espacios; no se tocan dígitos, ceros iniciales ni O↔0. Las fechas se comparan como
  fecha. El domicilio ignora `, . ;`. El texto sin acentos es PARTIAL, nunca correcto.
- **Clasificación**: exactitud, precisión/recall/F1 por clase, matriz de confusión, no clasificados.
- **Rendimiento**: latencia media, P50/P95/P99 (solo con muestras suficientes), documentos por segundo, tasa de
  error, tiempo de carga del modelo y tiempo por etapa.
- **Comparación** (`compare`): deltas globales, por tipo y por campo crítico; señala regresiones por tipo o
  campo aunque la exactitud global suba.

## Límites que conviene tener presentes

- Los datos son sintéticos: replican geometría y distribuciones, no documentos reales. Una mejora medida aquí
  debe confirmarse con documentos reales, con consentimiento, solo para evaluar.
- Las campañas pequeñas sirven para detectar patrones y comparar antes/después; no para fijar un porcentaje.
- La latencia varía hasta ~15 % entre corridas idénticas en un equipo de 8 GB con swap alto.
- El timeout por documento del OCR no cancela el hilo en segundo plano (ver `docs_evaluacion.md` en
  `tt/ia_documentos`).
- Los bytes de los PDFs solo son idénticos entre máquinas dentro de Docker linux/amd64.
