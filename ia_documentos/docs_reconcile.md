# Reconciliación de campos (`application/reconcile.py`)

Postproceso determinista (Python stdlib) que corrige errores de **asignación/extracción**
(el valor correcto SÍ estaba en el texto crudo del OCR). No mejora el reconocimiento.

## Cómo activarlo
`POSTPROCESS_RECONCILE=true` (variable de entorno; en `config/settings.py`, default **False**).
Con el flag apagado el pipeline y el contrato del modo web son idénticos a los anteriores
(no se añade ninguna clave). Con el flag encendido y al menos un cambio, `metadatos`
incluye `campos_reconciliados`: lista de `{"campo", "regla"}` (sin valores).
API: `reconcile(doc_type, raw_text, datos) -> ReconcileResult(datos, cambios)`; cada
`Cambio` tiene `campo`, `anterior`, `nuevo`, `regla`. No muta la entrada.

**No activar por defecto sin una campaña de regresión completa** (ver Limitaciones).

## Principios
- Todo valor nuevo sale del texto crudo, o se ensambla con campos que ya salen de él.
- El LLM manda: solo se sustituye con basura identificable (etiqueta, entidad federativa
  como apellido, CURP del progenitor en la persona, valor truncado) o ante una
  inconsistencia con una estructura verificable (dígito verificador, fecha/sexo de la CURP).
- Ante la duda no se cambia; candidatos ambiguos (más de uno) = sin cambio.
- Cada regla va en `try/except`: una falla nunca rompe el pipeline.

## Reglas
| Regla | Justificación estructural |
|---|---|
| `curp_recuperada_del_texto` | CURP ausente/inválida/contradictoria con fecha y sexo propios: se toma del texto la ÚNICA CURP con dígito verificador válido (reparando confusiones OCR de mínimo cambio por posición: homoclave dígito si nació antes de 2000, O/0, D/0, I/1...). Las letras del nombre solo desempatan. En actas se excluyen las CURP de la región de filiación. |
| `apellido_es_etiqueta_o_entidad` | Un apellido no es una etiqueta del formulario ni una entidad federativa -> null. |
| `componente_nombre_por_curp_y_texto` | Con CURP válida, ap1/ap2/nombre deben cumplir las letras que aportan a la CURP (posiciones 0,1,13 / 2,14 / 3,15). Si no, se busca la ÚNICA palabra en mayúsculas del texto que sí las cumple (excluyendo palabras de otros componentes correctos). |
| `nombres_resegmentados_por_curp` | Los apellidos/nombres mal repartidos (p. ej. dos apellidos en `primer_apellido`) se reparten de forma única consistente con la CURP. |
| `nombre_completo_ensamblado` | `nombre_completo = nombres + primer + segundo apellido` (convención del esquema/GT) cuando los componentes son consistentes con la CURP (o el valor actual es una permutación/supraconjunto). |
| `clave_elector_consistente_con_curp` | Clave = 6 letras + fecha (=CURP[4:10]) + entidad + sexo (=CURP[10]) + 3 dígitos; se toma el único token del texto que lo cumple tras mapeo por posición. |
| `domicilio_ensamblado` | `domicilio` ausente pero ≥2 de calle/colonia/municipio presentes. |
| `acta_valor_por_etiqueta`, `acta_filiacion_por_etiqueta` | El acta imprime `VALOR Etiqueta`: el valor es el texto en mayúsculas inmediatamente antes de la etiqueta. Persona = etiquetas fuera de la región "Datos de Filiación"; padres = 1ª y 2ª ocurrencia (el orden se corrige con el sexo de las CURP). Se reemplaza solo si el valor actual está vacío, es de otro campo, es etiqueta/entidad o es un prefijo truncado. `nombre_padre/madre` = nombre + apellidos. |
| `acta_curp_progenitor_por_sexo` | Padre = CURP con sexo H, madre = M. CURP con sexo incompatible con el rol -> se reemplaza o se anula. |
| `acta_identificador_20_digitos` | El identificador electrónico es el único número de 20 dígitos del acta. |
| `curp_folio_8_digitos_junto_a_etiqueta` | Folio de la constancia CURP: único número de 8 dígitos a ±(60,80) caracteres de la etiqueta "Folio". |
| `mrz_linea2_reparada_por_digitos_control` | MRZ TD3 (ICAO 9303): línea 2 se repara por posición y solo se acepta si pasan los 5 dígitos de control (campo opcional = relleno `<`). |
| `mrz_linea1_normalizada` | Línea 1 = `P<MEX` + apellidos + `<<` + nombres, rellenada con `<` a 44; los glifos `‹«≤`, espacios y ruido tras el nombre se descartan. Si los campos visuales difieren ≤2 caracteres, mandan (son literales del texto). |
| `sexo_desde_mrz_o_curp` | Pasaporte (convención ICAO M/F): sexo de la MRZ válida o, si no, de la CURP (H->M, M->F). |
| `sexo_desde_curp`, `fecha_nacimiento_desde_curp` | Solo RELLENAN valores ausentes con los que codifica una CURP válida. |

