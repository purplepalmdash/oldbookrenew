"""S2: render page (grayscale, book.json render_dpi) and segment layout.

Outputs per page:
  work/pages/NNN_<dpi>.png  grayscale render
  work/pages/NNN_L{i}.png   per text-line crop (for LLM reading / verification)
  work/pages/NNN_dbg.png    debug overlay (frame / lines / rules / tokens)
  work/pages/NNN.json       segmentation result (updated, keeps prior fields)

Usage (from src/):
  python s2_segment.py 4                 # one page (auto frame detection)
  python s2_segment.py 3 --no-frame      # text-only page (no illustration frame)
  python s2_segment.py 138 --no-frame    # colophon: rules recorded, lines detected
  python s2_segment.py 62 --line-box 400,520 --line-box 530,650  # dirty scan
  python s2_segment.py 2 --color-render  # also keep a color render (ink removal)

Frame priority: llm_frame_inner in NNN.json (LLM-read inner corners) is the
final authority; the auto long-line frame is only a hint. book.json
no_frame_pages skip auto detection. Pages with llm_done=true are skipped
(their layout is final).

Note: assumes horizontal text lines. For vertical (line-bound) books see
SKILL.md "adaptation notes".
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from bookcfg import (PAGES, cfg, ensure_dirs, no_frame_pages, pnn,
                     render_name, save_page_json, src_pdf)


# ---------------------------------------------------------------- numpy utils
def otsu(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    total_sum = (hist * np.arange(256)).sum()
    sum_b, w_b, best, thr = 0.0, 0.0, 0.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        var = w_b * w_f * ((sum_b / w_b) - (total_sum - sum_b) / w_f) ** 2
        if var > best:
            best, thr = var, t
    return int(thr)


def render_page(n: int, color: bool = False) -> str:
    """Render page n; returns path of the (grayscale unless color) PNG."""
    dpi = cfg()["render_dpi"]
    suffix = f"{dpi}c" if color else str(dpi)
    out = PAGES / render_name(n, color=color)
    if out.exists():
        return str(out)
    prefix = PAGES / f"{pnn(n)}_{suffix}-src"
    subprocess.run(
        ["pdftoppm", "-png", *([] if color else ["-gray"]),
         "-r", str(dpi), "-f", str(n), "-l", str(n),
         str(src_pdf().resolve()), str(prefix)],
        check=True,
    )
    # pdftoppm zero-pads the page number to the document's page count
    # width, so discover the file instead of assuming a width.
    rendered = sorted(PAGES.glob(f"{pnn(n)}_{suffix}-src-*.png"))
    if not rendered:
        raise SystemExit(f"page {n}: pdftoppm produced no output")
    img = Image.open(rendered[0]).convert("L" if not color else "RGB")
    img.save(out)
    rendered[0].unlink()
    return str(out)


# ----------------------------------------------------------------- detection
def find_frame(binm: np.ndarray):
    """Outermost long lines => illustration frame. Returns (t,l,b,r) or None."""
    H, W = binm.shape
    row_dark = binm.sum(axis=1)
    col_dark = binm.sum(axis=0)
    h_lines = np.where(row_dark > 0.55 * W)[0]
    v_lines = np.where(col_dark > 0.50 * H)[0]
    if len(h_lines) < 2 or len(v_lines) < 2:
        return None
    t, b = int(h_lines[0]), int(h_lines[-1])
    l, r = int(v_lines[0]), int(v_lines[-1])
    if b - t < 0.25 * H or r - l < 0.30 * W:
        return None
    return (t, l, b, r)


def find_lines(binm: np.ndarray, top: int, min_height: int = 12,
               thr_frac: float = 0.010) -> list[dict]:
    """Horizontal text-line (and thin-rule) boxes in rows >= top."""
    H, W = binm.shape
    sub = binm[top:, :]
    row_dark = sub.sum(axis=1)
    thr = max(20.0, thr_frac * W)
    on = row_dark > thr
    boxes = []
    i, n = 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j + 1 < n and (on[j + 1] or (j + 2 < n and on[j + 2])):
                j += 1
            t, b = top + i, top + j
            seg = sub[i:j + 1, :]
            cols = np.where(seg.sum(axis=0) > 0)[0]
            if len(cols):
                boxes.append({"bbox": [int(cols[0]), t, int(cols[-1]), b],
                              "thin": (b - t + 1) < min_height})
            i = j + 1
        else:
            i += 1
    return boxes


def est_tokens(line_bin: np.ndarray, line_h: int) -> list[dict]:
    """Split a binarized line into tokens at gaps >= 0.9*line_h.
    Returns [{x0,x1,est_chars}] - est = round(width/line_h) (CJK ~square).
    Tokens preserve the original x positions (paragraph numbers, columns).
    """
    col = line_bin.sum(axis=0)
    on = col > 0
    toks = []
    i, n = 0, len(on)
    gap_thr = int(0.9 * line_h)
    while i < n:
        if on[i]:
            j, gap = i, 0
            while j + 1 < n:
                if on[j + 1]:
                    gap = 0
                else:
                    gap += 1
                    if gap >= gap_thr:
                        break
                j += 1
            toks.append({"x0": i, "x1": j + 1,
                         "est_chars": max(1, int(round((j + 1 - i) / line_h)))})
            i = j + 1 + gap
        else:
            i += 1
    return toks


# ---------------------------------------------------------------------- main
def segment(n: int, no_frame: bool = False,
            manual_boxes: list[tuple[int, int]] | None = None) -> dict:
    ensure_dirs()
    manual_boxes = manual_boxes or []
    jpath = PAGES / f"{pnn(n)}.json"
    prev_all = json.loads(jpath.read_text()) if jpath.exists() else {}
    if prev_all.get("llm_done"):
        print(f"page {n}: llm_done - keeping LLM layout, skipping")
        return prev_all
    path = render_page(n)
    gray = np.asarray(Image.open(path).convert("L"), dtype=np.uint8)
    H, W = gray.shape
    thr = otsu(gray)
    binm = gray < thr
    # despeckle
    bm_img = Image.fromarray((binm * 255).astype(np.uint8))
    bm_img = bm_img.filter(ImageFilter.MedianFilter(3))
    binm = np.asarray(bm_img) > 127

    obj = {"page": n, "class": prev_all.get("class", "body"),
           "lines": [], "rules": [], "images": [],
           "flags": list(prev_all.get("flags", []))}

    frame = None if no_frame else find_frame(binm)
    if frame:
        t, l, b, r = frame
        inside = binm[t + 6:b - 5, l + 6:r - 5]
        density = float(inside.mean())
        if density < 0.08:
            obj["flags"].append(
                f"frame interior sparse ({density:.2f}) - check manually")
            frame = None
    # LLM corners are the final authority; auto frame is only a hint
    llm_inner = prev_all.get("llm_frame_inner")
    llm_corners = prev_all.get("llm_frame_corners")
    if llm_inner:
        x0, y0, x1, y1 = llm_inner
        pad = 2  # safety margin inside the frame line
        obj["images"] = [{"id": 0, "bbox": [x0 + pad, y0 + pad,
                                            x1 - pad, y1 - pad],
                          "source": "llm-visual-inner"}]
        top = y1 + 10
        if llm_corners:
            obj["llm_frame_corners"] = llm_corners
        obj["llm_frame_inner"] = llm_inner
    elif frame:
        t, l, b, r = frame
        pad = 8
        obj["images"] = [{"id": 0, "bbox": [l + pad, t + pad, r - pad,
                                            b - pad],
                          "source": "auto-frame-hint"}]
        top = b + 10
    else:
        obj["images"] = []
        top = 0
    obj["frame_hint"] = list(frame) if frame else None

    boxes = [] if manual_boxes else find_lines(binm, top)
    if manual_boxes:
        for (y0, y1) in manual_boxes:
            seg = binm[max(0, y0):y1 + 1, :]
            cols = np.where(seg.sum(axis=0) > 0)[0]
            if len(cols):
                boxes.append({"bbox": [int(cols[0]), int(y0), int(cols[-1]),
                                       int(y1)], "thin": False})
    # classify: rules (thin), noise (medium-thin), text (tall)
    hs = [b["bbox"][3] - b["bbox"][1] + 1 for b in boxes]
    max_h = max(hs) if hs else 0
    lines, rules, noise = [], [], []
    for k, box in enumerate(boxes):
        x0, y0, x1, y1 = box["bbox"]
        h = y1 - y0 + 1
        if not manual_boxes:
            if h < 12:
                rules.append(box["bbox"])
                continue
            if max_h and h < 0.5 * max_h:
                noise.append(box["bbox"])
                continue
        crop = gray[max(0, y0 - 4):y1 + 5, max(0, x0 - 6):x1 + 7]
        Image.fromarray(crop).save(PAGES / f"{pnn(n)}_L{k}.png")
        lb = binm[max(0, y0 - 4):y1 + 5, max(0, x0 - 6):x1 + 7]
        toks = est_tokens(lb, h)
        offx = max(0, x0 - 6)
        entry = {"id": k, "bbox": box["bbox"], "crop": f"{pnn(n)}_L{k}.png",
                 "tokens": [{"x0": offx + t["x0"], "x1": offx + t["x1"],
                             "est_chars": t["est_chars"], "text": "",
                             "verified": False} for t in toks],
                 "text": "", "verified": False}
        lines.append(entry)
    # keep previously verified texts when re-segmenting
    for e in lines:
        for p in prev_all.get("lines", []):
            if p.get("bbox") == e["bbox"]:
                for te, pt in zip(e["tokens"], p.get("tokens", [])):
                    if pt.get("text"):
                        te["text"], te["verified"] = pt["text"], pt["verified"]
                e["text"] = p.get("text", "")
                e["verified"] = p.get("verified", False)
                break
    obj["lines"] = lines
    obj["rules"] = rules
    if noise:
        obj["noise_boxes"] = noise
        obj["flags"].append(f"{len(noise)} noise band(s) ignored")
    obj["bin_thr"] = int(thr)

    # content bbox
    boxes_all = [img["bbox"] for img in obj["images"]] + [l["bbox"]
                                                          for l in lines]
    if boxes_all:
        x0 = min(b[0] for b in boxes_all)
        y0 = min(b[1] for b in boxes_all)
        x1 = max(b[2] for b in boxes_all)
        y1 = max(b[3] for b in boxes_all)
        obj["content_bbox"] = [x0, y0, x1, y1]

    # debug overlay
    dbg = Image.open(path).convert("RGB")
    dr = ImageDraw.Draw(dbg)
    if obj["images"]:
        bx = obj["images"][0]["bbox"]
        dr.rectangle([bx[0], bx[1], bx[2], bx[3]], outline="red", width=4)
    if frame:
        t, l, b, r = frame
        dr.rectangle([l, t, r, b], outline="orange", width=2)
    for lbox in lines:
        dr.rectangle(lbox["bbox"], outline="blue", width=3)
        for tk in lbox["tokens"]:
            dr.line([tk["x0"], lbox["bbox"][1] - 8, tk["x1"],
                     lbox["bbox"][1] - 8], fill="magenta", width=3)
    for rbox in rules:
        dr.rectangle(rbox, outline="green", width=2)
    for nbox in noise:
        dr.rectangle(nbox, outline="yellow", width=2)
    dbg.save(PAGES / f"{pnn(n)}_dbg.png")

    save_page_json(n, obj)
    src = obj["images"][0]["source"] if obj["images"] else "no-image"
    print(f"page {n}: image={src} lines={len(lines)} rules={len(rules)} "
          f"content={obj.get('content_bbox')}")
    for e in lines:
        x0, y0, x1, y1 = e["bbox"]
        tks = " | ".join(
            f"[{t['x0']}-{t['x1']} est{t['est_chars']}]"
            f"{t['text'][:14] if t['text'] else ''}" for t in e["tokens"])
        print(f"  L{e['id']} h={y1 - y0 + 1:4d} {tks}")
    if obj["flags"]:
        print("  FLAGS:", obj["flags"])
    return obj


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", type=int, nargs="+")
    ap.add_argument("--no-frame", action="store_true")
    ap.add_argument("--line-box", action="append", default=[], metavar="Y0,Y1",
                    help="manual text-line y-range (repeatable, in order); "
                         "x auto-detected from ink. For dirty scans where "
                         "lines merge (failure catalog F1).")
    ap.add_argument("--color-render", action="store_true",
                    help="also render a color version (for red/blue ink removal)")
    a = ap.parse_args()
    nf = no_frame_pages()
    yboxes = [tuple(map(int, s.split(","))) for s in a.line_box]
    for n in a.pages:
        if a.color_render:
            render_page(n, color=True)
        segment(n, no_frame=a.no_frame or n in nf, manual_boxes=yboxes)


if __name__ == "__main__":
    sys.exit(main())
