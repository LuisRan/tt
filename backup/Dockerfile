# Servicio de respaldos (secciones 2.8 y 4.5): pg_dump + mongodump cifrados.
FROM postgres:16-alpine
RUN apk add --no-cache mongodb-tools openssl tzdata
COPY respaldo.sh restaurar.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/respaldo.sh /usr/local/bin/restaurar.sh
CMD ["/usr/local/bin/respaldo.sh"]
