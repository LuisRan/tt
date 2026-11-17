# Seguridad v09 para TT

## Activación en la VPS

El instalador está preparado para la aplicación existente con PostgreSQL 16, MongoDB 7, Node, Nginx, IA, Ollama y el servicio de respaldos. Parte de las imágenes que estén corriendo. El proyecto de campañas flash se ejecuta por separado en Windows.

1. Aplica los archivos del paquete con `aplicar.py` como el usuario propietario de `/home/eden/tt`. La opción `--check` revisa el parche antes de escribir. En una copia sin historial Git, el aplicador también funciona desde la raíz de la distribución.
2. Conserva fuera de la VPS las llaves actuales y una copia recuperable de los datos. El cifrado de bases y respaldos utiliza las llaves que ya tienes. Solo se genera una llave independiente para los archivos de trabajo IA cuando corresponde. La cuenta de Windows que use `scp` necesita la llave privada del operador.
3. En el servidor ejecuta:

```bash
cd /home/eden/tt
chmod 600 .env
sudo python3 deploy/seguridad/configurar.py --check
sudo python3 deploy/seguridad/configurar.py --apply
sudo python3 deploy/seguridad/configurar.py --verify
sudo docker compose --env-file .env ps
```

La activación reconstruye imágenes, cierra la web, comprueba de nuevo la cola OCR y detiene los escritores. Activa TLS en las bases, crea los roles limitados, genera un respaldo cifrado y exige una restauración de prueba antes de modificar los valores de negocio. Después ejecuta la conversión, arranca los servicios, comprueba las conexiones y activa el temporizador de mantenimiento. Calcula la ventana con el tamaño de los datos y del respaldo. Tu sesión SSH y el túnel pueden seguir abiertos.

La salida esperada de `--verify` incluye `ssl: true`, privilegios administrativos en `false`, rol Mongo `readWrite` limitado a la base de la aplicación, cero valores protegidos legados o inválidos y cero referencias inconsistentes. También verifica los artefactos IA y las rutas backend→IA, IA→Ollama y Nginx→backend→bases. La comprobación exterior del proxy usa la conexión local al contenedor. Para comprobar la confianza pública se hace otra petición al nombre del dominio con la validación del navegador activa.

## Red, acceso y cifrado

| Tramo o recurso | Cómo queda | Evidencia |
|---|---|---|
| Navegador a Nginx | Los puertos y el certificado que usaba el VPS se conservan durante este parche | `docker compose ps` y prueba contra el nombre público tras el paso DNS |
| Nginx a Node | HTTPS y verificación del nombre `backend` contra la CA interna | Comprobación de la ruta `/api/salud` a través del proxy |
| Node a PostgreSQL y Mongo | TLS con CA y verificación de nombre. PostgreSQL exige SCRAM para conexiones nuevas | Auditoría y pruebas con cuentas de ejecución |
| Node a IA, IA a Ollama | HTTPS en los enlaces entre contenedores. La API de IA exige su llave en rutas de documentos | Certificados propios de cada servicio. El último salto del proxy de Ollama usa loopback en su mismo espacio de red |
| PostgreSQL | Rol diario con operaciones sobre datos. Rol de respaldos separado. Las migraciones tienen otra cuenta | Flags de `pg_roles`, `pg_stat_ssl` y pruebas de permisos |
| MongoDB | Cuenta diaria `readWrite` en la base de la aplicación, cuenta de respaldo `read` | `connectionStatus`. Los valores OCR y confirmados se autentican y cifran recursivamente |
| Archivos IA | AES-256-GCM para resultados, texto e índices. Entradas, temporales y logs activos en tmpfs | El trabajo de migración también cubre las copias antiguas conocidas en el volumen físico |
| Acceso administrativo | El detalle del panel oculta domicilio y enmascara CURP. La verificación por correo al iniciar sesión es opcional según la cuenta | Prueba de integración con datos sintéticos |

El cifrado por campo deja legibles los identificadores, nombres de campos, indicadores de validación y datos operativos que requieren las consultas. Algunos datos personales de cuenta, como correo y nombre, siguen legibles para procesos con permisos de base. Documenta su necesidad y el acceso autorizado. Los archivos y valores se descifran al usarlos. El operador con control de Docker o de las llaves conserva capacidad técnica de acceso. El cifrado de la aplicación tampoco demuestra que el disco físico del proveedor esté cifrado.

## Operación y reversión

