#!/usr/bin/env python3
"""Convertir copias conocidas, sin crear respaldos de texto plano. API detenida."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import RAW_TEXT_DIR, JSON_RESULTS_DIR, INPUT_DIR, DATA_DIR
from infrastructure.storage.crypto_file import MAGIC, decode_bytes, storage_key, write_private


def migrate(apply: bool = False, rollback: bool = False, include_logs: bool = False) -> dict:
    storage_key()
    documentos = set(RAW_TEXT_DIR.rglob("*.md")) | set(JSON_RESULTS_DIR.rglob("*.json"))
    directorios = [INPUT_DIR, DATA_DIR / "tmp"]
    if include_logs:
        directorios.append(DATA_DIR / "logs")
    entradas = {p for raiz in directorios if raiz.exists()
                for p in raiz.rglob("*") if p.is_file() and p.name != ".gitkeep"}
    archivos = sorted(documentos | entradas)
    pendientes = []
    cifrados = 0
    # Validar todos antes de reemplazar el primero. No se ignoran errores de integridad.
    for ruta in archivos:
        if ruta.is_symlink():
            raise RuntimeError("No se admiten enlaces simbólicos en almacenamiento IA")
        paquete = ruta.read_bytes()
        es_cifrado = paquete.startswith(MAGIC)
        texto = decode_bytes(ruta, paquete) if es_cifrado else paquete
        if ruta in documentos and ruta.suffix == ".json":
            json.loads(texto.decode("utf-8"))
        if es_cifrado:
            cifrados += 1
        if es_cifrado == rollback:
            pendientes.append((ruta, hashlib.sha256(paquete).digest()))
    if apply:
        for ruta, firma in pendientes:
            paquete = ruta.read_bytes()
            if hashlib.sha256(paquete).digest() != firma:
                raise RuntimeError("El almacenamiento cambió durante la migración")
            texto = decode_bytes(ruta, paquete) if rollback else paquete
            write_private(ruta, texto, decrypt_for_rollback=rollback)
    return {"archivos": len(archivos), "cifrados_previamente": cifrados,
            "convertidos" if apply else "pendientes": len(pendientes)}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--decrypt-for-rollback", action="store_true")
    p.add_argument("--include-logs", action="store_true", help="Solo para un volumen detenido cuyos logs activos están en tmpfs")
    a = p.parse_args()
    try:
        print(json.dumps(migrate(a.apply, a.decrypt_for_rollback, a.include_logs)))
    except Exception as e:
        print("Migración detenida: " + type(e).__name__, file=sys.stderr)
        sys.exit(1)
