# Acta de nacimiento: reglas por filas sobre el texto de `filas_acta` (RECONCILE_ACTA)

Objetivo: colchon sobre el 90 % de exactitud de campos del `acta_nacimiento_2015` en calidad `none` y `default`
(`heavy` se reporta aparte: texto ilegible, no es objetivo). Todo OFFLINE: texto crudo y predicciones guardados
de la campana `filas-acta-completa` (102 actas con `DOCLING_TEXT_MODE=filas_acta`), puntuados con el evaluador
del puente. No se ejecuto OCR, LLM, Ollama ni Docling.

## Diagnostico (solo dev, none+default, reconcile existente)

En el texto por filas las cabeceras van en una linea y los valores en la de al lado, en el mismo orden; el LLM
los empareja mal. Causas de los 61 fallos de dev (reconcile existente), por frecuencia:

| causa | campos | ejemplo de mecanismo |
|---|---|---|
| presente, mal asignado / separado | numero_acta, oficialia, libro, fecha_registro | valores de la fila de registro desplazados; libro = numero |
| presente, truncado o con ruido pegado | nombre_padre, nombre_madre | falta el segundo apellido; nacionalidad pegada al nombre |
| confundido por OCR (recuperable) | curp_padre, curp_madre | digito cambiado (6/8, 8/9), `0` por `O`, `.` suelto |
| diacritico de OCR | nombres, entidad, municipio | `AGUSTIN` con acento, `MEXICO` con cedilla |
| ausente / ilegible en el texto | fecha_registro, curp_*, lugar_nacimiento, nombre_padre | el OCR no leyo el valor: no recuperable sin inventar |
| artefacto del evaluador | municipio `MONTAÑA` leido `MONTANA` | la eñe se perdio en el OCR |

Las reglas existentes de `reconcile.py` (`acta_*_por_etiqueta`) fueron disenadas para texto markdown con el valor
ANTES de la etiqueta; sobre el texto por filas siguen aplicando (dev none+default 93.2 -> 93.9 %) pero no
resuelven la fila de registro ni el nombre completo de los progenitores.

## Reglas (`application/reconcile_acta.py`, flag `RECONCILE_ACTA`, default apagado)

Se ejecutan al final de `reconcile()` (enganche de 4 lineas), por lo que requieren `POSTPROCESS_RECONCILE=true`.
Cada regla es independiente, segura (si falla se registra y se ignora) y exige estructura inequivoca.

| regla | campos | literal o derivada |
|---|---|---|
| `acta_fila_registro` | oficialia, fecha_registro, libro, numero_acta | literal (fecha con digitos O/D/B/I... reparados) |
| `acta_filiacion_filas` | nombre_padre, nombre_madre, curp_padre, curp_madre, nacionalidad_* (solo si vacia) | literal (CURP con la reparacion letra/digito existente) |
| `acta_persona_fila` | segundo_apellido | literal |
| `acta_diacriticos` | nombres, apellidos, nombre_*, entidad, municipio, lugar | literal (quita diacriticos, conserva la eñe) |
| `acta_curp_progenitor_dv` | curp_padre, curp_madre | DERIVADA (`RECONCILE_ALLOW_DERIVED`) |

* Fila de registro: region entre "Municipio de Registro" y la primera etiqueta "Nombre(s)"; una unica fecha
  valida (>= año de nacimiento) ancla la fila: el numero previo es la oficialia y los dos siguientes libro y
  numero de acta. Si solo hay un numero tras la fecha, y tiene >= 4 digitos, es el numero de acta (el libro no
  se inventa).
* Filiacion: se descartan las etiquetas (cualquier palabra con minusculas o parecida a una etiqueta) y el flujo
  de palabras se corta en cada CURP; la palabra previa es la nacionalidad (tolerando ruido: `MEXIGANA`). Padre y
  madre salen del orden del texto, confirmado por el sexo de la CURP. Si alguna persona queda dudosa (palabra
  repetida seguida, <2 o >8 palabras) no se toca nada.
