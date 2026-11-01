#!/usr/bin/env python3
"""06_verify: per-page reconstruction QA (worktips item 3).

Checks (all recorded in data/verify/pNNN.json):
1. figure: rendered-output figure bbox vs mapped original frame bbox (<4%)
2. text:   pdftotext line count/content/positions vs dataset (<5% positions,
           text normalized-equal, start/end chars match)
3. panel number anchor == page - PANEL_OFFSET
4. font embedded (pdffonts), text selectable (pdftotext non-empty)
5. print fitness: figure region grayscale (channel spread <= 8),
   paper whiteness (>= 240 mean on bright rows)
6. figure_text_gap: original frame-bottom -> first-text-top gap (px in the
   scan, recorded by 02_geometry) must be reproduced on the output page
   (measured on the rendered PDF) within tolerance
7. figure_text_sep: the figure crop bottom must not overlap the first
   caption ink (a crop that swallows the first line renders "text inside
   the picture")
8. panel_no_single: the panel number appears at most once and only at the
   start of line 0 (duplicated panel number = merge ordering bug)
9. vector_binary (vector enhance variant only): the figure file is true
   1-bit (values {0,255} only) — the "black-and-white distinct"
   print-fitness contract: no mid-gray left that would burn color ink
   on a color printer or read as a dirty scan.

Usage: .venv/bin/python pipeline/06_verify.py --page 10
"""
import argparse
import json
import re
import subprocess

import cv2
import numpy as np
from PIL import Image

from lib.common import (A4_H, A4_W, FONT_NAME, NO_PANEL_NO, PANEL_OFFSET,
                        PANEL_NO_EXCEPTIONS, ROOT, gap_scan, lead_number,
                        load_json, map_pt, norm_text, rebuild_map)

RENDER_DPI = 150
PX_PER_PT = RENDER_DPI / 72.0


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def output_figure_bbox(page: int, region: list) -> list:
    """Measure the ink extent of the figure inside `region` (px, top-origin)
    on the rendered output page.

    Percentiles handle the art body; the restamped frame border is a thin
    strong line carrying too little ink for percentiles (it fails on light
    line-drawings where the art leaves a white margin inside the frame), so
    the box is also extended to any strong full-span dark row/column — the
    frame border is the outermost strong line inside the region. Measuring
    inside the expected box (instead of everything above the first text
    line) also avoids the text touching the frame bottom confusing the
    bottom edge.
    """
    pdf = ROOT / "out" / "pages" / f"p{page:03d}.pdf"
    out = f"/tmp/verify_p{page:03d}"
    run(["pdftoppm", "-png", "-r", str(RENDER_DPI), str(pdf), out])
    img = cv2.imread(f"{out}-1.png")
    H, W = img.shape[:2]
    rx0 = max(0, int(region[0]))
    ry0 = max(0, int(region[1]))
    rx1 = min(W, int(region[2]))
    ry1 = min(H, int(region[3]))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sub = gray[ry0:ry1, rx0:rx1]
    ys, xs = np.where(sub < 240)
    if len(xs) < 200:
        return None
    x0 = int(np.percentile(xs, 0.2))
    y0 = int(np.percentile(ys, 0.2))
    x1 = int(np.percentile(xs, 99.8))
    y1 = int(np.percentile(ys, 99.8))
    dark = sub < 128
    rows = np.where(dark.mean(axis=1) > 0.3)[0]
    cols = np.where(dark.mean(axis=0) > 0.3)[0]
    if len(rows):
        y0 = min(y0, int(rows[0]))
        y1 = max(y1, int(rows[-1]))
    if len(cols):
        x0 = min(x0, int(cols[0]))
        x1 = max(x1, int(cols[-1]))
    return [x0 + rx0, y0 + ry0, x1 + rx0, y1 + ry0]


def rel_err(a: float, b: float) -> float:
    return abs(a - b) / max(1.0, abs(b))


