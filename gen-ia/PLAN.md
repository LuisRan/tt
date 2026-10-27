# PLAN — Generador de datos sintéticos para OCR

Estado: **FASE 1 completa (T1–T4) en master (2026-10-23). 356 tests en verde.**

## 1. Inventario de `formatos/` (solo referencia local, ignorada por git)

| Archivo | Contenido útil | Limitaciones |
|---|---|---|
| `modeloactual2016-ine.pdf` (4 pág., carta) | Pág. 1: anverso/reverso INE 2016 con callouts de campos (A–J, CIC, OCR, QR, barras, recuadros de voto, MRZ 3 líneas). Pág. 2–4: catálogo de 16 elementos de seguridad. | Es la variante "votar desde el extranjero". Imagen escalada, sin cotas en mm. Págs. 2–4 = lista de lo que **NO** se replica. |
| `Diapositiva1.png` (960×720) | Constancia CURP 2019: encabezado, clave, nombre, fecha inscripción, folio, entidad, QR, código de barras, CURPs asociadas, carta, firma, 2º QR, leyenda. | Baja resolución; sin cotas. Contiene datos de ejemplo que **no** se copian. |
| `OFICIO DE SEGOB DG-410-0075-2017 Y ANEXOS.pdf` (8 pág., escaneado) | Anexo 1: Formato Único de Acta de Nacimiento con bloques A–O y descripción de contenido de cada campo (§II); medidas de seguridad (§III); transición (§IV). | Escaneo sin capa de texto; sin cotas. |
| `pasaportelibretae.pdf` (1 pág., A4, tríptico) | Página de datos del pasaporte tipo "E": campos, foto, foto fantasma, firmas, MRZ 2×44, tabla de información contenida. | El folleto describe el pasaporte **de emergencia** tipo "E" (SRE, consulados). Imagen pequeña y rotada. |

Ninguna referencia trae medidas físicas → tamaños nominales por estándar
(ID-1, carta, TD3) y posiciones medidas **proporcionalmente** sobre las imágenes.

## 2. Stack

| Necesidad | Elección | Motivo |
|---|---|---|
| Lenguaje | Python 3.11 (venv en `.venv`) | Pedido; disponible en el equipo. |
| Contratos | Pydantic v2 + JSON Schema exportado | Validación en runtime y esquema portable. |
| Datos | Faker `es_MX` + `random.Random(seed)` | CURP/claves se construyen con reglas propias y dígitos verificadores; nunca datos reales. |
| Render PDF limpio | **reportlab** (BSD) | Vectorial en mm exactos; trae Code128 y QR integrados (sin deps extra). Se descarta PyMuPDF para render por licencia AGPL. |
| Rasterizar | **pypdfium2** (Apache/BSD) | PDF → imagen al DPI exacto, determinista. |
| Degradación | OpenCV (headless) + numpy | Matrices afines/homografías explícitas para el transform_log. |
| PDF degradado | Pillow (imagen → PDF) | La salida degradada es raster: la marca de agua queda "quemada". |
| Tests | pytest | |
| Fuentes | Familias lógicas (`sans`, `serif`, `mono`, `ocrb`) → fuentes base de PDF que trae reportlab (Helvetica, Times, Courier), sin archivos de fuente en el repo. OCR-B: solo si hay una versión con licencia libre; si no, `mono`. | |

## 3. Contratos (`contracts/`)

- `common.py` — **mm, origen top-left, y hacia abajo**; `BBox(x0,y0,x1,y1)`, `Quad` (4 puntos TL,TR,BR,BL), matrices 3×3 homogéneas, `project_bbox`.
- `layout_spec.py` (a) — `LayoutSpec → FaceSpec → FieldSpec{name, type, bbox, font{family,size_pt,color,uppercase}, align, lines, max_chars, static_value, label, in_ground_truth}`. Tipos: text, multiline, photo, qr, barcode, mrz, signature, static, shape.
- `field_catalog.py` — nombres canónicos de campo por documento/cara + formato esperado del valor + tamaños físicos. Es lo que permite a T1 y T2 trabajar en paralelo.
- `ground_truth.py` (b) — `GroundTruth{schema_version, sample_id, doc_type, layout_version, seed, degradation_seed, generator_version, synthetic{is_synthetic, watermark_text, faker_locale}, pages[{face, size, fields[{name,type,value,bbox_mm, transformed{quad_px,bbox_px,image size}}]}]}`.
- `degradation.py` (c) — `DegradationConfig{render_dpi, steps[{kind, probability, params: rangos, options}]}`; `TransformLog{config completo, pages[{steps[{kind, applied, params concretos, matrix 3×3|None, tamaños}], composed, final_size_px}]}`.
- `schemas/*.schema.json` y `fixtures/` (layouts de rejilla, config de degradación por defecto, GT de ejemplo) generados con `python -m contracts.build_artifacts`.

**Regla de GT**: `composed = M_n·…·M_render` mapea mm de la cara → px de la imagen final. `transformed.quad_px = composed · esquinas(bbox_mm)`; `bbox_px` = envolvente. Cambiar un contrato requiere aprobación del orquestador.

## 4. DAG de tareas

```
F0 contratos ──┬──> T1 Layouts (src/layouts) ───────────────┐
               ├──> T2 Datos+GT (src/data_injection) [fixtures]├──> T4 Integración + CLI + guardrails
               └──> T3 Degradación (src/degradation) [fixtures]┘
```
T1, T2 y T3 arrancan en paralelo (T2/T3 contra fixtures). Antes de T4 se
re-ejecutan los tests de T2 contra los layouts reales de T1.

