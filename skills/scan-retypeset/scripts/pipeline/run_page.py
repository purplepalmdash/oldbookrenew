#!/usr/bin/env python3
"""run_page: full pipeline for one page (after MCP results are saved).

Prereqs: data/layout/pNNN.geo.json (02_geometry),
         data/vl/pNNN.json (MCP parse_document result),
         data/ocr/pNNN.json (MCP ocr_image result).

Steps: 03_enhance -> 04_merge -> 05_typeset -> 06_verify -> preview compare.
Usage: .venv/bin/python pipeline/run_page.py --page 10
"""
import argparse
import subprocess
import sys
from pathlib import Path

import cv2
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.common import ROOT  # noqa: E402

PY = sys.executable


def sh(*cmd):
    r = subprocess.run(list(cmd), capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.returncode != 0:
        print(f"FAILED: {cmd}\n{r.stderr}", file=sys.stderr)
        raise SystemExit(r.returncode)


def make_preview(page: int):
    """Side-by-side: original render | output render."""
    orig = Image.open(ROOT / "data" / "pages" / f"p{page:03d}.png")
    r = subprocess.run(["pdftoppm", "-png", "-r", "150",
                        str(ROOT / "out" / "pages" / f"p{page:03d}.pdf"),
                        f"/tmp/prev_p{page:03d}"], capture_output=True)
    if r.returncode != 0:
        print("preview render failed:", r.stderr, file=sys.stderr)
        return
    import glob
    files = glob.glob(f"/tmp/prev_p{page:03d}*.png")
    if not files:
        print("preview: no render produced", file=sys.stderr)
        return
    out = Image.open(sorted(files)[0])
    h = 1100
    o2 = orig.resize((int(orig.width * h / orig.height), h))
    o2 = o2.convert("RGB")
    o2 = cv2.cvtColor(np.array(o2), cv2.COLOR_RGB2BGR)
    out2 = out.convert("RGB")
    out2 = out2.resize((int(out2.width * h / out2.height), h))
    out2 = cv2.cvtColor(np.array(out2), cv2.COLOR_RGB2BGR)
    gap = Image.new("RGB", (20, h), (200, 200, 200))
    combo = Image.new("RGB", (o2.shape[1] + 20 + out2.shape[1], h))
    combo.paste(Image.fromarray(cv2.cvtColor(o2, cv2.COLOR_BGR2RGB)), (0, 0))
    combo.paste(Image.fromarray(cv2.cvtColor(out2, cv2.COLOR_BGR2RGB)), (o2.shape[1] + 20, 0))
    combo.save(ROOT / "out" / "preview" / f"p{page:03d}_compare.png")
    print("preview:", ROOT / "out" / "preview" / f"p{page:03d}_compare.png")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", type=int, required=True)
    args = ap.parse_args()
    page = args.page
    base = Path(__file__).resolve().parent
    sh(PY, str(base / "03_enhance.py"), "--page", str(page))
    sh(PY, str(base / "04_merge.py"), "--page", str(page))
    sh(PY, str(base / "05_typeset.py"), "--page", str(page))
    sh(PY, str(base / "06_verify.py"), "--page", str(page))
    make_preview(page)


if __name__ == "__main__":
    import numpy as np  # noqa: F401
    main()
