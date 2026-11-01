#!/usr/bin/env python3
"""03_enhance: crop figure from deskewed render -> print-ready image.

Two variants (config `enhance.variant`):

  bw_clear (default)
    Grayscale + illumination-division whitening + level stretch +
    unsharp mask. Keeps soft halftone texture; "cleaned scan" look.

  vector
    The "freshly printed" high-contrast look (black-and-white distinct,
    minimal ink, sharp at any zoom):
      1. luma of the figure crop
      2. page-level illumination surface fit from paper pixels
         (quadratic 2D poly; Gaussian-blur fallback) — deliberately
         PAGE-level, not local division: local division flattens large
         halftone tone regions (a gray rock reads white after it),
      3. two-scale binarization:
           fine   = flat < fine_thr          (line art, isolated dots)
           fill   = blur(fine, k) < coarse_thr (dense tone regions:
                  halftone shading is solidified to flat black)
         dense shading -> solid black (white cracks preserved), sparse
         stippling -> discrete dots, line art stays sharp,
      4. potrace -> SVG vector master (data/images/pNNN.svg),
      5. rsvg-convert rasterizes the SVG at 72*raster_scale dpi,
         composited on white, saved as 1-bit pNNN_bw.png.
    Fallbacks: missing potrace/rsvg degrades gracefully to a direct
    nearest-neighbour raster of the binary (still 1-bit).

Usage: .venv/bin/python pipeline/03_enhance.py --page 10
Reads  data/pages/p010.png, data/pages/p010_deskew.png, data/layout/p010.geo.json
Writes data/images/p010_bw.png      (the figure, consumed by 05_typeset)
       data/images/p010.svg         (vector master, vector variant)
       data/layout/p010.geo.json   (adds glyph_heights, enhance meta)
"""
import argparse
import json
import shutil
import subprocess
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from lib.common import (ENHANCE, MARGIN_STRIP, ROOT, WATERMARK_BOXES,
                        is_color_image, load_json, save_json)


def crop_figure(deskew: Image.Image, frame_bbox: list) -> Image.Image:
    x0, y0, x1, y1 = [int(v) for v in frame_bbox]
    w, h = deskew.size
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    return deskew.crop((x0, y0, x1, y1))


