# Atajos de Docker (ver docs/DOCKER.md). Requiere Docker con Compose v2.
# Exporta UID/GID para que ./data no quede como root en Linux.
export UID := $(shell id -u)
export GID := $(shell id -g)

# Argumentos para `make generate`, p. ej.: make generate ARGS="--doc-type all --n 5 --seed 1"
ARGS ?= --doc-type all --n 5 --seed 1

.PHONY: build menu generate test data-dir

data-dir:
	@mkdir -p data

build:
	docker compose build

menu: data-dir
	docker compose run --rm menu

generate: data-dir
	docker compose run --rm generate $(ARGS)

test:
	docker compose run --rm tests
