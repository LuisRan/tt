# src/layouts — T1 Layouts

Este módulo contiene las `LayoutSpec` reales de los 4 documentos (6 caras) y un motor
de render en reportlab que genera PDFs de **prueba** (datos sintéticos, con la marca
"MOCK DATA" en cada página). No reproduce ningún elemento de seguridad.

## Uso

```python
from contracts import Face, Variant
from src.layouts import load_spec, render_document, render_template

spec = load_spec("ine_2016")                       # valida contra contracts.LayoutSpec
pdf = render_document(spec)                        # sólo placeholders, variante por defecto (nacional)
pdf = render_document(spec, {
    Face.ANVERSO: {"nombre": ["PEREZ", "LOPEZ", "ANA"], "foto": "avatar:1f2e3d"},
    Face.REVERSO: {"mrz": ["IDMEX...", "...", "..."], "firma": "firma:9a8b"},
}, variant=Variant.EXTRANJERO)
one_face = render_template(spec, Face.REVERSO, {"cic": "123456789"})
open("muestra.pdf", "wb").write(pdf)
```

API pública (`src/layouts/__init__.py`):

| Función | Descripción |
|---|---|
| `load_spec(doc_type) -> LayoutSpec` | Lee `specs/<doc_type>.json`. |
| `render_document(spec, values=None, variant=None, watermark=True, faces=None) -> bytes` | PDF, una página por cara (anverso, reverso / unica), página = tamaño físico de la cara. |
| `render_template(spec, face, values=None, variant=None, watermark=True) -> bytes` | PDF de una sola cara. |
| `draw_avatar(canvas, x, y, w, h, avatar_id, faded=False)` | Avatar procedural determinista (pt, origen abajo-izquierda). |
| `draw_signature(canvas, x, y, w, h, signature_id)` | Firma bezier sembrada con el id. |
| `draw_shape(canvas, x, y, w, h, desc)` | Figura plana de un campo SHAPE. |
| `draw_watermark(canvas, width_mm, height_mm)` | Una diagonal "MOCK DATA" centrada, tenue. |
| `placeholder_value(doc_type, field)` | Placeholder de un campo sin valor. |

Tipos de valor: TEXT `str`; MULTILINE/MRZ `list[str]` (o `str` con `\n`); QR/BARCODE
`str` (Code128 / QR de reportlab); PHOTO `"avatar:<hex>"`; SIGNATURE `"firma:<hex>"`.
Campo ausente (o `values` = None, o valor `None`) → placeholder (`"X" * max_chars`,
avatar/firma derivados del nombre del campo, QR con "MOCK DATA"). Un valor **vacío
presente** (`""`, `[]` o lista de cadenas vacías) se deja en blanco: la etiqueta del
campo se dibuja, el valor no (ni placeholder, ni avatar/firma/QR/código de barras).

**`watermark=False` está PROHIBIDO en producción.** Sólo existe para los tests de este
módulo (comparar el raster con y sin marca).

Regenerar las specs tras cambiar cotas: `python -m src.layouts.build_specs`
(un test exige que los JSON estén sincronizados con el builder).

Tests: `.venv/bin/python -m pytest src/layouts -q`.

## Método de medición

Ninguna referencia trae cotas; se midió **proporcionalmente**:

1. Rasterizar la referencia a alta resolución con pypdfium2 en un directorio temporal
   fuera del repo (INE pág. 1 a 432 dpi; Acta = pág. 3 del oficio a 216 dpi; folleto del
   pasaporte a 360 dpi con la página de datos girada 90°; la CURP es un PNG de 960×720).
2. Localizar el marco del documento (perfiles de filas/columnas de píxeles oscuros y
   revisión visual).
3. Recortar el marco, escalarlo al tamaño nominal (`contracts.FACE_SIZES_MM`) por eje y
   superponer una rejilla en mm; leer cada bloque en la rejilla.
4. Verificación: se superpuso el render con placeholders sobre la referencia al 50 %.
   Las cajas de texto principales caen dentro de ±1 mm (INE) / ±2 mm (carta), dentro
   de la tolerancia de ±2 % del lado.

| Documento | Fuente | Marco (px) | Notas |
|---|---|---|---|
| INE anverso | `modeloactual2016-ine.pdf` p.1 | 2003×1245 | relación 1.609 vs 1.586 nominal (imagen algo ensanchada) → escala por eje. |
| INE reverso | ídem | 1982×1254 | MRZ TD1 3×30, paso fijo 82/30 mm. |
| CURP | `Diapositiva1.png` | — | la hoja no tiene borde visible: escala 2.376 px/mm fijada asumiendo el recuadro verde a 12.7 mm de cada borde lateral (supuesto). |
| Acta | `OFICIO ... ANEXOS.pdf` p.3 | 1232×1600 | el marco exterior se toma como la hoja carta completa (relación 0.770 vs 0.773). |
| Pasaporte | `pasaportelibretae.pdf` | 1210×800 | relación del folleto 1.51 vs 1.42 de TD3 → escala por eje; MRZ ajustada a ICAO 9303 TD3 (44 caracteres, paso 2.54 mm, centrada). |

