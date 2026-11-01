#!/usr/bin/env python3
"""01_render: render PDF pages to 250dpi PNG.

Usage: .venv/bin/python pipeline/01_render.py --page 10
       .venv/bin/python pipeline/01_render.py --all
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.common import ROOT, SOURCE_PDF, RENDER_DPI, TOTAL_PAGES  # noqa: E402

PDF = ROOT / SOURCE_PDF
OUT = ROOT / "data" / "pages"


def render_page(page: int):
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"p{page:03d}"
    if not (OUT / f"p{page:03d}.png").exists():
        # clean stale fragments of a partial run (suffix width varies with
        # total page count: 70-page doc -> "-03", 139-page doc -> "-139")
        for stale in OUT.glob(f"p{page:03d}-*.png"):
            stale.unlink()
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(RENDER_DPI), "-f", str(page), "-l", str(page), str(PDF), str(dest)],
            check=True)
    # pdftoppm names output pNNN-XXX.png; normalize to pNNN.png
    for rendered in OUT.glob(f"p{page:03d}-*.png"):
        rendered.rename(OUT / f"p{page:03d}.png")
    print(f"page {page}: {OUT / f'p{page:03d}.png'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", type=int)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.all:
        for p in range(1, TOTAL_PAGES + 1):
            render_page(p)
    elif args.page:
        render_page(args.page)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
