# Ajuste de Docling en el modulo OCR (rama docling/tuning)

Todo se midio con **Docling 2.130.0, motor OCR ocrmac (Apple Vision), dispositivo MPS, tablas desactivadas
(auto en Mac de 8 GB), sin LLM ni Ollama**, solo la etapa OCR. Datos: campana sintetica
`baseline100-4242-1c6975d8`. Cribado solo en `dev`; confirmacion en `val`; `test` se miro una vez con la
configuracion fijada (solo agregados).

## Causa raiz del fallo en pasaportes (y de gran parte de INE/CURP/acta)
Los PDF son **una sola imagen** (pasaporte 354x250 pt, INE 243x153 pt). El layout de Docling corre a 72 dpi
(`page.get_image(scale=1.0)`, fijo en el codigo; `images_scale` NO lo cambia) y clasifica la tarjeta entera como
una `picture`. El OCR si lee dentro (en un pasaporte limpio: 25 items de texto hijos de la picture), pero
`export_to_markdown()` **omite el texto hijo de las pictures** y solo emite `<!-- image -->` (+ la MRZ, que cae
fuera). Resultado medido (dev, 24 pasaportes, Docling solo): deteccion exacta con markdown 8/24; con el mismo
texto exportado incluyendo hijos de picture 18-19/24. No es orden de lectura ni umbral de bitmap.

## Flags nuevos (todos apagados = comportamiento anterior byte a byte)
| Variable | Defecto | Efecto |
|---|---|---|
| `DOCLING_TRAVERSE_PICTURES` | false | `export_to_markdown(traverse_pictures=True)` |
| `DOCLING_TEXT_MODE` | markdown | `filas`: texto desde los items (incluye hijos de picture), un renglon por fila visual, izquierda a derecha |
| `DOCLING_OCR_MODE` | "" | `full_page` / `layout_regions` (en 2.130 `force_full_page_ocr` esta deprecado: es `OcrMode`) |
| `DOCLING_OCR_SCALE` | 0 | `OcrMacOptions.scale` (defecto de Docling 3.0) |
| `DOCLING_OCR_RECOGNITION` | "" | `accurate` / `fast` |
| `DOCLING_OCR_LANGS` | "" | idiomas separados por coma |
| `DOCLING_IMAGES_SCALE` | 0 | `images_scale`; sin efecto en el OCR en 2.130 (verificado en el codigo) |
| `DOCLING_RETRY_ON_UNKNOWN` | false | si Docling no detecta tipo, reconvierte con `DOCLING_RETRY_OCR_MODE/SCALE/TRAVERSE_PICTURES` antes de Tesseract/Vision; tope `DOCLING_RETRY_MAX_SECONDS`; deja `docling_ocr` en `metadatos` (solo notas, sin datos personales) |

`bitmap_area_threshold` ya no existe en Docling 2.130 (reemplazado por `OcrMode` PDF-aware): no hay que probarlo.
`framework=livetext` no existe (solo `vision`). RapidOCR/EasyOCR no se probaron: requieren onnxruntime/easyocr, no instalados.

## Cribado Docling solo (dev, 8 por tipo = 32 docs; texto = salida del conversor, sin rescates Tesseract/Vision)
Recall de campos / deteccion exacta (tolerante entre parentesis); baseline completo (adaptador) = 90.0 % / 31 de 32.
| Config (export) | markdown | traverse | filas |
|---|---|---|---|
| actual (scale 3, default) | 70.0 % / 26 (31) | 89.1 % / 31 (32) | 91.0 % / 31 (32) |
| full_page | 69.4 % / 26 | 88.9 % / 31 | 90.8 % / 31 |
| layout_regions | 70.0 % / 26 | 89.1 % / 31 | 91.0 % / 31 |
| scale 4 | 69.7 % / 26 | 88.5 % / 31 | 90.7 % / 31 |
| recognition fast | 31.3 % / 18 | 38.0 % / 20 | 40.0 % / 20 |
| Tesseract CLI (OCR_ENGINE=tesseract) | 42.2 % / 19 | 56.2 % / 24 | 57.8 % / 24 |

