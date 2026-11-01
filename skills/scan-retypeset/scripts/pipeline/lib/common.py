"""Shared helpers: config, paths, coordinate mapping, data loading.

ROOT resolution (first match wins):
  1. $RETYPESET_ROOT environment variable
  2. the book project root (grandparent of this file, when the pipeline was
     copied into the book project as <book>/pipeline/)
  3. the current working directory

ROOT must contain `pipeline/config.json` (see config.example.json), and gets
`data/` + `out/` subdirectories created by the pipeline.
"""
import json
import os
import re
from pathlib import Path

import numpy as np


def _resolve_root() -> Path:
    env = os.environ.get("RETYPESET_ROOT")
    if env:
        p = Path(env).resolve()
        if (p / "pipeline" / "config.json").exists():
            return p
    here = Path(__file__).resolve()
    for cand in (here.parent.parent.parent, Path.cwd()):
        if (cand / "pipeline" / "config.json").exists():
            return cand
    raise SystemExit(
        "config.json not found: set RETYPESET_ROOT to the book project root, "
        "or copy scripts/pipeline/ into <book>/pipeline/ and create "
        "<book>/pipeline/config.json")


RENDER_DPI_FALLBACK = 250  # only used when pdfinfo is unavailable


ROOT = _resolve_root()
CONFIG = json.loads((ROOT / "pipeline" / "config.json").read_text())


def _source_page_pt() -> tuple:
    """Physical size (pt) of the source book's pages, from pdfinfo.

    pdfinfo reports the common page size; for mixed-size PDFs (e.g. a small
    watermark last page) that is the main content page. Falls back to the
    first rendered panel page (render px / dpi * 72).
    """
    import subprocess
    try:
        out = subprocess.run(
            ["pdfinfo", str(ROOT / CONFIG["source_pdf"])],
            capture_output=True, text=True, timeout=30).stdout
        m = re.search(r"Page size:\s*([\d.]+)\s*x\s*([\d.]+)\s*pts",
                      out or "")
        if m:
            return float(m.group(1)), float(m.group(2))
    except Exception:
        pass
    for pg in range(CONFIG.get("panel_pages", [1, 1])[0],
                     CONFIG.get("panel_pages", [1, 1])[1] + 1):
        p = ROOT / "data" / "pages" / f"p{pg:03d}.png"
        if p.exists():
            from PIL import Image
            w, h = Image.open(p).size
            return w / RENDER_DPI_FALLBACK * 72.0, h / RENDER_DPI_FALLBACK * 72.0
    raise SystemExit("page_size=source: cannot determine source page size "
                     "(pdfinfo missing and no renders yet — run 01_render first)")


# --- geometry of the output page ---
# page_size: "a4" (default) uses the configured A4 box; "source" makes the
# output page the exact size of the source scan, so the mapping can be a
# true 1:1 identity transform (every element lands at its original
# coordinates — strict layout fidelity). See config.md.
PAGE_SIZE_MODE = CONFIG.get("page_size", "a4")
if PAGE_SIZE_MODE == "source":
    A4_W, A4_H = _source_page_pt()
else:
    A4_W = CONFIG["a4"]["width_pt"]
    A4_H = CONFIG["a4"]["height_pt"]
MARGIN = CONFIG["a4"]["margin_pt"]
TYPESET = CONFIG.get("typeset", {})

# --- typography ---
FONT_PATH = CONFIG["font"]
FONT_NAME = CONFIG.get("font_name", "LXGWWenKai")
FONT_PT = CONFIG.get("font_pt", 13.5)
INK_EM = CONFIG.get("ink_em", 0.914)      # CJK ink height = INK_EM * em (measured)
ASC_EM = CONFIG.get("asc_em", 0.818)      # ink top above baseline

