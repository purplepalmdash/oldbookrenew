"""S3: whiten illustration crops (kill yellow background, keep tone shading).

For each image bbox in NNN.json:
  crop from render
  -> (optional color pre-pass: remove red/blue scanner ink, e.g. title pages)
  -> grayscale float
  -> background estimate (MaxFilter) + division (flatten illumination)
  -> tone smoothing (halftone dots) + tone LUT (paper -> pure white)
  -> border flood-fill forces outer paper to 255
  -> save NNN_img{i}.png (grayscale)

Params precedence (low -> high):
  code DEFAULT_PARAMS < book.json "whiten_params" < work/params.json (frozen)
  < book.json "page_whiten"["N"]

Calibrate on the baseline page against the example (see the skill's
references/calibration.md), then FREEZE into work/params.json.
No batch before the baseline page is user-approved.

Usage (from src/):
  python s3_whiten.py 4                        # process page 4 images
  python s3_whiten.py 4 --img 0                # only first image
  python s3_whiten.py --pxstats work/pages/004_img0.png [X0 Y0 X1 Y1]
"""
from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from bookcfg import (PAGES, cfg, ensure_dirs, load_page_json, load_params,
                     pnn, render_name)


def make_lut(anchors: list[list[float]]) -> np.ndarray:
    pts = sorted(anchors)
    xs = np.array([p[0] for p in pts], dtype=np.float64)
    ys = np.array([p[1] for p in pts], dtype=np.float64)
    lut = np.interp(np.linspace(0, 1, 256), xs, ys)
    return np.clip(lut, 0, 1)


DEFAULT_PARAMS = {
    "max_kernel": 25,      # MaxFilter: local paper level (bright gaps)
    "tone_radius": 7,      # BoxBlur radius for region tone (halftone average)
    "tex_gain_dark": 1.2,  # texture gain for ink pixels (keeps lines sharp)
    "tex_gain_light": 0.6, # texture gain for halftone/paper
    "dark_cut": 0.60,      # flat below this = ink (line/dot core)
    "paper_seed": 0.975,   # border-connected white zones -> 255
    # LUT on region tone. Anchor meaning (calibrate against your example):
    #   paper & light stipple (0.93-1.0) -> 0.99-1.0 (MUST reach pure white)
    #   mid halftone (0.75) -> ~0.6 ; dark woodcut fill (0.68) -> ~0.45
    #   ink cores (<=0.4) -> 0.02-0.15 (lines near black)
    "lut": [[0.00, 0.02], [0.40, 0.15], [0.55, 0.30], [0.68, 0.45],
            [0.75, 0.62], [0.85, 0.90], [0.93, 0.99], [1.00, 1.00]],
    "remove_red": False,
    "remove_blue": False,
}


def page_params(n: int) -> dict:
    p = dict(DEFAULT_PARAMS)
    p.update(cfg().get("whiten_params", {}))
    p.update(load_params())                    # frozen calibration wins
    p.update(cfg().get("page_whiten", {}).get(str(n), {}))
    return p


def flood_paper_white(out: np.ndarray, seed_thr: float) -> np.ndarray:
    """Everything connected to the border and lighter than seed_thr -> 255.
    BFS at half resolution (fast, 2px steps still blocked by thin lines),
    then vectorized 1px refinement."""
    H, W = out.shape
    light = out >= seed_thr
    hs, ws = H // 2, W // 2
    l = light[:hs * 2, :ws * 2].reshape(hs, 2, ws, 2).max(axis=(1, 3))
    mask = np.zeros((hs, ws), bool)
    q = deque()
    for x in range(ws):
        if l[0, x]:
            mask[0, x] = True
            q.append((x, 0))
        if l[hs - 1, x]:
            mask[hs - 1, x] = True
            q.append((x, hs - 1))
    for y in range(1, hs - 1):
        if l[y, 0]:
            mask[y, 0] = True
            q.append((0, y))
        if l[y, ws - 1]:
            mask[y, ws - 1] = True
            q.append((ws - 1, y))
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < ws and 0 <= ny < hs and l[ny, nx] \
                    and not mask[ny, nx]:
                mask[ny, nx] = True
                q.append((nx, ny))
    full = np.zeros((H, W), bool)
    full[:hs * 2, :ws * 2] = np.kron(mask, np.ones((2, 2), bool))
    for _ in range(6):
        nbr = (np.roll(full, 1, 0) | np.roll(full, -1, 0)
               | np.roll(full, 1, 1) | np.roll(full, -1, 1))
        new = nbr & light & ~full
        if not new.any():
            break
        full |= new
    out[full] = 1.0
    return out


