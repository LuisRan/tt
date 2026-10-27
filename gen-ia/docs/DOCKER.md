# Ejecutar ocr-synth con Docker

Todo el generador (menú interactivo, modo por flags y tests) corre dentro de una
imagen con las versiones de Python, OpenCV, pdfium, libjpeg, etc. **fijadas**. Así
la misma `--seed` produce los mismos bytes en tu Mac, en el portátil de un
compañero o en un servidor Linux, sin depender de lo que tenga instalado cada equipo.

> Son datos MOCK de prueba. La imagen **no** contiene `formatos/` (referencias
> oficiales), ni `.venv`, ni `.git`, ni datos reales: `.dockerignore` los excluye
> del contexto de build y el `Dockerfile` solo copia `contracts/`, `src/`,
> `tests/`, `pyproject.toml`, `PLAN.md`, `README.md` y `.gitignore`.

## Requisitos

- Docker Desktop (macOS/Windows) o Docker Engine (Linux) con Compose v2
  (`docker compose version`).
- El daemon tiene que estar en marcha: `docker info` debe responder sin error.
- Opcional: `make` (los atajos del `Makefile`).

## Construir la imagen

```bash
docker compose build          # o: make build
```

La primera vez descarga `python:3.11.16-slim-bookworm` (fijada por tag **y** digest)
e instala las dependencias desde `requirements.lock` (solo wheels, con hashes).

## Menú interactivo

```bash
mkdir -p data
docker compose run --rm menu  # o: make menu
```

`menu` abre una terminal interactiva (`stdin_open` + `tty`) y ejecuta `python -m src`
sin argumentos, que con TTY abre el menú. *(El menú lo implementa la tarea M1; hasta
que se fusione, `menu` solo mostrará el uso del CLI.)*

Dentro del contenedor (`OCR_DOCKER=1`) el menú propone `/app/data` como carpeta de salida
(en tu equipo: `./data`). En el paso de la seed, Enter genera una seed aleatoria distinta en
cada ejecución (se muestra y queda en el comando equivalente y en `run.json`); la degradación
por defecto es `mixed` (mezcla de presets por muestra).

## Modo por flags

```bash
docker compose run --rm generate --doc-type all --n 5 --seed 1
docker compose run --rm generate --doc-type ine_2016 --n 100 --seed 42 --variant mixed --degradation light
# o: make generate ARGS="--doc-type all --n 5 --seed 1"
```

El servicio `generate` ejecuta `python -m src generate --out /app/data` y añade tus
argumentos (sin `--seed` la seed es aleatoria y se imprime al final). Opciones disponibles: ver el README (`--doc-type`, `--n`, `--seed`,
`--variant`, `--degradation`, `--out`). Sin `docker compose` también vale:

```bash
docker run --rm --platform linux/amd64 -v "$PWD/data:/app/data" ocr-synth:local \
    generate --doc-type all --n 10 --seed 1 --out /app/data
```

## Dónde caen los datos

En el host, en `./data/` (montado como `/app/data` dentro del contenedor), con una carpeta por
tipo de documento y los archivos **planos** dentro (sin subcarpeta por muestra):

```
data/
  run.json
  ine_2016/
    manifest.jsonl
    ine_2016-nacional-1-000000.pdf    # PDF degradado (lo que consume el OCR)
    ine_2016-nacional-1-000000.json   # Ground Truth (mismo nombre base: X.pdf <-> X.json)
    ...
  curp_2019/ ...
```

Con `--extras` también aparece `<documento>/_extras/` con `<sample_id>.clean.pdf` y
`<sample_id>.transform_log.json` (p. ej. `docker compose run --rm generate --doc-type all --n 5 --seed 1 --extras`).
Para leer un par y reconstruir las posiciones con `bbox_px`, ver "Formato del Ground Truth" en el
README. La ruta la decide el CLI, Docker solo monta `./data`. `data/` está en `.dockerignore`
(no entra a la imagen).
Importante: crea `./data` antes de ejecutar (`mkdir -p data`); si no existe, en Linux
Docker lo crearía como `root`.

## Correr los tests

```bash
docker compose run --rm tests   # o: make test
```

Ejecuta `python -m pytest -q` dentro del contenedor (toda la suite, incluido
`src/degradation/tests`). Tarda unos minutos (ver "Rendimiento"). Los tests que
necesitan `git`, `formatos/` o los archivos de Docker se omiten dentro de la imagen
(no están ahí por diseño). Los tests estáticos de este directorio
(`tests/test_docker_guardrails.py`) se ejecutan en el host con `pytest` normal.

## Garantía de reproducibilidad y sus límites

### Qué se garantiza