# --- content model ---
SOURCE_PDF = CONFIG["source_pdf"]
TOTAL_PAGES = CONFIG["total_pages"]
DROP_PAGES = set(CONFIG.get("drop_pages", []))
PANEL_PAGES = list(range(CONFIG["panel_pages"][0], CONFIG["panel_pages"][1] + 1))
PANEL_OFFSET = CONFIG.get("panel_no_offset", 3)
# Leading-number style: "bare" ("9  text"), "paren" ("（9）text"), or
# "dot" ("9. text" — number + period + space, e.g. 《巴黎圣母院》 1990s
# 连环画). The number is EXTRACTED from OCR text independent of this
# setting; the setting only fixes the canonical re-typeset token.
PANEL_NO_FORMAT = CONFIG.get("panel_no_format", "bare")
# Panel-range pages that print NO leading number (e.g. an unnumbered
# prologue run, 《巴黎圣母院》 p4-9 序幕): the number anchor check is
# skipped for them — line 0 must simply not start with a number.
NO_PANEL_NO = set(CONFIG.get("no_panel_no", []))
# Pages where the ORIGINAL BOOK misprinted the running number (《巴黎圣母院》
# p112 prints "48." where the sequence says 103 — neighbours p111=102 and
# p113=104 confirm the run). The retypeset keeps the misprint faithfully
# (1:1 fidelity); the anchor check just needs the expected value overridden.
# {page: printed_number}
PANEL_NO_EXCEPTIONS = {int(k): int(v) for k, v in
                       CONFIG.get("panel_no_exceptions", {}).items()}
RENDER_DPI = CONFIG.get("render_dpi", 250)
ENHANCE = CONFIG.get("enhance", {})
# Right-margin design strip (running-title tab / page-edge line / owner's red
# seal): some books print a decorative strip in the right page margin on
# every panel page. When present it is cropped + whitened per-channel and
# redrawn 1:1 so the retypeset page keeps the original layout. It is
# auto-detected (skipped when the margin is blank); set to false to force off.
MARGIN_STRIP = bool(CONFIG.get("margin_strip", True))
# Watermarks added by later (re)scanners: normalized page boxes (fractions of
# render W/H) that are painted out of the deskewed page in 03_enhance (so
# figure + margin-strip crops come out clean) and whose OCR lines are dropped
# in 04_merge (a line is dropped when its bbox CENTRE is inside a box AND it
# starts from the left 60% of that box's x span — real caption lines in the
# same corner start further right). 刘秀 下册: horse + "天岸马奇通人中龙"
# slogan, identical on every panel page at bottom-left.
WATERMARK_BOXES = CONFIG.get("watermark_boxes") or []
MCP = CONFIG.get("mcp", {})
MAX_OCR_SIDE = 1920  # MCP server preprocessing cap (PADDLEOCR_MAX_IMAGE_SIZE)


def p_path(page: int, subdir: str, name: str) -> Path:
    return ROOT / subdir / f"{name}{page:03d}"


def load_json(path: Path):
    return json.loads(path.read_text())


def save_json(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2))


def content_bbox(geo: dict) -> list:
    """Union of frame bbox and text bands (deskewed page-space px)."""
    fb = geo["frame"]["bbox"]
    boxes = [fb]
    for b in geo.get("text_bands", []):
        boxes.append(b["bbox"])
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return [x0, y0, x1, y1]


def rebuild_map(geo: dict) -> dict:
    """Mapping: content region of the original (deskewed, px) -> output pt.

    "a4" mode (default): uniform scale + translate; the content region is
    fit into the output content area, x centered, y top-anchored.

    "source" mode: output page = source page size, so the mapping is the
    identity transform at 72/render_dpi (tx = ty = 0): every pixel lands at
    its original position on the page. Figure, text lines and the
    figure-text gap are all at their original coordinates.

    Returns {scale, tx, ty, content_bbox, mode}.
    """
    cb = content_bbox(geo)
    if PAGE_SIZE_MODE == "source":
        rw, rh = geo["render_size"]
        s = min(A4_W / rw, A4_H / rh)
        return {"scale": s, "tx": 0.0, "ty": 0.0,
                "content_bbox": cb, "mode": "source"}
    cw, ch = cb[2] - cb[0], cb[3] - cb[1]
    s = min((A4_W - 2 * MARGIN) / cw, (A4_H - 2 * MARGIN) / ch)
    tx = (A4_W - cw * s) / 2.0 - cb[0] * s
    ty = MARGIN - cb[1] * s
    return {"scale": s, "tx": tx, "ty": ty, "content_bbox": cb,
            "mode": "a4"}


