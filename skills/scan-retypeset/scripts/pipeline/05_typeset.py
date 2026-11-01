#!/usr/bin/env python3
"""05_typeset: rebuild the output page from the per-page dataset.

- Figure placed at the mapped frame bbox (source mode: exact original
  coordinates; a4 mode: uniform scale, original proportions).
- Each text line's baseline is derived from its original measured ink row
  (the >=3% row-average scan definition, i.e. the *dense* ink top): a probe
  render of the line in the output font gives the baseline offset of that
  measured row, so the output's measured row lands on the original's.
- Two passes: pass 1 uses the probe estimate, then the page is rasterized
  (150 dpi, the verify dpi) and the *actual* rendered row is measured with
  the identical scan; pass 2 applies the residual delta. PDF rasterization
  (poppler) sits a few pt below the PIL probe for sparse glyph terminals,
  and the second pass removes that offset so the figure/text gap the user
  sees matches the original by construction.
- Full lines get per-character tracking so each line spans exactly its
  original width (recreates the original justification); lines mixing
  CJK with digits/Latin use advance-weighted gaps (B5 weighted slots,
  lib.common.weighted_gaps) so narrow digits are not over-spaced.
- LXGW WenKai embedded; text selectable.

Usage: .venv/bin/python pipeline/05_typeset.py --page 10
Reads  data/layout/p010.json, data/layout/p010.geo.json, data/images/p010_bw.png
Writes out/pages/p010.pdf
"""
import argparse
import json as _json
import statistics
import subprocess

from PIL import Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from lib.common import (A4_H, A4_W, FONT_NAME, FONT_PATH, INK_EM, ROOT,
                        TYPESET, gap_scan, line_dense_top_pt, load_json,
                        map_pt, rebuild_map, weighted_gaps)

# maximum per-character tracking used to stretch a line to its original
# width, in em. 0.35 covers justified lines of most mid-century books;
# the stretch amount is always exactly (target - natural), so a larger cap
# only matters when the original spacing was wide.
TRACK_CAP_EM = float(TYPESET.get("track_cap_em", 0.35))

VERIFY_DPI = 150
PPT = VERIFY_DPI / 72.0

PDF_FONT = None


def line_font_pt(glyph_h_px: int, rm: dict) -> float:
    h_pt = glyph_h_px * rm["scale"]
    return max(6.0, h_pt / INK_EM)


def ascii_unit_em(c) -> float:
    """Width in em of one ASCII (non-space) char in the output font.

    Measured, not assumed: proportional LXGW WenKai digits are ~0.55em,
    LXGWWenKaiMono digits are 1.0em. The pre-estimate below only picks the
    font size; the final line width is exact via c.stringWidth + tracking.
    """
    return c.stringWidth("0123456789", FONT_NAME, 10.0) / 10.0 / 10.0


def is_full_line(band: list, max_x1: int, page_w: int) -> bool:
    return band[2] >= max_x1 - 0.03 * page_w


