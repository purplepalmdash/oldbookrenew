"""S0: render all pages at 50 dpi and classify color vs monochrome.

Writes work/manifest.csv. The HSV thresholds are a starting point for
typical scanned books - for other material (color-plate books, blueprints,
old newsprint) check flagged rows visually and tune the two thresholds.

After this step: visually confirm the manifest (agent views work/render50/)
and set keep_pages / drop_pages / no_frame_pages in book.json.

Usage (from src/):  python s0_classify.py
"""
from __future__ import annotations

import csv
import subprocess
import sys

import numpy as np
from PIL import Image

from bookcfg import WORK, ensure_dirs, src_pdf

R50 = WORK / "render50"


def render_all() -> int:
    subprocess.run(
        ["pdftoppm", "-png", "-r", "50", str(src_pdf().resolve()),
         str(R50 / "pg")],
        check=True,
    )
    return len(sorted(R50.glob("pg-*.png")))


def metrics(img: Image.Image) -> dict:
    hsv = np.asarray(img.convert("HSV"), dtype=np.float32)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    dark = v < 200
    return {
        "mean_sat": round(float(s.mean()), 3),
        "sat_dark_mean": round(float(s[dark].mean()) if dark.any() else 0.0, 3),
        "frac_strong_sat": round(float(((s > 115) & (v < 240)).mean()), 4),
        "frac_dark_sat": round(float(((s > 130) & (v < 200)).mean()), 4),
    }


def main() -> None:
    ensure_dirs()
    n = render_all()
    files = sorted(R50.glob("pg-*.png"))
    rows = []
    for f in files:
        m = metrics(Image.open(f))
        is_color = m["frac_dark_sat"] > 0.05 or m["frac_strong_sat"] > 0.10
        rows.append({
            "page": int(f.stem.split("-")[1]),
            **m,
            "class": "color" if is_color else "mono",
        })
    with open(WORK / "manifest.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        flag = "  <== COLOR" if r["class"] == "color" else ""
        print(
            f"p{r['page']:>3} sat={r['mean_sat']:.3f} "
            f"dark_sat={r['frac_dark_sat']:.4f} "
            f"strong={r['frac_strong_sat']:.4f}{flag}")
    print(f"{n} pages -> {WORK / 'manifest.csv'}")
    print("Next: confirm manifest visually and set keep_pages / drop_pages / "
          "no_frame_pages in book.json.")


if __name__ == "__main__":
    sys.exit(main())
