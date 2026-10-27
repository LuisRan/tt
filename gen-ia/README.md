# ocr-synth: datos sintéticos de PRUEBA para OCR de documentos mexicanos

Genera pares **PDF degradado + JSON de Ground Truth** para entrenar, estresar y evaluar un
modelo OCR sobre cuatro documentos: INE 2016 (variantes `nacional` y `extranjero`),
Constancia CURP 2019, Acta de Nacimiento 2015 y Pasaporte electrónico 2021.

> **Son datos MOCK, nunca documentos reales.** Cada página lleva la marca de agua legible
> "MOCK DATA", todos los datos salen de Faker con una seed registrada y no hay ningún
> asset oficial ni elemento de seguridad. Ver [Directriz crítica y guardrails](#directriz-crítica-y-guardrails).

## Instalación

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Requiere Python >= 3.11 (pydantic, faker, reportlab, pypdfium2, numpy, opencv-python-headless, pillow).

También puedes ejecutarlo en un contenedor reproducible: ver [docs/DOCKER.md](docs/DOCKER.md).

## Uso del CLI

### Menú interactivo

```bash
python -m src            # sin argumentos y con una terminal (TTY): abre el menú
python -m src menu       # lo abre siempre (también con stdin canalizado)
python -m src generate -i
```

Sin TTY y sin argumentos imprime la ayuda y sale con código `2` (nunca espera input). El menú
pregunta, en español y con el valor por defecto entre corchetes (Enter lo acepta; una entrada
inválida repite la pregunta):

1. **Documentos**: `1` INE 2016 (anverso y reverso), `2` Constancia CURP 2019, `3` Acta de Nacimiento
   2015, `4` Pasaporte electrónico 2021. Se escriben números separados por comas (`1,3`), rangos
   (`1-3`) o `a` para todos.
2. **Cantidad** (1-2000 por documento): la misma para todos o distinta por documento; muestra el tiempo
   estimado (~1-2 s por muestra).
3. **Variante de la INE** (solo si eligió la INE): nacional, extranjero o mixta (80/20).
4. **Degradación**: `1` `mixed` (default, recomendada: mayoría de documentos normales/limpios y algunos
   escaneados, sucios o muy degradados en el mismo lote), `none`, `light`, `default`, `heavy`,
   `scanner` o la ruta a un JSON.
5. **Seed**: Enter genera una seed **aleatoria distinta en cada ejecución** (el menú muestra
   "Seed usada: N" y la incluye en el comando equivalente); escriba un número para repetir
   exactamente un lote anterior.
6. **Carpeta de salida** (default `data/`; dentro de Docker, `/app/data`, que en tu equipo es `./data`).
   Dentro se crea una carpeta por tipo de documento con los PDFs y su JSON directamente, sin
   subcarpetas por muestra. Después pregunta "¿Guardar también el PDF limpio y el log de
   transformaciones? [s/N]" (default No; ver `--extras`). Si la carpeta de algún documento ya tiene
   muestras, pregunta: sobrescribir, añadir con otro índice inicial o cancelar (nunca sobrescribe en
   silencio).
7. **Resumen** con todo lo elegido ("Archivos por muestra: PDF + JSON (GT)", o "... + PDF limpio y
   log de transformaciones (_extras/)"), el **comando `generate` equivalente** (para repetirlo sin
   menú; lleva `--extras` si se eligió) y confirmación `[S/n]`.

Tras confirmar muestra el progreso por documento (`[2/4] curp_2019: 10/10`) y el resumen final con
la carpeta de cada documento (ruta absoluta) y su número de PDFs. Ctrl+C o Ctrl+D cancelan sin traza
(código `130`).

### Línea de comandos

```bash
# 10 INE nacionales, seed ALEATORIA (sin --seed) y degradación mixed (salida en data/)
python -m src generate --doc-type ine_2016 --n 10

# Lo mismo, reproducible: con --seed N siempre salen los mismos bytes
python -m src generate --doc-type ine_2016 --n 10 --seed 42

# INE mixta: ~80 % nacional / 20 % extranjero (determinista por índice)
python -m src generate --doc-type ine_2016 --n 100 --seed 42 --variant mixed --out data/

# Solo variante extranjero, degradación fuerte
python -m src generate --doc-type ine_2016 --n 5 --seed 1 --variant extranjero --degradation heavy

# Varios documentos, y cantidades distintas por documento
python -m src generate --doc-type ine_2016,curp_2019 --n ine_2016=5,curp_2019=2 --seed 3

# N muestras de CADA documento
python -m src generate --doc-type all --n 3 --seed 7 --degradation scanner

# Con una configuración propia (DegradationConfig en JSON)
python -m src generate --doc-type curp_2019 --n 2 --seed 3 --degradation mi_config.json

# Carpeta ya usada: añadir más muestras a partir del índice 10
python -m src generate --doc-type curp_2019 --n 5 --seed 3 --on-exist append --start-index 10

# Conservar también el PDF limpio y el log de transformaciones (en <documento>/_extras/)
python -m src generate --doc-type curp_2019 --n 5 --seed 3 --extras
```

`generate` (script de consola tras `pip install -e .`) y `python -m src` son equivalentes; el
subcomando `generate` es opcional.

| Opción | Valores |
|---|---|
| `--doc-type` | `ine_2016`, `curp_2019`, `acta_nacimiento_2015`, `pasaporte_electronico_2021`, `all`, o varios separados por comas |
| `--n` | muestras por documento (default 1), o `DOC=N,DOC=N` para cantidades distintas |
| `--seed` | seed de datos. Sin `--seed` se genera una **aleatoria** distinta en cada ejecución (se imprime en el resumen, en el comando "Para repetirlo exactamente" y queda en `run.json`); con `--seed N` el lote es reproducible |
| `--variant` | `nacional`, `extranjero`, `mixed` (solo INE; se ignora en los demás) |
| `--degradation` | `mixed` (**default**; antes era `default`), `none`, `light`, `default`, `heavy`, `scanner` o ruta a un `.json` |
| `--out` | carpeta de datos (default `data/`, ignorada por git) |
| `--extras` | guarda además, por muestra, el PDF limpio (`<sample_id>.clean.pdf`) y el log de transformaciones (`<sample_id>.transform_log.json`) en `<documento>/_extras/`. Sin esta opción solo se guardan el PDF degradado y el GT |
| `--on-exist` | si la carpeta de un documento ya tiene muestras (archivos `<doc_type>-*.pdf/.json`, `manifest.jsonl` o subcarpetas de la estructura antigua con `gt.json`): `error` (default, no escribe nada), `overwrite` (borra solo lo que generó la herramienta, nunca otros archivos tuyos, y regenera), `skip` (omite ese documento) o `append` (añade desde `--start-index`; falla si el índice ya existe) |
| `--start-index` | índice de la primera muestra (default 0) |
| `-i`, `--interactive` | abre el menú |

Al terminar muestra un resumen (muestras y PDFs por documento y variante, páginas, degradación, reparto
real de presets con `mixed`, seed y la ruta absoluta de la carpeta de cada documento) y el comando exacto para repetir el lote con la seed ya resuelta.
Códigos de salida: `0` correcto, `1` error del pipeline (p. ej. carpeta existente con `--on-exist error`,
degradación inválida, falta la marca de agua), `2` uso incorrecto de la línea de comandos, `130`
cancelado.

### Desde Python

```python
from src.pipeline import generate_sample, generate_batch

r = generate_sample("pasaporte_electronico_2021", seed=42, index=0, degradation="default", out_dir="data")
print(r.sample_id, r.degradation_seed, r.path(r.degraded_pdf))   # data/pasaporte_electronico_2021/<sample_id>.pdf

rs = generate_batch("ine_2016", 100, 42, variant="mixed", degradation="light", out_dir="data")
```

`out_dir` es la carpeta de datos; cada documento va a `out_dir/<doc_type>/` (archivos planos
`<sample_id>.pdf` y `<sample_id>.json`).
`generate_sample(doc_type, seed, index, *, variant=None, degradation="default", out_dir, extras=False)` ejecuta:
layout, datos + GT, render limpio con marca de agua, **verificación de la marca** (texto
"MOCK DATA" en cada página, si falta lanza `WatermarkMissingError` y no escribe nada),
degradación con una `degradation_seed` derivada de `(seed, index)`, proyección del GT y escritura. El PDF
limpio y el `transform_log` se usan en memoria y solo se escriben con `extras=True`.
`generate_batch(..., start_index=0, extras=False)` repite para los índices `start_index..start_index+n-1` y escribe
el `manifest.jsonl` del documento. La política de carpetas existentes (`--on-exist`) y `run.json` los
aplica `src.cli.execute_run` (lo usan el CLI y el menú). La marca de agua no se puede desactivar
desde esta API.
`degradation="mixed"` también funciona aquí (un preset por muestra según `(seed, doc_type, index)`); el default de
estas funciones de biblioteca sigue siendo `"default"`, y la seed aleatoria es solo cosa del CLI y el menú
(la API siempre recibe la seed explícita).

## Estructura de salida

Archivos **planos** dentro de la carpeta de cada documento: sin una carpeta por muestra, así que se
puede hojear (por ejemplo en Finder) sin abrir carpeta por carpeta.

```
data/                                   # --out (default data/)
  run.json                              # parámetros del run (determinista, sin reloj)
  ine_2016/                             # una carpeta por documento elegido (solo las elegidas)
    manifest.jsonl                      # una línea por muestra
    ine_2016-nacional-42-000000.pdf     # PDF degradado (lo que consume el OCR)
    ine_2016-nacional-42-000000.json    # Ground Truth (mismo nombre base)
    ine_2016-extranjero-42-000001.pdf   # la INE nacional y extranjero conviven aquí
    ine_2016-extranjero-42-000001.json
    ...
  curp_2019/ ...
  acta_nacimiento_2015/ ...
  pasaporte_electronico_2021/ ...
```

`sample_id` = `<doc_type>-<variante>-<seed>-<índice de 6 dígitos>`. Por muestra solo hay dos archivos con el
mismo nombre base `<sample_id>`: el `.pdf` (raster degradado; la marca "MOCK DATA" queda quemada en la
imagen; una página por cara) y el `.json` (Ground Truth con bboxes originales y transformadas). El
emparejado `X.pdf` + `X.json` es el formato estándar de los datasets OCR.

Con `--extras` (o respondiendo `s` en el menú) se añade **una sola** subcarpeta plana por documento:

```
  ine_2016/
    _extras/
      ine_2016-nacional-42-000000.clean.pdf            # render vectorial limpio (con marca "MOCK DATA")
      ine_2016-nacional-42-000000.transform_log.json   # configuración y matrices de cada paso aplicado
```

Sin `--extras` el PDF limpio y el log solo existen en memoria (el pipeline los necesita para verificar la
marca de agua y proyectar el GT) y no se escriben.

**Rutas relativas** (los tests lo verifican). Todas son relativas a la carpeta del **documento**:

- `<sample_id>.json` (`degraded_pdf`, `clean_pdf`, `transform_log`): `degraded_pdf` es siempre el nombre
  del PDF hermano (`<sample_id>.pdf`); con `--extras`, `clean_pdf` y `transform_log` son
  `_extras/<sample_id>.clean.pdf` y `_extras/<sample_id>.transform_log.json`; sin `--extras` ambos son `null`.
- `manifest.jsonl` (`degraded_pdf`, `gt`, `clean_pdf`, `transform_log`): `<sample_id>.pdf`, `<sample_id>.json` y,
  con extras, las rutas de `_extras/` (`null` sin extras). Otros campos: `sample_id`, `doc_type`, `variant`,
  `seed`, `index`, `degradation_seed`, `degradation_preset` (preset efectivo de esa muestra; con `mixed`, el
  elegido para ella; coincide con `config_name` del `transform_log`). En modo `append` el manifest conserva las
  entradas anteriores, ordenadas por índice.

`--on-exist overwrite` borra **solo** lo que genera la herramienta en la carpeta del documento
(`<doc_type>-*.pdf`, `<doc_type>-*.json`, `manifest.jsonl`, los archivos `<doc_type>-*` de `_extras/` y, por
compatibilidad, las subcarpetas de la estructura antigua que contienen `gt.json`); nunca otros archivos
tuyos. Una carpeta con muestras de la estructura antigua (`<sample_id>/gt.json`) también cuenta como
"ya tiene muestras" para `--on-exist`.

`run.json` guarda `doc_types`, `n` por documento, `seed`, `start_index`, `extras`, `variant`, `degradation`
(nombre, origen, dpi y, con `mixed`, los `weights` usados), `versions` (contratos, generador y layout de cada documento) y el `command`
equivalente. No lleva fecha, hora, commit ni datos de la máquina: los mismos parámetros producen los
mismos bytes. Con `--on-exist skip` y todo omitido no se reescribe. Los JSON se escriben con
`ensure_ascii=False`, indentados y de forma determinista: misma seed implica bytes idénticos en el `.pdf` y
el `.json` de cada muestra (y en los extras, si se piden).

## Formato del Ground Truth (`<sample_id>.json`)

Valida contra `contracts.GroundTruth` (esquemas JSON en `contracts/schemas/`). Resumen:

```json
{
  "schema_version": "1.0.0",
  "sample_id": "curp_2019-default-7-000000",
  "doc_type": "curp_2019", "variant": "default", "layout_version": "1.0.0",
  "seed": 7, "degradation_seed": 1451673859, "generator_version": "0.1.0",
  "synthetic": {"is_synthetic": true, "watermark_text": "MOCK DATA", "faker_locale": "es_MX"},
  "pages": [{
    "page_index": 0, "face": "unica", "width_mm": 215.9, "height_mm": 279.4,
    "fields": [{
      "name": "...", "type": "text", "value": "...",
      "bbox_mm": {"x0": 0, "y0": 0, "x1": 0, "y1": 0},
      "transformed": {"quad_px": {"points": [[0,0],[0,0],[0,0],[0,0]]},
                      "bbox_px": {"x0": 0, "y0": 0, "x1": 0, "y1": 0},
                      "image_width_px": 0, "image_height_px": 0}
    }]
  }],
  "clean_pdf": null, "degraded_pdf": "curp_2019-default-7-000000.pdf", "transform_log": null
}
```

(`clean_pdf` y `transform_log` valen `null` sin `--extras`; con `--extras` apuntan a
`_extras/<sample_id>.clean.pdf` y `_extras/<sample_id>.transform_log.json`.)

- `bbox_mm`: caja **original** en milímetros, origen arriba-izquierda, y hacia abajo (coordenadas de la cara).
- `transformed`: la misma caja en **píxeles de la imagen final** (el `<sample_id>.pdf` degradado). `quad_px`
  son las 4 esquinas (TL, TR, BR, BL) y `bbox_px` su envolvente alineada a ejes. **No hace falta el
  `transform_log`** para ubicar los campos: `bbox_px`/`quad_px` ya están en píxeles de la imagen final.
- Ejemplo: emparejar `X.pdf` con `X.json` y reconstruir las posiciones con `bbox_px`:

```python
import json
from pathlib import Path
import pypdfium2 as pdfium

doc = Path("data/curp_2019")
for pdf_path in sorted(doc.glob("*.pdf")):
    gt = json.loads(pdf_path.with_suffix(".json").read_text(encoding="utf-8"))   # X.pdf <-> X.json
    pdf = pdfium.PdfDocument(pdf_path)
    for page, gt_page in zip(pdf, gt["pages"]):                 # una página por cara
        w_px = gt_page["fields"][0]["transformed"]["image_width_px"]
        scale = w_px / page.get_width()                         # pt -> px (el PDF raster es 1 imagen por página)
        image = page.render(scale=scale).to_pil()               # imagen de la cara, en los px del GT
        for f in gt_page["fields"]:
            b = f["transformed"]["bbox_px"]
            crop = image.crop((b["x0"], b["y0"], b["x1"], b["y1"]))   # recorte del campo f["name"]
            # f["value"] es el texto esperado para ese recorte (entrenar/evaluar el OCR)
```

- Con `--extras`, la matriz `composed` (3x3 homogénea) de cada página del `transform_log.json`
  (`pages[i].composed`) mapea mm de la cara a px de la imagen final (así se obtuvo `transformed`):

```python
import json, numpy as np
log = json.load(open("data/curp_2019/_extras/<sample_id>.transform_log.json"))
M = np.array(log["pages"][0]["composed"])              # 3x3
x_px, y_px, w = M @ np.array([x_mm, y_mm, 1.0])
x_px, y_px = x_px / w, y_px / w                        # w != 1 solo con perspectiva
```

- Valores: texto `str`; multilínea y MRZ `list[str]`; QR/código de barras, su payload; foto/firma, el id
  del avatar/firma sintético.
- Las coordenadas px son de borde de píxel (el píxel `(fila i, col j)` cubre `[j, j+1) x [i, i+1)`).
  Para rasterizar el PDF degradado alineado con el GT use `image_width_px / page.get_width()` como escala
  (como arriba) o el DPI efectivo (`src.degradation.effective_dpi(page_log)`, `final_px / lado_mm * 25.4`,
  que requiere el `transform_log`); el redondeo da +-1 px.
- Una página por cara: INE tiene anverso y reverso; los demás, una sola.

## Presets de degradación

Todos renderizan a 300 dpi; cada paso se aplica con su probabilidad y parámetros muestreados con
la `degradation_seed` (reproducible).

| Preset | Contenido |
|---|---|
| `none` | Solo el render a 300 dpi (sin degradación). |
| `light` | Pasos suaves: rotación +-1 deg, ruido sigma 2-6, JPEG calidad 50-85, sombras leves. |
| `default` | Rotación +-3 deg, shear, sombras, contraste/brillo, remuestreo 100-300 dpi, blur, ruido gaussiano y sal y pimienta, JPEG 10-60. |
| `heavy` | Rotación +-6 deg, perspectiva, sombras fuertes, remuestreo 80-200 dpi, ruido sigma 8-28, JPEG 5-35. |
| `scanner` | `default` pero la cara se coloca siempre sobre una "cama de escáner" tamaño carta (`canvas_placement` con probabilidad 1). |
| `mixed` | No es un preset: elige uno **por muestra** de forma determinista a partir de `(seed, doc_type, index)` (SHA-256; el mismo índice no recibe el mismo preset en todos los documentos) con estos pesos (`MIXED_DEGRADATION_WEIGHTS` en `src/pipeline.py`): `none` 10 %, `light` 45 %, `default` 25 %, `scanner` 12 %, `heavy` 8 %. Así el 55 % de los documentos son "normales" (`none` + `light`) y el 45 % restante son más sucios, en el mismo lote. El preset de cada muestra queda en `degradation_preset` (manifest) y `config_name` (transform_log). |
| `<ruta>.json` | Cualquier `contracts.DegradationConfig` serializada (ver `contracts/fixtures/degradation_default.json`). |

Detalle de los pasos y de la convención de coordenadas en `src/degradation/README.md`.

## Tests

```bash
python -m pytest -q                      # toda la suite (contratos, módulos, integración, guardrails)
python -m pytest tests -q                # solo integración y guardrails
python -m pytest tests/test_watermark_e2e.py tests/test_guardrails.py -q   # directriz crítica
```

- `tests/test_pipeline.py`: extremo a extremo por documento y variante, reproducibilidad byte a byte,
  CLI por subproceso, estructura plana de salida, `--extras` y precisión (con `heavy`, las `bbox_px`
  transformadas caen sobre tinta).
- `tests/test_watermark_e2e.py`: marca de agua de punta a punta (ver abajo).
- `tests/test_guardrails.py`: assets oficiales, elementos de seguridad y datos de especímenes.

## Directriz crítica y guardrails

El sistema genera **datos de prueba**, nunca documentos que puedan pasar por reales:

- **Marca de agua** legible "MOCK DATA" en cada página, dibujada como contenido de la página
  **antes** de la degradación (no es una capa removible) y verificada en el PDF limpio antes de
  degradar; tras degradar queda quemada en el raster.
- **Datos** solo de Faker (`es_MX`) con la seed registrada en el GT; nunca datos de personas reales.
- **Sin elementos de seguridad** (guilloche, microtextos, tintas especiales, hologramas, etc.).
- **Logotipos y escudos oficiales** sustituidos por figuras geométricas; fotos y foto fantasma por
  avatares genéricos.
- **`formatos/`** es solo referencia local de medidas (ignorada por git); no se copia ningún asset.

Tests automatizados que fallan si se rompe la directriz:

| Guardrail | Test |
|---|---|
| Falta la marca de agua en el PDF limpio de algún documento | `tests/test_watermark_e2e.py::test_clean_pdf_has_mock_data_on_every_page` |
| La marca no sobrevive a la degradación (comparación raster con/sin marca en la diagonal) | `tests/test_watermark_e2e.py::test_watermark_survives_degradation` |
| El pipeline no falla si se fuerza un render sin marca (o sin marca en una sola página) | `tests/test_watermark_e2e.py::test_pipeline_fails_without_watermark`, `::test_pipeline_fails_if_a_single_page_lacks_watermark` |
| El pipeline expone o usa `watermark=False` | `tests/test_pipeline.py::test_watermark_cannot_be_disabled_from_the_api` |
| Asset oficial o binario/imagen en el repo | `tests/test_guardrails.py` (`test_no_official_asset_copies`, `test_no_binary_assets_in_repo`) |
| Elemento de seguridad en código o specs | `tests/test_guardrails.py` (`test_no_security_element_code`, `test_specs_have_no_security_elements`) |
| Datos personales de los especímenes de referencia | `tests/test_guardrails.py::test_no_specimen_personal_data` |

`layouts.render_document(..., watermark=False)` existe solo para pruebas internas de comparación y
está **prohibido en producción**: `src/pipeline.py` y `src/cli.py` no lo usan ni lo exponen.

## Estructura del repo

```
contracts/          contratos (layout_spec, ground_truth, degradation) y esquemas JSON
src/layouts/        specs de los 4 documentos + render con reportlab
src/data_injection/ datos Faker + construcción del Ground Truth + validadores
src/degradation/    degradación, transform_log y proyección del GT
src/pipeline.py     integración extremo a extremo
src/cli.py          CLI `generate` (python -m src)
tests/              integración y guardrails
```
