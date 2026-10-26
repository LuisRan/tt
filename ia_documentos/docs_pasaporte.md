# Pasaporte >= 90 %: reglas de reconciliacion y medicion offline

Rama `pasaporte/90` (parte de `docling/tuning`). Todo es OFFLINE sobre texto crudo y predicciones
guardadas de la campana `mejoras-ocr-parcial` (sin OCR, sin LLM, sin Ollama). Datos sinteticos;
este documento solo contiene conteos y nombres de campo.

## Flags (todos apagados por defecto)

| Flag | Default | Efecto |
|---|---|---|
| `POSTPROCESS_RECONCILE` | `False` | (existente) activa la etapa de reconciliacion |
| `RECONCILE_ALLOW_DERIVED` | `False` | (existente) permite reglas que escriben valores NO literales |
| `RECONCILE_PASAPORTE` | `False` | (nuevo) reglas propias de `application/reconcile_pasaporte.py` |

`RECONCILE_PASAPORTE` solo actua si `POSTPROCESS_RECONCILE` esta activo. Las reglas literales se
aplican con `RECONCILE_PASAPORTE`; las derivadas exigen ademas `RECONCILE_ALLOW_DERIVED`.

## Reglas (separadas literal / derivado)

Literal (valor presente en el texto, tal cual o con reparacion OCR minima):
- `sexo_por_etiqueta`: M/F de la fila bajo "Sexo / Sex / Sexe" (voto con la MRZ 2 valida y la CURP).
- `nombres_sin_observaciones`: Docling funde "Nombres" y "Observaciones" en una fila; quita el
  valor de observaciones (NINGUNA) de `nombres` y `nombre_completo`.
- `apellidos_separados`: `primer_apellido` con los dos apellidos de la fila "Apellidos" -> separa.
- `mrz_linea2_literal`: linea 2 exacta (glifos `<` normalizados, espacios fuera) o con UNA edicion
  validada por los 5 digitos de control; si hay varias ediciones validas se elige la que cambia un
  caracter implausible por uno plausible en esa posicion; si sigue habiendo duda no se cambia.
- `mrz_linea1_literal`: linea 1 de 44 caracteres exacta y consistente con los nombres visuales.
- `curp_con_espacios`: CURP partida por espacios, validada por su digito verificador.

Derivado (requiere `RECONCILE_ALLOW_DERIVED`; el valor NO es literal):
- `mrz_linea2_reparada_multi`: varios cambios (campo personal vacio, confusiones de digitos) validados por DV.
- `mrz_linea2_desde_campos`: linea 2 reconstruida de numero/fechas/sexo con DV calculados; exige que
  una ventana parcial del propio texto concuerde (>= 14 de 28 posiciones).
- `mrz_linea1_desde_campos`: linea 1 normalizada (relleno `<` a 44) desde apellidos y nombres, con
  evidencia de una linea 1 ruidosa en el texto.
- (existentes en `reconcile.py`) `mrz_linea1_normalizada`, `mrz_linea2_reparada_por_digitos_control`,
  `sexo_desde_mrz_o_curp`.

## Diagnostico en dev (none + default, sin derivados, antes de las reglas nuevas)

Errores totales: 57 de 465 campos. Taxonomia por campo:

| Campo | Errores | Causa |
|---|---|---|
| `mrz_linea1` | 20 | Relleno `<` final con glifos/espacios/caracteres de mas: valor correcto NO esta literal en el texto (confundido por OCR / longitud distinta). Solo derivable |
| `sexo` | 11 | Valor presente en el texto (fila bajo la etiqueta) pero mal asignado por el LLM (invierte M/F); 2 de ellos ausentes del texto |
| `mrz_linea2` | 10 | 1 caracter de ruido en el campo personal (recuperable literal) y lineas truncadas/muy ruidosas (solo derivable) |
| `nombres` / `nombre_completo` | 4 / 5 | Valor de "Observaciones" fundido en la fila de nombres (separado mal); 1 `nombre_completo` con letra confundida por OCR |
| `primer_apellido` | 1 | Dos apellidos en un campo (mal asignado) |
| `curp` | 2 | CURP partida por espacios (1) y confusion E/Z (1) |
| `fecha_*` | 4 | Documento `default` muy degradado (etiquetas ilegibles, digitos confundidos) |

