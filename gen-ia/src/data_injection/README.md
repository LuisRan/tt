# `src/data_injection` — T2: datos sintéticos y Ground Truth

Genera valores **100 % sintéticos** (Faker `es_MX` + RNG sembrado) para cada campo
del catálogo (`contracts/field_catalog.py`) y construye el Ground Truth
(`contracts.GroundTruth`) a partir de un `LayoutSpec`. No usa datos de personas
reales ni nada de `formatos/`. No depende de `src/layouts`.

## Uso

```python
from contracts import DocType, LayoutSpec, Variant
from src.data_injection import generate_record, build_ground_truth, inject, validate_record

layout = LayoutSpec.model_validate_json(open("contracts/fixtures/layout_ine_2016.json").read())

# 1) solo valores: dict[Face, dict[campo, valor]]
rec = generate_record(DocType.INE_2016, seed=42, index=0, variant=Variant.EXTRANJERO)
assert validate_record(DocType.INE_2016, rec, Variant.EXTRANJERO) == []

# 2) Ground Truth a partir de layout + registro
gt = build_ground_truth(layout, rec, variant=Variant.EXTRANJERO, seed=42, index=0)
print(gt.sample_id)            # ine_2016-extranjero-42-000000
open("gt.json", "w").write(gt.model_dump_json(indent=2))

# 3) atajo
rec, gt = inject(layout, seed=42, index=0)   # variant=None -> default (INE: nacional)
```

### API pública

| Función | Descripción |
|---|---|
| `generate_record(doc_type, seed, index=0, variant=None) -> dict[Face, dict[str, value]]` | Todos los campos `in_gt=True` de la variante, excepto STATIC. |
| `build_ground_truth(layout, record, *, variant, seed, index, generator_version="0.1.0") -> GroundTruth` | Una `PageGT` por cara (orden del layout), un `FieldGT` por campo del layout con `in_ground_truth=True` visible en la variante. STATIC/SHAPE toman `static_value`. `transformed=None`. Falta un valor -> `MissingValueError`; tipo erróneo -> `TypeError`. |
| `inject(layout, seed, index=0, variant=None) -> (record, GroundTruth)` | Atajo. |
| `validate_record(doc_type, record, variant=None) -> list[str]` | Formato (regex del catálogo) + coherencia cruzada. |
| `validators.*` | CURP, clave de elector, MRZ TD1/TD3, fechas (ver abajo). |

Tipos de valor: TEXT `str`; MULTILINE/MRZ `list[str]` (una por línea; puede ser
`[]`, p.ej. `curps_asociadas` o `anotaciones_marginales`); QR/BARCODE `str`
(payload); PHOTO `"avatar:<16 hex>"`; SIGNATURE `"firma:<16 hex>"`.

### Validadores (`validators.py`)

- **CURP**: `validate_curp`, `is_valid_curp`, `curp_check_digit` (diccionario
  RENAPO con Ñ, pesos 18..2, `(10 - suma % 10) % 10`), `curp_name_letters`,
  `curp_consonants`, `build_curp`, `validate_curp_person` (nombre/fecha/sexo/
  entidad/homoclave por siglo), `curp_matches_full_name`.
  Reglas: 1a vocal interna del 1er apellido; consonantes internas; palabras
  inconvenientes (Anexo 2) -> 2a letra X; partículas (DE, DEL, LA, LOS, MC, VAN, Y…)
  ignoradas; nombre compuesto que empieza por JOSÉ/MARÍA (J, MA, M) usa el siguiente;
  Ñ -> X; sin 2o apellido -> X; posición 17 dígito (<2000) o letra (2000+).
- **Clave de elector**: `build_clave_elector`, `validate_clave_elector`, `clave_elector_letters`.
- **MRZ ICAO 9303**: `mrz_check_digit` (7-3-1), `icao_transliterate`,
  `mrz_name_field` (truncado ICAO), `build_td1_ine`/`validate_td1`,
  `build_td3_passport`/`validate_td3`.
- **Fechas**: `parse_date(s, "dmy"|"spaced")`, `format_date`, `is_valid_date`, `age_on`, `add_years`.

## Reproducibilidad

- Semilla por muestra: `random.Random(f"{seed}:{doc_type}:{variant}:{index}")`
  (Python siembra con SHA-512 del texto; no usa `hash()`, por lo que no depende
  de `PYTHONHASHSEED`). De ese RNG sale el seed de **una instancia propia** de
  Faker (`seed_instance`, nunca la global).
