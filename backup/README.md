# Respaldos y recuperación

El servicio genera respaldos de PostgreSQL, MongoDB y los archivos de la bóveda. El despliegue con `docker-compose.security.yml` añade el volumen de IA, usa cuentas de respaldo independientes y verifica los certificados de las dos bases. La salida de cada exportador se cifra por bloques con AES-256-GCM antes de escribir el archivo final. El formato TTBACKUP2 usa sal de 16 bytes, nonce de 12 bytes, PBKDF2-HMAC-SHA256 con 310 000 iteraciones y etiqueta de 16 bytes.

Cada conjunto completo tiene el mismo identificador de fecha UTC en `pg_*.enc`, `mongo_*.enc`, `archivos_*.enc` e `ia_*.enc`. El ciclo ordinario conserva los últimos `BACKUP_RETENCION` conjuntos por tipo. `BACKUP_RETRASO_INICIAL` controla la espera inicial y `BACKUP_INTERVALO_HORAS` el intervalo. Las copias periódicas se toman con la aplicación en uso. La instantánea de mantenimiento detiene a los escritores para comprobar coherencia entre ambos motores.

Desde la raíz del proyecto, el operador puede probar una instantánea nueva con una ventana de mantenimiento:

```bash
sudo python3 deploy/seguridad/configurar.py --restore-test
```

El instalador autentica los cuatro archivos y restaura PostgreSQL y MongoDB en instancias desechables sin puertos publicados. Comprueba seis tablas y la colección de resultados. Recorre los dos tar sin extraer rutas al host. Los temporales de descifrado se guardan en un directorio privado de `/dev/shm`. Las instancias de prueba crean sus propios volúmenes anónimos y los retiran al terminar. Para recuperar un volumen en un incidente real, primero restaura en un entorno nuevo, valida datos, documentos y llaves y programa el cambio de servicio.

La primera instantánea está en `backups/antes-v09/`, las pruebas posteriores en `backups/prueba-v09/`. La rotación ordinaria de `backups/` las deja fuera de su conjunto. La cuenta de respaldo necesita lectura de las tablas y secuencias de la aplicación y permiso de inserción en `respaldos`. La tabla guarda el nombre del archivo. Una copia periódica registra su nombre relativo a `backups/`. En las instantáneas de mantenimiento, el directorio exacto figura en el estado privado del instalador.

`--export-recovery` genera `backups/claves_*.enc` con el material privado de recuperación. Lleva tanto ese archivo como **un conjunto de datos cifrado** a un almacenamiento externo con acceso restringido. Conserva `BACKUP_ENCRYPTION_KEY` por un canal separado. La recuperación de los documentos también requiere `DATA_ENCRYPTION_KEY` y la llave de IA, conservadas en el paquete de recuperación. Define responsable y plazo de conservación para estas copias.

Los archivos históricos `*.dump.enc` y `*.archive.enc` de la versión CBC pueden leerse de forma explícita con `archivo_seguro.py decrypt ... --output ... --allow-legacy` en un entorno aislado. El descifrado CBC anterior carece de etiqueta de autenticación. Los antiguos `*.tar.gz` pueden contener archivos cifrados individualmente y nombres legibles. Revisa también posibles `*.dump` o `*.archive` de operaciones interrumpidas, instantáneas del proveedor y copias externas. Conserva los originales hasta verificar una recuperación equivalente y aplicar la política de retención aprobada.
