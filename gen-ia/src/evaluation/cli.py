"""CLI de evaluación: `python -m src.evaluation run|report|compare`."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Callable

from . import campaign
from .ocr_clients import OcrClient, OcrClientError

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.evaluation",
                                description="Evaluación supervisada del OCR con datos sintéticos.")
    p.add_argument("-v", "--verbose", action="store_true", help="logs detallados (DEBUG)")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="genera la campaña, procesa en el OCR, evalúa y escribe informes")
    r.add_argument("--config", required=True, help="campaign.json (CampaignConfig)")
    r.add_argument("--out-dir", default=None, help="sobrescribe out_dir de la config")
    r.add_argument("--resume", action="store_true", help="reutiliza muestras generadas/procesadas")
    r.add_argument("--force-new-run", action="store_true",
                   help="si la campaña existe, crea una nueva (sufijo -rN); nunca sobrescribe")
    r.add_argument("--run-dir", default=None, help="run-dir existente a reanudar (con --resume)")
    r.add_argument("--max-per-type", type=int, default=campaign.DEFAULT_MAX_PER_TYPE,
                   help=f"máximo de documentos base por tipo (default {campaign.DEFAULT_MAX_PER_TYPE})")

    rp = sub.add_parser("report", help="re-evalúa un run-dir y reescribe results/")
    rp.add_argument("--run-dir", required=True)

    c = sub.add_parser("compare", help="compara dos campañas (baseline vs candidate)")
    c.add_argument("--baseline", required=True)
    c.add_argument("--candidate", required=True)
    c.add_argument("--out", default=None, help="archivo JSON donde guardar la comparación")
    return p


def main(argv: list[str] | None = None,
         client_factory: Callable[[], OcrClient] | None = None) -> int:
    """`client_factory` permite inyectar un OcrClient (tests); por defecto se elige por ocr.kind."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "run":
            cfg = campaign.load_config(args.config)
            out = campaign.run(cfg, client=client_factory() if client_factory else None,
                               out_dir=args.out_dir, resume=args.resume, force_new_run=args.force_new_run,
                               run_dir=args.run_dir, max_per_type=args.max_per_type)
            print(f"run_id:  {out.run_id}\nrun-dir: {out.run_dir.resolve()}\n"
                  f"muestras: {out.n_samples} (sin respuesta del OCR: {out.n_missing})")
            for name, path in sorted(out.files.items()):
                print(f"  {name}: {path}")
        elif args.command == "report":
            _, files = campaign.report_run(args.run_dir)
            for name, path in sorted(files.items()):
                print(f"{name}: {path}")
        else:
            comp = campaign.compare_runs(args.baseline, args.candidate)
            text = comp.model_dump_json(indent=2) if hasattr(comp, "model_dump_json") else str(comp)
            if args.out:
                Path(args.out).write_text(text + "\n", encoding="utf-8")
                print(f"comparación escrita en {args.out}")
            else:
                print(text)
    except (campaign.CampaignError, OcrClientError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrumpido (use --resume para continuar).", file=sys.stderr)
        return 130
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