- Sin reloj: la fecha "hoy" es la constante `REFERENCE_DATE = 2026-09-30`.
- Mismo `(doc_type, variant, seed, index)` ⇒ mismo registro y mismo JSON byte a
  byte (probado entre subprocesos con distinto `PYTHONHASHSEED`).
  Cambiar la versión de Faker puede cambiar los valores (no la validez).

## Decisiones

1. **Acentos**: todos los nombres, apellidos, domicilios y entidades van en
   **MAYÚSCULAS SIN ACENTOS, conservando la Ñ** (como la credencial INE y
   RENAPO), en los 4 documentos. Excepciones con texto de oración:
   `lugar_fecha` ("Ciudad de México, a 5 de marzo de 2021") y `estatus_curp`.
   En la MRZ se aplica transliteración ICAO (Ñ -> N).
2. **Clave de elector** (regla real no publicada con precisión): por cada uno de
   1er apellido, 2o apellido y nombre -> inicial + primera consonante interna
   (QUINTERO LEDESMA ROSALBA -> `QNLDRS`), + AAMMDD + entidad de **nacimiento**
   (01-32) + sexo H/M + 3 dígitos.
3. **INE**: `estado` = entidad del domicilio (60 % igual a la de nacimiento);
   en `extranjero` = entidad de nacimiento. `ocr` = `seccion` + 9 dígitos. MRZ TD1:
   `IDMEX`+CIC+dígito+`<<`+OCR / nac.+dígito+sexo(M/F)+`AA1231` de la vigencia+dígito+`MEX`+11 `<`+compuesto
   / `AP1<AP2<<NOMBRES`. `vigencia = emision + 10`; edad ≥ 18 en la emisión;
   emisión entre 2016-02 y 2026-09. Domicilio nacional: `CALLE NUM` / `COL. COLONIA CP` /
   `MUNICIPIO, ABREV.`; extranjero: `NUM STREET SUF [APT n]` / `CIUDAD, ESTADO CP` /
   `PAÍS` (EUA mayoritario, también Canadá/España). Líneas ≤ 38 caracteres.
4. **foto_secundaria = foto** (mismo id de avatar: la "foto fantasma" es la misma persona).
5. **Acta**: progenitor 1 = padre (H), progenitor 2 = madre (M); el registrado
   lleva 1er apellido del padre y 1er apellido de la madre. Progenitores 16–55/45
   años mayores, nacidos ≥ 1900. 5 % nacionalidad extranjera -> CURP con entidad `NE`.
   `identificador_electronico` = entidad(2)+municipio(3)+oficialía(4)+año registro(4)+acta(5)+2 aleatorios.
   `certificacion`, `anotaciones_marginales` = textos genéricos con datos sintéticos
   (incluye "DOCUMENTO SINTETICO DE PRUEBA"). `firma_electronica` = 256 bytes aleatorios en base64 (4 líneas).
6. **Constancia CURP**: `curps_asociadas` (0–2) = CURPs anteriores de la misma
   persona (mismos 16 primeros caracteres, otra homoclave, dígito válido).
7. **Pasaporte**: sexo `F/M`; vigencia 3/6/10 años (3 si es menor); número `G|N`+8 dígitos;
   el dato opcional de la MRZ va en `<` (la CURP de 18 caracteres no cabe en los
   14 del campo de número personal) y su dígito es `0`.
8. **`codigo_2d` (Acta, bloque I)**: payload largo (380–520 caracteres, determinista por seed):
   `MOCK DATA|ACTA2D|identificador|CURP|ap1|ap2|nombres|sexo|fecha nac.|entidad nac.|entidad registro|municipio|oficialía|libro|acta|fecha registro|CURP padre|CURP madre|<base64 aleatorio sembrado>`.
   El núcleo lo arma `validators.acta_2d_core` (fuente única) y `validate_record` lo
   verifica campo a campo (`check_codigo_2d`: prefijo, longitud 300–600, base64, distinto del `qr`).
   Se genera al final del registro para no alterar el RNG de los demás campos. El `qr`
   pequeño (bloque M) sigue siendo `MOCK DATA|ACTA|ident|curp|verif`.
9. **MRZ INE línea 1**: `IDMEX` + `cic`(9, pos. 5-13) + dígito + `<<` + `ocr`(13, pos. 17-29);
   `validate_record` exige que `cic`/`ocr` coincidan con esos sub-rangos.
10. **Payloads**: todos los QR incluyen `MOCK DATA`; el código de barras INE empieza con `MOCK`.

## Tests

```
.venv/bin/python -m pytest src/data_injection -q
# contra layouts reales (directorio con <doc_type>.json):
DATA_INJECTION_LAYOUT_DIR=src/layouts/specs .venv/bin/python -m pytest src/data_injection -q
```