| Tarea | Entrega | Interfaz pública |
|---|---|---|
| T1 | `specs/<doc>.json` (4 docs, 6 caras), motor `render_template(spec, face, values|None, watermark=True) -> bytes PDF` con placeholders, formas sustitutas y marca "MOCK DATA" en el contenido. | `src/layouts/__init__.py` |
| T2 | `generate_record(doc_type, seed, index) -> dict[face][field]=value`; `build_ground_truth(layout, values, seed, ...) -> GroundTruth`; validadores CURP / clave de elector / MRZ (ICAO 9303) / fechas; avatares y firmas sintéticas. | `src/data_injection/__init__.py` |
| T3 | `degrade_page(image, cfg, rng, face_size_mm, render_dpi) -> (image, PageTransformLog)`; `degrade_pdf(clean_pdf, cfg, seed) -> (pdf_bytes, TransformLog)`; `project_ground_truth(gt, log) -> GroundTruth`. | `src/degradation/__init__.py` |
| T4 | `src/pipeline.py`, CLI `generate --doc-type --n --seed [--config] [--out]`, salida `out/<sample_id>/{clean.pdf, degraded.pdf, gt.json, transform_log.json}`; `tests/test_guardrails.py`. | |

## 5. Criterios de aceptación

Por módulo: tests propios en verde, README con ejemplo, misma seed ⇒ mismo resultado (bytes de JSON idénticos; imagen idéntica), sin deps/assets prohibidos, solo toca su directorio.

- **T1**: cada spec valida contra `LayoutSpec` y contiene exactamente los campos del catálogo; ningún bbox fuera de la cara; render de las 6 caras sin error; marca de agua presente en el contenido de cada página; proporciones de bloques verificadas contra la referencia (tolerancia ±2 % del lado).
- **T2**: 1 000 registros por doc pasan los regex del catálogo; CURP con estructura y dígito verificador válidos; MRZ con dígitos verificadores ICAO correctos; coherencia cruzada (CURP ↔ nombre/fecha/sexo/entidad, MRZ ↔ campos); GT valida contra el schema.
- **T3**: cada paso respeta probabilidad y rango; para cada paso geométrico, un punto marcado en la imagen cae a ≤1.5 px de `composed·p`; misma seed ⇒ misma imagen; salida es PDF.
- **T4 / guardrails** (fallan el pipeline): (1) cada página degradada contiene "MOCK DATA" (verificado en el contenido renderizado antes de degradar + detección en la imagen); (2) ningún archivo del repo coincide por hash/nombre con `formatos/` ni es imagen fuera de una allowlist; (3) no hay términos/elementos de seguridad prohibidos en specs ni código (guilloche, microtexto, OVI, UV, foto fantasma real, escudo real); (4) `synthetic.is_synthetic == true` en todo GT.

## 6. Decisiones (aprobadas por el usuario 2026-10-22)

1. **Pasaporte**: el folleto de `formatos/` es del pasaporte *de emergencia* tipo "E". El objetivo correcto es el **pasaporte electrónico ordinario (desde oct-2021)**: página de datos TD3 125×88 mm, Tipo "P", campos: foto, tipo, código de país, número, apellidos, nombres, nacionalidad, observaciones, fecha de nacimiento, CURP, sexo, lugar de nacimiento, expedición, autoridad, caducidad, imagen secundaria centro-derecha, MRZ 2×44. El folleto se usa solo como referencia proporcional de la familia de diseño. `doc_type = pasaporte_electronico_2021`.
2. **INE**: dos variantes testeables, `nacional` (default, ~80 % del volumen en modo mixto) y `extranjero` (leyenda "PARA VOTAR DESDE EL EXTRANJERO", domicilio en el exterior). Una sola layout_spec; los campos exclusivos llevan `variants`.
3. **Textos oficiales largos** (carta de la constancia CURP, leyendas de datos personales/verificación): se mantienen los **originales** transcritos de la referencia como STATIC; lo ilegible se rellena con texto de longitud similar. Nombres de firmantes/funcionarios siempre Faker.
4. **Tamaño**: las tarjetas/libretas se generan a su tamaño físico real; `canvas_placement` existe pero está desactivado por defecto.
5. **Precisión**: sin cotas oficiales → posiciones proporcionales sobre la referencia, tolerancia ±2 %.
6. `formatos/` en `.gitignore`. Cada muestra = un PDF con una página por cara. Foto fantasma → segundo avatar genérico; huella → óvalo vacío; bordes decorados del Acta → línea lisa.

## 7. Orquestación

El entorno no expone Orca; se usa el equivalente: subagentes en **worktree git aislado** (uno por tarea), cada uno restringido a su directorio, sin sub-workers. El orquestador revisa cada entrega (tests + contrato) antes de fusionar y despachar dependientes.

## 8. Cierre de Fase 1 (2026-10-23)

- Decisiones adicionales del usuario: marca de agua = una diagonal ~20 % de opacidad (1b); `codigo_2d` añadido al Acta (bloque I).
- Ajustes posteriores: L2 (marca 1b, campos vacíos en blanco, codigo_2d), D2 (payload codigo_2d, regla cic/ocr↔MRZ), G2 (preset `scanner`, posición aleatoria).
- Prueba de precisión e2e del GT: diferencial (render con/sin valores, misma seed) — detecta desplazamientos ≥~2 mm y errores de escala ≥3 %; la precisión sub-píxel la cubren los tests de marcadores de T3 (≤0.2 px medido).
- Riesgo conocido: `src/degradation/tests/golden_presets.json` compara bytes y depende de las versiones de OpenCV/libjpeg; puede fallar en otra máquina sin que haya bug.
- Workers de Fase 1 (T1–T3) corrieron en Opus; desde entonces todos los workers usan Sonnet 5.5, esfuerzo alto (ver proyecto.md, sección MODELOS).