## Resultados (exactitud de campos, línea base del run baseline-2026-2396f44c, evaluador `evaluate_sample`)
Las reglas se derivaron mirando solo `dev`; `val` y `test` no se usaron para diseñar
(salvo lo indicado abajo). Cero regresiones (campo correcto -> incorrecto) en dev y val.

| Split | Tipo | Antes | Después |
|---|---|---|---|
| dev | acta | 58.3% | 93.3% |
| dev | curp | 88.9% | 96.8% |
| dev | ine | 88.9% | 98.9% |
| dev | pasaporte | 84.0% | 97.3% |
| dev | total | 81.2% | 96.9% |
| val | ine | 66.7% | 75.6% |
| val | pasaporte | 91.1% | 97.8% |
| val | total | 78.9% | 86.7% |
| test (agregado, una sola medición, con `fecha_nacimiento_desde_curp` aún con reemplazo) | acta | 55.0% | 67.8% |
| test | curp | 100% | 100% |
| test | ine | 86.7% | 93.3% |
| test | pasaporte | 77.8% | 93.3% |
| test | total | 65.6% | 77.2% |

Campos críticos (aciertos antes -> después): dev curp 17/23->23/23 (acta+curp+ine+pasaporte),
clave_elector 5/6->6/6, mrz_linea1 1/5->5/5, mrz_linea2 3/5->5/5, numero_pasaporte 5/5->5/5;
val mrz_linea2 2/3->3/3, demás sin cambio; test curp_padre 4/9->7/9, curp_madre 3/9->8/9,
curp (acta) 5/9->7/9, mrz_linea1 1/3->3/3, mrz_linea2 1/3->3/3, sin descensos netos.

En test apareció 1 regresión bruta, en `fecha_nacimiento` (no crítico), atribuida (por conteo
de reglas, sin inspeccionar la muestra) a la variante que SUSTITUÍA la fecha por la derivada
de la CURP; esa variante se eliminó (ahora solo rellena) y no se volvió a medir en test.

## Limitaciones
- Conjunto muy pequeño (≈16 grupos; dev contiene un solo acta y val ninguno): las reglas del
  acta están validadas casi solo por su justificación estructural y por el agregado de test.
- Las CURP/diseños del generador sintético pueden ser más regulares que los documentos
  reales (p. ej. orden "VALOR Etiqueta" del acta, CURP de progenitores con dígito válido).
- No corrige errores de reconocimiento (sección, folio_impresion ausente, nombres con
  letras mal leídas, MRZ con letras de nombre corrompidas).
- Sin CURP válida no hay verificación cruzada y la mayoría de reglas de nombre se abstienen.
- La MRZ reconstruida y los valores ensamblados no son literales del texto crudo (se derivan
  de campos que sí lo son); si se aplica el filtro anti-alucinación después, hay que exceptuarlos.

## Antes de activarlo por defecto
Ejecutar la campaña de regresión completa (modelo real y los tres presets de degradación,
incluidos documentos reales anonimizados si existen) con el flag encendido vs. apagado y
revisar `campos_reconciliados` por regla; solo entonces cambiar el default.