def map_pt(rm: dict, x_px: float, y_px: float):
    """Map a deskewed-page pixel coord to output top-origin pt (y down)."""
    return (x_px * rm["scale"] + rm["tx"], y_px * rm["scale"] + rm["ty"])


def map_pt_pdf(rm: dict, x_px: float, y_px: float):
    """Same but PDF origin (bottom-left)."""
    mx, my = map_pt(rm, x_px, y_px)
    return mx, A4_H - my


def refine_frame_bottom(gray: np.ndarray, fb: list):
    """Correct a frame bbox whose bottom edge fused with the first caption
    line (deskewed page px).

    Mid-century books print the first caption line close to the frame's
    bottom border. The contour detector then either fuses the bottom edge
    with the caption text (box bottom up to ~90 px too low, "text inside
    the frame") or stops inside a blurry/gradient frame border (box bottom
    a few px too high). Consequences of the first case: the figure crop
    swallows the top of the first line, the textband OCR crop starts
    mid-line (clipped ink height -> wrong font size), and the
    figure/text gap is measured from inside the text.

    Detection: a white horizontal gap (>=4 rows, row-avg ink <6%) in
    [y1-200, y1+60] such that
      * below it sits a single TEXT line: a run (row ink >=15 %, height
        15-90 px) whose per-column ink is variable (rel std >=0.50 —
        character strokes with white space between) and whose bottom
        reaches within 100 px of the reported bottom (the box fused with
        this line);
      * above it the frame border / figure bottom edge: the bottom 25 px
        of that dark band are nearly uniform in x (rel std <0.75) — a
        frame border, even a blurry one, is uniform in x, whereas a text
        line (an inter-line gap's band above) has rel std ~1.4.
    The frame bottom is the row above the gap. It is returned only when it
    sits >=8 px above the reported bottom (the box swallowed the text);
    otherwise None (keep the detected box — a bbox a few px INSIDE the
    frame stroke is handled by frame_ink_extent).
    """
    x0, y0, x1, y1 = [int(v) for v in fb]
    H = gray.shape[0]
    xa, xb = x0 + 40, x1 - 40
    ylo, yhi = max(0, y1 - 200), min(H, y1 + 60)
    if yhi - ylo < 24:
        return None
    f = (gray[ylo:yhi, xa:xb] < 140).mean(axis=1)
    n = len(f)
    gaps = []
    i = 0
    while i < n:
        if f[i] < 0.06:
            j = i
            while j + 1 < n and f[j + 1] < 0.06:
                j += 1
            if j - i + 1 >= 4:
                gaps.append((i, j))
            i = j + 1
        else:
            i += 1
    for w0, w1 in gaps:
        # --- single text line below the gap?
        t0 = None
        for t in range(w1 + 1, min(n, w1 + 1 + 40)):
            if f[t] >= 0.15:
                t0 = t
                break
        if t0 is None:
            continue
        t1 = t0
        while t1 + 1 < n and f[t1 + 1] >= 0.08:
            t1 += 1
        h_low = t1 - t0 + 1
        if h_low < 15 or h_low > 90:
            continue
        if t1 < y1 - ylo - 100:        # line must reach near the box bottom
            continue
        seg = (gray[ylo + t0: ylo + t1 + 1, xa:xb] < 140)
        col = seg.mean(axis=0)
        if float(col.std()) / max(float(col.mean()), 1e-6) < 0.50:
            continue                    # uniform in x -> not text
        # --- dark band above the gap (frame border / figure bottom)?
        a = w0 - 1
        for _ in range(4):              # allow a few feathering rows
            if a < 0 or f[a] >= 0.10:
                break
            a -= 1
        if a < 0 or f[a] < 0.10:
            continue
        s = max(a - 24, 0)
        seg2 = (gray[ylo + s: ylo + a + 1, xa:xb] < 140)
        col2 = seg2.mean(axis=0)
        if float(col2.std()) / max(float(col2.mean()), 1e-6) >= 0.75:
            continue                    # band above is a text line
        cand = ylo + a
        if cand - y1 <= -8:
            return int(cand)
    return None


