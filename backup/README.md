# Servicio de respaldos (`backup`)

Contenedor independiente que respalda periódicamente las bases de datos y los archivos. No recibe
peticiones; se comunica solo con PostgreSQL y MongoDB por la red interna.

| Archivo | Función |
|---|---|
| `Dockerfile` | Imagen con `pg_dump`, `mongodump`, `openssl`. |
| `respaldo.sh` | Bucle: espera `BACKUP_RETRASO_INICIAL` (120 s) y respalda cada `BACKUP_INTERVALO_HORAS` (24). |
| `restaurar.sh` | Descifra y restaura un respaldo concreto. |

Cada ciclo genera en `./backups/` (montado desde el equipo):

- `pg_AAAAMMDD_HHMMSS.dump.enc` — `pg_dump` en formato custom.
- `mongo_AAAAMMDD_HHMMSS.archive.enc` — `mongodump --archive --gzip`.
- `archivos_AAAAMMDD_HHMMSS.tar.gz` — copia de `storage` (cada archivo ya está cifrado con AES-256-GCM).

Los respaldos de las bases se cifran con AES-256-CBC + PBKDF2 (200 000 iteraciones) usando `BACKUP_ENCRYPTION_KEY`;
todo se registra en la tabla `respaldos` y se conservan los últimos `BACKUP_RETENCION` (7) de cada tipo.

## Restaurar

```bash
cd ~/Documents/tt
ls backups/
docker compose exec backup restaurar.sh pg_20260101_030000.dump.enc
docker compose exec backup restaurar.sh mongo_20260101_030000.archive.enc
```

Para restaurar en otro equipo necesitas el mismo `BACKUP_ENCRYPTION_KEY` (para descifrar el
respaldo) **y** el mismo `DATA_ENCRYPTION_KEY` (para leer los datos cifrados dentro de la base).
