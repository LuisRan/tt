# Rendimiento del LLM y de la ingesta (rama perf/ia-latency)

Mediciones con el CLI de evaluacion (`integration.evaluation_cli`), 12 PDFs fijos (3 por tipo, preset `none`,
ids `*-000000/1/2-none` de la campana baseline-2026-2396f44c), Mac Apple Silicon 8 GB, qwen3:4b-instruct-2507-q4_K_M,
concurrencia 1, Ollama compartido (11434). Referencia de calidad: `predictions.jsonl` de esa campana (mismo sample_id).

## Perfil de una llamada al LLM (promedio de 12 docs)
- Tokens de entrada 1684 (prefill ~11-15 s, ~100-150 tok/s; varia por cache de prefijo de Ollama).
- Tokens de salida 295 (decode ~34-39 s, ~8-9 tok/s). El JSON de salida es el cuello de botella: ~70-75 % del tiempo de extraccion.
- `load_duration` ~0 (keep_alive 30m ya activo). Render de PDF: 0.1-0.6 s (no es problema). OCR medio ~5-7 s.
- Ollama imprime el JSON con sangria aunque se use `format` con esquema: mismos 309 tokens.

## Resultados (latencia por documento, s)
| Configuracion | Media | P50 | OCR | Extraccion | Tok. ent. | Tok. sal. | Campos vs referencia |
|---|---|---|---|---|---|---|---|
| Referencia campana (48 docs/12 docs) | 62.8 | 61.9 | - | - | - | - | - |
| Base, calentamiento (descartado) | 53.2 | 52.2 | 5.8 | 46.9 | 1684 | 295 | 228 iguales / 0 |
| ctx4096 (`LLM_NUM_CTX=4096`) | 53.1 | 50.0 | 4.9 | 47.8 | 1684 | 295 | 228 iguales / 0 |
| Base, repeticion final (deriva) | 61.2 | 59.3 | 6.7 | 54.1 | 1684 | 295 | 228 iguales / 0 |
| `LLM_SKIP_RESOLVED` (RECHAZADO) | 51.7 | 51.4 | 6.3 | 44.9 | 1671 | 263 | 210 iguales / 18 cambian |

La deriva entre dos corridas base (53.2 -> 61.2 s, +15 %) es mayor que cualquier ganancia medida: la contencion de
memoria/termica de la maquina domina. Memoria del modelo en Ollama: 3.81 GB (ctx 8192) -> 3.18 GB (ctx 4096). RSS pico del proceso Python ~0.56 GB.

## Aceptado
- Contadores de Ollama (`prompt_eval_count`, `eval_count`, `*_duration`) en `OllamaQwenAdapter.ultimas_metricas` y en el log `llm_response_ok`.
- `LLM_NUM_CTX=4096` (variable existente; default sigue 8192): resultados identicos campo a campo, -0.63 GB de RAM. Se anade
  `_limite_texto()`: recorta el texto OCR segun el contexto (con 8192 sigue siendo 8000 caracteres, sin cambio) para que prompt + salida quepan.
  Advertencia: cambiar num_ctx recarga el modelo (~4 s) y, si otro cliente usa el mismo Ollama con otro num_ctx, se alternan recargas.

## Rechazado (medido)
- `format` con esquema JSON: sin `required` el modelo omite casi todos los campos (4 de 19); con `required` emite los mismos 309 tokens (sin ganancia) y reordena claves.
- Pedir JSON compacto y/o omitir nulos en el prompt: -19 % tokens de salida (309 -> 250) pero 30 de 212 valores del LLM cambian (apellidos de INE rotos, padres/madres de actas); "omitir nulos" solo: sin ahorro (el modelo lo ignora) y 12 cambios.
- Omitir al LLM los campos que el regex ya resolvio (CURP valida, clave de elector, codigos INE, pasaporte): -11 % tokens de salida, solo -1.5 s medios (dentro del ruido), y 18 campos cambian, incluidos criticos (`folio_impresion`, `identificador_electronico` pasan de null a un valor, uno con forma de CURP). Revertido.
- Cache de render PDF->imagen: cada render cuesta 0.1-0.6 s; no vale la complejidad. `verificar_conexion` ya esta cacheado 30 s.
- Cambiar modelo/cuantizacion: fuera de alcance.

## Suposiciones no verificadas
- La precarga del modelo en Ollama al arrancar (ahorro ~4-10 s solo en el primer documento) no se midio para no descargar el modelo del Ollama compartido.
- Sin ground truth: la calidad se compara contra la referencia del baseline, no contra la verdad de terreno.
