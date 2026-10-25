# Segunda revisión independiente (W9) del módulo OCR/IA

Rama `ext2/second-review` (desde `ext/reconcile`). Alcance: `ia_documentos/`. Nada se ejecutó contra
Ollama/Docling/CLI de evaluación; el único OCR real fue **Tesseract** (`nice -n 19`) sobre 4 imágenes
(acta dev 000002 en none/default/heavy y el pasaporte dev heavy fallido) más el recorte de franja con
`pdftoppm`/`pdfimages`/`sips` (render, no OCR). Disciplina de datos: solo dev y val; no se miró el detalle
de `test` ni `baseline100-*`.

## 1. Hallazgos (severidad)

| # | Sev. | Dónde | Hallazgo | Estado |
|---|---|---|---|---|
| H1 | ALTA | `reconcile.py` `_regla_nombres` (regla `apellido_es_etiqueta_o_entidad`) | `GUERRERO`, `HIDALGO`, `MORELOS`, `PUEBLA`, `COLIMA`, `DURANGO`, `TABASCO`, `OAXACA`, `JALISCO`... son apellidos reales y la regla los ponía en `null` aunque una CURP válida los confirmara. Reproducido (INE con CURP válida). | Corregido: se conserva si la CURP válida confirma las letras del componente; test. |
| H2 | ALTA | `reconcile.py` `_valor_antes` / reglas del acta | Asume el diseño `VALOR Etiqueta`. Con `Etiqueta: VALOR` (diseño etiqueta-primero) tomaba el valor del campo anterior: `primer_apellido` correcto `PEREZ` -> `ROSA MARIA`, `nombre_completo` corrupto, nacionalidades equivocadas. Solo validado con el generador sintético. | Mitigado: etiqueta seguida de `:` -> sin candidato. Riesgo residual con otros diseños (ver 4). |
| H3 | MEDIA | `pipeline.py` etapa 4b | El filtro anti-alucinación corre ANTES de reconcile: un valor que reconcile escribe en un campo protegido (CURP reparada, clave, folio...) nunca se verificaba contra el texto. | Corregido: `DocumentPipeline._reconciliar` revierte cambios no derivados en campos protegidos que no pasen `aparece_con_tolerancia`; 0 reversiones en dev/val. |
| H4 | MEDIA | `pipeline.py` / `reconcile()` | `reconcile` lanzaba con `raw_text` no-str (`bytes`, int) o `datos` no-dict (`ValueError/TypeError`); la llamada del pipeline no estaba protegida; `_seguro` tragaba excepciones sin registrar. | Corregido: `reconcile` nunca lanza, `_reconciliar` captura y registra (`reconcile_fallo`, `reconcile_regla_fallo`, solo nombre de regla/tipo de error, sin PII). |
| H5 | MEDIA | `reconcile.py` `nombre_completo_ensamblado` | Con CURP válida reemplazaba un `nombre_completo` más completo (`MARIA JOSE GARCIA LOPEZ`) por el ensamblado de un `nombres` truncado (`MARIA GARCIA LOPEZ`). | Corregido solo para el patrón estructural nombres + 1-2 palabras + apellidos; la basura (`MINARE ... DULCE`) se sigue corrigiendo. Primer intento más laxo causó regresión en dev/val y se descartó. |
| H6 | MEDIA | `reconcile.py` filiación del acta | Familias homoparentales (dos CURP del mismo sexo): `acta_curp_progenitor_sexo_incoherente` ANULABA la CURP legítima. | Corregido: con dos CURP del mismo sexo no se reasigna ni se anula. |
| H7 | MEDIA | `docling_adapter.detectar_tipo` | Clasificador exacto: bajo ruido de OCR devolvía tipo **equivocado** (1-2 de 116 variantes ruidosas, p. ej. por una única coincidencia frágil) y DESCONOCIDO en 60% con ruido 15%. La spec pide Tesseract antes de rechazar: se cumple (`extraer_texto`, pasos 1-2: página completa y modo disperso), pero el clasificador no tolera ruido. | Mejora tras flag `CLASSIFIER_TOLERANT`. |
| H8 | BAJA | `reconcile.py` `_curps_en_texto` | Coste lineal pero alto con muchos tokens de 18 alfanuméricos confundibles: 200 KB -> 2.1 s. Sin backtracking catastrófico ni O(n²) (probados 8 formas de texto de 200 KB en 4 tipos: <=2.2 s). | Cota de 400 tokens. |
| H9 | BAJA | estilo | `_PARTICULAS`, `_A_DIGITO`, `_A_LETRA`, `_plano` duplican `correccion_ocr` (con diferencias sutiles); `_decidir_reemplazo(campo)` y `_valor_antes(plano)` tienen parámetros sin uso; el módulo (780 líneas) mezcla 4 tipos de documento (candidato a dividir en `reconcile/{acta,ine,pasaporte,curp}.py`); import muerto `digito_verificador_curp` (quitado). | Documentado; refactor no hecho para no mover código validado. |
| H10 | BAJA | `correccion_ocr.pistas_curp` (hipótesis NO verificada) | No modela la sustitución por palabras inconvenientes de la CURP (p. ej. segunda letra -> `X`), que haría ver "inconsistente" un nombre correcto en `_regla_nombre_por_pool`/`_resegmentar`. Rara. | Sin cambio. |
| H11 | INFO | spec | Metadatos `campos_reconciliados` no contienen valores. Valores derivados fuera de `RECONCILE_ALLOW_DERIVED`: ninguno (verificado por test de 0 derivados sin el flag existente). Entrega siempre resultado con `errores_validacion`: sin cambios. | OK |