def frame_ink_extent(gray: np.ndarray, fb: list, text_top: int = None) -> list:
    """Outer ink extent of the frame region (deskewed page px).

    The detected frame bbox can sit INSIDE a thick/blurry frame stroke —
    mid-century scans often carry a double or heavily anti-aliased border
    whose outer edge overhangs the fitted bbox by up to 30+ px (Nezha p3:
    10 px above, 14 px below). Cropping the figure at the bbox clips the
    stroke, so the output frame looks thinner than the original and the
    measured figure/text gap is off by the bottom overhang. Scan outward
    from each bbox edge while rows/columns stay >= 3 % dark; stop at the
    first white row (so the text below the frame is never swallowed), with
    conservative caps per side. When `text_top` (first caption ink row)
    is known, the downward cap stops just above it — without that cap the
    scan swallows the top of a caption line that sits within 25 px of the
    frame (the flush-against-frame case).
    """
    x0, y0, x1, y1 = [int(v) for v in fb]
    H, W = gray.shape
    cx0, cx1 = x0 + 100, x1 - 100

    def rcov(y: int) -> float:
        return float((gray[y, cx0:cx1] < 140).mean())

    y = y0 - 1
    while y >= max(0, y0 - 80) and rcov(y) >= 0.03:
        y -= 1
    top = y + 1
    # Top-edge guard: if the row just above the fitted bbox is faint
    # (3-10 % dark), the contour bottom sits at/below the strong frame
    # line and a faint band above may be included; pull the crop top to
    # the strong line's top (first strong row scanning down from y0).
    if y0 - 1 >= 0 and 0.03 <= rcov(y0 - 1) < 0.10:
        yy = y0
        while yy < min(H, y0 + 40) and rcov(yy) < 0.30:
            yy += 1
        if yy < min(H, y0 + 40):
            top = max(top, max(0, yy))
    dcap = 25
    if text_top is not None:
        dcap = max(2, min(25, int(text_top) - y1 - 1))
    if y1 + 1 < H and 0.03 <= rcov(y1 + 1) < 0.10:
        # The fitted bbox bottom sits AT/BELOW the strong frame line:
        # the rows immediately below are a faint band (scan noise /
        # gradient), not stroke. Scanning down would swallow it and push
        # the figure crop bottom into the figure/text gap. Instead the
        # crop bottom is the strong line's bottom edge: the lowest strong
        # row (>=30 % dark) scanning UP from y1 within 40 px, extended
        # over a short faint anti-alias tail (>=10 %, <=6 rows). If no
        # strong row is found (bbox far below the line), keep y1.
        bottom = y1
        yy = y1
        while yy > max(0, y1 - 40) and rcov(yy) < 0.30:
            yy -= 1
        if yy >= max(0, y1 - 40) and rcov(yy) >= 0.30:
            b = yy
            k = 0
            while (b + 1 < H and rcov(b + 1) >= 0.10 and k < 6
                   and (text_top is None or b + 1 < int(text_top) - 1)):
                b += 1
                k += 1
            bottom = min(b, y1)
    else:
        # Normal case: the stroke continues below the bbox. Scan down
        # while rows stay >=3 % dark, but stop at the first SUSTAINED
        # faint run (>=3 consecutive rows <10 % dark): a faint band /
        # gradient below the frame line is not part of the stroke
        # (p70-class pages: swallowing it moves the crop bottom ~20 pt
        # into the figure/text gap and compresses the visible gap).
        y = y1 + 1
        consec_faint = 0
        while y < min(H, y1 + dcap):
            cov = rcov(y)
            if cov < 0.03:
                break
            if cov < 0.10:
                consec_faint += 1
                if consec_faint >= 3:
                    break
            else:
                consec_faint = 0
            y += 1
        bottom = y - 1
    cy0, cy1 = y0 + 150, y1 - 150
    x = x0 - 1
    while x >= max(0, x0 - 30) and float((gray[cy0:cy1, x] < 140).mean()) >= 0.03:
        x -= 1
    left = x + 1
    x = x1 + 1
    while x < min(W, x1 + 30) and float((gray[cy0:cy1, x] < 140).mean()) >= 0.03:
        x += 1
    right = x - 1
    return [max(0, left), max(0, top), min(W - 1, right), min(H - 1, bottom)]