- `sudo python3 deploy/seguridad/configurar.py --maintenance` comprueba vigencia, recarga certificados internos cuando corresponde, aplica la retención y verifica la instalación. La tarea diaria usa una CA privada de diez años y certificados de servicio de un año. Planifica manualmente el cambio de CA antes de su vencimiento.
- Las copias JSON/MD del procesamiento normal se eliminan tras la persistencia del resultado y del texto. La tarea diaria atiende pares pendientes con JSON identificado de más de 24 horas por defecto. Un MD huérfano, las entradas históricas y los respaldos requieren una decisión de retención separada. En una tarea diaria las 24 horas son un umbral, y el retiro ocurre en la siguiente ejecución.
- `sudo python3 deploy/seguridad/configurar.py --restore-test` abre una ventana breve para tomar una instantánea nueva y luego restaura en instancias aisladas. Comprueba espacio libre en `/dev/shm` y en disco para las instancias de prueba. Véase `backup/README.md`.
- `sudo python3 deploy/seguridad/configurar.py --export-recovery` produce una copia cifrada adicional de las llaves y la configuración. Guarda el conjunto de datos y el archivo de recuperación fuera de la VPS, y la contraseña maestra por separado.
- `sudo python3 deploy/seguridad/configurar.py --rollback` recupera la configuración y las imágenes anteriores sin borrar los volúmenes y vuelve los valores al formato que entiende la versión anterior. El rollback de esa versión restablece sus permisos y almacenamiento más amplios. Las nuevas cuentas de servicio y el respaldo de prueba siguen disponibles. Los permisos PUBLIC de PostgreSQL que se hayan restringido requieren revisión si había clientes externos al TT.
- Si `--apply` se interrumpe con los servicios detenidos, usa `--rollback` con las imágenes y el estado privado presentes. El instalador protege ediciones posteriores a `.env`. Si publicaste HTTPS después del parche, primero usa el respaldo y rollback que informó el instalador HTTPS para recuperar la configuración anterior. Conserva los archivos privados y los volúmenes mientras diagnosticas una reversión incompleta.

## Dominio y aviso

La CA interna se usa solo entre servicios. Una vez que el dueño de la zona confirme la grafía del dominio y que el registro A del nombre elegido apunte a `147.93.133.251`, puedes usar `sudo python3 deploy/seguridad/https_publico.py --domain redes-noc-alerts.xyz --check`. Si la revisión y el DNS son correctos, `--apply` solicita el certificado público, configura Nginx y prueba la renovación. El dueño del DNS puede hacer ese paso en su panel sin entrar al VPS. Mantén los registros de correo que ya utiliza el dominio.

El aviso de registro admite los campos públicos `PUBLIC_PRIVACIDAD_RESPONSABLE`, `PUBLIC_PRIVACIDAD_DOMICILIO`, `PUBLIC_PRIVACIDAD_CONTACTO_ARCO` y `PUBLIC_PRIVACIDAD_CONSERVACION` en `.env`. Astro los incorpora al construir la web. Completa sus valores con información aprobada antes de usar documentos de terceros. Para una actualización posterior construye solo `web` y recréalo. Archiva el aviso divulgado con su identificador y fecha, registra futuros cambios de versión y las comunicaciones que correspondan.

Para LFPDPPP, verifica el contenido íntegro del aviso, datos y finalidades, opciones de limitación, procedimiento ARCO, mecanismo de comunicación de cambios y consentimiento cuando proceda. Asigna responsables de incidentes, inventario y controles de acceso. Documenta contratos y tratamiento de Contabo y el proveedor de correo, ubicaciones, subencargados, copias, plazos y eliminación. Para ARCO, el art. 31 prevé hasta 20 días para comunicar la determinación y, cuando proceda, 15 días para hacerla efectiva, con las condiciones de prórroga que establece la ley. Ante una vulneración que afecte significativamente los derechos contemplados, el art. 19 dispone informar de inmediato a los titulares. Si el IPN decide fines y medios del tratamiento en carácter institucional, confirma con su Unidad de Transparencia el régimen de sujetos obligados. La afiliación académica por sí sola no determina el responsable.

Fuentes oficiales: [LFPDPPP, arts. 14–20, 29–31 y 35–36](https://www.diputados.gob.mx/LeyesBiblio/pdf/LFPDPPP.pdf) y [Ley General de Protección de Datos Personales en Posesión de Sujetos Obligados](https://www.diputados.gob.mx/LeyesBiblio/pdf/LGPDPPSO.pdf).

## Copia local y controles pendientes

Para una instalación nueva, los scripts de arranque generan `IA_STORAGE_KEY` junto con el resto de los secretos. Para una instalación de desarrollo existente, conserva el `.env` y añade una llave nueva de 32 bytes en base64 si el almacenamiento IA todavía usa el formato anterior. En ese entorno, con IA detenida, reconstruye su imagen y ejecuta `docker compose --profile ia run --rm --no-deps ia python scripts/migrar_almacenamiento.py --apply` antes de iniciar la aplicación. En el laboratorio flash el runner hace esa migración automáticamente. En la VPS usa el instalador, que monta la llave fuera del volumen.

Revisa el acceso SSH por persona, las cuentas que tienen `sudo` o acceso al socket de Docker, las copias antiguas y el tratamiento de datos en la extensión y en los sitios donde cada titular decide rellenar formularios. La salida del auditor contiene conteos. Nunca pegues `.env`, dumps, PDFs, CURP, respuestas OCR o el contenido de `private/` en la bitácora.
