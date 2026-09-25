#!/bin/sh
# Restaura un respaldo cifrado.  Uso (dentro del contenedor "backup"):
#   docker compose exec backup restaurar.sh pg_20260101_030000.dump.enc
#   docker compose exec backup restaurar.sh mongo_20260101_030000.archive.enc
set -eu
ARCHIVO="/backups/$1"
TMP="/tmp/restauracion"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_ENCRYPTION_KEY -in "$ARCHIVO" -out "$TMP"
case "$1" in
  pg_*)    pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" "$TMP" ;;
  mongo_*) mongorestore --uri="$MONGO_URL" --archive="$TMP" --gzip --drop ;;
  *) echo "Tipo de respaldo desconocido"; exit 1 ;;
esac
rm -f "$TMP"
echo "Restauración completada desde $1"