def textband_crop_box(geo: dict) -> list:
    """Exact crop box (deskewed page-space px) of the textband OCR image.

    The geometry text-band union spans the full projection width, so page
    margins end up in the crop — e.g. Nezha stamps a red owner's seal in
    the right margin of every panel page, which line-OCR reads as short
    low-confidence garble. When the union clearly overhangs the frame box
    (>80px), clamp the crop x-range to 65px beyond the frame edge: text
    overhangs the frame by at most ~52px, seals start at ~51px+ — this
    keeps all text in and most seal ink out. The merge step still filters
    residual low-confidence blocks outside the frame (04_merge).
    """
    ub = geo["text_band_union"]
    W, H = geo["render_size"]
    pad = 30
    x0, y0, x1, y1 = ub[0] - pad, ub[1] - pad, ub[2] + pad, ub[3] + pad
    frame = geo.get("frame", {}).get("bbox")
    if frame:
        if x1 - frame[2] > 80:
            x1 = min(x1, frame[2] + 65)
        if frame[0] - x0 > 80:
            x0 = max(x0, frame[0] - 65)
    return [max(0, x0), max(0, y0), min(W, x1), min(H, y1)]


def ocr_to_page_scale(geo: dict) -> tuple:
    """Scale factor mapping MCP ocr bbox coords back to page pixels.

    The textband crop is `textband_crop_box(geo)`; the MCP server
    downscales the crop so its long side <= MAX_OCR_SIDE.
    """
    x0, y0, x1, y1 = textband_crop_box(geo)
    scale = min(1.0, MAX_OCR_SIDE / max(x1 - x0, y1 - y0))
    return scale, (x0, y0)


def ocr_bbox_to_page(bbox: list, geo: dict) -> list:
    scale, (x0, y0) = ocr_to_page_scale(geo)
    return [
        int(x0 + bbox[0] / scale),
        int(y0 + bbox[1] / scale),
        int(x0 + bbox[2] / scale),
        int(x0 + bbox[3] / scale),
    ]


def weighted_gaps(text: str, font_name: str, pt: float, extra: float,
                  cap: float = 0.0) -> list:
    """Inter-character gaps for a justified line (B5 weighted-slot rule).

    Bread's rule for justified text: each character gets a slot
    proportional to its ADVANCE (slot_i ∝ adv_i) — uniform tracking gives
    narrow digits the same gaps as CJK characters, so mixed lines like
    "共 156 页" visibly over-space the digits.  Weighting the gap between
    two characters by the mean of their advances is exactly equivalent to
    slot ∝ advance, and degenerates to uniform tracking for pure
    full-width lines (the historical behavior).

    Returns len(text)-1 gaps in pt that sum to `extra` (unless clipped by
    `cap`, which only bites on very wide original spacing).  All zeros
    when `extra <= 0`.
    """
    from reportlab.pdfbase import pdfmetrics
    n = len(text)
    if n < 2:
        return []
    if extra <= 0:
        return [0.0] * (n - 1)
    advs = [pdfmetrics.stringWidth(ch, font_name, pt) for ch in text]
    ws = [(advs[i] + advs[i + 1]) * 0.5 for i in range(n - 1)]
    total = sum(ws)
    if total <= 0:
        return [min(extra / (n - 1), cap) if cap else extra / (n - 1)
                ] * (n - 1)
    if cap:
        return [min(extra * w / total, cap) for w in ws]
    return [extra * w / total for w in ws]


def _row_cov(gray: np.ndarray, y: int, x0: int, x1: int) -> float:
    return float((gray[y, x0:x1] < 140).mean())


