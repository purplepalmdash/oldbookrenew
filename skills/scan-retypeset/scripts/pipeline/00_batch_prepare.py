#!/usr/bin/env python3
"""00_batch_prepare: render all pages, run geometry on panel pages,
crop text bands for MCP OCR.

Usage: .venv/bin/python pipeline/00_batch_prepare.py [--pages 4-137]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from lib.common import (ROOT, SOURCE_PDF, RENDER_DPI, TOTAL_PAGES,  # noqa: E402
                        PANEL_PAGES)


def render_all():
    missing = [p for p in range(1, TOTAL_PAGES + 1)
               if not (ROOT / "data" / "pages" / f"p{p:03d}.png").exists()]
    if not missing:
        print("render: all present")
        return
    print(f"render: {len(missing)} pages missing -> rendering")
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(RENDER_DPI), str(ROOT / SOURCE_PDF),
         str(ROOT / "data" / "pages" / "all")], check=True)
    for p in range(1, TOTAL_PAGES + 1):
        src = ROOT / "data" / "pages" / f"all-{p:03d}.png"
        dst = ROOT / "data" / "pages" / f"p{p:03d}.png"
        if src.exists():
            src.rename(dst)
    # cleanup leftovers
    for f in (ROOT / "data" / "pages").glob("all-*.png"):
        f.unlink()
    print("render: done")


def crop_textband(page: int) -> bool:
    geo = json.loads((ROOT / "data" / "layout" / f"p{page:03d}.geo.json").read_text())
    if not geo.get("text_band_union"):
        return False
    im = Image.open(ROOT / "data" / "pages" / f"p{page:03d}_deskew.png")
    # MUST equal the box /lib/common.ocr_to_page_scale assumes when mapping
    # OCR bboxes back to page coords (frame-clamped so margin seals stay
    # out of the crop; a naive union+pad crop shifts every x mapping by up
    # to ~70px on pages where the clamp engages).
    from lib.common import textband_crop_box
    box = textband_crop_box(geo)
    im.crop(tuple(int(v) for v in box)).save(ROOT / "data" / "layout" / f"p{page:03d}_textband.png")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default="",
                    help="comma list, default: config panel_pages range")
    args = ap.parse_args()
    pages = PANEL_PAGES if not args.pages else [int(p) for p in args.pages.split(",")]

    render_all()

    failed = []
    for p in pages:
        r = subprocess.run([sys.executable, str(ROOT / "pipeline" / "02_geometry.py"),
                            "--page", str(p)], capture_output=True, text=True)
        if r.returncode != 0:
            failed.append(p)
        if not crop_textband(p):
            failed.append(p)
    print(json.dumps({"pages": len(pages), "failed": failed}))


if __name__ == "__main__":
    main()
