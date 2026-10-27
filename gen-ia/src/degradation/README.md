# src/degradation — T3 Degradación (chaos testing)

Convierte el PDF limpio (que ya trae la marca **MOCK DATA** dibujada) en un PDF
raster degradado y registra la geometría exacta aplicada (`TransformLog`) para
proyectar el Ground Truth a píxeles de la imagen final.

## Uso

```python
from contracts import GroundTruth, Face
from src.degradation import degrade_pdf, project_ground_truth, load_preset, load_config

cfg = load_preset("default")            # none | light | default | heavy
# cfg = load_config("mi_config.json")   # cualquier DegradationConfig en JSON
faces = [(p.face, (p.width_mm, p.height_mm)) for p in gt.pages]   # una entrada por página
pdf_bytes, log = degrade_pdf(clean_pdf, cfg, sample_id=gt.sample_id,
                             degradation_seed=1234, faces=faces)
gt_final = project_ground_truth(gt, log)   # copia con `transformed` rellenado
open("degraded.pdf", "wb").write(pdf_bytes)
open("transform_log.json", "w").write(log.model_dump_json(indent=2))
```

API pública (`src/degradation/__init__.py`):

| Función | Firma |
|---|---|
| `rasterize_pdf` | `(pdf_bytes, dpi) -> list[np.ndarray]` (RGB uint8, una por página) |
| `degrade_page` | `(image, cfg, rng, *, page_index, face, face_size_mm, render_dpi) -> (np.ndarray, PageTransformLog)` |
| `degrade_pdf` | `(clean_pdf, cfg, *, sample_id, degradation_seed, faces) -> (bytes, TransformLog)` |
| `degrade_pdf_with_images` | igual que `degrade_pdf` pero devuelve además las imágenes degradadas |
| `project_ground_truth` | `(gt, log) -> GroundTruth` (copia; `bbox_mm` intacto) |
| `effective_dpi` | `(page_log) -> float` DPI efectivo final de la página |
| `load_config` / `load_preset` | `(path) / (name) -> DegradationConfig` |

## Convención de coordenadas

- Cara: mm, origen arriba-izquierda, y hacia abajo (contrato `common.py`).
- Píxeles: coordenadas continuas de **borde**: el píxel (fila i, col j) cubre
  `[j, j+1) × [i, i+1)`. mm 0 → px 0.0; mm W → px `W·dpi/25.4`. El centro del
  píxel j está en `j + 0.5`.
- Cada paso geométrico registra `matrix` 3×3 (entrada → salida del paso, en
  coordenadas de borde). `composed = M_n·…·M_render` lleva mm de la cara → px de
  la imagen final; `final_size_px = (w, h)`.
- OpenCV usa coordenadas de centro: al llamar `warpAffine/warpPerspective` se
  usa `M_cv = T(-0.5)·M·T(+0.5)`. `cv2.resize` ya mapea bordes con bordes, así
  que su matriz es `diag(w_out/w_in, h_out/h_in)` (exacta tras redondear el tamaño).
- Rasterizado: `rasterize_pdf` usa `FPDF_RenderPageBitmapWithMatrix` con escala
  **exacta** `dpi/72` (el `render(scale=)` de pypdfium2 estira la página al bitmap
  redondeado hacia arriba, ~1 px de error en el borde lejano). El bitmap mide
  `ceil(lado·dpi/72)`; la fracción de píxel sobrante queda blanca.

## Ejecución de pasos

1. `render` (siempre, implícito): matriz `diag(render_dpi/25.4)`, params `{"dpi"}`.
2. `cfg.steps` en orden. Por cada paso se consume siempre `u ~ U(0,1)`; se aplica
   si `u < probability` (si no: `applied=False`, sin params ni matriz). Si se
   aplica, cada parámetro se muestrea uniforme en su `Range` (en orden alfabético
   del nombre) y el valor concreto queda en `params`, junto con valores derivados.
3. RNG por página en `degrade_pdf`: `np.random.default_rng([degradation_seed, page_index])`.

| Paso | Params (default si falta) | Opciones | Notas |
|---|---|---|---|
| `dpi_resample` | `target_dpi` (150–300) | — | Escala `target/dpi_actual`; el DPI efectivo se sigue entre pasos. INTER_AREA al reducir, INTER_CUBIC al ampliar. Registra `dpi_in`, `scale_x`, `scale_y`. |
| `canvas_placement` | `margin_mm` (5–30), `margin_y_mm` (si falta: 2º muestreo de `margin_mm`), `background` (235–250) | `canvas`: `letter` (default) / `a4` / `legal` / `[w_mm, h_mm]`; `position`: `margin` (default) / `random` | Pega la cara sin remuestrear (traslación entera) sobre un lienzo casi blanco al DPI efectivo. Si la cara no cabe, el lienzo crece (nunca recorta). `margin`: offset = margen. `random`: offset entero uniforme en todo el lienzo con margen mínimo `margin_mm` (x) / `margin_y_mm` (y); la cara siempre cabe entera (consume 2 enteros del rng; `margin` no consume nada extra). Registra `offset_px`, `offset_mm`, `position`, `canvas_mm`, `dpi`. La matriz es la traslación exacta `(offset_px)`. |
| `rotation` | `angle_deg` (−3–3) | `expand` (default `true`) | Positivo = antihorario en pantalla, alrededor del centro. Con `expand` el lienzo crece para no recortar; la traslación va en la matriz. |
| `skew` | afín: `shear` (−0.03–0.03), `shear_y` opcional (0) · perspectiva: `corner_jitter` (−0.03–0.03, fracción del lado) | `mode`: `affine` (default) / `perspective`; `expand` (default `true`) | Perspectiva: cada esquina se desplaza `dx_*·w, dy_*·h` (8 valores registrados: `dx_tl, dy_tl, …`); matriz no afín. |
| `jpeg` | `quality` (30–80, entero) | — | Codifica/decodifica JPEG (libjpeg de OpenCV). |
| `gaussian_noise` | `sigma` (3–12) | — | Ruido aditivo N(0, σ) por canal. |
| `salt_pepper` | `amount` (0.001–0.005) | — | Fracción de píxeles; mitad blancos, mitad negros. |
| `contrast_brightness` | `contrast` (0.8–1.2), `brightness` (−20–20) | — | `(v−128)·contrast + 128 + brightness`. |
| `illumination_shadow` | `strength` (0.1–0.4) | — | Gradiente lineal (dirección aleatoria) × sombra gaussiana suave; factor mínimo ≈ `(1−strength/2)²`. Registra `gradient_angle_deg`, `shadow_center`, `shadow_radius`. |
| `blur` | `sigma` (0.3–1.0) | — | Desenfoque gaussiano. |

