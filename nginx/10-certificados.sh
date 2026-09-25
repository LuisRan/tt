#!/bin/sh
# Genera un certificado autofirmado para HTTPS local si no existe uno.
# Para evitar la advertencia del navegador, coloca en nginx/certs/ un
# certificado de confianza (p. ej. generado con mkcert; los scripts de
# arranque lo hacen automáticamente si mkcert está instalado).
set -e
DIR=/etc/nginx/certs
if [ ! -s "$DIR/cert.pem" ] || [ ! -s "$DIR/key.pem" ]; then
  echo "[certs] generando certificado autofirmado para localhost"
  mkdir -p "$DIR"
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout "$DIR/key.pem" -out "$DIR/cert.pem" \
    -subj "/CN=localhost/O=TT OCR Platform" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1
  chmod 600 "$DIR/key.pem" 2>/dev/null || true
fi