Con `OCR_PLATFORM=linux/amd64` (el valor por defecto) y la misma imagen
(mismo `Dockerfile` + `requirements.lock`):

- Dos ejecuciones con los mismos argumentos producen **bytes idénticos** (`<sample_id>.pdf`,
  `<sample_id>.json`, `manifest.jsonl`, `run.json` y, con `--extras`, `_extras/<sample_id>.clean.pdf`
  y `_extras/<sample_id>.transform_log.json`).
- El resultado no depende del reloj, la zona horaria, el locale, `PYTHONHASHSEED`
  ni del número de hilos de OpenCV/numpy (probado con 1 y 8 hilos: mismos bytes) ni del
  sistema anfitrión: solo del contenedor.
- Las dependencias de Python son exactamente las del lock (instaladas con
  `--require-hashes --no-deps`): ninguna resolución de pip puede cambiarlas.

Variables fijadas en el `Dockerfile` y por qué:

| Variable | Motivo |
|---|---|
| `SOURCE_DATE_EPOCH=0` | **Necesaria**: sin ella el PDF limpio (reportlab) incrusta la fecha de creación y cambia en cada ejecución (comprobado: dos ejecuciones sin la variable difieren solo en el PDF limpio, el de `_extras/`). |
| `PYTHONHASHSEED=0`, `TZ=UTC`, `LANG/LC_ALL=C.UTF-8` | Defensivas: eliminan fuentes clásicas de variación. En las pruebas, cambiarlas (`PYTHONHASHSEED=random`, `TZ=America/Mexico_City`, `LANG=es_MX.UTF-8`) no alteró los bytes, así que hoy no son imprescindibles. |
| `PYTHONDONTWRITEBYTECODE=1` | Evita escribir `.pyc` (el usuario no root no puede escribir en `/app`). |

### Qué NO se garantiza

- **Identidad entre arquitecturas.** `linux/arm64` y `linux/amd64` **no** producen bytes
  idénticos: en la comparación hecha con `--doc-type all --n 2 --seed 1` (versión anterior, que
  siempre guardaba todo), el PDF limpio coincidió, pero hubo diferencias en el GT y en el
  `transform_log` (matrices con diferencias del orden de 1e-11 por aritmética de coma flotante
  distinta) y en algunos PDFs degradados (diferencias de píxel). Por eso el valor por defecto es
  `linux/amd64` en todas las máquinas.
- **Identidad con la ejecución nativa en macOS/Windows** (fuera de Docker): pdfium,
  libjpeg y OpenCV de macOS rasterizan distinto que los de Linux; incluso el PDF limpio
  coincide solo si se define `SOURCE_DATE_EPOCH=0`, pero el PDF degradado no.
- **Goldens del repositorio fuera de `linux/amd64`.** `golden_presets.json` esta capturado
  en `linux/amd64`; en macOS o arm64 los hashes no coinciden, por lo que
  `test_existing_presets_unchanged` se omite alli (ver "Goldens y plataforma").
- Cambiar `Dockerfile`, el digest de la base o `requirements.lock` puede cambiar los bytes:
  tras tocarlos, regenera datos de referencia y goldens.

### Apple Silicon (arm64) vs amd64

En un Mac M1/M2/M3, `linux/amd64` corre **emulado** (QEMU/Rosetta): es correcto y
reproducible, pero más lento. Para ir más rápido:

```bash
OCR_PLATFORM=linux/arm64 docker compose run --rm generate --doc-type all --n 5 --seed 1
```

Lo que pierdes: los bytes ya no coinciden con los de amd64 (diferencias de coma flotante
y de píxel descritas arriba). Sigue siendo reproducible entre ejecuciones arm64, pero
**no** compares ni mezcles conjuntos generados con plataformas distintas. Si usas arm64
para iterar, genera el conjunto final con amd64.

## Rendimiento

Medido en un Mac con Apple Silicon (contenedor `linux/arm64` nativo): la suite completa
tarda ~4 min (~2 min en el host con venv). **No** se limitan los hilos de OpenCV/OpenMP
(`OMP_NUM_THREADS`, `OPENCV_FOR_THREADS_NUM`): con ellos a 1 la misma suite tardó ~10 min
y las pruebas no mostraron ningún efecto sobre los bytes (1 hilo y 8 hilos dan lo mismo).
En Apple Silicon, `linux/amd64` corre emulado y es notablemente más lento; generar unas
pocas muestras sigue siendo cuestión de segundos (8 muestras: ~10-12 s emulado).

## Actualizar el lock

`requirements.lock` fija versiones exactas y hashes sha256 de wheels `cp311` para
linux x86_64, linux aarch64 y macOS arm64. No se edita a mano.

