# Corrección de regresiones de `application/reconcile.py`

Campaña limpia baseline100-4242 (408 muestras, semilla 4242). Mejora real de la reconciliación,
pero con 6 regresiones del mismo tipo (composición/asignación de nombres y CURP de progenitores).

## Causa raíz y corrección

| Patrón | Regla | Causa | Corrección |
|---|---|---|---|
| nombres 'GILBERTO' -> 'GILBERTO COLON'; nombre_completo con apellido duplicado | `acta_valor_por_etiqueta` + `nombre_completo_ensamblado` | el valor previo a la etiqueta 'Nombre(s)' incluía el apellido de la celda contigua y `_decidir_reemplazo` acepta ampliaciones por prefijo | `_persona_protegida`: no se reemplaza un componente que la CURP válida confirma, ni se amplía con palabras que ya son el valor de otro componente. Al ensamblar, si el ensamblado repetiría un apellido consecutivo que el valor actual no repite, se conserva el actual |
| 'CURP ANEL ALMANZA VILLALPANDO' | `acta_filiacion_por_etiqueta` vía `_valor_antes` | la etiqueta 'CURP' (mayúsculas) de la columna anterior se tomaba como parte del valor | `_valor_antes` se detiene en cualquier palabra de `_PALABRAS_ETIQUETA` |
| primer_apellido CASAREZ -> IGLESIAS | `acta_valor_por_etiqueta` | 'Nombre(s) IGLESIAS / Primer Apellido': el valor previo era el segundo apellido | misma protección por CURP (letras 'CA' confirman CASAREZ) |
| curp_madre sustituida por la de la persona | `acta_curp_progenitor_por_sexo` | la CURP de la persona caía en la región de filiación (las de los padres quedaban tras 'Anotaciones') y era la única 'M'; además la condición `_n(cur)!=_n(curp_rol)` reemplazaba siempre | se excluye de los candidatos la CURP propia (solo si es consistente con SU fecha y sexo, pues a veces la 'propia' extraída es la de un progenitor); una CURP de progenitor presente, válida, con el sexo del rol, que figura en el texto, distinta de la propia y con fecha distinta a la de la persona NO se sustituye |
| pasaporte nombre_completo con apellido duplicado | `nombre_completo_ensamblado` | apellidos mal segmentados repetían un token | guarda de duplicado consecutivo (arriba) |

Pruebas: `tests/unit/test_reconcile_regresiones.py` (8 casos sintéticos; 6 fallan con el código anterior).

## Medición offline (exactitud de campos, % antes de reconciliar -> reconciliado; regresiones en paréntesis, antes -> después)

Ver el informe de entrega; las regresiones pasan a 0 en dev, val y test en los modos sin derivados y con derivados.
Sin ganancias perdidas: acta dev 63.9 -> 64.5 (con derivados), val 60.8 -> 60.8, test 68.9 -> 69.6.
