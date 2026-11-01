#!/usr/bin/env python3
"""07_special: typeset special (non-panel) pages from config.

Config (pipeline/config.json):
  "special_pages": {
    "<page>": {
      "handler": "cover_as_is" | "retype" | "title_page" | "colophon",
      ...handler-specific fields, see references/config.md
    }
  }

- cover_as_is: original render kept as-is, centered fit on the output page
  (e.g. color cover). Optional `whiteout_box` removes a scanner watermark
  from a plain background region.
- retype:      full-page white retype. Either an explicit `layout` line list
  (recommended: agent writes it from the MCP OCR dump) or `ocr`-driven
  title+body wrapping. Optional `images` (crops placed at fixed positions).
- title_page:  vertical big title + optional emblem crop + credit columns
- colophon:    boxed 2-column publication-info layout (optional colored
  frame, header lines above the box, footer below)

Usage: .venv/bin/python pipeline/07_special.py [all | <page> ...]
Writes out/pages/pNNN.pdf
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.common import (A4_H, A4_W, CONFIG, FONT_NAME, FONT_PATH, MARGIN,  # noqa: E402
                        RENDER_DPI, ROOT, load_json)

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))
SPECIAL = CONFIG.get("special_pages", {})


def _page_canvas(n: int) -> canvas.Canvas:
    out = ROOT / "out" / "pages" / f"p{n:03d}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    return canvas.Canvas(str(out), pagesize=(A4_W, A4_H))


def _crop_render(page: int, box, color: bool = True,
                 force_gray: bool = False, erase_ink: list = None,
                 erase_tmpl: dict = None) -> Image.Image:
    """Crop a region (normalized page fractions x0,y0,x1,y1) from the 250dpi
    render of a page.

    force_gray: run the grayscale enhance even when is_color_image() says the
    crop is colorful — for heavily yellowed scans the paper chroma alone can
    flip the test to True, which would otherwise skip the de-yellow treatment
    (e.g. a full-page background image of a yellowed special page).
    erase_ink: list of normalized PAGE-fraction boxes whose dark ink pixels
    (a watermark sitting on top of the crop, e.g. the 刘秀 下册 horse +
    slogan on the p2 banner) are replaced pixel-by-pixel with the local mean
    of nearby non-ink pixels, instead of a flat patch.
    erase_tmpl: {"page": N, "boxes": [[x0,y0,x1,y1], ...]} — un-blend a
    pixel-identical scan-time watermark using the same box on page N as an
    absorption template (see _erase_template). Preferred over erase_ink when
    the watermark sits on colored art: it only removes the watermark strokes
    and preserves the original underneath.
    """
    img = Image.open(ROOT / "data" / "pages" / f"p{page:03d}.png")
    W, H = img.size
    x0, y0, x1, y1 = [int(v * s) for v, s in zip(box, (W, H, W, H))]
    crop = img.crop((x0, y0, x1, y1))
    if erase_tmpl:
        crop = _erase_template(crop, box, erase_tmpl.get("page"),
                               erase_tmpl.get("boxes", []))
    if erase_ink:
        crop = _erase_ink(crop, box, erase_ink)
    if not color:
        from lib.common import is_color_image
        # Color-figure rule: a color crop keeps its original color even
        # when color:false — the grayscale treatment is for gray scans.
        if not force_gray and is_color_image(crop):
            return crop
        from importlib import import_module
        enh = import_module("03_enhance")
        crop = enh.enhance(crop)
    return crop


def _erase_ink(crop: Image.Image, box, ink_boxes) -> Image.Image:
    """Remove dark watermark ink inside ink_boxes (normalized PAGE fractions,
    mapped into the crop) by replacing each ink pixel with the mean of the
    surrounding non-ink pixels (31 px window, integral-image fast path).
    Used for a watermark painted over a colored banner crop, where a flat
    median patch would smudge the art.
    """
    a = np.array(crop.convert("RGB")).astype(np.float32)
    ch, cw = a.shape[:2]
    ink = a.min(axis=2) < 100.0
    half = 15
    H2, W2 = ch + 1, cw + 1
    wgt = np.where(ink, 0.0, 1.0)
    S = np.zeros((H2, W2, 3), dtype=np.float64)
    C = np.zeros((H2, W2), dtype=np.float64)
    S[1:, 1:] = np.cumsum(np.cumsum(a * wgt[..., None], axis=0), axis=1)
    C[1:, 1:] = np.cumsum(np.cumsum(wgt, axis=0), axis=1)
    cx0, cy0, cx1, cy1 = box
    r_all = np.clip(np.arange(ch) - half, 0, ch - 2 * half)
    r_end = np.minimum(r_all + 2 * half + 1, ch)
    c_all = np.clip(np.arange(cw) - half, 0, cw - 2 * half)
    c_end = np.minimum(c_all + 2 * half + 1, cw)
    Sg = (S[r_end[:, None], c_end[None, :], :]
          - S[r_all[:, None], c_end[None, :], :]
          - S[r_end[:, None], c_all[None, :], :]
          + S[r_all[:, None], c_all[None, :], :])
    Cg = (C[r_end[:, None], c_end[None, :]]
          - C[r_all[:, None], c_end[None, :]]
          - C[r_end[:, None], c_all[None, :]]
          + C[r_all[:, None], c_all[None, :]])
    mean = Sg / np.maximum(Cg[..., None], 1e-6)
    if not ink.any():
        return crop
    fallback = a[~ink].mean(axis=0) if (~ink).any() else a.mean(axis=(0, 1))
    for (nx0, ny0, nx1, ny1) in ink_boxes:
        px0 = int((nx0 - cx0) / (cx1 - cx0) * cw)
        px1 = int((nx1 - cx0) / (cx1 - cx0) * cw)
        py0 = int((ny0 - cy0) / (cy1 - cy0) * ch)
        py1 = int((ny1 - cy0) / (cy1 - cy0) * ch)
        px0, py0 = max(0, px0), max(0, py0)
        px1, py1 = min(cw, px1), min(ch, py1)
        if px1 <= px0 or py1 <= py0:
            continue
        sub = ink[py0:py1, px0:px1]
        m = mean[py0:py1, px0:px1]
        bad = (Cg[py0:py1, px0:px1] < 3.0)
        m[bad] = fallback
        a[py0:py1, px0:px1][sub] = m[sub]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _erase_template(crop: Image.Image, box, tmpl_page: int, ink_boxes):
    """Un-blend a pixel-identical scan-time watermark from a colored region.

    The watermark is overlaid before scanning, so at every pixel:
        observed = base * (1 - a(x))          (multiplicative ink absorption)
    a(x) is measured from a template page where the SAME watermark sits on
    plain paper:  a_c(x) = (P_c - T_c(x)) / P_c   (P = paper color, per
    channel). Then base = observed / (1 - a) restores the underlying art
    (e.g. the 刘秀 下册 p2 banner painting) where the watermark strokes ran.
    Requires the watermark to be pixel-identical across pages (scan-time
    overlay) — see references/watermark.md.
    """
    timg = Image.open(ROOT / "data" / "pages" / f"p{tmpl_page:03d}.png")
    t = np.array(timg.convert("RGB")).astype(np.float32)
    th, tw = t.shape[:2]
    a = np.array(crop.convert("RGB")).astype(np.float32)
    chh, cww = a.shape[:2]
    cx0, cy0, cx1, cy1 = box
    for (nx0, ny0, nx1, ny1) in ink_boxes:
        tx0, ty0 = int(nx0 * tw), int(ny0 * th)
        tx1, ty1 = int(nx1 * tw), int(ny1 * th)
        tsub = t[ty0:ty1, tx0:tx1]
        if tsub.size == 0:
            continue
        # paper color: median of the bright ring just outside the box on the
        # template page (the watermark sits on plain paper there)
        m = 30
        rx0, ry0 = max(0, tx0 - m), max(0, ty0 - m)
        rx1, ry1 = min(tw, tx1 + m), min(th, ty1 + m)
        ring = np.concatenate([
            t[ry0:ry0 + 1, rx0:rx1].reshape(-1, 3),
            t[ry1 - 1:ry1, rx0:rx1].reshape(-1, 3),
            t[ry0:ry1, rx0:rx0 + 1].reshape(-1, 3),
            t[ry0:ry1, rx1 - 1:rx1].reshape(-1, 3),
        ])
        bright = ring[ring.min(axis=1) > 120]
        P = np.median(bright, axis=0) if bright.size else np.median(ring, axis=0)
        P = np.maximum(P.astype(np.float32), 40.0)
        abs_ = np.clip((P[None, None, :] - tsub) / P[None, None, :], 0.0, 0.97)
        abs_ = cv2.GaussianBlur(abs_, (5, 5), 1.2)
        # map the same PAGE-fraction box into the crop's pixel space
        px0 = int((nx0 - cx0) / (cx1 - cx0) * cww)
        px1 = int((nx1 - cx0) / (cx1 - cx0) * cww)
        py0 = int((ny0 - cy0) / (cy1 - cy0) * chh)
        py1 = int((ny1 - cy0) / (cy1 - cy0) * chh)
        px0, py0 = max(0, px0), max(0, py0)
        px1, py1 = min(cww, px1), min(chh, py1)
        if px1 <= px0 or py1 <= py0:
            continue
        # the template region and the crop region cover the same page area,
        # but int() rounding can differ by a pixel - resize the absorption
        # field to the crop region shape
        if abs_.shape[:2] != (py1 - py0, px1 - px0):
            abs_ = cv2.resize(abs_, (px1 - px0, py1 - py0))
        sub = a[py0:py1, px0:px1]
        f = 1.0 - abs_
        keep = abs_ > 0.05
        for cc in range(3):
            k = keep[:, :, cc]
            sub[k, cc] = np.clip(sub[k, cc] / f[k, cc], 0.0, 255.0)
        a[py0:py1, px0:px1] = sub
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _place_image(c: canvas.Canvas, img, x_pt: float, top_pt: float,
                 size_pt: float):
    """Place image with its top-center at (x_pt, top_pt) in A4 coords.
    `img` is a PIL Image or a path string.

    reportlab caches embedded images by file path, so a PIL image must go
    through a unique temp path (content-hash based) — reusing one path for
    two different images makes the second draw silently reuse the first.
    """
    if isinstance(img, Image.Image):
        import hashlib
        digest = hashlib.md5(img.tobytes()).hexdigest()[:12]
        tmp = ROOT / "data" / "images" / f".tmp_place_{digest}.png"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        img.save(tmp)
        path = str(tmp)
    else:
        path = str(img)
    w0, h0 = img.size if isinstance(img, Image.Image) else (
        Image.open(img).size)
    s = min(size_pt / w0, size_pt / h0)
    w, h = w0 * s, h0 * s
    c.drawImage(path, x_pt - w / 2, A4_H - top_pt - h, width=w, height=h)
    return h


def _vtext(c: canvas.Canvas, text: str, x: float, y_top: float, pt: float,
           color=(0, 0, 0)):
    """Vertical CJK text, top-to-bottom, centered on x."""
    c.setFont(FONT_NAME, pt)
    c.setFillColorRGB(*color)
    y = y_top
    for ch in text:
        w = c.stringWidth(ch, FONT_NAME, pt)
        c.drawString(x - w / 2, y, ch)
        y -= pt * 1.18
    return y


def _wrap_lines(c: canvas.Canvas, body: str, pt: float, width: float,
                first_indent: float = 0.0):
    """Manual justified wrapping with hanging punctuation (no leading
    sentence punctuation; pull it back to the previous line)."""
    c.setFont(FONT_NAME, pt)
    lines, line = [], ""
    hang = "，。；：、！？）”’》〉」』"
    for i, ch in enumerate(body):
        is_first = i == 0
        lim = width - (first_indent if is_first else 0)
        if line and c.stringWidth(line + ch, FONT_NAME, pt) > lim:
            if ch in hang:
                line += ch
                lines.append(line)
                line = ""
            else:
                lines.append(line)
                line = ch
        else:
            line += ch
    if line:
        lines.append(line)
    return lines


def _draw_justified(c: canvas.Canvas, text: str, x: float, y: float,
                    pt: float, width, color):
    """Draw one line with its baseline at y (pt from the page bottom).

    When `width` is given and exceeds the natural width, per-character
    tracking stretches the line to exactly `width` — the special pages
    keep the original line spans (a justified line reaching the right
    margin must keep reaching it).
    """
    track = 0.0
    if width and len(text) > 1:
        natural = c.stringWidth(text, FONT_NAME, pt)
        if width > natural:
            track = (width - natural) / (len(text) - 1)
    tobject = c.beginText(x, y)
    tobject.setFont(FONT_NAME, pt)
    # ALWAYS set charSpace: reportlab only emits the Tc operator for
    # NON-ZERO values, so setCharSpace(0) is a no-op and the canvas's
    # previous tracking leaks into the next text object — a justified
    # line's tracking otherwise stretches every following line's cells
    # to pt + track (《孙膑和庞涓》 p114 colophon: the title's 28.75 pt
    # tracking made the left column 2.9 em cells and pushed it into the
    # right column's x-range). An imperceptible -0.001 pt forces a reset.
    tobject.setCharSpace(track if track else -0.001)
    tobject.setFillColorRGB(*color)
    tobject.textLine(text)
    c.drawText(tobject)


def _draw_string(c: canvas.Canvas, x: float, y: float, s: str):
    """canvas.drawString with charSpace force-reset. reportlab only emits
    Tc for non-zero values, so a preceding justified line's tracking
    leaks into plain drawString lines (a fresh TextObject starts at 0
    and emits no Tc); the -0.001 pt sentinel forces the reset."""
    t = c.beginText(x, y)
    t.setCharSpace(-0.001)
    t.textOut(s)
    c.drawText(t)


def _draw_spec(c: canvas.Canvas, spec: dict, x_pt: float, y_pt: float,
               size_pt: float, page: int):
    """Shared image placement for retype/title_page `images` entries."""
    box = spec["box"]
    if spec.get("component"):
        em = spec.get("emblem", {})
        crop = _largest_component_crop(
            str(ROOT / "data" / "pages" / f"{page:03d}.png"),
            em.get("region", box), color=spec.get("color", True))
    else:
        crop = _crop_render(page, box, color=spec.get("color", True),
                            force_gray=spec.get("force_gray", False),
                            erase_ink=spec.get("erase_ink"),
                            erase_tmpl=spec.get("erase_template"))
    return _place_image(c, crop, x_pt, y_pt, size_pt)


def _largest_component_crop(png_path: str, region, color: bool = True):
    """Crop the largest ink connected component inside a normalized
    region (x0, y0, x1, y1 as page fractions)."""
    img = Image.open(png_path)
    g = cv2.imread(png_path, cv2.IMREAD_GRAYSCALE)
    H, W = g.shape
    x0, y0, x1, y1 = [int(v * s) for v, s in zip(region, (W, H, W, H))]
    roi = g[y0:y1, x0:x1]
    _, bw = cv2.threshold(roi, 160, 255, cv2.THRESH_BINARY_INV)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k)
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    if n < 2:
        raise SystemExit(f"no component found in {png_path} {region}")
    areas = stats[1:, cv2.CC_STAT_AREA]
    i = int(np.argmax(areas)) + 1
    ex0, ey0 = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP]
    ew, eh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
    pad = 30
    box = (max(0, x0 + ex0 - pad), max(0, y0 + ey0 - pad),
           min(W, x0 + ex0 + ew + pad), min(H, y0 + ey0 + eh + pad))
    crop = img.convert("RGB").crop(box)
    if not color:
        from importlib import import_module
        enh = import_module("03_enhance")
        crop = enh.enhance(crop)
    return crop


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------

def cover_as_is(page: int, cfg: dict):
    """Keep the original scan as-is, centered fit on the output page."""
    src = ROOT / "data" / "pages" / f"p{page:03d}.png"
    img = Image.open(src)
    if cfg.get("whiteout_box"):
        W, H = img.size
        x0, y0, x1, y1 = [int(v * s) for v, s in
                          zip(cfg["whiteout_box"], (W, H, W, H))]
        arr = np.array(img)
        # fill with the median color of a border ring around the box
        m = 40
        ring = np.concatenate([
            arr[max(0, y0 - m):y0, x0:x1].reshape(-1, 3),
            arr[y1:min(H, y1 + m), x0:x1].reshape(-1, 3),
            arr[y0:y1, max(0, x0 - m):x0].reshape(-1, 3),
            arr[y0:y1, x1:min(W, x1 + m)].reshape(-1, 3),
        ])
        fill = np.median(ring, axis=0).astype(int)
        arr[y0:y1, x0:x1] = fill
        src = ROOT / "data" / "images" / f"p{page:03d}_cover_clean.png"
        src.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(arr).save(src)
    c = _page_canvas(page)
    s = min(A4_W / img.width, A4_H / img.height)
    w, h = img.width * s, img.height * s
    c.drawImage(str(src), (A4_W - w) / 2, (A4_H - h) / 2, width=w, height=h)
    c.save()
    print(f"p{page:03d} cover_as_is -> out/pages/p{page:03d}.pdf")


def enhance_full(page: int, cfg: dict):
    """Full-page scan placement for cover pages / color plates.

    Color-figure rule (auto, default): if the page render carries
    meaningful color it is placed 1:1 AS-IS — no grayscale, no
    whitening, no stretch, no sharpen. Grayscale pages get the bw_clear
    treatment (illumination-division whitening + level stretch +
    unsharp mask) from 03_enhance. Force with cfg mode:
    "as_is" | "enhance" | "auto" (default).
    """
    import importlib
    from lib.common import is_color_image
    src = ROOT / "data" / "pages" / f"p{page:03d}.png"
    img = Image.open(str(src))
    mode = str(cfg.get("mode", "auto"))
    if mode == "auto":
        mode = "as_is" if is_color_image(img) else "enhance"
    if mode == "as_is":
        c = _page_canvas(page)
        s = min(A4_W / img.width, A4_H / img.height)
        c.drawImage(str(src), (A4_W - img.width * s) / 2,
                    (A4_H - img.height * s) / 2,
                    width=img.width * s, height=img.height * s)
        c.save()
        return {"variant": "color_as_is", "image": str(src)}
    out_png = ROOT / "data" / "images" / f"p{page:03d}_full_bw.png"
    out_png.parent.mkdir(parents=True, exist_ok=True)
    if not out_png.exists():
        enh = importlib.import_module("03_enhance")
        img = enh.enhance(img, contrast_boost=1.15, sharpen=True)
        img.save(str(out_png))
    c = _page_canvas(page)
    im = Image.open(str(out_png))
    s = min(A4_W / im.width, A4_H / im.height)
    c.drawImage(str(out_png), (A4_W - im.width * s) / 2,
                (A4_H - im.height * s) / 2,
                width=im.width * s, height=im.height * s)
    c.save()
    return {"variant": "bw_clear", "image": str(out_png)}


def _whiteout_boxes(c: canvas.Canvas, page: int, cfg: dict):
    """Fill normalized page-fraction boxes white on the output page.

    Boxes are fractions of the SOURCE page (same coordinate space as
    `images.box`); mapped to the output with the same contain-fit the
    full-page images use (min-scale, centered). Used to erase the
    original printed text underneath re-typeset lines when a full-page
    scan serves as the background image (e.g. an ornate title-page
    frame with printed credits).
    """
    boxes = cfg.get("whiteout_boxes", [])
    if not boxes:
        return
    W, H = Image.open(ROOT / "data" / "pages" / f"p{page:03d}.png").size
    sw, sh = W / RENDER_DPI * 72.0, H / RENDER_DPI * 72.0
    s = min(A4_W / sw, A4_H / sh)
    tx, ty = (A4_W - sw * s) / 2.0, (A4_H - sh * s) / 2.0
    c.setFillColorRGB(1, 1, 1)
    for fx0, fy0, fx1, fy1 in boxes:
        x0, x1 = fx0 * sw * s + tx, fx1 * sw * s + tx
        t0, t1 = fy0 * sh * s + ty, fy1 * sh * s + ty
        c.rect(x0, A4_H - t1, x1 - x0, t1 - t0, fill=1, stroke=0)


def retype(page: int, cfg: dict):
    """Full-page white retype.

    cfg (explicit, recommended):
      background: {box: [0,0,1,1], x_pt, top_pt, size_pt, color: bool,
                force_gray: bool} — optional full-page (or any-region) base
                image drawn FIRST, before whiteout_boxes and text.
      layout: [[text, pt, [color r,g,b], {opts}], ...]
        opts.before    = pt gap from the previous row (flow layout)
        opts.x         = absolute left x (pt) of the line
        opts.baseline  = absolute baseline, pt from the TOP of the page
        opts.width     = justify the line to exactly this width (pt)
        opts.align/x_off as before. Rows with `x`/`baseline` are placed at
        their original coordinates (strict-fidelity mode).
      images: [{box: [x0,y0,x1,y1] fractions, x_pt, top_pt, size_pt,
                color: bool, force_gray: bool}, ...]
        force_gray (color:false only): apply the grayscale enhance even if
        is_color_image() flags the crop as color (yellowed-paper scans).
      draw_images_first: true draws `images` BEFORE the text (background
        plates, e.g. an ornate title-page frame); default false keeps
        the historical order (images on top, e.g. seals over text).
        NOTE: `images` are drawn after whiteout_boxes in either order —
        use `background` for plates that whiteout_boxes must erase under.
      whiteout_boxes: [[x0,y0,x1,y1], ...] normalized SOURCE-page boxes
        filled white after images and before text (erases the original
        printed text under a re-typeset line; only meaningful with a
        full-page background image).
    cfg (ocr-driven fallback):
      ocr: json path (default data/ocr/spec_pNNN.json)
      title_idx, title_pt, body_pt
    """
    c = _page_canvas(page)
    # base background image, drawn under everything (then whiteout erases
    # the original printed text, then retyped text + `images` go on top)
    bg = cfg.get("background")
    if bg:
        _draw_spec(c, bg, float(bg["x_pt"]), float(bg["top_pt"]),
                   float(bg.get("size_pt", 120)), page)
    images_first = bool(cfg.get("draw_images_first"))
    if images_first:
        for spec in cfg.get("images", []):
            _draw_spec(c, spec, float(spec["x_pt"]), float(spec["top_pt"]),
                       float(spec.get("size_pt", 120)), page)
        _whiteout_boxes(c, page, cfg)
    if bg:
        _whiteout_boxes(c, page, cfg)
    if not bg and not images_first:
        # plain-canvas page: whiteout before text/images (a no-op on the
        # white canvas itself, but it must NOT run after `images` — that
        # would fill the boxes over the placed crops)
        _whiteout_boxes(c, page, cfg)
    # y_start is pt from the TOP of the page (intuitive config space)
    y = A4_H - float(cfg.get("y_start", 160))
    for spec in cfg.get("layout", []):
        text, pt = spec[0], float(spec[1])
        color = tuple(spec[2]) if len(spec) > 2 and spec[2] else (0, 0, 0)
        opts = spec[3] if len(spec) > 3 else {}
        width = float(opts["width"]) if "width" in opts else None
        c.setFont(FONT_NAME, pt)
        w = c.stringWidth(text, FONT_NAME, pt)
        if "baseline" in opts:
            y = A4_H - float(opts["baseline"])
        else:
            y -= float(opts.get("before", 0))
            y -= pt
        if "x" in opts:
            x = float(opts["x"])
        elif opts.get("align") == "left":
            x = MARGIN + float(opts.get("x_off", 0))
        else:
            wc = width if width is not None else w
            x = (A4_W - wc) / 2 + float(opts.get("x_off", 0))
        _draw_justified(c, text, x, y, pt, width, color)
        y -= pt * 0.55
    if not cfg.get("layout"):
        ocr_path = ROOT / cfg.get("ocr", f"data/ocr/spec_p{page:03d}.json")
        ocr = load_json(ocr_path)
        blocks = [b["text"] for b in ocr.get("blocks", [])]
        title = cfg.get("title") or (blocks[cfg.get("title_idx", 0)]
                                     if blocks else "")
        body = "".join(b for i, b in enumerate(blocks)
                       if i != cfg.get("title_idx", 0))
        pt_t = float(cfg.get("title_pt", 24))
        c.setFont(FONT_NAME, pt_t)
        tw = c.stringWidth(title, FONT_NAME, pt_t)
        c.drawString((A4_W - tw) / 2, A4_H - 150, title)
        pt_b = float(cfg.get("body_pt", 15))
        lead = pt_b * float(cfg.get("lead_factor", 1.8))
        x0, width = MARGIN + 4, A4_W - 2 * MARGIN - 8
        lines = _wrap_lines(c, body, pt_b, width, first_indent=pt_b * 2)
        yb = A4_H - 200
        for i, ln in enumerate(lines):
            c.setFont(FONT_NAME, pt_b)
            _draw_string(c, x0 + (pt_b * 2 if i == 0 else 0), yb, ln)
            yb -= lead
    if images_first:
        pass
    else:
        for spec in cfg.get("images", []):
            _draw_spec(c, spec, float(spec["x_pt"]), float(spec["top_pt"]),
                       float(spec.get("size_pt", 120)), page)
        # plain-canvas whiteout already ran before the text; only the
        # draw_images_first case still needs it here (erasing the original
        # print under the plate images, then nothing is drawn over them)
        pass
    c.save()
    print(f"p{page:03d} retype -> out/pages/p{page:03d}.pdf")


def title_page(page: int, cfg: dict):
    """Vertical title page: big vertical title on the right, optional
    emblem (largest ink component in a page region), credit columns.

    cfg: title, title_pt, title_x_offset (from right edge), subtitle
         emblem: {region: [x0,y0,x1,y1 fractions], color: bool, size_pt}
         credits: [[label, value], ...] right-to-left vertical columns
         publisher: str, note: str (optional small text at the bottom)
    """
    c = _page_canvas(page)
    title = cfg["title"]
    tpt = float(cfg.get("title_pt", 64))
    tx = A4_W - float(cfg.get("title_x_offset", 80))
    _vtext(c, title, tx, A4_H - 120, tpt)
    if cfg.get("subtitle"):
        c.setFont(FONT_NAME, float(cfg.get("subtitle_pt", 18)))
        c.drawString(tx - cfg.get("subtitle_pt", 18) / 2,
                     A4_H - 120 - len(title) * tpt * 1.18 - 30,
                     cfg["subtitle"])
    if cfg.get("emblem"):
        em = cfg["emblem"]
        crop = _largest_component_crop(
            str(ROOT / "data" / "pages" / f"p{page:03d}.png"),
            em.get("region", [0.25, 0.1, 0.75, 0.85]),
            color=em.get("color", True))
        out_img = ROOT / "data" / "images" / f"p{page:03d}_emblem.png"
        out_img.parent.mkdir(parents=True, exist_ok=True)
        crop.save(out_img)
        _place_image(c, crop, A4_W / 2 - float(em.get("center_x_offset", 40)),
                     float(em.get("top_margin_pt", 140)),
                     float(em.get("size_pt", 260)))
    credits = cfg.get("credits", [])
    if credits:
        col_pt = float(cfg.get("credit_pt", 15))
        x = A4_W - float(cfg.get("title_x_offset", 80)) - 110
        for label, value in credits:  # right-to-left
            _vtext(c, label, x, A4_H - 240, col_pt)
            _vtext(c, value, x,
                   A4_H - 240 - (len(label) * col_pt * 1.18 + 16),
                   col_pt - 1)
            x -= float(cfg.get("credit_col_gap", 40))
    if cfg.get("publisher"):
        _vtext(c, cfg["publisher"], float(cfg.get("publisher_x", 50)),
               A4_H - 250, float(cfg.get("publisher_pt", 14)))
    if cfg.get("note"):
        c.setFont(FONT_NAME, float(cfg.get("note_pt", 9)))
        c.setFillColorRGB(0.55, 0.05, 0.05)
        c.drawString(MARGIN, 60, cfg["note"])
        c.setFillColorRGB(0, 0, 0)
    c.save()
    print(f"p{page:03d} title_page -> out/pages/p{page:03d}.pdf")


def colophon(page: int, cfg: dict):
    """Boxed 2-column publication-info page.

    cfg: header: [[text, pt, color?, before?, x?, baseline?, width?], ...]
         above the box; x/baseline/width place a line at its original
         coordinates (baseline = pt from the top of the page, width =
         justified line width in pt).
         box_w, box_h, box_top_margin, box_x (absolute left edge, optional),
         tab_title, left: [lines...],
         right_at: {"<left line index>": "right text"},
         x_left / x_right (absolute column x, optional),
         left_edge / right_edge (justified right edges of the columns),
         first_baseline (pt from top of the first box line, optional),
         footer: string (centered) or [[text, x], ...],
         footer_baseline (pt from top, optional),
         color: [r, g, b] or null (black)
    """
    c = _page_canvas(page)
    top_of_box = A4_H - float(cfg.get("box_top_margin", 120))
    y = A4_H - 80
    for h in cfg.get("header", []):
        text, pt = h[0], float(h[1])
        color = tuple(h[2]) if len(h) > 2 and h[2] else (0, 0, 0)
        before = float(h[3]) if len(h) > 3 and h[3] is not None else 18.0
        x_abs = float(h[4]) if len(h) > 4 and h[4] is not None else None
        baseline = float(h[5]) if len(h) > 5 and h[5] is not None else None
        width = float(h[6]) if len(h) > 6 and h[6] is not None else None
        if baseline is not None:
            y = A4_H - baseline
        else:
            y -= before
            y -= pt
        c.setFont(FONT_NAME, pt)
        w = c.stringWidth(text, FONT_NAME, pt)
        if x_abs is not None:
            x = x_abs
        elif width is not None:
            x = (A4_W - width) / 2
        else:
            x = (A4_W - w) / 2
        _draw_justified(c, text, x, y, pt, width, color)
        if baseline is None:
            y -= pt * 0.6
    bw = float(cfg.get("box_w", 470))
    bh = float(cfg.get("box_h", 165))
    if cfg.get("box_x") is not None:
        bx = float(cfg["box_x"])
    else:
        bx = (A4_W - bw) / 2
    by = top_of_box - bh
    color = cfg.get("color") or (0, 0, 0)
    c.setFillColorRGB(1, 1, 1)
    c.rect(bx, by, bw, bh, stroke=0)
    c.setFillColorRGB(*color)
    top = by + bh
    if cfg.get("tab_title"):
        pt_t = float(cfg.get("tab_pt", 20))
        t = cfg["tab_title"]
        tab_w = c.stringWidth(t, FONT_NAME, pt_t) + (len(t) - 1) * 6 + 26
        tab_in, tab_out = 10, 18
        c.setLineWidth(1.2)
        p = c.beginPath()
        p.moveTo(bx, top)
        p.lineTo(bx, by)
        p.lineTo(bx + bw, by)
        p.lineTo(bx + bw, top)
        c.drawPath(p, stroke=1, fill=0)
        tl, tr = A4_W / 2 - tab_w / 2, A4_W / 2 + tab_w / 2
        c.line(bx, top, tl, top)
        c.line(tr, top, bx + bw, top)
        c.setFillColorRGB(1, 1, 1)
        c.rect(tl, top - tab_in, tab_w, tab_in + tab_out, stroke=0)
        c.setFillColorRGB(*color)
        c.rect(tl, top - tab_in, tab_w, tab_in + tab_out, stroke=1)
        c.setFont(FONT_NAME, pt_t)
        tx = A4_W / 2 - (c.stringWidth(t, FONT_NAME, pt_t)
                         + (len(t) - 1) * 6) / 2
        for ch in t:
            c.drawString(tx, top - tab_in + 3, ch)
            tx += c.stringWidth(ch, FONT_NAME, pt_t) + 6
    else:
        c.setLineWidth(1.2)
        c.rect(bx, by, bw, bh, stroke=1, fill=0)
    pt = float(cfg.get("pt", 10.5))
    lead = float(cfg.get("lead", 17.5))
    if cfg.get("x_left") is not None:
        xL = float(cfg["x_left"])
    else:
        xL = bx + 22
    xR = bx + float(cfg.get("x_right", 290))
    left_edge = cfg.get("left_edge")
    right_edge = cfg.get("right_edge")
    if cfg.get("first_baseline") is not None:
        y = A4_H - float(cfg["first_baseline"])
    else:
        y = by + bh - 26
    right_at = {int(k): v for k, v in cfg.get("right_at", {}).items()}
    for i, l in enumerate(cfg.get("left", [])):
        c.setFillColorRGB(*color)
        if left_edge is not None:
            _draw_justified(c, l, xL, y - i * lead, pt,
                            float(left_edge) - xL, color)
        else:
            c.setFont(FONT_NAME, pt)
            _draw_string(c, xL, y - i * lead, l)
        if i in right_at:
            rt = right_at[i]
            if right_edge is not None:
                _draw_justified(c, rt, xR, y - i * lead, pt,
                               float(right_edge) - xR, color)
            else:
                c.setFont(FONT_NAME, pt)
                _draw_string(c, xR, y - i * lead, rt)
    if cfg.get("footer"):
        fpt = float(cfg.get("footer_pt", 11))
        if cfg.get("footer_baseline") is not None:
            yf = A4_H - float(cfg["footer_baseline"])
        else:
            yf = by - 24
        fc = cfg["footer"]
        if isinstance(fc, str):
            c.setFont(FONT_NAME, fpt)
            c.setFillColorRGB(*color)
            fw = c.stringWidth(fc, FONT_NAME, fpt)
            _draw_string(c, A4_W / 2 - fw / 2, yf, fc)
        else:
            for text, x in fc:
                _draw_justified(c, text, float(x), yf, fpt, None, color)
    c.save()
    print(f"p{page:03d} colophon -> out/pages/p{page:03d}.pdf")


HANDLERS = {
    "cover_as_is": cover_as_is,
    "enhance_full": enhance_full,
    "retype": retype,
    "title_page": title_page,
    "colophon": colophon,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="*", default=["all"])
    args = ap.parse_args()
    sel = args.pages
    if "all" in sel:
        sel = [str(k) for k in SPECIAL]
    for p in sel:
        p = int(p)
        cfg = SPECIAL.get(str(p)) or SPECIAL.get(str(p).zfill(3))
        if not cfg:
            raise SystemExit(f"no special_pages config for page {p}")
        handler = HANDLERS[cfg["handler"]]
        handler(p, cfg)


if __name__ == "__main__":
    main()