Zonas expuestas por rotación/skew: casi blanco (245) si no hay lienzo; sobre
lienzo, la mediana del borde actual de la imagen (sigue a la iluminación previa).

## Salida PDF

Escritor PDF mínimo propio: una página por imagen, JPEG (`DCTDecode`) con
`cfg.output_jpeg_quality`, tamaño de página = `final_px · 72 / dpi_efectivo` pt.
Sin fechas ni `/ID` → misma entrada ⇒ mismos bytes. Re-rasterizar el PDF al DPI
efectivo reproduce la rejilla de píxeles del GT (las coordenadas `*_px` del GT se
refieren a la imagen embebida).

## Ground Truth

`project_ground_truth` rellena para cada campo `transformed.quad_px =
composed·esquinas(bbox_mm)` (TL, TR, BR, BL), `bbox_px` = envolvente y el tamaño
de imagen. **No recorta** `bbox_px` a la imagen (con `expand` todo campo dentro
de la cara cae dentro de la imagen). Fija `degradation_seed` desde el log. Falla
si falta la página en el log o si la cara no coincide.

## Marca de agua

La degradación nunca recorta la página (rotación/skew con `expand`, lienzo que
crece): la marca MOCK DATA del render limpio queda quemada en el raster.

## Presets (`presets/`)

`none` (solo render), `light`, `default` (= `contracts/fixtures/degradation_default.json`),
`heavy` (incluye perspectiva). `canvas_placement` está en probabilidad 0 en todos
estos (decisión 4 del PLAN).

`scanner`: simula un escaneo en cama plana, todos los pasos con probabilidad 1:
`canvas_placement` (carta, `position: "random"`, margen mínimo 0–5 mm, fondo 238–250),
`illumination_shadow` leve (0.05–0.2), `rotation` ±2° (expand), `dpi_resample`
150–300, `gaussian_noise` σ 1.5–5 y `jpeg` 50–85 (`output_jpeg_quality` 90).
Error medido de marcadores con este preset ≤ 0.2 px (tolerancia 1.5 px).

## Tests

```
.venv/bin/python -m pytest src/degradation -q
```

`tests/test_scanner.py`: `position: "random"` (cara siempre dentro del lienzo con
INE y carta, aleatoriedad, determinismo, matriz exacta), precisión del preset
`scanner` y un golden (`tests/golden_presets.json`: hashes de PDF/imágenes/geometría
de `none|light|default|heavy`, con y sin probabilidades forzadas a 1) que garantiza
que los presets existentes no cambian.

### Golden y plataforma

Los hashes de bytes (PDF, imágenes) y de matrices float dependen de la plataforma
(libjpeg/OpenCV/pdfium, coma flotante: macOS, linux/arm64 y linux/amd64 difieren), así
que `golden_presets.json` está **atado al contenedor `linux/amd64`** (su clave `_meta`
registra plataforma y versiones de OpenCV/numpy/pillow/pypdfium2). Por eso
`test_existing_presets_unchanged` solo corre con `OCR_DOCKER=1` (lo fija el Dockerfile) y
`x86_64`; fuera de ahí se omite con `pytest.skip`-equivalente y el motivo
"golden de bytes válido solo en el contenedor linux/amd64: docker compose run --rm tests".
Los demás tests (precisión ≤ 1.5 px, determinismo, rangos) corren en todas partes.

Regenerar solo tras un cambio intencionado de presets/pasos, del Dockerfile o del lock
(y con el resto de tests en verde), desde la raíz del repo:

```bash
docker compose build
docker compose run --rm -T --no-deps --entrypoint python tests \
    -m src.degradation.tests.golden > src/degradation/tests/golden_presets.json
```

Detalles en `docs/DOCKER.md` ("Goldens y plataforma").

Precisión: discos en mm conocidos (PDF de reportlab) → centroide detectado vs
`composed·p`. Tolerancia 1.5 px en cadenas completas con ruido y 0.3 px en pasos
geométricos aislados (atrapa errores de medio píxel en resize/traslación).