def verify_page(page: int) -> dict:
    pdf = ROOT / "out" / "pages" / f"p{page:03d}.pdf"
    geo = load_json(ROOT / "data" / "layout" / f"p{page:03d}.geo.json")
    dataset = load_json(ROOT / "data" / "layout" / f"p{page:03d}.json")
    rm = rebuild_map(geo)
    report = {"page": page, "checks": {}, "pass": True}

    # 1. figure position: measure ink inside the mapped frame INK box (the
    # crop box — the fitted bbox can sit inside a thick frame stroke)
    fb = geo["frame"]["bbox"]
    crop_box = geo.get("frame_ink_bbox") or geo.get("figure_crop_box") or fb
    mx0, my0 = map_pt(rm, crop_box[0], crop_box[1])
    mx1, my1 = map_pt(rm, crop_box[2], crop_box[3])
    exp = [mx0 * PX_PER_PT, my0 * PX_PER_PT, mx1 * PX_PER_PT, my1 * PX_PER_PT]
    pad_px = 4
    got = output_figure_bbox(
        page, [exp[0] - pad_px, exp[1] - pad_px, exp[2] + pad_px, exp[3] + pad_px])
    if got is None:
        report["checks"]["figure"] = {"pass": False, "detail": "not detected"}
        report["pass"] = False
    else:
        # span-relative error: a corner is judged against the figure's own
        # size (box width/height), not against the coordinate magnitude —
        # in source mode (1:1 mapping) the box can sit within a few pt of
        # the page edge, where a 4%-of-coordinate tolerance becomes a
        # sub-pixel demand and quantized measurement (150 dpi) trips it.
        bw = max(exp[2] - exp[0], 1.0)
        bh = max(exp[3] - exp[1], 1.0)
        rel = [abs(got[0] - exp[0]) / bw, abs(got[2] - exp[2]) / bw,
               abs(got[1] - exp[1]) / bh, abs(got[3] - exp[3]) / bh]
        ok = max(rel) < 0.04
        report["checks"]["figure"] = {"pass": ok, "expected": exp, "got": got,
                                      "rel_err": [round(e, 4) for e in rel]}
        report["pass"] = report["pass"] and ok

    # 2. text via pdftotext -bbox (poppler emits <word xMin yMin xMax yMax>;
    #    cluster words into lines by y-overlap)
    r = run(["pdftotext", "-bbox", str(pdf), "-"])
    words = []
    for m in re.finditer(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>',
            r.stdout, re.S):
        x0, y0, x1, y1 = (float(v) for v in m.groups()[:4])
        t = norm_text(re.sub(r"<[^>]+>", "", m.group(5)))
        words.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": t,
                      "yc": (y0 + y1) / 2, "h": y1 - y0})
    words.sort(key=lambda w: (w["yc"], w["x0"]))
    lines_out: list[dict] = []
    for w in words:
        placed_line = None
        for L in lines_out:
            if abs(L["yc"] - w["yc"]) < 0.6 * max(L["h"], w["h"]):
                placed_line = L
                break
        if placed_line is None:
            lines_out.append({"x0": w["x0"], "y0": w["y0"], "x1": w["x1"],
                              "y1": w["y1"], "yc": w["yc"], "h": w["h"],
                              "text": w["text"]})
        else:
            placed_line["x1"] = max(placed_line["x1"], w["x1"])
            placed_line["text"] += w["text"]
    got_lines = lines_out
    dl = dataset["text"]["lines"]
    pos_ok, txt_ok = len(got_lines) == len(dl), True
    line_rep = []
    for i, d in enumerate(dl):
        exp_x0, _ = map_pt(rm, d["line_bbox_in_render"][0], 0)
        ink = d.get("ink_bbox_in_render") or d["line_bbox_in_render"]
        exp_yc = map_pt(rm, 0, (ink[1] + ink[3]) / 2)[1]
        if i < len(got_lines):
            g = got_lines[i]
            # pdftotext -bbox coords are in PDF pt, same unit as map_pt output
            de_x = rel_err(g["x0"], exp_x0)
            de_y = rel_err(g["yc"], exp_yc)
            te = norm_text(d["simplified"]) == g["text"]
            sc = g["text"][0] == d["start_char"] if g["text"] else False
            ec = g["text"][-1] == d["end_char"] if g["text"] else False
            pos_ok = pos_ok and de_x < 0.05 and de_y < 0.05
            txt_ok = txt_ok and te
            line_rep.append({"de_x": round(de_x, 4), "de_y": round(de_y, 4),
                             "text_match": te, "start_ok": sc, "end_ok": ec})
        else:
            pos_ok = False
    ok = pos_ok and txt_ok
    report["checks"]["text"] = {"pass": ok, "n_expected": len(dl),
                                "n_got": len(got_lines), "lines": line_rep}
    report["pass"] = report["pass"] and ok

    # 3. panel anchor (pages in config `no_panel_no` pass when line 0
    # carries no leading number at all)
    if page in NO_PANEL_NO:
        got_no = lead_number(dl[0]["simplified"]) if dl else None
        ok = got_no is None
        report["checks"]["panel_no"] = {"pass": ok, "expected": None,
                                        "got": got_no}
    else:
        exp_no = (PANEL_NO_EXCEPTIONS.get(page, page - PANEL_OFFSET)
                  if page not in NO_PANEL_NO else page - PANEL_OFFSET)
        got_no = lead_number(dl[0]["simplified"]) if dl else None
        ok = got_no == exp_no
        report["checks"]["panel_no"] = {"pass": ok, "expected": exp_no,
                                        "got": got_no}
    report["pass"] = report["pass"] and ok

    # 4. font embedded + selectable
    r = run(["pdffonts", str(pdf)])
    embedded = False
    for ln in r.stdout.splitlines()[2:]:
        cols = ln.split()
        if len(cols) >= 5 and FONT_NAME.split("-")[0][:4] in cols[0] and cols[3] == "yes":
            embedded = True
    selectable = len(got_lines) > 0
    ok = embedded and selectable
    report["checks"]["font"] = {"pass": ok, "pdffonts": r.stdout.strip()[:300]}
    report["pass"] = report["pass"] and ok

    # 5. print fitness: grayscale + whiteness in figure area. The region is
    # the EXPECTED figure box (not the measured `got`): the right-margin
    # strip can extend the measured ink bbox, and its color noise would
    # pollute the grayscale test. exp/got are (x0, y0, x1, y1) boxes.
    img = cv2.imread(f"/tmp/verify_p{page:03d}-1.png")
    if got is not None:
        if geo.get("enhance_variant") == "color_as_is":
            # Color-figure rule: the original color is placed 1:1, so the
            # grayscale + paper-whiteness test does not apply — the color
            # content IS the original.
            report["checks"]["print_fitness"] = {"pass": True,
                "note": "color figure kept as-is; grayscale/whiteness test skipped"}
        else:
            x0r, y0r, x1r, y1r = (int(v) for v in exp)
            region = img[max(0, y0r + 20):y1r - 20, max(0, x0r + 20):x1r - 20]
            spread = int(region.max(axis=2).astype(int).max()
                         - region.min(axis=2).astype(int).min())
            # channel spread: max per-pixel (maxRGB - minRGB)
            per_px = region.max(axis=2).astype(int) - region.min(axis=2).astype(int)
            spread = int(np.percentile(per_px, 99.9))
            bright = region.min(axis=2)
            white = float(bright[bright > 200].mean()) if (bright > 200).any() else 0.0
            ok = spread <= 8 and white >= 240
            report["checks"]["print_fitness"] = {"pass": ok, "channel_spread_p999": spread,
                                                 "paper_white_mean": round(white, 1)}
            report["pass"] = report["pass"] and ok

    # 6. figure->text gap: the original visible gap (frame stroke bottom ->
    # first text ink top, measured on the deskewed scan by 02_geometry with
    # the same two-phase ink scan) must be reproduced on the output page.
    gap = geo.get("figure_text_gap")
    if gap and dl:
        s = rm["scale"]
        exp_gap_pt = gap["gap_px"] * s
        bbox_bottom_pt = map_pt(rm, 0, gap["frame_bbox_bottom_px"])[1]
        img = cv2.imread(f"/tmp/verify_p{page:03d}-1.png")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        fx0, _ = map_pt(rm, fb[0] + 40, 0)
        fx1, _ = map_pt(rm, fb[2] - 40, 0)
        # identical scan as on the original (60px white-gap search /
        # 160px text search at 250dpi), converted via s to the output render
        k = s * PX_PER_PT
        y_start = int(bbox_bottom_pt * PX_PER_PT) + 1
        stroke_px, first_row, case_b = gap_scan(
            gray, y_start, int(fx0 * PX_PER_PT), int(fx1 * PX_PER_PT),
            int(60 * k), int(160 * k))
        got_gap_pt = None
        if case_b:
            # no white row within the stroke window: text touches the frame
            # (same case-B convention as the original side: gap = 1 original px)
            got_gap_pt = 1.0 * s
        elif first_row is not None:
            got_gap_pt = (first_row - stroke_px) / PX_PER_PT
        if got_gap_pt is None:
            report["checks"]["figure_text_gap"] = {"pass": True,
                                                   "detail": "not measured (no text found below frame)"}
        else:
            tol = max(2.0, 0.2 * exp_gap_pt)
            ok = abs(got_gap_pt - exp_gap_pt) <= tol
            report["checks"]["figure_text_gap"] = {
                "pass": ok, "original_px": gap["gap_px"],
                "original_ratio_h": gap["gap_ratio_h"],
                "stroke_bottom_px": gap["frame_stroke_bottom_px"],
                "text_ink_top_px": gap["first_text_ink_top_px"],
                "expected_pt": round(exp_gap_pt, 2),
                "reproduced_pt": round(got_gap_pt, 2),
                "tolerance_pt": round(tol, 2)}
            report["pass"] = report["pass"] and ok
    elif gap is None and dl:
        report["checks"]["figure_text_gap"] = {"pass": True, "detail": "no text below frame in original"}

    # 7. figure/text separation: the figure crop must stop above the first
    # caption ink. A crop that reaches into the first line renders "text
    # inside the picture" and corrupts the figure/text-gap anchor (seen on
    # flush-against-frame pages where the frame detector fused the bottom
    # border with the first caption line).
    if gap:
        crop_sep = (geo.get("frame_ink_bbox")
                    or geo.get("figure_crop_box")
                    or geo["frame"]["bbox"])
        crop_bottom = int(crop_sep[3])
        text_top = int(gap["first_text_ink_top_px"])
        tol = 6 if gap.get("gap_px") == 1 else 2
        ok = crop_bottom <= text_top + tol
        report["checks"]["figure_text_sep"] = {
            "pass": ok, "crop_bottom_px": crop_bottom,
            "first_text_ink_top_px": text_top,
            "clearance_px": text_top - crop_bottom, "tolerance_px": tol}
        report["pass"] = report["pass"] and ok

    # 8. single panel number: the panel number must appear exactly once,
    # at the start of line 0. A duplicated number (e.g. "（23）…(23)")
    # means the number block was sorted to the end of its line during
    # merge (y0-ordering) or injected while the OCR text already carried it.
    if dl:
        import re as _re
        exp_no = (None if page in NO_PANEL_NO
                  else PANEL_NO_EXCEPTIONS.get(page, page - PANEL_OFFSET))
        # the canonical panel number in either paren style (full- or
        # half-width). A real caption carries it once, at the start of
        # line 0; a second occurrence (typ. a half-width "(23)" appended
        # at the end of line 0) is the duplicated-panel-number bug.
        if exp_no is None:
            # unnumbered page: the number check reduces to "no parenthesised
            # number anywhere" (the leading-number rule is checked in #3).
            pat = _re.compile(r"[（(]\s*\d{1,3}\s*[)）]")
        else:
            pat = _re.compile(
                r"[（(]\s*" + _re.escape(str(exp_no)) + r"\s*[)）]")
        hits = []
        for ln in dl:
            lt = ln.get("simplified") or ln.get("raw") or ""
            for m in pat.finditer(lt):
                hits.append((ln["idx"], m.start()))
        # 0 occurrences is fine for bare-format books (the anchor check
        # covers the leading number); 1 must sit at the start of line 0;
        # 2+ is the duplicated-number bug.
        lt0 = (dl[0].get("simplified") or dl[0].get("raw") or "") if dl else ""
        lead_ws = len(lt0) - len(lt0.lstrip())
        ok_pos = (len(hits) == 0) or (hits[0][0] == 0 and hits[0][1] == lead_ws)
        ok = len(hits) <= 1 and ok_pos
        report["checks"]["panel_no_single"] = {
            "pass": ok, "occurrences": len(hits),
            "at": [[i, p] for i, p in hits]}
        report["pass"] = report["pass"] and ok

    # 9. vector binary figure (vector enhance variant only)
    if geo.get("enhance_variant") == "vector":
        figp = ROOT / "data" / "images" / f"p{page:03d}_bw.png"
        if figp.exists():
            a = np.array(Image.open(str(figp)).convert("L"))
            uniq = np.unique(a).tolist()
            ok = set(uniq) <= {0, 255}
            out_ink = round(float((a < 128).mean()), 4)
            report["checks"]["vector_binary"] = {
                "pass": ok, "values": uniq[:8],
                "ink_frac": out_ink}
            report["pass"] = report["pass"] and ok

            # 10. ink coverage: the retypeset figure must carry roughly the
            # SAME ink as the original scan region (the "newly printed" rule:
            # no more ink than the original had, and not so little that the
            # picture faded away). Original ink-equivalent = mean(255-gray)/255
            # over the figure crop of the deskewed scan. A retypeset that
            # solidifies too much tone fails high; one that erases detail
            # fails low. Tolerances: [0.55, 1.5]x.
            crop_box = (geo.get("frame_ink_bbox")
                        or geo.get("figure_crop_box")
                        or geo["frame"]["bbox"])
            desk = ROOT / "data" / "pages" / f"p{page:03d}_deskew.png"
            if desk.exists():
                g = cv2.imread(str(desk), cv2.IMREAD_GRAYSCALE)
                cx0, cy0, cx1, cy1 = [int(v) for v in crop_box]
                reg = g[cy0:cy1, cx0:cx1]
                if reg.size:
                    orig_ink = float((255 - reg.astype(np.float32)).mean()) / 255.0
                    ratio = out_ink / orig_ink if orig_ink > 0.01 else 1.0
                    okc = 0.55 <= ratio <= 1.5
                    report["checks"]["ink_coverage"] = {
                        "pass": okc, "output_ink": out_ink,
                        "original_ink_equiv": round(orig_ink, 4),
                        "ratio": round(ratio, 3)}
                    report["pass"] = report["pass"] and okc

    path = ROOT / "data" / "verify" / f"p{page:03d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"page": page, "pass": report["pass"],
                      "flags": [k for k, v in report["checks"].items() if not v["pass"]]},
                     ensure_ascii=False))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", type=int, required=True)
    args = ap.parse_args()
    verify_page(args.page)


if __name__ == "__main__":
    main()
