# Modelo de datos del TT y contraste con las figuras 31–33

La muestra de la VPS que compartió el equipo contenía 17 tablas, 155 columnas y 16 claves foráneas en PostgreSQL. MongoDB tenía un resultado OCR. La comprobación del contenido protegido autenticó 44 valores y los cuatro controles de consistencia del diagnóstico dieron cero incidencias. La figura con nombres ficticios muestra el modelo lógico que consume el usuario autorizado. Para documentar el almacenamiento físico, usa nombres de campos, tipos y conteos obtenidos en la VPS y un ejemplo sintético de las envolturas de cifrado.

| Área | Tablas PostgreSQL |
|---|---|
| Cuenta y privacidad | usuarios, perfil_usuario, codigos_2fa, consentimientos_privacidad |
| Documentos y vigencia | tipos_documento, documentos, documento_versiones, validaciones_dato, alertas_vigencia |
| Extensión | plantillas_autofill, extension_codigos, extension_dispositivos |
| Operación y auditoría | metricas_ia, accesos, eventos_sistema, trafico_api, respaldos |

**Figuras 31 y 32.** Un usuario puede registrar varios documentos y generar varios códigos. La restricción única sobre perfil_usuario.usuario_id limita a un perfil por cuenta y permite que una cuenta recién creada aún no tenga perfil. Un tipo documental puede clasificar varios documentos. La columna documentos.tipo_id admite nulos, por lo que un documento puede quedar inicialmente sin tipo. Coloca el lado «muchos» de esa relación junto a documentos. Para la figura física, toma además las columnas incorporadas por los ALTER TABLE del esquema, las claves únicas y los índices parciales. Una FK en PostgreSQL no puede imponer por sí sola la integridad de una referencia externa a MongoDB.

**Figura 33.** En resultados_ocr, documento_id es la referencia al documento SQL y tiene índice único. El _id es único por definición en una colección ordinaria. Un listado que muestra la propiedad opcional unique ausente en ese índice puede inducir a error. El backend autentica los valores OCR extraídos y confirmados al descifrarlos. Con v09, v2 representa cadenas, números y booleanos en esos objetos, incluidos los anidados. El texto OCR y los valores de campos_detectados usan v1. Ambos formatos usan GCM. Los null, nombres de campos, tipo documental y metadatos operativos continúan diferenciables para la aplicación.

## Primera, segunda y tercera formas normales

| Forma | Ejemplo de diseño inicial para explicar en la defensa | Cambio de diseño y dependencia |
|---|---|---|
| 1FN | Una celda de usuario contiene una lista de documentos separados por comas | Cada documento pasa a una fila con su identificador y la FK del usuario. Cada celda representa un valor del dominio definido. |
| 2FN | En una tabla con clave candidata (documento_id, numero_version), la fecha de esa versión depende del par, pero el usuario propietario estable depende solo de documento_id | Separa el propietario en documentos y la fecha en versiones. La 2FN exige 1FN y elimina dependencias parciales de atributos no primos respecto de cualquier clave candidata. |
| 3FN | Guardar tipo_id y nombre_tipo en documentos introduce la cadena documento_id → tipo_id → nombre_tipo | Lleva el nombre a tipos_documento. Formalmente, para cada dependencia no trivial X → A, X es superclave o A pertenece a alguna clave candidata. |

El uso de un id serial por tabla por sí solo no demuestra 3FN. En documento_versiones hay que acordar si usuario_id y tipo_id son hechos históricos propios de cada versión o copias del documento actual. Aplica el mismo razonamiento a alertas_vigencia. Si son copias derivables, documenta la redundancia y cómo se garantiza su consistencia antes de proponer una migración. Si son hechos del momento de emitir la alerta o versión, documenta esa regla. Este parche preserva el esquema y los datos de negocio.

## Comandos de revisión en la VPS

```bash
cd /home/eden/tt
git status --short 2>/dev/null || true
rg -n -i 'create table|references |unique|create index|alter table' backend/src/db/schema.sql
sudo docker compose --env-file .env exec -T postgres sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off' <<'SQL'
SELECT table_name, count(*) AS columnas
FROM information_schema.columns WHERE table_schema='public'
GROUP BY table_name ORDER BY table_name;
SELECT conrelid::regclass AS tabla, conname, pg_get_constraintdef(oid) AS definicion
FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1,2;
SELECT tablename, indexname, indexdef FROM pg_indexes
WHERE schemaname='public' ORDER BY 1,2;
SQL
sudo python3 deploy/seguridad/configurar.py --verify
```

El último comando requiere la versión activada. Para ver los índices Mongo con la conexión real del backend y sin imprimir datos de personas:

```bash
sudo docker compose --env-file .env exec -T backend node --input-type=module <<'JS'
import { MongoClient } from 'mongodb';
const m = new MongoClient(process.env.MONGO_URL);
try {
  await m.connect();
  const c = m.db(process.env.MONGO_DB).collection('resultados_ocr');
  for (const idx of await c.indexes()) {
    console.log({nombre: idx.name, columnas: idx.key, unico: idx.name === '_id_' || idx.unique === true});
  }
} finally { await m.close(); }
JS
```

Para revisar código busca las escrituras en backend/src/services/documentos.ts, el esquema y los índices en backend/src/db, la criptografía en backend/src/lib/crypto.ts y los adaptadores de archivos en ia_documentos/infrastructure/storage. Si la VPS carece de rg, usa grep -nEi con el mismo patrón para el archivo SQL.