def _text_rising(gray: np.ndarray, ty: int, x0: int, x1: int) -> bool:
    """True if coverage is rising through row `ty` toward a text body:
    the mean coverage of the next 8 rows is >= 0.08. Catches the faint top
    of a text line (sparse stroke tips, coverage <0.14 so it reads
    "white") while rejecting isolated corner specks (flat/isolated, not
    rising)."""
    H = gray.shape[0]
    seg = gray[ty:min(ty + 8, H), x0:x1]
    if seg.size == 0:
        return False
    return float((seg < 140).mean(axis=1).mean()) >= 0.08



def gap_scan(gray: np.ndarray, y_start: int, x0: int, x1: int,
             stroke_window: int, search_window: int,
             thresh: float = 0.03) -> tuple:
    """Locate the figure/stroke bottom and the first text ink top. Used
    identically on the original page and the rendered output (the
    figure_text_gap fidelity check).

    White-gap-first: from `y_start` (just below the frame bbox bottom) find
    the first SUSTAINED white gap (>=3 consecutive rows whose dark-column
    coverage is <0.14) within `stroke_window` px — that is the figure/text
    gap. The stroke bottom is the row just above it. The first text row is
    the first row after the gap with dark-column coverage >= `thresh` that
    is part of a sustained text run.

    If no sustained white gap exists within `stroke_window`, the first text
    line touches the frame (case B): stroke bottom = y_start-1, and the
    text top is the first text-like row below y_start.

    The previous version advanced while rows were `>=thresh` dark and
    stopped at the first single light row. That failed when (a) the frame
    stroke is a blurry gradient extending many px below the bbox, or (b)
    dark figure-corner content (water, frame-corner blur) kept isolated
    rows `>=thresh` inside the white gap — both gave false case-B or
    mislocated the text top.

    Returns (stroke_bottom, first_text_row, case_b).
    """
    H = gray.shape[0]
    white_thresh = 0.14
    y = int(y_start)
    gap_start = None
    gap_end = None
    limit_gap = min(H, y + int(stroke_window))
    while y < limit_gap:
        if _row_cov(gray, y, x0, x1) < white_thresh:
            y2 = y
            limit2 = min(H, limit_gap + 40)
            while y2 < limit2 and _row_cov(gray, y2, x0, x1) < white_thresh:
                y2 += 1
            if y2 - y >= 3:
                gap_start, gap_end = y, y2
                break
            y = y2
        else:
            y += 1
    if gap_start is None:
        stroke_bottom = int(y_start) - 1
        ty = int(y_start)
        limit = min(H, ty + int(search_window))
        while ty < limit:
            if _row_cov(gray, ty, x0, x1) >= 0.02 and _text_rising(gray, ty, x0, x1):
                return stroke_bottom, int(ty), True
            ty += 1
        return stroke_bottom, None, True
    stroke_bottom = gap_start - 1
    ty = gap_start
    limit = min(H, int(y_start) + int(search_window))
    while ty < limit:
        if _row_cov(gray, ty, x0, x1) >= 0.02 and _text_rising(gray, ty, x0, x1):
            return int(stroke_bottom), int(ty), False
        ty += 1
    return int(stroke_bottom), None, False


def figure_text_gap(gray: np.ndarray, geo: dict):
    """Visible gap between the figure frame and the first text line, in px.

    Anchored at the frame bbox bottom, runs gap_scan across the frame's
    inner width. gap_scan finds the sustained white gap (figure/text gap)
    below the frame, so a blurry frame stroke or dark figure-corner content
    can no longer trigger a false case-B. When no white gap exists (case B)
    the text touches the frame: gap_px = 1 and the placement target is the
    first text-like row below the bbox bottom (or bbox bottom + 1).

    06_verify runs the identical scan on the rendered output page and the
    two must agree — that is the figure/text-gap fidelity check.
    """
    fb = geo["frame"]["bbox"]
    H = gray.shape[0]
    x0, x1 = fb[0] + 40, fb[2] - 40
    y1 = fb[3]
    stroke_bottom, first_row, case_b = gap_scan(gray, y1 + 1, x0, x1,
                                                60, 160)
    if case_b:
        return {"frame_bbox_bottom_px": int(y1),
                "frame_stroke_bottom_px": int(stroke_bottom),
                "first_text_ink_top_px": int(first_row) if first_row else int(y1) + 1,
                "gap_px": 1,
                "gap_ratio_h": round(1.0 / H, 5)}
    if first_row is None:
        return None
    return {"frame_bbox_bottom_px": int(y1),
            "frame_stroke_bottom_px": int(stroke_bottom),
            "first_text_ink_top_px": int(first_row),
            "gap_px": int(first_row - stroke_bottom),
            "gap_ratio_h": round((first_row - stroke_bottom) / H, 5)}


