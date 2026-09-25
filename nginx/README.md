# Nginx (imagen `web`)

Punto de entrada único de la plataforma: HTTPS, reverse proxy hacia el backend y servidor del
frontend estático. Es el **único** contenedor que publica puertos.

| Archivo | Función |
|---|---|
| `Dockerfile` | Etapa 1 compila el frontend (Astro); etapa 2 lo copia a Nginx 1.27. Contexto de build: raíz del repo. |
| `templates/default.conf.template` | Configuración; `envsubst` sustituye `HTTPS_PORT` al arrancar. |
| `10-certificados.sh` | Si no hay `certs/cert.pem` y `certs/key.pem`, genera un certificado autofirmado para `localhost`. |
| `certs/` | Certificados montados en el contenedor. Los scripts de arranque colocan aquí uno de `mkcert` si está instalado. |

## Qué hace la configuración

- Puerto 80 (`HTTP_PORT`, 8080 por defecto) → redirección 301 a HTTPS; `/salud` para healthcheck.
- Puerto 443 (`HTTPS_PORT`, 8443 por defecto): TLS 1.2/1.3, HSTS, CSP estricta y cabeceras de seguridad.
- `/api/` → `backend:3000` con `X-Real-IP` / `X-Forwarded-*`, cuerpo máximo 12 MB, 20 req/s por IP.
- `/api/auth/(login|registro)...` → 10 peticiones/min por IP (429 al exceder), cuerpo máximo 16 KB.
- `/_astro/` → recursos con hash, caché inmutable.
- `/` → páginas del frontend (`try_files $uri $uri/index.html $uri.html =404`), sin caché.
- `absolute_redirect off` para que las redirecciones conserven el puerto 8443.

## Certificado de confianza (sin advertencia del navegador)

```bash
brew install mkcert && mkcert -install          # macOS (una vez)
rm nginx/certs/*.pem && ./scripts/start.sh --ia-nativa
```
