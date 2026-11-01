"""S5/S6: re-typeset processed pages onto the output page + assemble final PDF.

Per page (from NNN.json):
  - global scale s (largest content bbox -> OUT box), content centered X,
    top-anchored
  - whitened images placed at original relative positions
  - rules (colophon frame lines) redrawn
  - vlines (vertical CJK columns, title page) drawn char by char
  - lines drawn with the book font (embedded as a subset), baseline offset
    calibrated on the baseline page (BASE_OFF)
  -> work/pages/NNN_out.png preview

book.json keep_pages (color cover ...): original color render inserted as-is
(contain-fit). book.json drop_pages: not in the output.

Geometry fidelity: every line/token keeps its original relative (x, y), so
line count, first/last char, paragraph-number positions, two-column layout
and blank lines (large y gaps) are preserved by construction.

--assemble : build out/<out_pdf> + verification (page count, text layer vs
             JSON per page, font embedded)

Usage (from src/):
  python s5_typeset.py 4            # preview one page
  python s5_typeset.py --assemble
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

from bookcfg import (OUT, PAGES, WORK, drop_pages, ensure_dirs, font_path,
                     keep_pages, load_page_json, margin_box, out_pdf,
                     page_count, pnn, process_pages, render_name)
from s2_segment import render_page

BASE_OFF = 0.13      # baseline below line bbox bottom (em) - calibrate on baseline page
VL_ADV = 1.14        # vertical column char advance (em)

SCALE_CACHE = WORK / "scale.json"


def content_scale() -> float:
    """Global scale: fits the largest content bbox into the OUT box."""
    if SCALE_CACHE.exists():
        return float(json.loads(SCALE_CACHE.read_text())["scale"])
    _, _, _, bw, bh = margin_box()
    worst = 0.0
    for n in process_pages():
        f = PAGES / f"{pnn(n)}.json"
        if not f.exists():
            continue
        o = json.loads(f.read_text())
        cb = o.get("content_bbox")
        if not cb:
            continue
        worst = max(worst, (cb[2] - cb[0]) / bw, (cb[3] - cb[1]) / bh)
    if worst == 0:
        return 1.0
    scale = 1.0 / worst * 0.999
    SCALE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SCALE_CACHE.write_text(json.dumps({"scale": scale}))
    return scale


def page_scale(n: int, s: float) -> float:
    o = load_page_json(n)
    cb = o.get("content_bbox")
    if not cb:
        return s
    _, _, _, bw, bh = margin_box()
    cw, ch = cb[2] - cb[0], cb[3] - cb[1]
    return min(s, bw / cw * 0.999, bh / ch * 0.999)


def font_size(o: dict, s: float) -> float:
    hs = [l["bbox"][3] - l["bbox"][1] + 1 for l in o["lines"]]
    if not hs:
        return 20.0
    return float(np.median(hs)) * s


def typeset_page(n: int, doc: pymupdf.Document | None = None,
                 preview: bool = True, s: float | None = None):
    """Typeset page n into `doc` (appended). Returns preview PIL or None."""
    o = load_page_json(n)
    own_doc = doc is None
    if own_doc:
        doc = pymupdf.Document()
    pw, ph, margin, _, _ = margin_box()
    cb = o["content_bbox"]
    cx0, cy0, cx1, cy1 = cb
    s = page_scale(n, s or content_scale())
    cw, ch = (cx1 - cx0) * s, (cy1 - cy0) * s
    x0 = (pw - cw) / 2
    y0 = margin

    page = doc.new_page(width=pw, height=ph)
    font = None
    if o["lines"] or o.get("vlines"):
        font = pymupdf.Font(fontfile=str(font_path()))

    # ---- images
    for img in o.get("images", []):
        x0i, y0i, x1i, y1i = img["bbox"]
        px = PAGES / f"{pnn(n)}_img{img['id']}.png"
        r = pymupdf.Rect(x0 + (x0i - cx0) * s, y0 + (y0i - cy0) * s,
                         x0 + (x1i - cx0) * s, y0 + (y1i - cy0) * s)
        if px.exists():
            page.insert_image(r, filename=str(px))
        else:
            print(f"  WARN: missing image {px.name}, using raw crop")

    # ---- rules (colophon boxes)
    for rb in o.get("rules", []):
        x0i, y0i, x1i, y1i = rb
        r = pymupdf.Rect(x0 + (x0i - cx0) * s, y0 + (y0i - cy0) * s,
                         x0 + (x1i - cx0) * s, y0 + (y1i - cy0) * s)
        if r.get_area() > 0:
            page.draw_rect(r, color=(0, 0, 0),
                           width=max(0.8, r.height if r.height < r.width
                                     else r.width), fill=None)

    # ---- text lines
    F = font_size(o, s)
    tw = pymupdf.TextWriter(page.rect) if font else None
    for l in o["lines"]:
        toks = [t for t in l.get("tokens", []) if t.get("text")]
        if not toks and not l.get("text"):
            print(f"  WARN: page {n} line {l['id']} has no text yet")
            continue
        x0i, y0i, x1i, y1i = l["bbox"]
        F_l = l["fontsize_src"] * s if l.get("fontsize_src") else F
        # fit-to-width: the embedded CJK font is often slightly wider than
        # the original typeface; if the drawn run would run past the right
        # margin, TextWriter silently clips the tail (last char of the line
        # disappears). Shrink this line to fit (failure catalog F27).
        if toks:
            run_w = sum(font.text_length(t["text"], fontsize=F_l)
                        for t in toks)
            for a, b in zip(toks, toks[1:]):
                run_w += max(0, (b["x0"] - a["x1"])) * s
            x_start = x0 + (toks[0]["x0"] - cx0) * s
        else:
            run_w = font.text_length(l["text"], fontsize=F_l)
            x_start = x0 + (x0i - cx0) * s
        avail = pw - margin - x_start
        if run_w > avail and run_w > 0:
            F_l *= avail / run_w
        yb = y0 + (y1i - cy0) * s - BASE_OFF * F_l
        if toks:
            # each token at its own original x (keeps 段号 gap / columns)
            for t in toks:
                x = x0 + (t["x0"] - cx0) * s
                tw.append((x, yb), t["text"], font=font, fontsize=F_l)
        else:
            x = x0 + (x0i - cx0) * s
            tw.append((x, yb), l["text"], font=font, fontsize=F_l)

    # ---- vertical columns (title page)
    for v in o.get("vlines", []):
        Fv = v["fontsize"]
        x = x0 + (v["x"] - cx0) * s
        ytop = y0 + (v["y"] - cy0) * s
        cx = x + Fv / 2
        for i, ch in enumerate(v["text"]):
            if ch == " ":
                continue
            yb = ytop + i * Fv * VL_ADV + 0.88 * Fv
            tw.append((cx - Fv / 2, yb), ch, font=font, fontsize=Fv)

    # single write: writing twice would duplicate the text layer
    # (historical bug when a page had both lines and vlines)
    if tw:
        tw.write_text(page)

    if not preview and own_doc:
        return None
    if preview:
        pix = page.get_pixmap(dpi=100)
        prev = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        prev.save(PAGES / f"{pnn(n)}_out.png")
        return prev
    return None


def keep_page(doc: pymupdf.Document, n: int) -> None:
    """Insert a keep page as-is (contain fit) using its color render."""
    pw, ph = margin_box()[:2]
    px = PAGES / render_name(n, color=True)
    if not px.exists():
        render_page(n, color=True)
    img = Image.open(px)
    iw, ih = img.size
    ar = iw / ih
    box_ar = pw / ph
    if ar > box_ar:
        w = pw
        h = pw / ar
    else:
        h = ph
        w = ph * ar
    x = (pw - w) / 2
    y = (ph - h) / 2
    doc.new_page(width=pw, height=ph).insert_image(
        pymupdf.Rect(x, y, x + w, y + h), filename=str(px))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def verify(doc: pymupdf.Document, doc_pages: list[int]) -> tuple:
    """doc_pages: source page numbers in output order (keep+process)."""
    probs = []
    if doc.page_count != len(doc_pages):
        probs.append(f"page count {doc.page_count} != {len(doc_pages)}")
    for pno, n in enumerate(doc_pages):
        try:
            o = load_page_json(n)
        except FileNotFoundError:
            continue
        if not o.get("lines"):
            continue
        want = "".join(l["text"] for l in sorted(o["lines"],
                                                 key=lambda l: (l["bbox"][1],
                                                                l["bbox"][0])))
        # PDF text extraction inserts spaces at token gaps and may report
        # a different Unicode value for the same glyph (e.g. \u2219 for \u00b7
        # in LXGW) — normalize whitespace before comparing (F30).
        got = re.sub(r"\s+", "", doc[pno].get_text())
        want = re.sub(r"\s+", "", want)
        if got != want:
            for i, (a, b) in enumerate(zip(want, got)):
                if a != b:
                    probs.append(
                        f"p{n} text mismatch @char {i}: want …{want[max(0, i-8):i+8]}… got …{got[max(0, i-8):i+8]}…")
                    break
            else:
                probs.append(
                    f"p{n} text mismatch (len {len(want)} vs {len(got)})")
    fonts = set()
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts():
            fonts.add((f[3], f[1]))  # (basefont, ext)
    tag = _norm(Path(font_path()).stem)
    if not any(tag in _norm(b or "") for b, _ in fonts):
        probs.append(f"{Path(font_path()).stem} font not embedded")
    return probs, fonts


def assemble() -> None:
    ensure_dirs()
    s = content_scale()
    print(f"global scale = {s:.5f} (delete work/scale.json to recompute)")
    doc = pymupdf.Document()
    keep = keep_pages()
    drop = drop_pages()
    total = page_count()
    doc_pages: list[int] = []
    for n in range(1, total + 1):
        if n in drop:
            print(f"  p{n} ... dropped")
            continue
        if n in keep:
            keep_page(doc, n)
            doc_pages.append(n)
            print(f"  p{n} ... kept as-is")
            continue
        if not (PAGES / f"{pnn(n)}.json").exists():
            print(f"  p{n} ... SKIP: no json")
            continue
        typeset_page(n, doc, preview=True, s=s)
        doc_pages.append(n)
        print(f"  p{n} ... ok")
    out = out_pdf().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out), garbage=3, deflate=True)
    probs, fonts = verify(doc, doc_pages)
    print(f"\nsaved {out} ({out.stat().st_size / 1e6:.1f} MB)")
    print("fonts:", fonts)
    for p in probs:
        print("PROBLEM:", p)
    if not probs:
        print("verification OK")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", type=int, nargs="*")
    ap.add_argument("--assemble", action="store_true")
    a = ap.parse_args()
    if a.assemble:
        assemble()
        return
    if not a.pages:
        ap.error("pages or --assemble")
    s = content_scale()
    for n in a.pages:
        typeset_page(n, None, preview=True, s=s)
        print(f"preview -> {pnn(n)}_out.png")


if __name__ == "__main__":
    sys.exit(main())