_ASC_CACHE = {}


def char_ink_ascent_em(font_path: str, ch: str, ref_size: int = 100) -> float:
    """Ink ascent of one glyph in the output font (em above the baseline),
    measured once per glyph from the TTF via PIL.

    CJK glyphs in LXGW WenKai sit at ~0.71 em while fullwidth parens reach
    ~1.2 em — the top of a rendered line is set by its tallest glyph, so
    the line baseline must be derived from the per-line maximum, not a
    single font-wide constant.
    """
    key = (font_path, ch)
    if key in _ASC_CACHE:
        return _ASC_CACHE[key]
    val = ASC_EM
    try:
        from PIL import Image, ImageDraw, ImageFont
        f = ImageFont.truetype(font_path, ref_size)
        w = int(ref_size * 1.6)
        img = Image.new("L", (w, ref_size * 2), 255)
        d = ImageDraw.Draw(img)
        base_y = int(ref_size * 1.4)
        d.text((w // 2 - ref_size // 4, base_y), ch, font=f, fill=0, anchor="ls")
        arr = np.array(img)
        rows = np.where((arr < 128).any(axis=1))[0]
        if len(rows):
            val = max(0.0, (base_y - int(rows[0])) / ref_size)
    except Exception:
        pass
    _ASC_CACHE[key] = val
    return val


def line_dense_top_pt(text: str, font_path: str, fpt_pt: float,
                      supersample: int = 3) -> float:
    """Distance (pt) from the baseline up to the first rendered row of
    `text` at `fpt_pt` whose row-average darkness reaches 3 % of the line
    width.

    This is the same row definition 06_verify's figure_text_gap scan uses on
    both the original page and the rendered output. Placing the line so this
    measured row lands on the original's measured row makes the reproduced
    gap match by construction — no font-metric assumptions needed. The
    offset is dominated by the font's sparse glyph terminals (the first
    ~0.05-0.1 em above a CJK body stroke is below the 3 % row threshold),
    which differ between the scanned book's typeface and the output font.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        size = max(8, int(round(fpt_pt * supersample)))
        f = ImageFont.truetype(font_path, size)
        length = f.getlength(text)
        W = max(8, int(length) + size)
        H = size * 2
        img = Image.new("L", (W, H), 255)
        d = ImageDraw.Draw(img)
        b = int(size * 1.4)
        d.text((size // 8, b), text, font=f, fill=0, anchor="ls")
        arr = np.array(img)
        rows = np.where((arr < 140).mean(axis=1) >= 0.03)[0]
        if len(rows) == 0:
            rows = np.where((arr < 128).any(axis=1))[0]
        if len(rows) == 0:
            return fpt_pt * 0.8
        return (b - int(rows[0])) / supersample
    except Exception:
        return fpt_pt * 0.8


def line_ink_ascent_em(text: str, font_path: str) -> float:
    """Ink ascent (em) of the tallest glyph in `text` — sets the top of the
    rendered line."""
    vals = [char_ink_ascent_em(font_path, ch) for ch in set(text)
            if not ch.isspace()]
    return max(vals) if vals else ASC_EM


def line_ink_bbox(gray: np.ndarray, bbox: list, min_frac: float = 0.02,
                  y_start: int = None, y_end: int = None):
    """Tighten an OCR line bbox to its actual ink extent (rows with >=
    min_frac dark pixels inside the line's x span). OCR bboxes carry 0-30 px
    of top padding that varies with the first glyph; placing lines at the
    ink top (not the bbox center) keeps text at its original position.

    y_start: explicit top of the scan window. When consecutive lines' x
    spans overlap (always, for justified text), the default window
    (y0-6) can start INSIDE the previous line's ink bottom, so the first
    >=min_frac row belongs to the previous line and the line's ink top is
    measured ~50 px too high. Pass the previous line's ink bottom + 1.
    """
    x0, y0, x1, y1 = [int(v) for v in bbox]
    top = max(0, y0 - 6) if y_start is None else max(0, int(y_start))
    bottom = min(gray.shape[0], y1 + 6)
    if y_end is not None:
        bottom = min(bottom, int(y_end) + 1)
    sub = gray[top:bottom, x0:x1]
    rows = np.where((sub < 140).mean(axis=1) >= min_frac)[0]
    if len(rows) == 0:
        return list(bbox)
    return [x0, top + int(rows[0]), x1, top + int(rows[-1])]


def norm_text(s: str) -> str:
    return "".join(s.split())


def vl_text(markdown: str) -> str:
    """Extract text from VL markdown (drop image placeholders)."""
    out = []
    for line in (markdown or "").splitlines():
        line = line.strip()
        if not line or line.startswith("![") or line == "---":
            continue
        out.append(line)
    return norm_text("".join(out))


# ---------------------------------------------------------------------------
# Panel-number handling.
#
# Different books typeset the running number differently:
#   * Lianhuanhua:  "9      商纣王..."            (bare number + wide gap)
#   * Nezha:        "（ 64 ）  哪吒又拍着..."      (fullwidth parens, spaced)
#   * 巴黎圣母院:   "31. “救命呀...”"               (number + period + space)
# A single helper normalizes all three to a canonical form and extracts
# the number.
# ---------------------------------------------------------------------------
_PAREN_NUM = re.compile(r"^\s*[（(]\s*(\d{1,3})\s*[）)]\s*(?=\S)")
_DOT_NUM = re.compile(r"^\s*(\d{1,3})\s*[.．、]\s*(?=\S)")
_BARE_NUM = re.compile(r"^\s*(\d{1,3})[\s\u3000]*(?=\S)")


def lead_number(text: str):
    """Return the leading panel number (int) regardless of number style
    (parenthesised / dotted / bare), else None."""
    m = _PAREN_NUM.match(text or "")
    if not m:
        m = _DOT_NUM.match(text or "")
    if not m:
        m = _BARE_NUM.match(text or "")
    return int(m.group(1)) if m else None


def norm_lead_number(text: str) -> str:
    """Canonicalize a leading panel number.

    Parenthesised -> "（N）" + remaining text (spaces removed inside).
    Dotted        -> "N. " + remaining text (period kept, one space).
    Bare          -> "N" + two fullwidth spaces + remaining text.
    No number     -> unchanged.
    """
    m = _PAREN_NUM.match(text or "")
    if m:
        return "（" + m.group(1) + "）" + text[m.end():].lstrip()
    m = _DOT_NUM.match(text or "")
    if m:
        return m.group(1) + ". " + text[m.end():].lstrip()
    m = _BARE_NUM.match(text or "")
    if m:
        return m.group(1) + "\u3000\u3000" + text[m.end():].lstrip()
    return text


def is_color_image(img, chroma_p99: float = 30.0) -> bool:
    """Whether an image carries meaningful color (not a gray scan).

    Skill rule: COLOR images are kept as-is — no grayscale, no
    illumination whitening, no level stretch, no sharpen, no binarize —
    and placed 1:1 in their original color. Per-pixel chroma =
    max(R,G,B) - min(R,G,B); a grayscale scan has p99 chroma ~0-3
    (scanner noise), a color illustration has p99 chroma in the dozens
    to hundreds (p001 cover: 202; a B&W panel: 0). p99 > 30 => color.
    """
    from PIL import Image
    im = img.convert("RGB")
    w, h = im.size
    s = 800 / max(w, h)
    if s < 1.0:
        im = im.resize((max(1, int(w * s)), max(1, int(h * s))),
                       Image.BILINEAR)
    arr = np.asarray(im, dtype=np.int16)
    chroma = arr.max(axis=2) - arr.min(axis=2)
    return float(np.percentile(chroma, 99)) > chroma_p99