def page_illumination(gray: np.ndarray):
    """Estimate the page-level paper illumination surface (uint8 scale).

    The scan's paper is not evenly lit (top/bottom vignetting, yellowed
    patches). We fit a quadratic surface in (x, y) to pixels that are
    bright AND locally smooth (paper, not ink); a large-Gaussian blur of
    the raw gray is the fallback when paper evidence is thin.

    Returns (illum, method).

    NOTE: this is deliberately a PAGE-scale estimate. A local
    division-by-illumination (blur kernel ~ page/20) normalizes away large
    halftone tone regions — a dense gray rock (raw ~110) divided by its
    own local dark mean reads as white. Page-level correction removes the
    vignetting while keeping tone regions dark, which the two-scale
    binarizer below needs to solidify them.
    """
    H, W = gray.shape
    mean = cv2.blur(gray, (5, 5))
    locstd = np.sqrt(np.clip(cv2.blur((gray - mean) ** 2, (5, 5)), 0, None))
    paper = (gray > 195) & (locstd < 15)
    if float(paper.mean()) >= 0.02:
        ys, xs = np.nonzero(paper)
        step = max(1, len(ys) // 200000)
        xs2, ys2, v2 = xs[::step], ys[::step], gray[ys[::step], xs[::step]]
        X = np.stack([xs2 / 1000.0, ys2 / 1000.0, (xs2 / 1000.0) ** 2,
                       (ys2 / 1000.0) ** 2,
                       (xs2 / 1000.0) * (ys2 / 1000.0) * 0.5,
                       np.ones_like(xs2, dtype=np.float64)], axis=1)
        try:
            coef, *_ = np.linalg.lstsq(X, v2.astype(np.float64), rcond=None)
        except np.linalg.LinAlgError:
            coef = None
        if coef is not None:
            resid = v2 - (X @ coef)
            if float(np.std(resid)) <= 18.0:
                xx, yy = np.meshgrid(np.arange(W) / 1000.0,
                                     np.arange(H) / 1000.0)
                I = (coef[0] * xx + coef[1] * yy + coef[2] * xx ** 2
                     + coef[3] * yy ** 2 + coef[4] * xx * yy + coef[5])
                return np.clip(I, 100, 260).astype(np.uint8), "poly2"
    sigma = max(30.0, min(H, W) / 10.0)
    I = cv2.GaussianBlur(gray, (0, 0), sigma)
    return np.clip(I, 40, 260).astype(np.uint8), "gauss"


def vectorize(fig: Image.Image, box: list, page_gray: np.ndarray,
              page: int, vcfg: dict):
    """Two-scale binarize + potrace + high-res raster. Returns meta dict."""
    x0, y0 = box[0], box[1]
    rgb = np.array(fig.convert("RGB")).astype(np.float32)
    gray = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])
    I, method = page_illumination(page_gray)
    crop_I = I[y0:y0 + fig.height, x0:x0 + fig.width].astype(np.float32)
    crop_I = np.maximum(crop_I, 40.0)
    flat = np.clip(gray * 255.0 / crop_I, 0, 255).astype(np.uint8)

    # ---- detail-preserving local binarization (v2, "newly printed") ----
    # The v1 two-scale pass (flat<fine_thr | blur(flat)<coarse_thr) judged
    # every pixel in ONE direction (dark = ink). That lost (a) light lines
    # carved into dark tone (white wavy lines on dark mountains, crack
    # highlights on dark rock) and (b) faint thin lines whose gray sits
    # above fine_thr (water waves, cliff cracks, plant fronds); and its
    # coarse fill solidified too many mid-tone regions ("too black").
    #
    # v2 judges each pixel against its LOCAL tone (a wide mean of the
    # illumination-flattened gray — lighting already removed):
    #   tone   = blur(flat, tone_kernel)
    #   solid  = tone < T_solid      # dense tone -> flat black (new-book
    #                                # dark tone prints as solid ink)
    #   bright = flat>tone+bright_dev & tone<bright_tone
    #                                # light detail carved into dark tone
    #                                # (white wavy lines / crack highlights)
    #   ink    = flat < T_dark       # dark line/dot on light paper
    #            | (flat < tone-dev) & (tone >= T_solid)
    #                                # locally darker than its own tone:
    #                                # halftone dots / lines of a mid-tone
    #                                # region — kept as a dot/line pattern,
    #                                # NOT solidified
    #   bw     = (ink | solid) & ~bright
    # Ink coverage then tracks the original ink-equivalent coverage
    # (measured per page by 06_verify `ink_coverage`), which is what makes
    # the result read as the same picture, freshly printed.
    T_dark = float(vcfg.get("T_dark", 190))
    T_solid = float(vcfg.get("T_solid", 150))
    dev = float(vcfg.get("dev", 25))
    bright_dev = float(vcfg.get("bright_dev", 45))
    bright_tone = float(vcfg.get("bright_tone", 185))
    tone_kernel = int(vcfg.get("tone_kernel", 31))
    speckle_px = int(vcfg.get("speckle_px", 10))
    close = int(vcfg.get("close", 1))

    fi = flat.astype(np.int16)
    tone = cv2.blur(fi, (tone_kernel, tone_kernel))
    solid = tone < T_solid
    bright = (fi > tone + bright_dev) & (tone < bright_tone)
    ink = (fi < T_dark) | ((fi < tone - dev) & (tone >= T_solid))
    bw = (ink | solid) & ~bright
    b = (~bw).astype(np.uint8) * 255  # 0 = ink (black)

    # 1-px closing reconnects thin 1px line segments that the threshold
    # fragments (water waves would otherwise render as dashes). A 3x3 cross
    # is conservative: it does not blob halftone dots together.
    if close:
        k = cv2.getStructuringElement(
            cv2.MORPH_CROSS, (close * 2 + 1, close * 2 + 1))
        b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, k)
    # isolated ink specks (scan noise) smaller than speckle_px are removed.
    # NOTE: deliberately NO median pre-filter — median(3) erases 1-px lines
    # (the median of a 1px line's 3x3 neighbourhood is paper).
    if speckle_px:
        n, lab, stats, _ = cv2.connectedComponentsWithStats(b, 8)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] < speckle_px:
                b[lab == i] = 255
    bw = b

    meta = {"method": method, "T_dark": T_dark, "T_solid": T_solid,
            "dev": dev, "bright_dev": bright_dev,
            "bright_tone": bright_tone, "tone_kernel": tone_kernel,
            "speckle_px": speckle_px, "close": close,
            "ink_frac": round(float((bw == 0).mean()), 4)}

    (ROOT / "data" / "images").mkdir(parents=True, exist_ok=True)
    img_path = ROOT / "data" / "images" / f"p{page:03d}_bw.png"

    # vector master (optional; degrades to direct raster if potrace missing)
    svg_path = ROOT / "data" / "images" / f"p{page:03d}.svg"
    have_potrace = shutil.which("potrace") is not None
    have_rsvg = shutil.which("rsvg-convert") is not None
    if have_potrace:
        pnm = ROOT / "data" / "images" / f"p{page:03d}_vec.pnm"
        Image.fromarray(bw, "L").save(pnm)
        t = float(vcfg.get("potrace_t", 2))
        a = float(vcfg.get("potrace_alpha", 1.0))
        o = float(vcfg.get("potrace_opt", 1.0))
        r = subprocess.run(
            ["potrace", str(pnm), "-s", "-o", str(svg_path),
             "-t", str(t), "-a", str(a), "-O", str(o)],
            capture_output=True, text=True)
        if r.returncode == 0 and svg_path.exists():
            meta["potrace"] = f"-t {t} -a {a} -O {o}"
        else:
            svg_path = None
            meta["potrace_error"] = r.stderr.strip()[:200]
    if svg_path is not None and have_rsvg:
        scale = float(vcfg.get("raster_scale", 2.0))
        dpi = int(round(72 * scale))
        # potrace emits width/height in pt (1/72in), so -d 72 is a 1:1
        # raster of the source; 72*scale oversamples. rsvg writes an RGBA
        # PNG with a TRANSPARENT background — composite on white before
        # converting to L (a naive .convert('L') turns the background
        # black).
        r = subprocess.run(
            ["rsvg-convert", "-d", str(dpi), "-p", str(dpi),
             "-o", str(img_path), str(svg_path)],
            capture_output=True, text=True)
        if r.returncode == 0:
            im = Image.open(str(img_path)).convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            out = Image.alpha_composite(bg, im).convert("L")
            out.convert("1").save(str(img_path))
            meta["raster"] = f"rsvg {dpi}dpi 1-bit"
            return meta

    # direct raster fallback
    scale = float(vcfg.get("raster_scale", 2.0))
    im = Image.fromarray(bw, "L")
    if scale > 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)),
                       Image.NEAREST)
    im.convert("1").save(str(img_path))
    meta["raster"] = f"direct-nearest {scale:g}x 1-bit"
    return meta


