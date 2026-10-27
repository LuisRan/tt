# Modo evaluacion del pipeline OCR

## Proposito
Medir el pipeline (Docling + regex + LLM + anti-alucinacion) contra un conjunto de PDFs
con verdad de terreno, por lotes y sin levantar el servidor web. Es un adaptador de entrada
mas (`integration/evaluation_cli.py`) sobre el caso de uso `application/evaluation.py`; ambos
reutilizan `DocumentPipeline.process` (una sola instancia por proceso: Docling se carga una vez).
No importa Flask, sesiones ni request.

## Uso
```bash
cd ia_documentos
python -m integration.evaluation_cli --manifest in.jsonl --out predictions.jsonl \
    --run-id R1 [--data-dir DIR] [--timeout-s 300] [--limit N]
```
- `--data-dir`: se fija como `IA_DATA_DIR` ANTES de importar `config.settings`; ahi van
  `output/` (texto crudo y JSON) y `logs/`. Si se omite se crea un directorio temporal nuevo
  que se borra al terminar. Nunca se usa el `data/` del usuario salvo que se pase explicitamente.
- `--timeout-s`: plazo por documento. El pipeline no tiene cancelacion: cada documento corre
  en un hilo daemon; al vencer se emite `code="TIMEOUT"` (`retryable=true`) pero el hilo sigue
  consumiendo CPU/RAM hasta terminar y los documentos siguientes compiten con el. Limitacion conocida.
- `--limit`: procesa solo las N primeras lineas del manifest.
- Exit code 0 aunque haya documentos con error; 2 solo si falla la inicializacion
  (run-id invalido, manifest ausente, settings/pipeline no inicializan).

## Contrato
Entrada (JSONL): `{"sample_id": str, "pdf_path": str}`; `pdf_path` relativo se resuelve desde el
directorio del manifest.

Salida (JSONL, una linea por documento, con flush por documento; OcrPrediction 1.0.0):
```json
{"schema_version":"1.0.0","sample_id":"s1","run_id":"R1","status":"ok|error",
 "error":{"code":"...","message":"...","retryable":false} ,
 "tipo_documento":"ine|curp|acta_nacimiento|pasaporte|null",
 "datos":{"campo":"str|null"},
 "metadatos":{"...envelope sin rutas absolutas..."},
 "timings":{"total_ms":0.0,"stages_ms":{"ocr_ms":0,"extraccion_ms":0},"model_load_ms":null},
 "algorithm":{"pipeline_version":"","llm_model":"","config_hash":"sha256","git_commit":null,"platform":null}}
```
- `error` es `null` si `status="ok"`. En error, `tipo_documento=null` y `datos={}`.
- Codigos: los del modo web (`DOCUMENTO_NO_SOPORTADO`, `PDF_INVALIDO`, `FORMATO_INVALIDO`,
  `OCR_NO_DISPONIBLE`, `ERROR_INTERNO`) mas los propios de la evaluacion: `ID_INVALIDO`,
  `RUTA_NO_PERMITIDA`, `MANIFEST_INVALIDO`, `TIMEOUT`. `retryable=true` solo en `TIMEOUT`.
- `timings.total_ms`: tiempo de pared del documento SIN la carga de modelos.
  `model_load_ms`: tiempo de `DocumentPipeline.precargar()` (import + creacion del conversor
  Docling), presente solo en el primer documento que supera la validacion de entrada; `null` en
  el resto. No incluye la carga de pesos que Docling difiere a la primera conversion (queda en
  `ocr_ms` del primer documento) ni la carga en frio del modelo en Ollama (queda en `extraccion_ms`).
- `config_hash`: sha256 del JSON ordenado de la configuracion efectiva que afecta el resultado
  (version, modelo/temperatura/ctx/num_predict del LLM, estrategia, anti-alucinacion, dpi, motor OCR,
  dispositivo/tablas Docling, umbrales de caracteres, limites de PDF). Excluye hosts, rutas y claves.
- `metadatos` excluye `ruta_texto_crudo`; `campos_descartados_por_alucinacion` conserva solo
  nombres de campo (el envelope original trae `campo=valor`).

## Limites de seguridad
- `sample_id` debe cumplir `^[A-Za-z0-9_\-]{1,64}$` (igual que `doc_id` en el modo web).
- `pdf_path`: debe existir, ser archivo regular, empezar por `%PDF-` y pesar <= `PDF_MAX_SIZE_MB`.
  Una ruta relativa no puede salir del directorio del manifest (`..`); un symlink (o una ruta que
  atraviese un symlink dentro del manifest dir) cuyo destino quede fuera de ese directorio se rechaza.
  Rutas absolutas sin symlink son aceptadas.
- `user_id="eval"`, `doc_id=sample_id`. La validacion de PDF (paginas, tamano, hash) y la
  anti-alucinacion del pipeline siguen activas: no hay flags para desactivarlas.
- Las escrituras del pipeline van solo a `--data-dir` (y la copia corregida en el temporal del SO,
  que el pipeline borra). El texto crudo OCR sigue guardandose en `--data-dir/output/raw_text`
  (contiene datos del documento): usar datos sinteticos o borrar el directorio.
- Los logs se configuran en WARNING y un filtro redacta `valor` y `campos_descartados` (solo
  nombres). Los errores inesperados reportan unicamente el tipo de excepcion, no su mensaje.

## Por que no se reutiliza la ruta HTTP del modo web
- Evita Flask, API key, multipart, limites de `MAX_CONTENT_LENGTH` y un servidor vivo: menos
  superficie y menos ruido en la medicion (los timings no incluyen red ni serializacion HTTP).
- La ruta web copia cada PDF a un temporal y usa un semaforo de concurrencia; aqui se procesa
  secuencialmente en el mismo proceso, cargando Docling una vez y midiendolo aparte.
- Las salidas del modo web van al `data/` del usuario; el modo evaluacion debe poder aislarse
  en un directorio propio (o desechable).
- El pipeline es el mismo (`DocumentPipeline.process`), asi que lo medido es lo que corre en
  produccion; solo cambia el adaptador de entrada.

## Prueba real
`EVAL_REAL=1 python -m pytest tests/integration/test_evaluation_cli.py -k real` genera un CURP
sintetico con gen-ia y lo procesa con Docling y Ollama locales (requiere `OLLAMA_HOST` alcanzable;
el `.env` del monorepo apunta a `host.docker.internal`, que no resuelve fuera de Docker).
