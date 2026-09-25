# Bases de datos

## PostgreSQL 16 (datos relacionales)

Esquema completo en [`backend/src/db/schema.sql`](../backend/src/db/schema.sql). Es **idempotente**
(`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) y se aplica al arrancar el
backend (`src/db/migrar.ts`), por lo que actualizar el código no requiere borrar la base.
Al arrancar también se crea el administrador inicial (`ADMIN_EMAIL` / `ADMIN_PASSWORD`) si no existe.

| Tabla | Propósito | Columnas clave |
|---|---|---|
| `usuarios` | Cuenta y estado. | `correo` único, `password_hash` (bcrypt), `rol`, `activo`, `correo_verificado`, `registro_completo`, `dosfa_activo`, `tutorial_visto`, `alertas_vigencia`, `token_version` (revocación). |
| `perfil_usuario` | Datos personales (1:1 con usuario). | `curp` y `direccion` **cifrados**, `curp_hash` (HMAC para búsquedas), `verificado`, `editado_manual`. |
| `tipos_documento` | Catálogo: INE, CURP, acta de nacimiento, pasaporte. | `clave`, `tiene_vigencia`. |
| `documentos` | Cada archivo cargado. | `estado` (`cargado` → `procesando` → `procesado` / `rechazado` / `error` → `validado`), `version`, `vigente` (RN-07), `fecha_vigencia` (RN-11), `advertencias`, `mongo_resultado_id`, `editado_en`, `folio` (orden de confirmación por usuario), `titular_iniciales`, `titular_hash`. |
| `documento_versiones` | Historial de versiones por tipo (RN-07). | `version`, `ruta_archivo`. |
| `validaciones_dato` | Valor detectado vs. confirmado por campo (RN-08, RN-09). | `valor_detectado`, `valor_confirmado` (cifrados), `corregido`, `origen` (`validacion` / `edicion`). |
| `metricas_ia` | Una fila por procesamiento. | `tiempo_procesamiento`, `tiempo_total`, `modelo_usado`, `confianza`, `alucinaciones`, `exito`, `error`. |
| `codigos_2fa` | Códigos de verificación. | `codigo` = HMAC-SHA256 (nunca en claro), `proposito`, `expiracion`, `intentos`, `usado`. |
| `accesos` | Inicios de sesión, intentos, registros. | `tipo`, `exito`, `ip`, `user_agent`. |
| `eventos_sistema` | Auditoría de operaciones. | `tipo_evento`, `tabla_afectada`, `operacion`, `descripcion`. |
| `respaldos` | Bitácora del servicio de respaldo. | `tipo`, `ubicacion`, `tamano`, `estado`. |
| `alertas_vigencia` | Alertas enviadas (evita duplicados). | `UNIQUE (documento_id, dias_antes)`. |
| `trafico_api` | Peticiones agregadas por hora. | `metodo`, `ruta` normalizada, `estado` (`2xx`/`4xx`/`5xx`), `total`. |
| `extension_codigos` | Códigos de vinculación de la extensión (un solo uso, 15 min). | `codigo_hash` (HMAC), `expira_en`, `usado`. |
| `extension_dispositivos` | Extensiones vinculadas. | `token_hash` (HMAC), `token_version` (copia del de la cuenta), `ultimo_uso`, `revocado_en`. |
| `plantillas_autofill` | Mapeo campo web → campo de BD para autocompletado externo. | `campo_web`, `campo_bd`. |

### Migraciones incluidas
Al final de `schema.sql`:

```sql
ALTER TABLE usuarios          ADD COLUMN IF NOT EXISTS dosfa_activo   BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE usuarios          ADD COLUMN IF NOT EXISTS tutorial_visto BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE validaciones_dato ADD COLUMN IF NOT EXISTS origen         VARCHAR(12) NOT NULL DEFAULT 'validacion';
ALTER TABLE perfil_usuario    ADD COLUMN IF NOT EXISTS editado_manual BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE documentos        ADD COLUMN IF NOT EXISTS editado_en     TIMESTAMPTZ;
ALTER TABLE documentos        ADD COLUMN IF NOT EXISTS folio          INTEGER;      -- + índice único (usuario_id, folio)
ALTER TABLE documentos        ADD COLUMN IF NOT EXISTS titular_iniciales VARCHAR(4);
ALTER TABLE documentos        ADD COLUMN IF NOT EXISTS titular_hash   VARCHAR(64);
ALTER TABLE documento_versiones ADD COLUMN IF NOT EXISTS titular_hash VARCHAR(64);
-- + tablas extension_codigos y extension_dispositivos
```

Consecuencias al actualizar una instalación existente:
- Todos los usuarios quedan **sin 2FA** (pueden activarla en Configuración) y los usuarios
  existentes verán la guía de uso una vez.
- Los documentos ya validados reciben folio por fecha de carga; al arrancar, el backend
  (`completarIdentificadores`) calcula sus iniciales y titular desde MongoDB y recalcula la versión
  vigente por titular.

### Identificador visible y versión vigente

- **Identificador** = `folio` con 4 dígitos + `_` + iniciales del titular (primer nombre, primer y
  segundo apellido; se omiten partículas como DE, LA, DEL; sin acentos). Ej.: `0001_CSC` para Carlos
  Saavedra Cinta. El folio se asigna al **confirmar** el documento y no se reutiliza.
- **Versión vigente (RN-07)** por usuario + tipo + **titular** (`titular_hash` = HMAC de la CURP o,
  si no hay, del nombre). Validar la INE de otra persona no deja la INE propia como "versión anterior".

### Métrica de precisión de la IA

```sql
SELECT ROUND(1 - AVG(CASE WHEN corregido THEN 1 ELSE 0 END)::numeric, 3) AS precision_campos
  FROM validaciones_dato
 WHERE origen = 'validacion';
```

Solo cuenta la primera confirmación del usuario. Las ediciones posteriores se guardan con
`origen = 'edicion'` para trazabilidad, pero no alteran la métrica.

## MongoDB 7 (resultados OCR)

Base `MONGO_DB` (por defecto `tt_ocr`), colección `resultados_ocr`, un documento por archivo:

```jsonc
{
  "documento_id": 12,                 // índice único, enlaza con documentos.id
  "usuario_id": 3,
  "tipo_documento": "INE",
  "datos_extraidos":   { "curp": "enc:v1:..." },       // valores cifrados
  "campos_detectados": [{ "campo": "curp", "valor": "enc:v1:..." }],
  "datos_confirmados": { "curp": "enc:v1:..." },       // tras validar / editar
  "validaciones": { "curp_valida": true },
  "confianza_ocr": 0.93,
  "modelo_ia": "qwen3:4b-instruct-2507-q4_K_M",
  "adaptador": "docling+ocrmac",
  "metadatos": { "tiempos_etapas_ms": { "ocr_ms": 5200, "extraccion_ms": 3100 },
                 "campos_corregidos_ocr": ["curp"] },
  "texto_crudo": "enc:v1:...",        // texto OCR cifrado
  "ia_doc_id": "u3_d12_...",
  "fecha_procesamiento": "...", "fecha_validacion": "...", "fecha_edicion": "..."
}
```

Para pruebas sin MongoDB, `MONGO_URL=memory://` usa una colección en memoria (se pierde al reiniciar).

## Cifrado de columnas

`lib/crypto.ts` cifra con **AES-256-GCM** (IV aleatorio de 12 bytes, etiqueta de autenticación) y
antepone `enc:v1:`. La clave es `DATA_ENCRYPTION_KEY` (32 bytes en base64). **Si se pierde, los datos
cifrados no se pueden recuperar**; cambiarla hace ilegibles los datos existentes.
