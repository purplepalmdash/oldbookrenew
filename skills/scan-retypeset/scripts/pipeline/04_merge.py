#!/usr/bin/env python3
"""04_merge: combine geometry + MCP VL layout + MCP OCR lines into the
per-page dataset (data/layout/pNNN.json).

One dataset per page, strict 1:1 mapping (worktips 2.2/2.3):
- figure: layout contour (OpenCV frame polygon, cross-checked with VL image box)
- textlines: per-line bbox, text, start_char, end_char, confidence

Usage: .venv/bin/python pipeline/04_merge.py --page 10
Reads  data/layout/p010.geo.json, data/vl/p010.json, data/ocr/p010.json
Writes data/layout/p010.json
"""
import argparse
import difflib
import json
import re

from lib.common import (
    NO_PANEL_NO, PANEL_NO_EXCEPTIONS, PANEL_OFFSET, RENDER_DPI, ROOT,
    SOURCE_PDF, WATERMARK_BOXES, lead_number, load_json, norm_lead_number,
    norm_text, ocr_bbox_to_page, save_json, vl_text,
)


def _panel_no_token(n: int) -> str:
    """Canonical leading number token for this book's style (config:
    panel_no_format = "bare" | "paren" | "dot")."""
    import lib.common as c
    fmt = c.CONFIG.get("panel_no_format", "bare")
    if fmt == "paren":
        return f"（{n}）\u3000\u3000"
    if fmt == "dot":
        return f"{n}. "
    return f"{n}\u3000\u3000"


def _is_number_token(s: str) -> bool:
    """True if s is only a number with optional parens/spaces/period
    ("1", "（ 1 ）", "31.")."""
    s = (s or "").strip().strip("\u3000（）().．").strip()
    return bool(re.fullmatch(r"\d{1,3}", s))


def _row_sorted_blocks(blocks: list) -> list:
    """Blocks of a text cluster in reading order.

    Group into sub-rows by vertical overlap first, then order sub-rows
    top-down and blocks within a sub-row left-right. A plain (y0, x0) sort
    breaks when the panel-number block's bbox top sits a few px BELOW the
    text block's bbox top (the number then sorts AFTER the text and is
    appended at the END of the line — the duplicated panel number, e.g.
    "（23）…我正要找你(23)"). Number blocks are vertically centered on the
    line, so overlap-grouping keeps them in the right row; x0 gives the
    reading order within the row.
    """
    blocks = sorted(blocks, key=lambda b: b["bbox"][1])
    rows: list[dict] = []
    for b in blocks:
        by0, by1 = b["bbox"][1], b["bbox"][3]
        placed = False
        for r in rows:
            ry0, ry1 = r["y0"], r["y1"]
            inter = min(by1, ry1) - max(by0, ry0)
            h = min(by1 - by0, ry1 - ry0)
            if h > 0 and inter >= 0.4 * h:
                r["blocks"].append(b)
                r["y1"] = max(ry1, by1)
                placed = True
                break
        if not placed:
            rows.append({"y0": by0, "y1": by1, "blocks": [b]})
    rows.sort(key=lambda r: r["y0"])
    out = []
    for r in rows:
        out.extend(sorted(r["blocks"], key=lambda b: (b["bbox"][0], b["bbox"][1])))
    return out


def band_index_for_bbox(bbox: list, bands: list) -> int:
    """Assign a bbox to the text band with maximal y-overlap."""
    best, best_ov = -1, 0
    for i, band in enumerate(bands):
        b = band["bbox"] if isinstance(band, dict) else band
        ov = min(bbox[3], b[3]) - max(bbox[1], b[1])
        if ov > best_ov:
            best, best_ov = i, ov
    return best if best_ov > 0.2 * (bbox[3] - bbox[1]) else -1


