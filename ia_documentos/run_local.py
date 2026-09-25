#!/usr/bin/env python3
"""
run_local.py
Menu interactivo para procesar documentos de identidad mexicanos.

Modos de uso:
    python run_local.py               -> menu interactivo
    python run_local.py mi_ine.pdf    -> directo con archivo
    python run_local.py --health      -> verificar estado del servicio
"""
import json
import os
import sys
from pathlib import Path

# Asegurar imports desde la raiz del proyecto
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from config.settings import INPUT_DIR, LOGS_DIR
from infrastructure.logging_setup import configurar_logging

configurar_logging(LOGS_DIR, nivel="INFO")

from application.pipeline import DocumentPipeline
from domain.entities.documento import DocumentoNoSoportadoError
from infrastructure.ocr.pdf_validator import PdfValidationError


# ── Utilidades de consola ─────────────────────────────────────────────────────

def limpiar_pantalla():
    os.system("cls" if os.name == "nt" else "clear")


def separador(char="-", ancho=60):
    print(char * ancho)


def titulo(texto: str):
    separador("=")
    print(f"  {texto}")
    separador("=")


def input_seguro(prompt: str) -> str:
    """input() que maneja Ctrl+C limpiamente."""
    try:
        return input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSaliendo...")
        sys.exit(0)


# ── Listar PDFs disponibles ───────────────────────────────────────────────────

def listar_pdfs() -> list[Path]:
    if not INPUT_DIR.exists():
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(INPUT_DIR.rglob("*.pdf"))


def mostrar_pdfs(pdfs: list[Path]) -> None:
    if not pdfs:
        print("\n  No hay PDFs en data/input/")
        print(f"  Coloca tus archivos PDF en:\n  {INPUT_DIR.resolve()}\n")
        return

    print(f"\n  PDFs encontrados en data/input/:\n")
    for i, pdf in enumerate(pdfs, 1):
        try:
            relativa = pdf.relative_to(INPUT_DIR)
        except ValueError:
            relativa = pdf.name
        tamaño_kb = round(pdf.stat().st_size / 1024, 1)
        print(f"  [{i}] {relativa}  ({tamaño_kb} KB)")
    print()


# ── Mostrar resultado bonito ──────────────────────────────────────────────────

def mostrar_resultado(resultado: dict) -> None:
    titulo("RESULTADO")
    separador()

    datos = resultado.get("datos", {})
    metadatos = resultado.get("metadatos", {})

    print("\n  DATOS EXTRAIDOS:\n")
    for campo, valor in datos.items():
        if valor is not None and str(valor).strip():
            etiqueta = campo.replace("_", " ").upper()
            valor_str = str(valor)
            if len(valor_str) > 50:
                valor_str = valor_str[:47] + "..."
            print(f"  {etiqueta:<28} {valor_str}")

    separador()
    print()
    print(f"  Tipo detectado : {resultado.get('tipo_documento', 'N/A').upper()}")
    print(f"  Adaptador      : {metadatos.get('adaptador_usado', 'N/A')}")
    print(f"  Tiempo         : {metadatos.get('tiempo_procesamiento_ms', 0)} ms")
    print(f"  Doc ID         : {resultado.get('doc_id', 'N/A')}")

    errores = metadatos.get("errores_validacion", [])
    if errores:
        print(f"\n  Advertencias ({len(errores)}):")
        for err in errores:
            print(f"     - {err}")

    print(f"\n  Texto crudo guardado en:")
    print(f"     {metadatos.get('ruta_texto_crudo', 'N/A')}")
    separador()


# ── Procesar un archivo ───────────────────────────────────────────────────────

def procesar_archivo(pipeline: DocumentPipeline, ruta: Path, user_id: str) -> None:
    print(f"\n  Procesando: {ruta.name}")
    print(f"  Esto puede tardar 30-90 segundos (Docling + LLM)...\n")

    try:
        resultado = pipeline.process(ruta, user_id=user_id)
        mostrar_resultado(resultado)

        ver_json = input_seguro("\n  Ver JSON completo? [s/N]: ")
        if ver_json.lower() == "s":
            print()
            print(json.dumps(resultado, indent=2, ensure_ascii=False))

        input_seguro("\n  Presiona Enter para volver al menu...")

    except DocumentoNoSoportadoError as e:
        print(f"\n  Documento NO soportado:")
        print(f"     {e.mensaje}")
        print(f"\n  Tipos aceptados: INE, Acta de Nacimiento, Pasaporte Mexicano, CURP")
        input_seguro("\n  Presiona Enter para volver al menu...")
    except PdfValidationError as e:
        print(f"\n  PDF invalido: {e}")
        input_seguro("\n  Presiona Enter para volver al menu...")
    except RuntimeError as e:
        print(f"\n  Error del pipeline: {e}")
        input_seguro("\n  Presiona Enter para volver al menu...")
    except Exception as e:
        print(f"\n  Error inesperado: {e}")
        import traceback
        traceback.print_exc()
        input_seguro("\n  Presiona Enter para volver al menu...")