Pasaportes (dev, 24 docs, solo Docling, export filas): scale 2/3/4/5/6 -> recall 77.3/74.6/77.3/76.1/75.4 %,
deteccion 19/18/19/18/19: la escala no tiene efecto robusto (ruido). `full_page` y `layout_regions` no cambian nada
en PDFs de una sola imagen. `fast` y Tesseract empeoran mucho: se descartan.

**Reintento de Docling (resultado negativo).** Sobre los 14 fallos `DOCUMENTO_NO_SOPORTADO` de dev (9 pasaportes heavy,
4 INE heavy, 1 pasaporte default) ninguna configuracion de Docling (scale 2/3/4/5, full_page, tres exportaciones) detecta
mas de 1-2 de 14. La linea de `det/enhanced-retry` (8/30 con imagen mejorada) no se iguala: esos documentos estan
limitados por la calidad de imagen, no por la configuracion de Docling. En el adaptador completo
(dev, 64 docs) `DOCLING_RETRY_ON_UNKNOWN` dejo los mismos 3 desconocidos. No se recomienda activarlo.

## Adaptador completo (con rescates Tesseract/Vision), baseline = raw_text guardado de la campana
Recall de campos / criticos / deteccion exacta (tolerante). "ctl" = re-ejecucion sin flags en este worktree
(reproduce la cifra: dev 64 docs 88.8 % vs 87.2 % guardado, deteccion 61/64 igual; la diferencia viene de variaciones
de Apple Vision entre corridas, p. ej. un pasaporte recuperado). Tiempos: ctl/filas se midieron intercalados
en el mismo proceso y por documento; **el baseline guardado se midio con Ollama compitiendo por memoria y no es comparable**.
La maquina tuvo mucha variacion de velocidad entre tandas: solo se comparan tiempos intercalados.

| Split (n) | baseline guardado | ctl | `DOCLING_TEXT_MODE=filas` | tiempo medio ctl / filas |
|---|---|---|---|---|
| dev (64) | 87.2 / 74.0 / 61 (61) | 88.8 / 74.5 / 61 (62) | 91.5 / 83.2 / 61 (62) | 5.7 / 4.7 s (corridas separadas, no comparable) |
| val (87) | 83.4 / 77.9 / 79 (79) | 86.3 / 79.1 / 79 (81) | 88.2 / 84.5 / 79 (81) | 2.2 / 2.0 s (intercalado) |
| test (93) | 81.6 / 70.1 / 85 (85) | 83.7 / 71.8 / 85 (85) | 85.5 / 78.0 / 85 (85) | 2.0 / 1.9 s (intercalado) |

Por tipo en test (recall / criticos, baseline guardado -> filas): ver tabla de reproduccion abajo; el efecto esta en
**acta** (criticos 58.7 -> 84.0: folio de impresion e identificador electronico, que Docling dejaba como imagen);
INE, CURP y pasaporte quedan iguales dentro del ruido (INE 83.2 -> 83.5, CURP 93.3 -> 93.3, pasaporte 79.4 -> 84.2 recall /
66.7 -> 70.0 criticos). Tipos equivocados: 0 en todas las corridas. Deteccion: sin cambio en el adaptador completo
(los niveles Tesseract/Vision ya rescataban esos casos); los 8 fallos de test/val/dev restantes son documentos heavy.

`DOCLING_TRAVERSE_PICTURES` (markdown conservado): dev 89.3 / 76.4; val 86.8 / 80.2, pero **regresion critica en val**:
`folio_impresion` de acta 3 -> 0 (3 documentos). Descartado.

## Advertencia de impacto aguas abajo (importante)
Se comprobo con el extractor regex (sin LLM) sobre los mismos textos: con `filas` el regex sube en CURP del acta pero
pasa a extraer valores equivocados de `folio_impresion` (dev: 0 -> 10 equivocados; val 0 -> 8; test total equivocados
14 -> 23, correctos 142 -> 139). Los extractores regex asumen el formato markdown. **El efecto sobre la ruta LLM
(produccion) NO se midio** (no se ejecuta Ollama en esta tarea). Hay ademas 1 documento por split con perdida
de un campo critico de acta (`curp` de un progenitor) frente al baseline.