def crop_margin_strip(deskew: Image.Image, geo: dict, side: str = "right"):
    """A page-margin design strip (the book's printed running design: the
    full-height vertical edge line, the running-title tab, the notch bar,
    the red seal — or a small emblem such as the 刘秀 horse mark in the
    LEFT margin of 刘秀 下册). Cropped from the color deskewed render on
    the given side, starting just past the figure's outer ink extent
    (zero overlap with the figure so the grayscale figure QA is
    unaffected) out to the page edge. The background is whitened
    per-channel (yellow cast removed) while the red seal keeps its
    color. Blank margins return (None, crop_box)."""
    bbox = geo.get("frame_ink_bbox", geo["frame"]["bbox"])
    w, h = deskew.size
    rgb = np.array(deskew.convert("RGB")).astype(np.float32)
    if side == "left":
        x1 = int(bbox[0]) - 2
        if x1 <= 0:
            return None, [0, 0, 0, h]
        strip = rgb[:, :x1]
        box = [0, 0, x1, h]
    else:
        x0 = int(bbox[2]) + 2
        strip = rgb[:, max(0, x0):]
        box = [max(0, x0), 0, w, h]
    # auto-detect: skip blank margins (no design, no ink and no saturated
    # color) so the feature is generic across books and sides.
    # Two keep-signals (either one keeps the strip):
    #  (a) area rule: >= 0.5% dark or >= 0.1% reddish (the historic rule);
    #  (b) thin full-height edge line: a narrow run (<= 10 px) of columns
    #      that are dark for >= 80% of the strip height. A 1-2 px printed
    #      page-edge line on a wide margin is only ~0.3-0.5% of the strip
    #      AREA and slips under (a) — detect it by column projection
    #      rather than scaling the area threshold down, which would
    #      false-positive on speckle in wide blank margins.
    g = strip.mean(axis=2)
    reddish = (strip[..., 0].astype(int) - strip[..., 1].astype(int) > 40)
    dark = g < 140
    col_dark = dark.mean(axis=0)
    thin_line, run = False, 0
    for c in range(strip.shape[1] + 1):  # +1: sentinel closes last run
        is_line = c < strip.shape[1] and col_dark[c] >= 0.80
        if is_line:
            run += 1
        else:
            if 1 <= run <= 10:
                thin_line = True
            run = 0
    if (dark.mean() < 0.005 and reddish.mean() < 0.001 and not thin_line):
        return None, box
    k = max(51, (strip.shape[0] // 20) | 1)
    out = np.empty_like(strip)
    for i in range(3):
        ch = strip[..., i]
        illum = cv2.blur(ch, (k, k))
        illum = np.maximum(illum, 40.0)
        white = np.clip(ch * 255.0 / illum, 0, 255)
        lo = np.percentile(white, 1.0)
        hi = np.percentile(white, 99.8)
        if hi - lo > 20:
            white = (white - lo) * (255.0 / (hi - lo))
        out[..., i] = np.clip(white, 0, 255)
    return Image.fromarray(out.astype(np.uint8), mode="RGB"), box


def enhance(img: Image.Image, contrast_boost: float = 1.15, sharpen: bool = True) -> Image.Image:
    """Grayscale + illumination-division whitening + level stretch.

    - gray: Luma
    - illumination estimate: large-kernel blur (paper background)
    - white = gray / illumination * 255  (removes yellow tint & uneven light)
    - level stretch: 1st percentile ink -> 0, 99.8th paper -> 255
    """
    rgb = np.array(img.convert("RGB")).astype(np.float32)
    gray = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])
    k = max(51, (min(gray.shape) // 20) | 1)
    illum = cv2.blur(gray, (k, k))
    illum = np.maximum(illum, 40.0)
    white = np.clip(gray * 255.0 / illum, 0, 255)
    lo = np.percentile(white, 1.0)
    hi = np.percentile(white, 99.8)
    if hi - lo > 20:
        white = (white - lo) * (255.0 / (hi - lo))
    white = np.clip(white, 0, 255)
    if contrast_boost != 1.0:
        mid = 255.0 / 2.0
        white = mid + (white - mid) * contrast_boost
        white = np.clip(white, 0, 255)
    out = Image.fromarray(white.astype(np.uint8), mode="L")
    if sharpen:
        out = out.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))
    return out


