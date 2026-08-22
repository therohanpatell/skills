"""Command line front end: `pdfocr serve`, `pdfocr ocr file.pdf`, `pdfocr engines`."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from .config import settings
from .engines import ENGINE_NAMES
from .engines import status as engine_status
from .jobs import JobManager
from .models import JobStatus


def _bar(percent: float, width: int = 32) -> str:
    filled = int(width * percent / 100)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _render(job, stream=sys.stderr) -> None:
    if not stream.isatty():
        return
    progress = job.progress
    eta = f" eta {progress.eta_seconds:.0f}s" if progress.eta_seconds else ""
    line = (
        f"\r{_bar(progress.percent)} {progress.percent:5.1f}% "
        f"{progress.pages_done}/{progress.pages_total} pages  {progress.message}{eta}"
    )
    stream.write(line.ljust(shutil.get_terminal_size((100, 20)).columns - 1)[:200])
    stream.flush()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import app

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def cmd_engines(_: argparse.Namespace) -> int:
    for info in engine_status(settings):
        mark = "ok  " if info.available else "down"
        print(f"{mark}  {info.name:<10} {info.detail}")
    print(f"\ndefault engine: {settings.engine}")
    return 0


def cmd_ocr(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.ERROR, format="%(message)s")
    path = Path(args.pdf)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    manager = JobManager(settings)
    job = manager.submit(path.name, path.read_bytes(), engine=args.engine, dpi=args.dpi)
    started = time.monotonic()
    for state in manager.watch(job.id):
        _render(state)
    final = manager.get(job.id)
    if sys.stderr.isatty():
        sys.stderr.write("\n")
    manager.shutdown()

    if final is None or final.status is not JobStatus.done:
        reason = final.error if final and final.error else "job did not complete"
        print(f"failed: {reason}", file=sys.stderr)
        return 1

    text = final.text()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        elapsed = time.monotonic() - started
        print(
            f"{final.progress.pages_done} page(s) via {final.engine} in {elapsed:.1f}s "
            f"-> {args.out} ({len(text)} chars)",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(text + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdfocr", description=__doc__)
    parser.add_argument("--version", action="version", version=f"pdfocr {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the HTTP API and web UI")
    serve.add_argument("--host", default=settings.host)
    serve.add_argument("--port", type=int, default=settings.port)
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=cmd_serve)

    ocr = subparsers.add_parser("ocr", help="transcribe a PDF without starting the server")
    ocr.add_argument("pdf")
    ocr.add_argument("-o", "--out", help="write the text here instead of stdout")
    ocr.add_argument("-e", "--engine", default=settings.engine, choices=list(ENGINE_NAMES))
    ocr.add_argument("--dpi", type=int, default=settings.dpi)
    ocr.set_defaults(func=cmd_ocr)

    engines = subparsers.add_parser("engines", help="show which OCR backends are reachable")
    engines.set_defaults(func=cmd_engines)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
