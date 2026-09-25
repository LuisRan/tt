# Operación: arrancar, reiniciar y reconstruir

Todos los comandos se ejecutan **desde la raíz del proyecto** (donde está `docker-compose.yml`).

```bash
cd ~/Documents/tt          # macOS / Linux
```
```powershell
cd C:\ruta\a\tt            # Windows (PowerShell)
```

## Arranque normal

| Sistema | Comando |
|---|---|
| macOS (recomendado) | `./scripts/start.sh --ia-nativa` |
| macOS / Linux todo en Docker | `./scripts/start.sh` |
| Windows | `.\scripts\start.ps1` (o `-IaNativa`) |

Abre **https://localhost:8443**. El script crea `.env` si no existe, configura Ollama, levanta los
contenedores con `--build` y espera a que el backend responda.

## Detener

```bash
./scripts/stop.sh              # detiene contenedores y la IA nativa; los datos se conservan
./scripts/stop.sh --borrar     # además BORRA bases de datos y archivos (pide confirmación)
```

## Reiniciar después de actualizar el código (lo más común)

```bash
cd ~/Documents/tt
./scripts/stop.sh
./scripts/start.sh --ia-nativa
```

`start.sh` reconstruye solo las imágenes que cambiaron y reinicia la IA nativa con el código nuevo.
Las migraciones de la base se aplican solas al arrancar el backend.

## Reconstrucción completa (si algo quedó en caché o no refleja los cambios)

```bash
cd ~/Documents/tt
./scripts/stop.sh
docker compose build --no-cache backend web      # frontend + backend desde cero
./scripts/start.sh --ia-nativa
```

Si usas la IA en contenedor (sin `--ia-nativa`), incluye `ia` en el `build --no-cache`
(tarda más porque vuelve a descargar los modelos de Docling).

## Reiniciar un solo servicio

```bash
docker compose up -d --build backend      # tras cambiar código del backend
docker compose up -d --build web          # tras cambiar el frontend o Nginx
docker compose restart backend            # solo reiniciar (p. ej. tras editar .env)
./scripts/ia-nativa.sh --fondo            # reiniciar la IA nativa en Mac
```

> Cambios en `.env` que usa el backend requieren `docker compose up -d backend` (recrea el
> contenedor con las variables nuevas; `restart` no las recarga).

## Estado y logs

```bash
docker compose ps                          # estado de los contenedores
docker compose logs -f backend             # backend (códigos 2FA en modo consola)
docker compose logs -f web                 # Nginx
tail -f .run/ia.log                        # IA nativa (Mac)
docker compose logs -f ia                  # IA en contenedor
curl -s http://localhost:5001/health       # plataforma, motor OCR y modelo de la IA
```

El panel del administrador (**Dashboard**) muestra el estado de PostgreSQL, MongoDB, IA y el modelo.

## Modelo de lenguaje

Por defecto `OLLAMA_MODEL=qwen3:4b-instruct-2507-q4_K_M` (sin razonamiento interno, rápido).
`OLLAMA_KEEP_ALIVE=30m` mantiene el modelo cargado entre documentos.

```bash
ollama pull qwen3:4b-instruct-2507-q4_K_M   # si no lo tienes
ollama ps                                   # ver si está cargado
```

Para cambiarlo, edita `OLLAMA_MODEL` en `.env` y reinicia la IA (`./scripts/ia-nativa.sh --fondo`
o `docker compose up -d ia`). El tiempo de cada etapa (OCR / extracción) se ve en el detalle del
documento y en `metadatos.tiempos_etapas_ms`.

## Respaldos

```bash
ls backups/                                                   # respaldos cifrados
docker compose restart backup                                 # fuerza un respaldo ~2 min después
docker compose exec backup restaurar.sh pg_AAAAMMDD_HHMMSS.dump.enc
```

## Pruebas

```bash
cd ia_documentos && .venv/bin/python -m pytest      # 95 pruebas del módulo de IA
cd backend && npm test                               # 22 pruebas (requiere PostgreSQL + IA)
```