Artefactos del evaluador: no se encontraron en pasaporte (comparacion de MRZ = mayusculas sin espacios).
Nota: `primer_apellido` es N/A en el GT con >= 3 palabras; no cuenta.

## Resultados (exactitud pooled de campos puntuados; docs fallidos no aportan campos)

Evaluador: `scripts/eval_pasaporte_offline.py` (usa el evaluador del puente, solo lectura).
Modos: base = predicciones guardadas (reconcile apagado); sin = reconcile existente sin derivados;
sin+pas = existente + reglas propias literales; con = existente con derivados; con+pas = todo.

### SIN derivados (lo literal)

| Split / preset | base | sin (existente) | sin+pas (nuevo) |
|---|---|---|---|
| dev none | 87.5 | 87.9 | 93.3 |
| dev default | 84.4 | 87.6 | 92.0 |
| dev none+default | 86.0 | 87.7 | **92.7** |
| dev heavy | 72.5 | 74.2 | 75.8 |
| val none | 90.7 | 91.5 | 94.1 |
| val default | 88.1 | 90.7 | 93.2 |
| val none+default | 89.4 | 91.1 | **93.6** |
| val heavy | 83.1 | 84.7 | 88.1 |
| test none | 92.6 | 94.0 | 95.3 |
| test default | 89.3 | 91.3 | 91.9 |
| test none+default | 90.9 | 92.6 | **93.6** |
| test heavy | 71.9 | 75.3 | 76.4 |

### CON derivados (`RECONCILE_ALLOW_DERIVED=1`)

| Split / preset | con (existente) | con+pas (nuevo) |
|---|---|---|
| dev none | 95.8 | 100.0 |
| dev default | 94.7 | 95.6 |
| dev none+default | 95.3 | **97.8** |
| dev heavy | 80.8 | 81.7 |
| val none+default | 97.0 | **98.3** |
| val heavy | 89.8 | 89.8 |
| test none | 99.3 | 100.0 |
| test default | 94.6 | 96.0 |
| test none+default | 97.0 | **98.0** |
| test heavy | 83.1 | 84.3 |

Cero regresiones por campo en dev+val+test (base -> sin+pas, sin -> sin+pas, con -> con+pas).
`test` se midio una sola vez, con las reglas congeladas (solo como agregado).

### Brecha restante

- Sin derivados, none y default quedan >= 90 % en los tres splits (92.7 / 93.6 / 93.6 none+default).
  En `test default` solo: 91.9 %.
- Lo que sigue fallando sin derivados es sobre todo `mrz_linea1` (~35 % de aciertos): el texto trae
  la linea 1 con relleno ruidoso y la forma correcta de 44 caracteres NO esta en el texto; solo se
  obtiene normalizando/reconstruyendo (derivado). Tambien lineas 2 truncadas o con varios errores.
- heavy: 16 de 34 documentos fallidos por texto ilegible no aportan campos; no es el objetivo.

## No medido

El efecto con el LLM real: las predicciones guardadas son la salida del LLM de la campana; si el
LLM cambia, los datos de entrada de las reglas cambian. Tampoco se probo el pipeline completo
(solo `DocumentPipeline._reconciliar` con el texto y los datos guardados, que incluye el filtro
anti-alucinacion de reconcile).

## Reproduccion

```bash
cd ia_documentos
PYTHONPATH=/Users/luis/Documents/gen-ia python scripts/eval_pasaporte_offline.py \
    --campana /Users/luis/Documents/gen-ia/out/campaigns/mejoras-ocr-parcial \
    --splits dev --modos base,sin,sin+pas,con,con+pas --por-campo
python -m pytest -q tests/unit/test_reconcile_pasaporte.py
```
Uso real: `POSTPROCESS_RECONCILE=1 RECONCILE_PASAPORTE=1` (literal) y ademas
`RECONCILE_ALLOW_DERIVED=1` (derivado, decision del usuario).