## 2. Qué se le pasó al primer agente (cruce con la línea base)
* **Folio de impresión del acta (11/12 MISSING) y parte de identificador electrónico**: no es fallo de
  extracción. El valor NO está en el texto porque Docling deja la franja superior (8% de la página, sobre
  marca de agua) como `<!-- image -->`. Un OCR de esa franja lo recupera (ver 3).
* El flag derivado y los fallos DOCUMENTO_NO_SOPORTADO: reconcile no puede actuar (no hay texto). El
  clasificador exacto y su tolerancia al ruido no se revisaron.
* Falsos positivos H1, H2, H5, H6 y la interacción con el filtro anti-alucinación (H3).

## 3. Cambios hechos (todos con prueba en `tests/unit/test_revision2.py`)
| Cambio | Flag (default) |
|---|---|
| Correcciones H1, H2, H4-H6, H8, regla `acta_folio_impresion_por_etiqueta` (8 dígitos tras la etiqueta, único, solo si el valor actual no es un número de 8 dígitos) | dentro de `POSTPROCESS_RECONCILE` (off) |
| `_reconciliar` (guarda anti-alucinación + captura de errores) | solo actúa con `POSTPROCESS_RECONCILE` |
| `infrastructure/ocr/clasificador_tolerante.py` + hook en `detectar_tipo` | `CLASSIFIER_TOLERANT` (**off**) |
| OCR de la franja superior (12%) del acta, texto AÑADIDO | `OCR_ACTA_ZONA_SUPERIOR` (**off**) |

Con ambos flags de OCR apagados el comportamiento de pipeline/adaptador es idéntico al anterior.

### Clasificador tolerante
Se consulta solo si el exacto da 0 coincidencias o 1 (frágil). Combina frases con coincidencia difusa
(ventanas de palabras, umbral 0.84, tolera acentos y espacios partidos/unidos) y estructuras (MRZ
`P<MEX`, línea 2 `MEX######`, relleno `<<<<<<`, clave de elector); exige >=3 puntos, margen >=2 sobre
el segundo tipo y al menos una señal fuerte. Si no, DESCONOCIDO (preferible a un tipo equivocado).
Medición con ruido sintético uniforme (sustitución/borrado/inserción/espacios) sobre los 29 textos
dev+val (4 variantes por texto y nivel; ruido artificial, no OCR real):