1. Cambia el rango en `pyproject.toml` si hace falta.
2. En un venv limpio con Python 3.11: `pip install -e .[dev]` y `pip freeze` (quita el
   paquete `ocr-synth` del resultado). Esas son las versiones candidatas.
3. Para cada paquete, descarga los wheels de cada plataforma sin dependencias y calcula
   sus hashes:
   ```bash
   pip download -r pins.txt --no-deps --only-binary=:all: --python-version 3.11 \
       --implementation cp --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
       --platform manylinux_2_17_x86_64 -d whl/      # repite con *_aarch64 y macosx_*_arm64
   pip hash whl/*.whl                                # o sha256sum
   ```
4. Escribe cada paquete como `nombre==versión \` seguido de una línea
   `    --hash=sha256:...` por wheel (sin comentarios dentro de la continuación: pip
   los trata como fin de la línea).
5. Reconstruye y verifica: `docker compose build --no-cache`, `docker compose run --rm tests`.
6. Si cambiaron versiones de pdfium/OpenCV/Pillow/numpy, los bytes pueden cambiar:
   regenera los goldens desde `linux/amd64`.

Para actualizar la imagen base: `docker buildx imagetools inspect python:3.11.16-slim-bookworm`
y copia el `Digest:` del índice multi-arquitectura al `FROM` del `Dockerfile`.

## Solución de problemas

- **`failed to connect to the docker API ... is the daemon running?`** El daemon está
  apagado. En macOS: `open -a Docker` y espera a que `docker info` responda.
- **`permission denied` en `./data` o ficheros propiedad de root (Linux).** Crea `data/`
  antes de ejecutar (`mkdir -p data`) y pasa tu usuario:
  `UID=$(id -u) GID=$(id -g) docker compose run --rm generate ...` (o usa `make`). Si
  ya quedó en root: `sudo chown -R "$USER" data`. En Docker Desktop para macOS el
  propietario se traduce solo y no hace falta.
- **Muy lento en Apple Silicon.** Es la emulación amd64; mira "Apple Silicon (arm64) vs
  amd64". También sube CPU/RAM en Docker Desktop (Settings > Resources).
- **`The requested image's platform (linux/amd64) does not match ...`** Es solo un aviso
  de emulación; ignóralo o fija `OCR_PLATFORM=linux/arm64`.
- **`menu` imprime el uso del CLI y sale.** Falta el menú (tarea M1) o no hay TTY: usa
  `docker compose run --rm menu` (no `docker compose run -T` ni `docker compose up`).
- **`pip ... hashes do not match`** al construir: el lock no corresponde a esta
  plataforma/Python. Hay hashes para `cp311` en linux x86_64/aarch64 y macOS arm64.

## Goldens y plataforma

`src/degradation/tests/golden_presets.json` guarda hashes de bytes (PDF, imagenes) y de
matrices en coma flotante de los presets de degradacion. Esos bytes dependen de la
plataforma (libjpeg/OpenCV/pdfium y aritmetica de coma flotante: macOS, linux/arm64 y
linux/amd64 no coinciden), asi que el golden esta **atado al contenedor `linux/amd64`**,
la referencia de reproducibilidad del proyecto. Lleva una clave `_meta` con la
plataforma de captura y las versiones de OpenCV/numpy/pillow/pypdfium2.

- `test_existing_presets_unchanged` (16 casos) **solo se ejecuta** con `OCR_DOCKER=1`
  (lo define el `Dockerfile`) y arquitectura `x86_64`. En cualquier otro caso (host
  macOS/Linux, contenedor arm64) se **omite** con un motivo explicito. Corre en
  `docker compose run --rm tests` con la plataforma por defecto.
- Los demas tests (precision geometrica <= 1.5 px, determinismo por seed, rangos...)
  corren en todas partes.

Cuando regenerar: solo tras un cambio **intencionado** de los presets, de los pasos de
degradacion, del `Dockerfile`/digest de la base o de `requirements.lock`. Comprueba antes
que la geometria/precision sigue bien (el resto de tests en verde). Comando exacto, desde
la raiz del repo:

```bash
docker compose build
docker compose run --rm -T --no-deps --entrypoint python tests \
    -m src.degradation.tests.golden > src/degradation/tests/golden_presets.json
docker compose build && docker compose run --rm tests   # verifica con el golden nuevo
```

(`-T` evita que el TTY convierta los saltos de linea; el script se niega a escribir
si no corre en linux/amd64 con `OCR_DOCKER=1`. Con `OCR_PLATFORM=linux/arm64` no
sirve para regenerar.)
