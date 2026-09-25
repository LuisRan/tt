# ─── Etapa 1: compilar el sitio estático de Astro ────────────────────────────
# (contexto de build: raíz del proyecto)
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ .
RUN npm run build

# ─── Etapa 2: Nginx (reverse proxy + HTTPS) sirviendo el frontend ────────────
FROM nginx:1.27-alpine
RUN apk add --no-cache openssl
COPY --from=build /app/dist /usr/share/nginx/html
# La plantilla se procesa con envsubst al arrancar (variable HTTPS_PORT)
COPY nginx/templates/ /etc/nginx/templates/
COPY nginx/10-certificados.sh /docker-entrypoint.d/10-certificados.sh
RUN chmod +x /docker-entrypoint.d/10-certificados.sh && rm -f /etc/nginx/conf.d/default.conf
ENV NGINX_ENVSUBST_FILTER=HTTPS_PORT
EXPOSE 80 443
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1/salud || exit 1
