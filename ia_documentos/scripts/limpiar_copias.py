"""Retención de copias OCR: no elimina archivos originales de la bóveda."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import JSON_RESULTS_DIR, RAW_TEXT_DIR
from infrastructure.storage.file_adapters import JsonFileAdapter, MarkdownFileAdapter


def limpiar(horas: int, aplicar: bool) -> dict:
    if horas < 1:
        raise ValueError("La retención debe ser al menos una hora")
    json_repo, texto_repo = JsonFileAdapter(JSON_RESULTS_DIR), MarkdownFileAdapter(RAW_TEXT_DIR)
    limite = time.time() - horas * 3600
    pendientes = []
    for ruta in JSON_RESULTS_DIR.glob("*.json"):
        if ruta.is_symlink():
            raise ValueError("Enlace no admitido")
        if ruta.stat().st_mtime < limite:
            # Validar antes de eliminar; un archivo corrupto se conserva y detiene la tarea.
            documento = json_repo.obtener_resultado(ruta.stem)
            if documento and documento.get("user_id"):
                pendientes.append(ruta.stem)
    if aplicar:
        for doc_id in pendientes:
            json_repo.eliminar(doc_id)
            texto_repo.eliminar(doc_id)
    return {"copias_candidatas": len(pendientes), "aplicado": aplicar}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    try:
        print(json.dumps(limpiar(a.hours, a.apply)))
    except Exception as e:
        print("Limpieza detenida: " + type(e).__name__, file=sys.stderr)
        sys.exit(1)