def merge_page(page: int) -> dict:
    geo = load_json(ROOT / "data" / "layout" / f"p{page:03d}.geo.json")
    vl = load_json(ROOT / "data" / "vl" / f"p{page:03d}.json")
    ocr = load_json(ROOT / "data" / "ocr" / f"p{page:03d}.json")

    page_w = geo["render_size"][0]
    bands = geo.get("text_bands", [])
    # OCR blocks in page space
    blocks = []
    for blk in ocr.get("blocks", []):
        pb = ocr_bbox_to_page(blk["bbox"], geo)
        blocks.append({"text": blk["text"], "score": blk.get("score", 1.0), "bbox": pb})

    # Margin seal: Nezha stamps a red vertical owner's seal in the right
    # margin, outside the frame box. Line-OCR reads it as short garble with
    # low confidence (e.g. "KH", "快", "亚大"). Drop low-confidence blocks
    # that sit outside the frame's x span; real text overhanging the frame
    # reads at high confidence (>=0.97) and is kept.
    fx0, _, fx1, _ = geo["frame"]["bbox"]

    def _in_margin(pb: list) -> bool:
        return pb[0] > fx1 + 20 or pb[2] < fx0 - 20

    blocks = [b for b in blocks
              if not (_in_margin(b["bbox"]) and b["score"] < 0.85)]

    def band_of(bbox: list) -> int:
        bi = band_index_for_bbox(bbox, bands)
        if bi >= 0 or not bands:
            return bi if bi >= 0 else 0
        cy = (bbox[1] + bbox[3]) / 2
        return min(range(len(bands)),
                   key=lambda k: abs(cy - (bands[k]["bbox"][1] + bands[k]["bbox"][3]) / 2))

    per_band: dict[int, list[dict]] = {i: [] for i in range(len(bands))}
    for b in blocks:
        per_band.setdefault(band_of(b["bbox"]), []).append(b)

    lines: list[dict] = []
    li = 0
    for i, band in enumerate(bands):
        assigned = per_band.get(i, [])
        # cluster assigned blocks into visual lines: a block joins the open
        # cluster when it starts within the top half of that cluster's
        # vertical span (side-by-side fragments share a line; stacked lines
        # do not, even when OCR bboxes overlap vertically)
        by_y = sorted(assigned, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        clusters: list[list[dict]] = []
        for b in by_y:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            h = y1 - y0
            joined = False
            for cl in clusters:
                cl_y0 = min(x["bbox"][1] for x in cl)
                cl_h = max(x["bbox"][3] - x["bbox"][1] for x in cl)
                if y0 < cl_y0 + 0.5 * cl_h:
                    cl.append(b)
                    joined = True
                    break
            if not joined:
                clusters.append([b])
        clusters.sort(key=lambda cl: min(x["bbox"][1] for x in cl))
        for cl in clusters:
            cl[:] = _row_sorted_blocks(cl)
            text_parts: list[str] = []
            for j, b in enumerate(cl):
                t = b["text"]
                if not text_parts:
                    text_parts.append(t)
                    continue
                prev_txt = text_parts[-1]
                prev_box = cl[j - 1]["bbox"]
                if _is_number_token(prev_txt):
                    # book typesets panel number as "N<two fullwidth spaces>text";
                    # but a multi-digit number can be split across blocks ("13"+"2…")
                    if re.match(r"^\d", t):
                        text_parts[-1] = prev_txt + t
                    else:
                        text_parts[-1] = prev_txt + "\u3000\u3000" + t
                else:
                    gap = b["bbox"][0] - prev_box[2]
                    n_prev = max(1, len(cl[j - 1]["text"]))
                    char_w = (prev_box[2] - prev_box[0]) / n_prev
                    if gap > char_w * 0.6:
                        text_parts[-1] += " " + t
                    else:
                        text_parts[-1] += t
            text = "".join(text_parts)
            # normalize leading number: canonical "N<two fullwidth spaces>";
            # tolerate a halfwidth space / nothing between number and text
            text = norm_lead_number("".join(text_parts))
            union = [
                min(b["bbox"][0] for b in cl), min(b["bbox"][1] for b in cl),
                max(b["bbox"][2] for b in cl), max(b["bbox"][3] for b in cl),
            ]
            score = min(b["score"] for b in cl) if cl else 1.0
            lines.append({
                "idx": li, "band_idx": i, "text": text, "bbox": union,
                "start_char": text[0] if text else "",
                "end_char": text[-1] if text else "",
                "score": round(score, 4),
                "n_blocks": len(cl),
                "block_texts": [b["text"] for b in cl],
            })
            li += 1

    # ---- split over-tall lines: OCR sometimes fuses two consecutive visual
    # lines into one block (e.g. a short last line "过来了。" merged with the
    # line above). If a line's bbox is >30% taller than the median line
    # height AND holds more chars than a full-width line, split it at the
    # book's wrap width (never starting the second part with punctuation).
    if len(lines) >= 2:
        import statistics
        hs = [l["bbox"][3] - l["bbox"][1] for l in lines]
        med_h = statistics.median(hs)
        ref_w = [(l["bbox"][2] - l["bbox"][0]) / max(1, len(l["text"]))
                 for l in lines if l["score"] >= 0.95 and len(l["text"]) >= 10]
        if ref_w:
            char_w = statistics.median(ref_w)
            band_w = (max(b["bbox"][2] for b in bands)
                      - min(b["bbox"][0] for b in bands))
            capacity = max(8, int(band_w / char_w))
            new_lines = []
            for l in lines:
                h = l["bbox"][3] - l["bbox"][1]
                t = l["text"]
                if h > 1.3 * med_h and len(t) > capacity + 1:
                    cut = capacity
                    while cut < len(t) - 1 and t[cut] in "，。！？、；：”’）》〉」』":
                        cut += 1
                    a, b = t[:cut], t[cut:]
                    x0, y0, x1, y1 = l["bbox"]
                    half = h / 2.0
                    new_lines.append({
                        "idx": l["idx"], "band_idx": l["band_idx"],
                        "text": a, "bbox": [x0, y0, x0 + cut * char_w,
                                            y0 + half],
                        "start_char": a[0] if a else "",
                        "end_char": a[-1] if a else "",
                        "score": l["score"], "n_blocks": 0,
                        "block_texts": [a],
                    })
                    new_lines.append({
                        "idx": l["idx"], "band_idx": l["band_idx"],
                        "text": b, "bbox": [x0, y1 - half,
                                            x0 + len(b) * char_w, y1],
                        "start_char": b[0] if b else "",
                        "end_char": b[-1] if b else "",
                        "score": l["score"], "n_blocks": 0,
                        "block_texts": [b],
                    })
                else:
                    new_lines.append(l)
            lines = new_lines
            li = len(lines)

    # renumber after structural edits (split / VL override)
    for k, l in enumerate(lines):
        l["idx"] = k

    # drop tiny low-confidence lines: OCR sometimes hallucinates 1-2
    # "characters" out of a scan speckle sitting beside a short line
    # (《巴黎圣母院》 p87: a 21-px-wide "2 7", score 0.39, right of the
    # 4-char last line). A real line is ~2 chars wide at least; a
    # sub-2-char-width line only survives when reasonably confident.
    if len(lines) >= 2:
        import statistics as _st
        _cws = [(l["bbox"][2] - l["bbox"][0]) / max(1, len(l["text"]))
                for l in lines if len(l["text"]) >= 8]
        if _cws:
            _cw_med = _st.median(_cws)
            lines = [l for l in lines
                     if l["score"] >= 0.5
                     or (l["bbox"][2] - l["bbox"][0]) >= 2 * _cw_med]
            for k, l in enumerate(lines):
                l["idx"] = k

    # tighten each line bbox to its actual ink extent (OCR bboxes carry
    # 0-30 px of variable top padding; the typesetter places lines at the
    # measured ink row so text lands at its original vertical position).
    # The top scan of line N must start below line N-1's ink: justified
    # lines share x spans, and without this the scan picks up the previous
    # line's bottom strokes as this line's "ink top" (p20: measured 58 px
    # above the true top, collapsing the output line spacing).
    from lib.common import line_ink_bbox
    from PIL import Image
    import numpy as _np
    deskew = ROOT / "data" / "pages" / f"p{page:03d}_deskew.png"
    if deskew.exists():
        _gray = _np.array(Image.open(deskew).convert("L"))
        # ---- inter-line ink splits. line_ink_bbox chains each line's top
        # scan from the previous line's ink bottom. When the inter-line dip
        # never drops below the min_frac threshold (dense type with large
        # strokes whose tips reach into the gap — e.g. p31 of 孙膑和庞涓),
        # the previous line's "ink bottom" runs into the next line's top
        # tips and every following line's ink top is clipped low: lines
        # drift down cumulatively and the last line can leave the page.
        # The true dip is the coverage LOCAL MINIMUM inside the bbox
        # overlap of consecutive lines; clip the ink extents at it.
        splits: dict = {}
        if len(lines) >= 2:
            for i in range(1, len(lines)):
                pb = lines[i - 1]["bbox"]
                cb = lines[i]["bbox"]
                lo = max(pb[1], cb[1]) - 20
                hi = min(pb[3], cb[3]) + 20
                if hi - lo < 12:
                    continue
                xa, xb = cb[0], cb[2]
                seg = _gray[max(0, lo):hi, xa:xb]
                if seg.size == 0:
                    continue
                covs = (seg < 140).mean(axis=1)
                ref = float(_np.percentile(covs, 75))
                mi = int(_np.argmin(covs))
                if float(covs[mi]) < 0.5 * max(ref, 0.02):
                    splits[i] = max(0, lo + mi)
        prev_ink_bottom = None
        for i, l in enumerate(lines):
            start = None
            if prev_ink_bottom is not None:
                start = max(l["bbox"][1] - 6, prev_ink_bottom + 1)
            if i in splits:
                start = max(start if start is not None else 0,
                            splits[i] + 1)
            y_end = splits[i + 1] - 1 if i + 1 in splits else None
            l["ink_bbox"] = line_ink_bbox(_gray, l["bbox"], y_start=start,
                                          y_end=y_end)
            prev_ink_bottom = l["ink_bbox"][3]

    # ---- VL text override: PaddleOCR-VL preserves the book's own line
    # breaks in its markdown; use it when the OCR line structure matches the
    # geometry bands but a line is low-confidence garble (e.g. a washed frame
    # line read as text). NOTE: VL often merges wrapped lines into one text
    # block, so we must NOT override on count mismatch alone.
    vl_lines_raw = [l for l in vl.get("markdown", "").split("\n")
                    if l.strip() and not l.strip().startswith("![")]
    n_vl = len(vl_lines_raw)
    n_bands = len(bands)
    ocr_min_score = min((l["score"] for l in lines), default=1.0)
    if (n_vl == n_bands and n_vl > 0 and len(lines) == n_bands
            and ocr_min_score < 0.85):
        lines = []
        for i, band in enumerate(bands):
            t = vl_lines_raw[i].strip()
            t = norm_lead_number(t)
            b = band["bbox"]
            lines.append({
                "idx": i, "band_idx": i, "text": t, "bbox": list(b),
                "start_char": t[0] if t else "",
                "end_char": t[-1] if t else "",
                "score": 0.99,
                "n_blocks": 0,
                "block_texts": [],
                "source": "vl_markdown",
            })

    # ---- watermark line drop: a later-generation watermark (e.g. 刘秀 下册
    # horse + "天岸马奇通人中龙" slogan, bottom-left) is read by OCR as an
    # extra line. Drop a line when its bbox centre is inside a configured
    # watermark box AND it starts from the left part of that box's span
    # (real caption lines ending in the same corner start further right).
    if WATERMARK_BOXES:
        _rw = geo["render_size"]

        def _wm_hit(lb: list) -> bool:
            cx, cy = (lb[0] + lb[2]) / 2, (lb[1] + lb[3]) / 2
            for (nx0, ny0, nx1, ny1) in WATERMARK_BOXES:
                X0, Y0 = nx0 * _rw[0], ny0 * _rw[1]
                X1, Y1 = nx1 * _rw[0], ny1 * _rw[1]
                if X0 <= cx <= X1 and Y0 <= cy <= Y1:
                    return lb[0] < X0 + 0.6 * (X1 - X0)
            return False

        _n_before = len(lines)
        lines = [l for l in lines if not _wm_hit(l["bbox"])]
        if len(lines) != _n_before:
            print(f"  watermark: dropped {_n_before - len(lines)} line(s)")

    # ---- panel number fallback: if line 0 lacks the expected number but
    # the VL text has it (OCR missed the small margin digit), prepend it.
    expected_no = (None if page in NO_PANEL_NO
                   else PANEL_NO_EXCEPTIONS.get(page, page - PANEL_OFFSET))

    if (lines and expected_no is not None
            and lead_number(lines[0]["text"]) != expected_no):
        vl_lead = lead_number(vl_text(vl.get("markdown", "")))
        if vl_lead == expected_no:
            first = lines[0]["text"].lstrip()
            lines[0]["text"] = _panel_no_token(expected_no) + first
            lines[0]["start_char"] = first[0] if first else ""
            lines[0]["number_recovered"] = True

    # panel number anchor: expected = page - PANEL_OFFSET; pages listed in
    # config `no_panel_no` (unnumbered prologue pages) pass when line 0
    # carries no leading number at all.
    panel_check = {"expected": expected_no, "ocr": None, "pass": False}
    if expected_no is None:
        panel_check["pass"] = not lines or lead_number(lines[0]["text"]) is None
    elif lines:
        got = lead_number(lines[0]["text"])
        if got is not None:
            panel_check["ocr"] = got
            panel_check["pass"] = got == expected_no

    # VL / OCR cross-check (normalized full text)
    ocr_full = norm_text("".join(l["text"] for l in lines))
    vl_full = vl_text(vl.get("markdown", ""))
    ratio = difflib.SequenceMatcher(None, ocr_full, vl_full).ratio()

    # figure cross-check: OpenCV frame vs VL image box
    fb = geo["frame"]["bbox"]
    # VL layout bboxes are in the ORIGINAL image pixel space (the MCP
    # server passes the original file to PaddleOCR-VL and the model
    # rescales its boxes back to the input image) — compare directly
    # against the OpenCV frame box (also page pixels).
    fig_check = {"opencv_bbox": fb, "vl_bbox": None, "pass": None}
    for box in vl.get("layout") or []:
        if box.get("label") == "image" and box.get("bbox"):
            vb = box["bbox"]
            iou = (
                max(0, min(fb[2], vb[2]) - max(fb[0], vb[0]))
                * max(0, min(fb[3], vb[3]) - max(fb[1], vb[1]))
            ) / max(1, (fb[2] - fb[0]) * (fb[3] - fb[1]))
            fig_check["vl_bbox"] = vb
            fig_check["iou"] = round(iou, 4)
            fig_check["pass"] = iou > 0.9

    low_conf = [l["idx"] for l in lines if l["score"] < 0.95]
    flags = []
    if not panel_check["pass"]:
        flags.append("panel_no_mismatch")
    if ratio < 0.9:
        flags.append("vl_ocr_mismatch")
    if low_conf:
        flags.append("low_confidence")
    if fig_check["pass"] is False:
        flags.append("figure_mismatch")
    if not lines:
        flags.append("no_text_lines")

    dataset = {
        "page": page,
        "panel_no": (panel_check["ocr"] if panel_check["pass"]
                     else expected_no),
        "type": "panel",
        "source": {
            "pdf": SOURCE_PDF, "render_dpi": RENDER_DPI,
            "render": f"data/pages/p{page:03d}.png",
            "deskew": f"data/pages/p{page:03d}_deskew.png",
            "width_px": geo["render_size"][0], "height_px": geo["render_size"][1],
            "skew_deg": geo.get("skew_deg"),
        },
        "layout": {
            "vl_markdown": f"data/vl/p{page:03d}.json",
            "figure": {
                "source": "opencv-contour (cross-checked vs VL layout box)",
                "polygon": geo["frame"]["polygon"],
                "bbox": fb,
            },
            "figure_text_gap": geo.get("figure_text_gap"),
            "textlines": [
                {"idx": l["idx"], "band_idx": l["band_idx"], "bbox": l["bbox"],
                 "ink_bbox": l.get("ink_bbox", l["bbox"]),
                 "source": "mcp-ocr_image", "score": l["score"]}
                for l in lines
            ],
        },
        "text": {
            "lines": [
                {
                    "idx": l["idx"], "band_idx": l["band_idx"], "raw": l["text"],
                    "simplified": l["text"],  # PP-OCRv6 ch outputs simplified directly
                    "start_char": l["start_char"], "end_char": l["end_char"],
                    "line_bbox_in_render": l["bbox"],
                    "ink_bbox_in_render": l.get("ink_bbox", l["bbox"]),
                    "score": l["score"],
                }
                for l in lines
            ],
            "panel_no_check": panel_check,
            "cross_check": {
                "vl_ocr_similarity": round(ratio, 4),
                "vl_text": vl_full,
                "ocr_text": ocr_full,
            },
        },
        "image": {"file": f"data/images/p{page:03d}_bw.png",
                  "variant": geo.get("enhance_variant", "bw_clear"),
                  "binary_variant": f"data/images/p{page:03d}_binary.png"},
        "qa": {"flags": flags, "low_conf_lines": low_conf, "figure_check": fig_check},
    }
    save_json(dataset, ROOT / "data" / "layout" / f"p{page:03d}.json")
    print(json.dumps({
        "page": page, "panel": dataset["panel_no"],
        "panel_check": panel_check["pass"],
        "vl_sim": dataset["text"]["cross_check"]["vl_ocr_similarity"],
        "fig_iou": fig_check.get("iou"),
        "lines": len(lines), "flags": flags,
    }, ensure_ascii=False))
    return dataset


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", type=int, required=True)
    args = ap.parse_args()
    merge_page(args.page)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    main()