def _typeset_once(page: int, dtop_delta_pt: float):
    """One typesetting pass. `dtop_delta_pt` shifts every line's measured
    dense top down by this many pt (the two-pass rasterizer correction)."""
    global PDF_FONT
    if PDF_FONT is None:
        pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))
        PDF_FONT = True
    dataset = load_json(ROOT / "data" / "layout" / f"p{page:03d}.json")
    geo = load_json(ROOT / "data" / "layout" / f"p{page:03d}.geo.json")
    rm = rebuild_map(geo)
    s = rm["scale"]
    bands = geo.get("text_bands", [])
    glyph_hs = geo.get("glyph_heights_px", [0] * len(bands))
    lines = dataset["text"]["lines"]
    page_w = geo["render_size"][0]
    max_x1 = max((b["bbox"][2] for b in bands), default=0)

    out = ROOT / "out" / "pages" / f"p{page:03d}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out), pagesize=(A4_W, A4_H))

    # figure at the mapped frame ink extent (the crop box — the fitted bbox
    # can sit inside a thick/blurry frame stroke, and the crop includes the
    # full stroke so the output frame matches the original)
    crop_box = geo.get("frame_ink_bbox") or geo["frame"]["bbox"]
    cx0, cy0, cx1, cy1 = crop_box
    mx0, my0 = map_pt(rm, cx0, cy0)
    fw, fh = (cx1 - cx0) * s, (cy1 - cy0) * s
    c.drawImage(str(ROOT / "data" / "images" / f"p{page:03d}_bw.png"),
                mx0, A4_H - my0 - fh, width=fw, height=fh)

    # margin design strips (running title tab / edge line / red seal /
    # left-side emblem), kept 1:1 with the original page layout; drawn
    # after the figure and before the text so lines that overhang the
    # frame stay on top
    page_h = geo["render_size"][1]
    for side, key, fname in (("left", "margin_crop_box_left",
                              f"p{page:03d}_margin_left.png"),
                             ("right", "margin_crop_box",
                              f"p{page:03d}_margin.png")):
        mimg = ROOT / "data" / "images" / fname
        if mimg.exists():
            mbox = geo.get(key) or ([0, 0, geo["frame"]["bbox"][0], page_h]
                                    if side == "left" else
                                    [geo["frame"]["bbox"][2], 0, page_w,
                                     page_h])
            msx0, msy0 = map_pt(rm, mbox[0], mbox[1])
            msx1, msy1 = map_pt(rm, mbox[2], mbox[3])
            c.drawImage(str(mimg), msx0, A4_H - msy1, width=msx1 - msx0,
                        height=msy1 - msy0)

    # text lines
    gap = geo.get("figure_text_gap")
    placed = []
    # ---- per-page unified font size. The original book sets every line of
    # a panel at ONE type size; per-line fitting produced visibly different
    # sizes across lines (up to 2x on pages where the first line's ink
    # measurement was clipped), which reads as jumpy. Compute each line's
    # natural size (ink height, width-capped for full lines), then use the
    # median for every line: full lines still reach the right edge (the
    # median can never exceed the tightest full line's width fit), ragged
    # lines keep their natural length, and tracking fills the width.
    line_meas = []
    aem = ascii_unit_em(c)
    for line in lines:
        bbox = line["line_bbox_in_render"]
        ink = line.get("ink_bbox_in_render") or bbox
        x0_pt, _ = map_pt(rm, bbox[0], 0)
        x1_pt = map_pt(rm, bbox[2], 0)[0]
        line_h = bbox[3] - bbox[1]
        band_idx = line.get("band_idx", line["idx"])
        gh = glyph_hs[band_idx] if band_idx < len(glyph_hs) else line_h
        ink_h = min(gh, 0.9 * line_h) if gh and gh > 0 else 0.9 * line_h
        fpt_ink = line_font_pt(ink_h, rm)
        text = line["simplified"]
        full = is_full_line(bbox, max_x1, page_w)
        target = x1_pt - x0_pt
        n_units = sum(aem if (ord(ch) < 0x2E80 and not ch.isspace()) else 1.0
                      for ch in text)
        fpt_w = target / n_units if (target > 10 and n_units >= 2) else fpt_ink
        fpt_line = min(fpt_ink, fpt_w)
        line_meas.append({"line": line, "fpt_ink": fpt_ink, "fpt_w": fpt_w,
                          "fpt_line": fpt_line, "full": full, "target": target,
                          "n_units": n_units, "text": text})
    fpt_ink_med = statistics.median([m["fpt_ink"] for m in line_meas])
    fpt_page = statistics.median([m["fpt_line"] for m in line_meas])
    full_fits = [m["fpt_w"] for m in line_meas
                 if m["full"] and m["target"] > 10 and m["n_units"] >= 8]
    if full_fits:
        fpt_page = min(fpt_page, min(full_fits))
    fpt_page = max(fpt_page, 0.60 * fpt_ink_med)
    for m in line_meas:
        bbox = m["line"]["line_bbox_in_render"]
        ink = m["line"].get("ink_bbox_in_render") or bbox
        li = m["line"]["idx"]
        x0_pt, _ = map_pt(rm, bbox[0], 0)
        y_center_pt = map_pt(rm, 0, (ink[1] + ink[3]) / 2)[1]
        fpt = fpt_page
        text = m["text"]
        full, target, n_units = m["full"], m["target"], m["n_units"]
        c.setFont(FONT_NAME, fpt)
        natural = c.stringWidth(text, FONT_NAME, fpt)
        track = 0.0
        gaps = None
        if target > 10 and n_units >= 2 and natural < target:
            # B5 weighted slots: mixed CJK + digit/Latin lines get gaps
            # weighted by the adjacent advances (uniform tracking would
            # over-space the narrow digits); pure full-width lines keep
            # the historic uniform path (identical output).
            if any(0x2E80 <= ord(ch) for ch in text) and any(
                    ord(ch) < 0x2E80 and not ch.isspace() for ch in text):
                gaps = weighted_gaps(text, FONT_NAME, fpt,
                                     target - natural, fpt * TRACK_CAP_EM)
                if max(gaps) < fpt * 0.005:
                    gaps = [0.0] * (len(text) - 1)
                track = sum(gaps) / len(gaps)
            else:
                track = min((target - natural) / max(1.0, n_units - 1),
                            fpt * TRACK_CAP_EM)
                if track < fpt * 0.005:
                    track = 0.0
        # Placement target: the original's *measured* ink row (>=3% row
        # average — the same row definition the verify scan uses). For the
        # first line below the frame that is the figure_text_gap anchor, so
        # the visible figure/text gap is reproduced exactly.
        ink_top_px = ink[1]
        if li == 0 and gap and gap.get("first_text_ink_top_px"):
            ink_top_px = gap["first_text_ink_top_px"]
        target_top_pt = map_pt(rm, 0, ink_top_px)[1]
        # Baseline = measured dense top + baseline offset of that row in
        # this font at this size (probe render, verify-scan row rule).
        d_top = line_dense_top_pt(text, FONT_PATH, fpt) + dtop_delta_pt
        baseline_top = target_top_pt + d_top
        tobject = c.beginText(x0_pt, A4_H - baseline_top)
        tobject.setFont(FONT_NAME, fpt)
        tobject.setFillColorRGB(0, 0, 0)
        if gaps:
            # B5: per-character draws with advance-weighted gaps.
            # reportlab emits Tc only for non-zero values: force a reset
            # with -0.001 for zero/tiny gaps.
            for i, ch in enumerate(text):
                g = gaps[i] if i < len(gaps) else 0.0
                tobject.setCharSpace(g if g >= fpt * 0.005 else -0.001)
                tobject.textOut(ch)
        else:
            # reportlab emits Tc only for non-zero values: setCharSpace(0)
            # is a no-op, so a previous line's tracking leaks into this one
            # (cells stretched to fpt + leaked_track). Force a reset with
            # -0.001.
            tobject.setCharSpace(track if track else -0.001)
            tobject.textLine(text)
        c.drawText(tobject)
        placed.append({"idx": li, "x0_pt": round(x0_pt, 2),
                       "y_center_pt": round(y_center_pt, 2),
                       "ink_bbox_pt": [round(map_pt(rm, 0, ink[1])[1], 2),
                                       round(map_pt(rm, 0, ink[3])[1], 2)],
                       "font_pt": round(fpt, 2), "track": round(track, 3),
                       "wtrack": bool(gaps), "full": full, "text": text})
    c.save()

    # stash placement info for verification (incl. figure->text gap audit)
    gap_meta = None
    if gap and placed:
        frame_bottom_pt = map_pt(rm, 0, gap["frame_bbox_bottom_px"])[1]
        stroke_bottom_pt = map_pt(rm, 0, gap["frame_stroke_bottom_px"])[1]
        first_ink_top_pt = map_pt(rm, 0, gap["first_text_ink_top_px"])[1]
        gap_meta = {
            "original": gap,
            "expected_pt": round(gap["gap_px"] * s, 2),
            "frame_bbox_bottom_pt": round(frame_bottom_pt, 2),
            "frame_stroke_bottom_pt": round(stroke_bottom_pt, 2),
            "first_text_ink_top_pt": round(first_ink_top_pt, 2),
            "dtop_delta_pt": round(dtop_delta_pt, 3),
        }
    vdir = ROOT / "data" / "verify"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"p{page:03d}.placed.json").write_text(
        _json.dumps({"map": rm, "placed": placed, "figure_text_gap": gap_meta},
                    ensure_ascii=False, indent=1))
    return out