* CURP derivada: CURP valida (fecha real, estado valido, digito verificador, sexo del rol) con el minimo de
  confusiones OCR (letra/digito, digito/digito 6-8-9-5..., I/L); desempata con las letras de la CURP que dependen
  del nombre; si hay mas de una solucion al minimo, no cambia. Se reporta aparte por ir con `ALLOW_DERIVED`.

## Resultados (exactitud de campos, reconcile aplicado offline sobre las predicciones guardadas)

`base` = predicciones guardadas; `existente` = reconcile actual; `+acta` = existente + estas reglas;
`+acta+der` = ademas `RECONCILE_ALLOW_DERIVED`.

| split / preset | base | existente | +acta | +acta+der |
|---|---|---|---|---|
| dev none | 94.2 | 95.0 | 98.6 | 99.0 |
| dev default | 92.2 | 92.8 | 98.8 | 99.0 |
| **dev none+default** | 93.2 | 93.9 | **98.7** | **99.0** |
| dev heavy (aparte) | 51.0 | 52.4 | 62.2 | 62.6 |
| val none | 95.0 | 97.5 | 98.8 | 98.8 |
| val default | 90.0 | 91.2 | 100.0 | 100.0 |
| **val none+default** | 92.5 | 94.4 | **99.4** | **99.4** |
| val heavy (aparte) | 52.5 | 52.5 | 58.8 | 58.8 |
| test none | 92.0 | 94.0 | 98.0 | 98.0 |
| test default | 88.0 | 89.0 | 95.0 | 95.0 |
| **test none+default** | 90.0 | 91.5 | **96.5** | **96.5** |
| test heavy (aparte) | 70.0 | 71.2 | 77.5 | 77.5 |

Disciplina: reglas elegidas solo con dev, confirmadas en val; test se corrio UNA vez, con reglas congeladas.
Regresiones por campo (acierto antes, fallo despues) en none y default: 0 en dev, val y test, contra base y
contra el reconcile existente. En `heavy` (no objetivo) hay 1 regresion en test (nombre_padre, texto con la
fila de filiacion mezclada) y ninguna en dev ni val.

Campos < 90 % en none+default tras las reglas: ninguno en dev, val ni test (tamano de test: 10 actas, 200
campos; un campo = 0.5 puntos).

Fallos residuales en dev (none+default): CURP de progenitor con dos errores OCR y varias soluciones validas
(ambigua, no se cambia), `TUERINA` por `TIJERINA` (letra mal leida en un apellido), `MONTANA` por `MONTAÑA`,
libro ausente del texto, lugar de nacimiento ausente del texto, fecha de nacimiento con mes 13 (OCR).

## Que no se pudo medir

El efecto con el LLM real: las reglas parten de las predicciones guardadas y sobrescriben con lo que dice el
texto por filas; con otra salida del LLM el resultado de estos campos es el mismo mientras el texto sea el mismo.
Solo se midio sobre `filas-acta-completa` (texto de `filas_acta`); con otro modo de texto (markdown) la fila de
registro y la de filiacion no tienen la misma forma y las reglas no actuan (ante la duda no cambian).

## Reproduccion

```
cd ia_documentos
PYTHONPATH=/Users/luis/Documents/gen-ia python scripts/eval_acta_offline.py \
    --campana /Users/luis/Documents/gen-ia/out/campaigns/filas-acta-completa \
    --splits dev --modos base,sin,con,sin+acta,con+acta --por-campo
python -m pytest -q tests/unit/test_reconcile_acta.py
```

Para usarlo en el pipeline: `POSTPROCESS_RECONCILE=true RECONCILE_ACTA=true` (y `RECONCILE_ALLOW_DERIVED=true`
solo para la regla derivada de CURP). Todos apagados por defecto.
