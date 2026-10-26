# Reglas adicionales de INE (`application/reconcile_ine.py`)

Objetivo: exactitud de campos de la INE (`ine_2016`) >= 90 % en documentos `none` y `default`.
Todo el trabajo es OFFLINE (texto crudo y datos del pipeline ya guardados; sin LLM, Docling ni Ollama).

## Flags (todos apagados por defecto)
| Flag | Default | Efecto |
|---|---|---|
| `POSTPROCESS_RECONCILE` | false | Prerrequisito: habilita `reconcile()` (existente). |
| `RECONCILE_INE` | false | Habilita las reglas de este módulo (solo dentro de `reconcile()` y para `tipo == "ine"`). |
| `RECONCILE_ALLOW_DERIVED` | false | Habilita ademas `ine_enie_por_curp` (valor NO literal); se reporta aparte (`derivado: true`). |

Enganche en `reconcile.py`: 3 lineas al final del bloque `tipo == "ine"`. Con el flag apagado no cambia nada.

## Reglas
| Regla | Campos | Derivada | Idea |
|---|---|---|---|
| `ine_nombre_por_ventana_curp` | nombres, primer/segundo apellido, nombre_completo | no (palabras literales) | Las letras 0,1,13 / 2,14 / 3,15 de la CURP validan una particion de palabras (ap1, ap2, nombres). Se buscan ventanas CONSECUTIVAS en el flujo de palabras en mayusculas (descartando etiquetas, fechas, numeros). Se toleran hasta 2 de 7 letras que no cuadren (CURP sintetica / homoclave) pero la solucion debe ser unica; el empate de longitud se resuelve con el valor actual y luego la ventana mas corta. Ancla: linea limpia de solo mayusculas justo antes de una linea `DOMICILIO` (segunda zona del OCR), valida si tiene <=3 palabras y cumple las letras 3 y 15. No sustituye un valor actual igual de consistente salvo que haya ancla. |
| `ine_domicilio_por_bloque` | domicilio | no (lineas literales; la N->Ñ se toma de la misma palabra con Ñ en otra zona) | La colonia (`COL. xxx 12345`, CP de 5 digitos) es el ancla; la calle es la linea en mayusculas con numero y sin etiquetas/claves, el municipio la que termina en `, ABREV.`. Cubre las 3 lineas limpias, la linea unica de la zona ruidosa y las lineas revueltas con etiquetas en medio. Respeta el valor actual si difiere en <=2 caracteres alfanumericos (ruido de lectura, no de asignacion). |
| `ine_clave_por_etiqueta` | clave_elector | no (reparacion OCR minima por posicion) | El OCR repite la clave con dos lecturas validas y la regla existente se abstiene; el token pegado a `CLAVE DE ELECTOR` desempata. Exige estructura (6 letras + AAMMDD = CURP[4:10] + entidad + sexo = CURP[10] + 3 digitos). |
| `ine_enie_por_curp` | primer/segundo apellido, nombres, nombre_completo | **si** (cambia un caracter) | La CURP codifica la Ñ interna como `X`: si la 1a consonante interna es `N` y la CURP trae `X` en 13/14/15, la Ñ se perdio en el OCR. |

Todas: `try/except` por regla, sin PII en logs, valores nuevos solo con palabras presentes en el texto crudo.

## Resultados (exactitud de campos INE, % EXACT+NORMALIZED sobre campos puntuados)
Evaluador del puente (`evaluate_sample`), campaña `mejoras-ocr-parcial`, 34 documentos base x 3 presets. Reglas elegidas mirando solo `dev`, confirmadas en `val`, `test` medido UNA vez con las reglas ya congeladas (solo agregado por campo).
Modos: `base` = guardado (reconcile apagado); `rec` = `POSTPROCESS_RECONCILE`; `ine` = rec + `RECONCILE_INE`; `ine+der` = ademas `RECONCILE_ALLOW_DERIVED`.