def remove_colored_ink(rgb: Image.Image, remove_red: bool,
                       remove_blue: bool) -> Image.Image:
    """Blank scanner-added red/blue ink (disclaimer lines, watermarks)."""
    if not (remove_red or remove_blue):
        return rgb
    a = np.asarray(rgb, dtype=np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    m = np.zeros(r.shape, bool)
    if remove_red:
        m |= (r - g > 45) & (r > 60)
    if remove_blue:
        m |= (b - r > 25) & (b > 60)
    a[m] = 255.0
    return Image.fromarray(a.astype(np.uint8))


def whiten_crop(gray: Image.Image, params: dict) -> Image.Image:
    img = np.asarray(gray, dtype=np.float64) / 255.0
    k = int(params["max_kernel"]) | 1
    bg = np.asarray(gray.filter(ImageFilter.MaxFilter(k)),
                    dtype=np.float64) / 255.0
    flat = np.clip(img / np.maximum(bg, 0.10), 0, 1)

    r = int(params["tone_radius"])
    tone = np.asarray(Image.fromarray((flat * 255).astype(np.uint8))
                      .filter(ImageFilter.BoxBlur(r)), dtype=np.float64) / 255.0

    lut = make_lut(params["lut"])
    base = lut[(tone * 255).clip(0, 255).astype(np.uint8)]
    dark_cut = float(params["dark_cut"])
    gain = np.where(flat < dark_cut, float(params["tex_gain_dark"]),
                    float(params["tex_gain_light"]))
    out = np.clip(base + (flat - tone) * gain, 0, 1)
    out = flood_paper_white(out, float(params["paper_seed"]))
    out[out >= 0.995] = 1.0
    o8 = (out * 255).round().astype(np.uint8)
    print(f"    stats: min={o8.min()} p5={np.percentile(o8, 5):.0f} "
          f"p50={np.percentile(o8, 50):.0f} p95={np.percentile(o8, 95):.0f} "
          f"frac255={(o8 == 255).mean():.2f}")
    return Image.fromarray(o8)


def process_page(n: int, only_img: int | None = None) -> None:
    ensure_dirs()
    obj = load_page_json(n)
    p = page_params(n)
    if p.get("remove_red") or p.get("remove_blue"):
        from s2_segment import render_page
        px = PAGES / render_name(n, color=True)
        if not px.exists():
            render_page(n, color=True)
        rgb = Image.open(px).convert("RGB")
        rgb = remove_colored_ink(rgb, p.get("remove_red", False),
                                 p.get("remove_blue", False))
        base_img = rgb.convert("L")
    else:
        base_img = Image.open(PAGES / render_name(n, color=False)).convert("L")
    for img in obj["images"]:
        if only_img is not None and img["id"] != only_img:
            continue
        x0, y0, x1, y1 = img["bbox"]
        crop = base_img.crop((x0, y0, x1, y1))
        out = whiten_crop(crop, p)
        out.save(PAGES / f"{pnn(n)}_img{img['id']}.png")
        print(f"page {n} img{img['id']} whitened -> "
              f"{pnn(n)}_img{img['id']}.png ({x1 - x0}x{y1 - y0})")


def pxstats(argv: list[str]) -> None:
    """Print pixel stats of a PNG (optionally a sub-rect) - for LUT calibration."""
    path = Path(argv[0])
    a = np.asarray(Image.open(path).convert("L"), dtype=np.float32)
    if len(argv) == 5:
        x0, y0, x1, y1 = map(int, argv[1:5])
        a = a[y0:y1, x0:x1]
    print(f"{path}: min={a.min():.0f} p5={np.percentile(a, 5):.0f} "
          f"p50={np.percentile(a, 50):.0f} p95={np.percentile(a, 95):.0f} "
          f"max={a.max():.0f} frac255={(a >= 255).mean():.3f} "
          f"size={a.shape[1]}x{a.shape[0]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", type=int, nargs="*")
    ap.add_argument("--img", type=int, default=None)
    ap.add_argument("--pxstats", nargs="+", default=None,
                    metavar="PNG [X0 Y0 X1 Y1]")
    a = ap.parse_args()
    if a.pxstats:
        pxstats(a.pxstats)
        return
    if not a.pages:
        ap.error("pages or --pxstats")
    for n in a.pages:
        process_page(n, only_img=a.img)


if __name__ == "__main__":
    sys.exit(main())