## Decisiones

- **Fuentes**: familias lógicas → fuentes base PDF (sin archivos de fuente):
  sans=Helvetica, sans_bold=Helvetica-Bold, serif=Times-Roman, serif_bold=Times-Bold,
  mono=Courier-Bold (valores de la página de datos del pasaporte), ocrb=Courier (no hay
  OCR-B con licencia libre en el entorno).
- **Ajuste de texto**: nunca sale del bbox. Orden: condensación horizontal hasta 80 %,
  reducción de tamaño hasta 60 %, truncado. Los textos largos multilínea (STATIC
  `cuerpo`/`leyenda_pie`, MULTILINE como `str`) se ajustan por palabras.
- **MRZ**: dibujada carácter a carácter en una rejilla de paso fijo
  (`ancho / max_chars`). En el reverso INE, `cic` y `ocr` son sub-rangos de la línea 1
  (posiciones 5–13 y 17–29, base 0). Si se da la MRZ, esos campos no se redibujan (ya
  están en la MRZ); si no, se dibujan en su posición de la rejilla.
- **Etiquetas** (`label`/`label_bbox`): estilo por documento (INE negrita gris, pasaporte
  rojo ES/EN/FR); el tamaño sale de la altura del `label_bbox` con un tope por documento.
- **Figuras sustitutas** (`static_value` de los SHAPE, gramática
  `"<figura> clave=valor | descripción"`): escudos → `circle` de doble contorno; logos →
  `rect`/`hexagon` de contorno; bandera → `flag3` (tres franjas lisas); huella →
  `oval`; recuadros de voto → `boxes`; símbolo de chip → `chip`; marcos decorados →
  `frame` (doble línea lisa); banda superior INE → `header_band` (tinte plano).
- **Fotos**: avatares geométricos (cabeza circular + hombros trapezoidales) de alto
  contraste, color plano; la imagen secundaria se dibuja en gris claro (`faded`).
- **Fondos**: tintes planos (INE beige/gris, pasaporte crema, CURP/Acta blanco).
- **Marca de agua**: UNA sola diagonal "MOCK DATA" por página (tarjetas y cartas),
  centrada, Helvetica-Bold gris (0.30) con opacidad 0.20, ancho ≈80 % de la diagonal de la
  página (cruza la mayor parte de ella), dibujada al final, por encima del contenido.
  Antes (45 % gris / 35 % opacidad, más dos copias extra en cartas) era más intensa. Es
  contenido normal de la página (texto extraíble con pypdfium2), sin anotaciones, capas
  opcionales ni formularios (cubierto por tests, incluidos raster y geometría).
- **Determinismo**: `Canvas(invariant=1)`, RNG de avatares/firmas/franjas sembrado con
  SHA-256 del id. Misma entrada ⇒ mismos bytes.
- **Textos oficiales transcritos** (STATIC): `cuerpo` y `leyenda_pie` de la CURP y
  `leyenda_pie` del Acta, más avisos decorativos de la CURP (`aviso_telcurp`,
  `aviso_impresion`, `tramite_gratuito`, `presente`, `dependencia_emisora`). Todos eran
  legibles en la referencia: **no hubo relleno** de partes ilegibles. Los nombres de
  funcionarios son campos variables (`firmante`, `nombre_director`).

## Limitaciones

- La referencia INE es la variante "extranjero"; la posición de `leyenda_extranjero`
  (línea bajo "CREDENCIAL PARA VOTAR", y 10.6–12.8 mm) se infiere del callout C, que
  tapa la zona.
- INE reverso: el bloque 2D grande se usa como `codigo_barras` (Code128 estirado); el
  código 1D pequeño superior es una franja decorativa (`barras_superior`) que no
  codifica datos. La firma y el nombre del funcionario del reverso no están en el
  catálogo y se omiten.
- Acta: `qr` es el "Código QR" pequeño (bloque M, abajo a la
  derecha) y `codigo_2d` el código bidimensional grande del bloque I (abajo a la
  izquierda, 46.5×46.4 mm, QR cuadrado centrado en su bbox que escala cualquier
  payload —cientos de caracteres— sin salirse). Ya no hay recuadro decorativo en el bloque I. La leyenda "Soy México" (bloque J del Acta y la de la constancia CURP) se omite.
- Pasaporte: el folleto es del tipo "E"; las posiciones siguen su familia de diseño con
  ajustes para la página TD3 (etiquetas encima de los valores, imagen secundaria más
  baja para no tapar "Observaciones"). Los statics del catálogo están en mayúsculas
  ("ESTADOS UNIDOS MEXICANOS") aunque el impreso usa mayúsculas/minúsculas.
- La CURP depende del supuesto de márgenes laterales de 12.7 mm (la diapositiva no
  muestra el borde de la hoja).
- Courier sustituye OCR-B: el ancho de carácter respeta el paso ICAO pero la forma de los
  glifos no es OCR-B.
