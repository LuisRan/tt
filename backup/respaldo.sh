#!/bin/sh
# ═════════════════════════════════════════════════════════════════════════
# Respaldo periódico de PostgreSQL y MongoDB (+ archivos cifrados).
#  - Opera independiente del flujo principal (no recibe peticiones).
#  - Cifra cada respaldo con AES-256 (openssl, clave BACKUP_ENCRYPTION_KEY).
#  - Conserva los últimos BACKUP_RETENCION respaldos de cada tipo.
#  - Registra cada respaldo en la tabla "respaldos".
# En Azure se reemplaza por Azure Backup / Blob Storage (sección 4.6).
# ═════════════════════════════════════════════════════════════════════════
set -eu
DESTINO=/backups
INTERVALO_HORAS=${BACKUP_INTERVALO_HORAS:-24}
RETENCION=${BACKUP_RETENCION:-7}
mkdir -p "$DESTINO"

registrar() { # tipo ubicacion tamano estado
  psql "$DATABASE_URL" -q -c "INSERT INTO respaldos (tipo, ubicacion, tamano, estado) VALUES ('$1', '$2', $3, '$4')" >/dev/null 2>&1 || true
}

cifrar() { # entrada salida
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_ENCRYPTION_KEY -in "$1" -out "$2"
  rm -f "$1"
}

podar() { # patron
  ls -1t $DESTINO/$1 2>/dev/null | tail -n +$((RETENCION + 1)) | xargs -r rm -f
}

respaldar() {
  TS=$(date +%Y%m%d_%H%M%S)
  echo "[respaldo] $TS iniciando"

  if pg_dump "$DATABASE_URL" -Fc -f "$DESTINO/pg_$TS.dump"; then
    cifrar "$DESTINO/pg_$TS.dump" "$DESTINO/pg_$TS.dump.enc"
    registrar postgres "pg_$TS.dump.enc" "$(stat -c %s "$DESTINO/pg_$TS.dump.enc")" completado
  else
    registrar postgres "pg_$TS" 0 fallido
  fi

  if mongodump --uri="$MONGO_URL" --archive="$DESTINO/mongo_$TS.archive" --gzip --quiet; then
    cifrar "$DESTINO/mongo_$TS.archive" "$DESTINO/mongo_$TS.archive.enc"
    registrar mongo "mongo_$TS.archive.enc" "$(stat -c %s "$DESTINO/mongo_$TS.archive.enc")" completado
  else
    registrar mongo "mongo_$TS" 0 fallido
  fi

  if [ -d /storage ]; then
    # los archivos ya están cifrados individualmente con AES-256-GCM
    tar -czf "$DESTINO/archivos_$TS.tar.gz" -C /storage . 2>/dev/null && \
      registrar archivos "archivos_$TS.tar.gz" "$(stat -c %s "$DESTINO/archivos_$TS.tar.gz")" completado
  fi

  podar 'pg_*.enc'; podar 'mongo_*.enc'; podar 'archivos_*.tar.gz'
  echo "[respaldo] $TS completado"
}

# Espera a que exista el esquema (lo crea el backend al arrancar)
until psql "$DATABASE_URL" -tAc "SELECT 1 FROM respaldos LIMIT 1" >/dev/null 2>&1 || \
      psql "$DATABASE_URL" -tAc "SELECT to_regclass('respaldos')" | grep -q respaldos; do
  echo "[respaldo] esperando a la base de datos…"; sleep 10
done

sleep "${BACKUP_RETRASO_INICIAL:-120}"
while true; do
  respaldar || echo "[respaldo] ERROR en el respaldo"
  sleep $((INTERVALO_HORAS * 3600))
done