def binarize(img: Image.Image) -> Image.Image:
    gray = np.array(img.convert("L"))
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return Image.fromarray(bw, mode="L")


def measure_glyph_heights(deskew: Image.Image, geo: dict) -> list:
    """Ink height (px) per text band, for font-size calibration."""
    gray = np.array(deskew.convert("L"))
    out = []
    for band in geo.get("text_bands", []):
        x0, y0, x1, y1 = band["bbox"]
        seg = gray[max(0, y0 - 4):y1 + 4, max(0, x0):min(x1 + 4, gray.shape[1])]
        if seg.size == 0:
            out.append(0)
            continue
        _, bw = cv2.threshold(seg, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        rows = (bw > 0).sum(axis=1)
        ys = np.where(rows > 3)[0]
        out.append(int(ys[-1] - ys[0] + 1) if len(ys) else 0)
    return out


def restamp_frame(bw: Image.Image, fill: int = 40) -> Image.Image:
    """Per-edge frame restamp: some scans (e.g. p94) have faded/broken frame
    lines that the whitening washes out; the original page always carries a
    thin dark rectangle around the picture. Only restamp edges that are
    missing/faded AFTER enhancement, so pages with a crisp frame do not get
    a doubled border.
    """
    a = np.array(bw.convert("L"))
    h, w = a.shape
    t = 8
    edges = {
        "top": float(a[:t, :].mean()),
        "bottom": float(a[h - t:, :].mean()),
        "left": float(a[:, :t].mean()),
        "right": float(a[:, w - t:].mean()),
    }
    if min(edges.values()) < 150 and max(edges.values()) < 200:
        return bw  # a crisp frame survived; keep it as-is
    if bw.mode != "L":
        bw = bw.convert("L")
    dr = ImageDraw.Draw(bw)
    t2 = max(4, w // 500)
    if edges["top"] > 150:
        dr.line([(0, 0), (w - 1, 0)], fill=fill, width=t2)
    if edges["bottom"] > 150:
        dr.line([(0, h - 1), (w - 1, h - 1)], fill=fill, width=t2)
    if edges["left"] > 150:
        dr.line([(0, 0), (0, h - 1)], fill=fill, width=t2)
    if edges["right"] > 150:
        dr.line([(w - 1, 0), (w - 1, h - 1)], fill=fill, width=t2)
    return bw


def whiteout_watermarks(im: Image.Image, boxes_norm: list) -> Image.Image:
    """Paint later-generation watermarks (scan-time horse/slogan overlays) out
    of the full deskewed page BEFORE figure / margin-strip crops, so neither
    crop carries the watermark. Each box (fractions of render W/H) is filled
    with the median color of a 24 px ring around it, then any dark VERTICAL
    LINE column that crosses the box (e.g. the printed left page-edge line,
    which is part of the original page and must be kept) is restored by
    tiling its segment taken from just above the box top.
    """
    if not boxes_norm:
        return im
    a = np.array(im.convert("RGB")).astype(np.float32)
    H, W = a.shape[:2]
    for (nx0, ny0, nx1, ny1) in boxes_norm:
        x0, y0 = max(0, int(nx0 * W)), max(0, int(ny0 * H))
        x1, y1 = min(W, int(nx1 * W)), min(H, int(ny1 * H))
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        r = 24
        rings = [a[max(0, y0 - r):y0, x0:x1], a[y1:min(H, y1 + r), x0:x1],
                 a[y0:y1, max(0, x0 - r):x0], a[y0:y1, x1:min(W, x1 + r)]]
        pix = np.concatenate([p.reshape(-1, 3) for p in rings if p.size], axis=0)
        fill = np.median(pix, axis=0) if len(pix) else np.array([250.0] * 3)
        a[y0:y1, x0:x1] = fill
        if y0 > 120:  # restore vertical line columns crossing the box
            above = a[max(0, y0 - 120):y0 - 4, x0:x1].mean(axis=(0, 2))
            dark_cols = np.nonzero(above < 0.85 * float(np.median(above)))[0]
            for x in dark_cols:
                seg = a[max(0, y0 - 60):y0 - 4, x0 + x].mean(axis=0)
                a[y0:y1, x0 + x] = seg
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", type=int, required=True)
    args = ap.parse_args()
    page = args.page
    geo = load_json(ROOT / "data" / "layout" / f"p{page:03d}.geo.json")
    deskew = Image.open(ROOT / "data" / "pages" / f"p{page:03d}_deskew.png")
    deskew = whiteout_watermarks(deskew, WATERMARK_BOXES)

    # crop at the frame's outer ink extent when known: the fitted bbox can
    # sit inside a thick/blurry frame stroke, and clipping the stroke makes
    # the output frame look thinner than the original (and biases the
    # measured figure/text gap)
    crop_box = geo.get("frame_ink_bbox") or geo["frame"]["bbox"]
    fig = crop_figure(deskew, crop_box)
    (ROOT / "data" / "images").mkdir(parents=True, exist_ok=True)
    variant = str(ENHANCE.get("variant", "bw_clear"))

    # Color-figure rule: a color illustration is placed 1:1 in its
    # original color — no grayscale, no whitening, no stretch, no
    # sharpen, no binarize. (Default variant only; an explicit
    # `variant: vector` is a deliberate B&W choice and keeps its path.)
    # `color_auto` overrides the per-figure detection:
    #   "auto" (default) — is_color_image (p99 chroma > 30)
    #   "off"            — force the grayscale path. REQUIRED for heavily
    #                      YELLOWED gray scans: the yellow cast makes
    #                      paper chroma ~60-70 (e.g. 刘秀 下册 paper
    #                      RGB ≈ (236,220,167) -> chroma ≈ 70 > 30), so
    #                      every figure would be mis-routed to
    #                      color_as_is and keep its yellow.
    #   "on"             — force the color path (subtle color below the
    #                      detection threshold).
    color_mode = str(ENHANCE.get("color_auto", "auto"))
    if variant == "vector" or color_mode == "off":
        color_fig = False
    elif color_mode == "on":
        color_fig = True
    else:
        color_fig = is_color_image(fig)

    if color_fig:
        fig.save(ROOT / "data" / "images" / f"p{page:03d}_bw.png")
        geo["enhance_variant"] = "color_as_is"
        geo.pop("vector_meta", None)
        meta = None
    elif variant == "vector":
        page_gray = np.array(deskew.convert("L"))
        meta = vectorize(fig, crop_box, page_gray, page,
                         ENHANCE.get("vector", {}))
        bw = Image.open(str(ROOT / "data" / "images" /
                             f"p{page:03d}_bw.png")).convert("L")
        # fill=0 keeps the figure strictly 1-bit (the vector_binary QA)
        bw = restamp_frame(bw, fill=0)
        bw.convert("1").save(str(ROOT / "data" / "images" / f"p{page:03d}_bw.png"))
        geo["enhance_variant"] = "vector"
        geo["vector_meta"] = meta
    else:
        boost = float(ENHANCE.get("contrast_boost", 1.15))
        bw = enhance(fig, contrast_boost=boost, sharpen=True)
        bw = restamp_frame(bw)
        bw.save(ROOT / "data" / "images" / f"p{page:03d}_bw.png")
        bin_ = binarize(fig)
        bin_.save(ROOT / "data" / "images" / f"p{page:03d}_binary.png")
        geo["enhance_variant"] = "bw_clear"
        meta = None

    # margin design strips — right (running title / edge line / red seal)
    # and left (e.g. the 刘秀 horse emblem); both auto-detected, blank
    # margins are skipped
    if MARGIN_STRIP:
        for side, key, tmpl in (("right", "margin_crop_box",
                                 "p{page:03d}_margin.png"),
                                ("left", "margin_crop_box_left",
                                 "p{page:03d}_margin_left.png")):
            try:
                margin, mbox = crop_margin_strip(deskew, geo, side=side)
                if margin is not None:
                    margin.save(ROOT / "data" / "images" /
                                tmpl.format(page=page))
                    geo[key] = list(mbox)
                else:
                    # blank margin: drop any STALE strip file from a
                    # previous run, or 05_typeset would redraw the old
                    # one (e.g. a watermark that is now painted out)
                    stale = ROOT / "data" / "images" / tmpl.format(page=page)
                    if stale.exists():
                        stale.unlink()
                    geo.pop(key, None)
            except Exception as e:
                print(f"{side} margin strip skipped:", e, file=sys.stderr)

    geo["glyph_heights_px"] = measure_glyph_heights(deskew, geo)
    geo["figure_crop"] = list(fig.size)
    geo["figure_crop_box"] = list(crop_box)
    save_json(geo, ROOT / "data" / "layout" / f"p{page:03d}.geo.json")
    print(json.dumps({"page": page, "variant": variant,
                      "figure": list(fig.size),
                      "vector": meta,
                      "glyph_heights_px": geo["glyph_heights_px"]}))


if __name__ == "__main__":
    main()