| ruido | exacto: ok/desconocido/equivocado | con flag: ok/desconocido/equivocado |
|---|---|---|
| 5% | 105/10/1 | 116/0/0 |
| 10% | 86/29/1 | 115/1/0 |
| 15% | 54/60/2 | 111/5/0 |
| 20% | 48/68/0 | 107/9/0 |
| 30% | 23/93/0 | 60/56/0 |

Textos limpios: idénticos. Controles negativos (lorem ipsum, contrato, factura, `<<<<`, vacío, "ESTADOS
UNIDOS MEXICANOS", palabras genéricas de INE sin frase fuerte, empate PASAPORTE/ACTA): todos
DESCONOCIDO (un primer diseño sin "señal fuerte" clasificó dos como INE y se endureció). Coste 30 KB: 0.4 s.
**Límite honesto**: el pasaporte heavy que falló (dev, `...000001-heavy`) con texto Tesseract (1x y 3x,
psm 3/6/11) no contiene ninguna frase clave ni MRZ legible (solo `ESTADOS UNIDOS MEXICANOS`, `<<`,
`G44270498`): el clasificador tolerante tampoco lo recupera y devuelve DESCONOCIDO (correcto bajo la
regla "nunca clasificar con basura"). Es fallo de reconocimiento (iii), no de clasificación. El texto de
Docling/Apple Vision real de ese documento no se conserva, así que no se pudo reproducir exactamente.

### OCR de franja superior del acta
Tesseract (psm 3, 200 dpi, 12% superior, ancho completo) sobre acta dev 000002: `Folio de Impresión
70345840` e `Identificador Electrónico 32036007219427516258` exactos en none/default/heavy (heavy: folio
legible, etiqueta con ruido). Simulación offline (texto de la franja anexado al texto crudo + reconcile
sobre las predicciones reales): acta dev 93.3% -> **100%** (folio_impresion 0/3 -> 3/3,
identificador_electronico 2/3 -> 3/3), 0 regresiones. Base: un solo acta (3 degradaciones) -> evidencia
estructural fuerte, estadística débil; la posición (franja superior) proviene del diseño del
generador (`bbox_mm` del GT) y puede no coincidir con actas reales.

## 4. Mediciones dev / val (exactitud de campos, evaluador `evaluate_sample`)
| | línea base | W8 (derivados) | W9 (derivados) | W9 + franja acta (simulada) |
|---|---|---|---|---|
| dev acta | 58.3 | 93.3 | 93.3 | **100.0** |
| dev curp | 88.9 | 96.8 | 96.8 | 96.8 |
| dev ine | 88.9 | 98.9 | 98.9 | 98.9 |
| dev pasaporte | 84.0 | 97.3 | 97.3 | 97.3 |
| val ine | 66.7 | 75.6 | 75.6 | 75.6 |
| val pasaporte | 91.1 | 97.8 | 97.8 | 97.8 |
Sin derivados: dev pasaporte 86.7, val pasaporte 95.6 (idéntico a W8). 0 regresiones correcto->incorrecto
en dev y val en todos los modos; curp, clave_elector, numero_pasaporte, mrz_*, folios sin regresión.
Los falsos positivos corregidos (H1, H2, H5, H6) no se manifestaban en dev/val: se demuestran con
tests, no con medición (las cifras no cambian por ellos; un primer intento de H5 sí causó regresión y se
corrigió). Suite: 378 passed, 1 skipped, cobertura 93.8%.

## 5. Errores restantes en dev+val (tras reconcile con derivados)
Clases: (i) corregible de forma determinista, (ii) requiere prompt/estrategia del LLM, (iii) fallo de
reconocimiento OCR, (iv) artefacto del evaluador/GT.

| Split / doc | Campo | Causa | Clase |
|---|---|---|---|
| dev acta (x3) | folio_impresion | franja superior sin leer | (iii) -> resuelto con `OCR_ACTA_ZONA_SUPERIOR` |
| dev acta none | identificador_electronico | idem | (iii) -> resuelto |
| dev ine default | domicilio (`TAMALLIPAS` vs `TAMAULIPAS`) | el texto trae dos lecturas; el LLM eligió la ruidosa | (ii) (o (i) con "consenso entre lecturas"; sin evidencia suficiente) |
| dev curp heavy | folio, fecha_inscripcion (`06/01/200`) | no está en el texto | (iii) |
| dev pasaporte heavy | nombres `ISABELA MATI` (+nombre_completo) | el LLM añadió ruido de la MRZ; ambos consistentes con la CURP | (ii) |
| dev pasaporte heavy (000001) | DOCUMENTO_NO_SOPORTADO | texto sin señales | (iii) |
| val ine none/default | seccion `8702` vs `6702` | dos lecturas: `SECCON 8702` (etiqueta corrupta) y `SECCIÓN 6702` (etiqueta exacta) | (i) débil: preferir la lectura con etiqueta intacta; no implementado (1 documento) |
| val ine heavy | nombres `CELA`, curp, clave_elector, domicilio, seccion | no están en el texto | (iii) |
| val ine heavy | sexo, estado `21`, municipio (`0342` es localidad) | valor presente, LLM erró | (ii) |
| val pasaporte heavy | mrz_linea1 | el texto trae lectura casi correcta (`P<MEXALCALA CROBLEDO<<EVELIO<GABINO<<...`) y otra ruidosa; la regla de campos visuales tolera distancia <=2 y aquí es 3 | (i) con umbral relativo; no implementado (1 documento de val) |
| (iv) | ninguno claro; el evaluador trata `HGO` / `HGO.` como equivalentes y no se hallaron artefactos de GT | | |

## 6. Experimentos propuestos con la campaña real
1. `OCR_ACTA_ZONA_SUPERIOR=true` en una campaña completa de actas (12+ documentos, tres presets):
   éxito = folio_impresion >=90% sin que baje ningún campo; medir también con Apple Vision (`ocrmac`),
   no medido aquí. Verificar con actas reales si la franja es la misma.
2. `CLASSIFIER_TOLERANT=true`: contar documentos que pasan de DOCUMENTO_NO_SOPORTADO a clasificados y
   confirmar 0 tipos equivocados (comparar contra tipo del GT). Revisar el log `tipo_documento_detectado_tolerante`.
3. Escalar x2-x3 las imágenes de baja resolución antes de OCR (en el pasaporte heavy, 3x hizo legible el
   encabezado y la MRZ parcialmente; 1x no): medir latencia y exactitud en heavy.
4. `POSTPROCESS_RECONCILE` (+derivados) en baseline100 (validación limpia): solo ahí se puede confirmar o
   refutar el efecto; revisar `campos_reconciliados` por regla.
5. Prompt (propuesta de texto, sin aplicar): indicar al LLM que cuando el texto trae dos lecturas del
   mismo campo prefiera la que sigue a una etiqueta completa y limpia (casos SECCION, domicilio), y que
   `nombres` no incluye palabras de la MRZ.
6. Regla pendiente: MRZ línea 1 con tolerancia relativa (<=10% de la parte de nombres) frente a los
   campos visuales; validar en baseline100.

## 7. Suposiciones no verificadas
* El comportamiento con Apple Vision/Docling reales (la franja y el clasificador se probaron con Tesseract
  y texto guardado).
* Que las actas reales tengan folio/identificador en la franja superior como el generador.
* Que el ruido sintético uniforme se parezca al ruido real de OCR.
* H10 y el diseño de actas reales distintos al generador (H2 residual).