| Split | Preset | base | rec | ine | ine+der |
|---|---|---|---|---|---|
| dev (16) | none | 86.7 | 93.8 | 98.8 | 99.6 |
| dev | default | 83.8 | 89.2 | 94.2 | 95.8 |
| dev | heavy | 56.1 | 61.7 | 63.3 | 63.3 |
| val (7) | none | 85.7 | 92.4 | 97.1 | 97.1 |
| val | default | 78.1 | 88.6 | 95.2 | 95.2 |
| val | heavy | 32.2 | 35.6 | 35.6 | 35.6 |
| test (11) | none | 84.2 | 92.7 | 98.2 | 98.2 |
| test | default | 83.0 | 92.1 | 92.7 | 92.7 |
| test | heavy | 58.5 | 62.2 | 66.7 | 66.7 |
| todos (34) | none | 85.7 | 93.1 | 98.2 | 98.6 |
| todos | default | 82.4 | 90.0 | 93.9 | 94.7 |
| todos | heavy | 51.6 | 56.0 | 58.3 | 58.3 |

Regresiones (campo correcto -> incorrecto) respecto de `base` y de `rec`: 0 en dev, val, test y todos, en `ine` e `ine+der`.
Ganancias (campos) todos los splits: base->rec 95; rec->ine 55; rec->ine+der 61.
Nota: `rec` ya aportaba +7/+8 puntos sobre lo guardado; las reglas nuevas aportan +5/+4 sobre `rec`. Con `RECONCILE_ALLOW_DERIVED` el unico efecto es `ine_enie_por_curp` (+6 campos en todos).

## Taxonomia de los errores restantes (none+default, dev+val; 109 errores en base -> 19)
| Causa | Campos | Cuenta |
|---|---|---|
| Confusion de OCR presente en el texto (G->Q, N por Ñ sin pista en la CURP, L->E en la abreviatura del estado, 6->9, 0->D en CURP) | nombre_completo/apellidos, domicilio, curp, seccion, municipio | ~11 |
| Valor presente en el texto pero la regla se abstiene (candidatos ambiguos en dos zonas del OCR, p. ej. fecha/clave con dos lecturas validas) | fecha_nacimiento, clave_elector, seccion | ~5 |
| Ausente del texto (campo no leido por el OCR) | clave_elector, curp, domicilio | ~3 |
| Artefacto del evaluador | ninguno hallado (la comparacion de domicilios ignora `, . ;` pero distingue acentos/Ñ) | 0 |
Antes (base), la causa dominante era asignacion/segmentacion: nombre_completo (casi 0 % por el orden de la convencion del GT), segundo apellido ausente, domicilio con puntuacion/orden distinto.

## Que NO se pudo medir
- El efecto con el LLM real (los datos de entrada son las predicciones guardadas de una corrida anterior). El pipeline real aplica `reconcile` ANTES de `validar_campos`; el evaluador offline lo aplica sobre la salida ya validada (diferencia esperada pequeña).
- Documentos reales (solo sinteticos). Heavy se reporta aparte: el texto es ilegible (mejora 51.6 -> 58.3, no es el objetivo).

## Gap restante y propuesta
Con las cifras de arriba none y default superan 90 % en todos los splits. Lo que queda (≈4-6 % en default) es principalmente confusion de lectura del OCR en el propio texto. Propuestas que NO se pudieron probar sin Ollama:
1. Prompt del LLM: pedir `segundo_apellido`/`nombres` separados por linea (ap1, ap2, nombres) y devolver `null` en lugar de unir apellidos; indicar que el nombre esta en las 3 lineas antes de `DOMICILIO`.
2. Segunda lectura del recorte de fecha/seccion (OCR dirigido a esa zona) para desempatar las dos lecturas validas.
3. Validar abreviatura de estado contra el codigo `estado` (p. ej. NL <-> 19) -> regla derivada candidata.

## Reproducir
```
cd ia_documentos
PYTHONPATH=/Users/luis/Documents/gen-ia .venv/bin/python scripts/eval_ine_offline.py \
    --split dev --modes base,rec,rec_der,ine,ine_der --detalle     # --split val | test | (todos)
PYTHONPATH=... python scripts/diag_ine.py ine_der dev,val [-v]      # causas por campo (formas enmascaradas)
PYTHONPATH=... python scripts/diag_reg.py rec ine dev               # regresiones (sin valores)
python -m pytest -q tests/unit/test_reconcile_ine.py
```
Para activarlo en el pipeline: `POSTPROCESS_RECONCILE=true RECONCILE_INE=true` (y `RECONCILE_ALLOW_DERIVED=true` si se acepta la regla derivada). No activar por defecto sin campaña de regresion con LLM real.