def _measure_actual_delta(page: int, geo: dict, out_pdf, rm: dict):
    """Rasterize the just-written page at the verify dpi and measure, with
    the identical gap_scan, how far the rendered first dark row of the text
    is from its placement target. Returns a pt delta (0 when nothing to
    correct)."""
    import numpy as np
    import cv2
    tmp = f"/tmp/typeset_chk_p{page:03d}"
    subprocess.run(["pdftoppm", "-png", "-r", str(VERIFY_DPI), str(out_pdf),
                    tmp], check=True, capture_output=True)
    img = cv2.imread(tmp + "-1.png")
    if img is None:
        return 0.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    fb = geo["frame"]["bbox"]
    gap = geo.get("figure_text_gap")
    if not gap or not gap.get("first_text_ink_top_px"):
        return 0.0
    s = rm["scale"]
    bbox_bottom_pt = map_pt(rm, 0, gap["frame_bbox_bottom_px"])[1]
    target_row = int(map_pt(rm, 0, gap["first_text_ink_top_px"])[1] * PPT)
    fx0 = int(map_pt(rm, fb[0] + 40, 0)[0] * PPT)
    fx1 = int(map_pt(rm, fb[2] - 40, 0)[0] * PPT)
    k = s * PPT
    y_start = int(bbox_bottom_pt * PPT) + 1
    _, first_row, case_b = gap_scan(gray, y_start, fx0, fx1,
                                    max(12, int(20 * k)), int(160 * k))
    if case_b or first_row is None:
        return 0.0
    return (first_row - target_row) / PPT


def typeset_page(page: int):
    geo = load_json(ROOT / "data" / "layout" / f"p{page:03d}.geo.json")
    rm = rebuild_map(geo)
    out = _typeset_once(page, 0.0)
    delta = _measure_actual_delta(page, geo, out, rm)
    if abs(delta) > 0.5:
        out = _typeset_once(page, delta)
    print("typeset", page, "->", out,
          ("(2-pass delta %.2f pt)" % delta) if abs(delta) > 0.5 else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", type=int, required=True)
    args = ap.parse_args()
    typeset_page(args.page)


if __name__ == "__main__":
    main()