# ── Menu interactivo ──────────────────────────────────────────────────────────

def menu_interactivo(pipeline: DocumentPipeline) -> None:
    while True:
        limpiar_pantalla()
        titulo("PIPELINE DE DOCUMENTOS DE IDENTIDAD")

        pdfs = listar_pdfs()
        mostrar_pdfs(pdfs)

        print("  Opciones:")
        print("    [numero]  - procesar PDF de la lista")
        print("    [r]       - refrescar lista")
        print("    [m]       - escribir ruta manual")
        print("    [h]       - verificar estado del servicio")
        print("    [q]       - salir")

        eleccion = input_seguro("\n  > ")

        if not eleccion:
            continue

        if eleccion.lower() == "q":
            print("\n  Hasta luego!\n")
            break

        if eleccion.lower() == "r":
            continue

        if eleccion.lower() == "h":
            mostrar_health(pipeline)
            input_seguro("\n  Presiona Enter para volver al menu...")
            continue

        if eleccion.lower() == "m":
            ruta_manual = input_seguro("  Ruta del PDF: ")
            ruta = Path(ruta_manual).expanduser().resolve()
            if not ruta.exists():
                print(f"\n  Archivo no encontrado: {ruta}")
                input_seguro("\n  Presiona Enter...")
                continue
            user_id = input_seguro("  user_id (Enter para 'usuario_local'): ") or "usuario_local"
            procesar_archivo(pipeline, ruta, user_id)
            continue

        # Eleccion por numero
        try:
            indice = int(eleccion) - 1
            if indice < 0 or indice >= len(pdfs):
                print(f"\n  Numero invalido. Hay {len(pdfs)} PDFs disponibles.")
                input_seguro("\n  Presiona Enter...")
                continue
        except ValueError:
            print(f"\n  Opcion no reconocida: {eleccion}")
            input_seguro("\n  Presiona Enter...")
            continue

        ruta = pdfs[indice]
        user_id = input_seguro("  user_id (Enter para 'usuario_local'): ") or "usuario_local"
        procesar_archivo(pipeline, ruta, user_id)


# ── Health check ──────────────────────────────────────────────────────────────

def mostrar_health(pipeline: DocumentPipeline) -> None:
    titulo("ESTADO DEL SERVICIO")
    print()
    health = pipeline.health_check()
    for clave, valor in health.items():
        etiqueta = clave.replace("_", " ").capitalize()
        if isinstance(valor, bool):
            estado = "OK" if valor else "NO DISPONIBLE"
            print(f"  {etiqueta:<28} {estado}")
        else:
            print(f"  {etiqueta:<28} {valor}")
    print()
    separador()


# ── Modo directo (con argumentos) ─────────────────────────────────────────────

def modo_directo(pipeline: DocumentPipeline, args: list[str]) -> None:
    archivo = None
    user_id = "usuario_local"

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--user_id" and i + 1 < len(args):
            user_id = args[i + 1]
            i += 2
            continue
        if a == "--health":
            mostrar_health(pipeline)
            return
        archivo = a
        i += 1

    if not archivo:
        print("Uso:")
        print("  python run_local.py                  - menu interactivo")
        print("  python run_local.py mi.pdf           - procesar archivo")
        print("  python run_local.py mi.pdf --user_id u_001")
        print("  python run_local.py --health         - estado del servicio")
        sys.exit(1)

    ruta = Path(archivo).expanduser().resolve()
    if not ruta.exists():
        # Intentar buscar en data/input
        ruta_alt = INPUT_DIR / archivo
        if ruta_alt.exists():
            ruta = ruta_alt
        else:
            print(f"Archivo no encontrado: {archivo}")
            sys.exit(1)

    procesar_archivo(pipeline, ruta, user_id)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    print("\n  Inicializando pipeline...")
    pipeline = DocumentPipeline()
    print("  Pipeline listo.\n")

    if args:
        modo_directo(pipeline, args)
    else:
        menu_interactivo(pipeline)


if __name__ == "__main__":
    main()