## Criterio de aceptacion (aplicado)
- Recall/deteccion neto: recall +3.9 pp en test (dev +4.3, val +4.8) con tiempo extra ~0; 0 tipos equivocados. CUMPLE a nivel OCR.
- 0 regresiones criticas por tipo: NO se cumple del todo (acta `curp_p1/p2`, 1 doc en dev y 1 en test) ni se midio el LLM.
- Conclusion: **no se activa por defecto**. Propuesta: campana completa con LLM y `DOCLING_TEXT_MODE=filas` antes de decidir;
  alternativa mas acotada: aplicar `filas` solo a actas. Nada de lo demas (scale, modo OCR, fast, Tesseract CLI, reintento) mejora.

## Como reproducir
```
cd ia_documentos
PY=.venv/bin/python   # el venv de tt/ia_documentos
$PY scripts/docling_bench.py baseline --split dev --campos          # referencia desde raw_text guardado
$PY scripts/docling_bench.py run --name x --split dev --per-type 8 --mode docling --out /tmp/runs --env DOCLING_OCR_SCALE=4
$PY scripts/docling_bench.py report /tmp/runs/x --variante traverse # variantes markdown|traverse|filas de una misma conversion
$PY scripts/docling_bench.py ab --name val --split val --ab filas=DOCLING_TEXT_MODE:filas --out /tmp/runs   # A/B intercalado con tiempos comparables
$PY scripts/docling_bench.py report /tmp/runs/val_filas --split val
```
Activar: `DOCLING_TEXT_MODE=filas` (o `DOCLING_TRAVERSE_PICTURES=1`) en el entorno / `.env`.
No medido: LLM, RapidOCR/EasyOCR (no instalados), TableFormer, layouts egret (no estan en cache), preprocesado de imagen.

## Modo filas_acta

Activar con `DOCLING_TEXT_MODE=filas_acta` (por defecto `markdown`; cualquier valor desconocido equivale a `markdown`).

- La detección de tipo y todo el flujo previo usan el markdown de siempre. Solo si el tipo detectado es acta de nacimiento, y el texto base sigue siendo el markdown de la 1ª conversión (sin reintento ni OCR añadido), el texto del acta pasa a ser el texto por filas del MISMO resultado de Docling (se conserva el `DoclingDocument` en memoria; no hay segunda llamada a `convert`). `OCR_ACTA_ZONA_SUPERIOR` sigue añadiendo la franja superior después. Si no se puede construir el texto por filas, se usa el markdown. Se anota `docling_texto:filas_acta` en `notas_ocr`.
- INE, CURP, pasaporte y desconocidos producen el mismo texto que con `markdown` (verificado por prueba unitaria).
- Por qué solo acta (medición del orquestador con LLM real, 27 actas y 54 pasaportes de val+test): actas con `filas` 59.0 % -> 81.9 % de campos correctos (17 de 18 campos mejoran); pasaportes 87.2 % -> 80.6 % (la MRZ colapsa: mrz_linea2 57->22, mrz_linea1 29->11 aciertos). INE y CURP no se midieron con LLM.
- `numero_acta` (única baja en actas con LLM: 76 -> 70 de 101), medido SOLO a nivel OCR en dev (75 actas, `docling_bench.py ab`, adaptador completo): recall en texto crudo 67/75 con markdown y 67/75 con `filas_acta` (idéntico; por preset none 25/25, default 25/25, heavy 17/25 en ambos). La pérdida no está en el texto sino en cómo lo lee el LLM: con filas, la etiqueta y el valor quedan en renglones distintos (cabecera "Oficialía Fecha de Registro Libro Número de Acta" y debajo la fila de valores, con el número al final); el número aparece a menos de 70 caracteres de su etiqueta en 46 documentos con filas frente a 55 con markdown. No se aplicó ninguna corrección en `docling_text.py`: no hay pérdida a nivel OCR que medir y reubicar valores bajo etiquetas sería una heurística frágil.
