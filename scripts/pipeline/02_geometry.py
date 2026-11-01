#!/usr/bin/env python3
"""02_geometry: deskew + picture-frame contour + text-band detection.

Usage: .venv/bin/python pipeline/02_geometry.py --page 10
Reads  data/pages/p010.png   (250dpi render)
Writes data/layout/p010.geo.json  (skew, frame polygon, text bands, page-space coords)
Writes out/preview/p010_geo.png   (annotated preview for eyeball check)
"""
import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def page_path(page: int) -> Path:
    return ROOT / "data" / "pages" / f"p{page:03d}.png"


def estimate_skew_and_frame(gray: np.ndarray):
    """Find the printed picture frame: largest closed quadrilateral."""
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # close small gaps in the frame line
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.25 * w * h:
            continue
        peri = cv2.arcLength(c, True)
        # progressively stronger simplification until a quadrilateral emerges
        poly4 = None
        for eps in (0.015, 0.02, 0.03, 0.04, 0.06):
            approx = cv2.approxPolyDP(c, eps * peri, True)
            if len(approx) == 4:
                poly4 = approx
                break
        if poly4 is None:
            continue
        rect = cv2.minAreaRect(poly4)
        (cx, cy), (rw, rh), ang = rect
        fill = area / max(rw * rh, 1e-6)
        if rw < rh:
            rw, rh = rh, rw
        if 0.8 < rw / max(rh, 1) < 2.5 and fill > 0.85:
            candidates.append((area, poly4, ang))
    if not candidates:
        return None, None, None
    candidates.sort(key=lambda t: -t[0])
    _, approx, ang = candidates[0]
    pts = approx.reshape(4, 2).astype(float)
    s = pts.sum(axis=1)
    d = pts[:, 0] - pts[:, 1]
    tl = pts[int(np.argmin(s))]
    br = pts[int(np.argmax(s))]
    tr = pts[int(np.argmax(d))]
    bl = pts[int(np.argmin(d))]
    poly = [tl.tolist(), tr.tolist(), br.tolist(), bl.tolist()]
    # skew from top edge (left -> right, so in (-90, 90))
    skew = math.degrees(math.atan2(poly[1][1] - poly[0][1], poly[1][0] - poly[0][0]))
    return poly, skew, ang


def rotate_image(img: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.15:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def text_bands(gray: np.ndarray, frame_poly) -> list:
    """Projection-profile text-line detection below the frame."""
    h, w = gray.shape
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    y1 = int(max(p[1] for p in frame_poly))
    roi = bw[y1 + 8 : h - 4, 8:w - 8]
    if roi.size == 0:
        return []
    rowsum = (roi > 0).sum(axis=1)
    thresh = roi.shape[1] * 0.004
    lines, start = [], None
    for y, v in enumerate(rowsum):
        if v > thresh and start is None:
            start = y
        elif v <= thresh and start is not None:
            if y - start >= 12:
                lines.append([start, y])
            start = None
    if start is not None and len(rowsum) - start >= 12:
        lines.append([start, len(rowsum)])
    out = []
    for (a, b) in lines:
        seg = roi[a:b, :]
        colsum = (seg > 0).sum(axis=0)
        xs = np.where(colsum > 0)[0]
        if len(xs) == 0:
            continue
        out.append({"bbox": [int(xs[0]) + 8, int(a + y1 + 8), int(xs[-1]) + 8, int(b + y1 + 8)]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--vl-frame", type=str, default="",
                    help="fallback frame bbox x0,y0,x1,y1 (from VL layout) if opencv fails")
    args = ap.parse_args()
    page = args.page
    src = page_path(page)
    img = cv2.imread(str(src))
    if img is None:
        raise SystemExit(f"render not found: {src} (run 01_render first)")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    poly, skew, _ = estimate_skew_and_frame(gray)
    frame_source = "opencv"
    if poly is None and not args.vl_frame:
        # self-contained VL fallback: opencv frame contour failed — read the
        # PaddleOCR-VL layout (data/vl/pNNN.json) and use its image box. This
        # keeps geometry reproducible without re-typing --vl-frame per page.
        try:
            from lib.common import load_json
            vl = load_json(ROOT / "data" / "vl" / f"p{page:03d}.json")
            vlboxes = [b for b in (vl.get("layout") or [])
                       if b.get("label") == "image"]
            if vlboxes:
                vb = max(vlboxes, key=lambda b: (b["bbox"][2] - b["bbox"][0])
                        * (b["bbox"][3] - b["bbox"][1]))
                # NOTE: VL layout bboxes are in the ORIGINAL image pixel
                # space (the server passes the original file to
                # PaddleOCR-VL; the model rescales its boxes back). No
                # rescaling needed here.
                args.vl_frame = ",".join(str(int(v)) for v in vb["bbox"])
        except Exception as e:
            print(f"vl fallback read failed: {e}")
    if poly is None and args.vl_frame:
        x0, y0, x1, y1 = [int(v) for v in args.vl_frame.split(",")]
        # estimate skew from the top frame edge via Hough lines in that strip
        strip = gray[max(0, y0 - 40):y0 + 80, x0:x1]
        edges = cv2.Canny(strip, 60, 160)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=120,
                                minLineLength=int((x1 - x0) * 0.3), maxLineGap=40)
        ang = 0.0
        if lines is not None:
            angs = []
            for l in lines:
                pt = np.asarray(l).reshape(-1)
                a = math.degrees(math.atan2(pt[3] - pt[1], pt[2] - pt[0]))
                if abs(a) < 15:
                    angs.append(a)
            if angs:
                ang = float(np.median(angs))
        if abs(ang) > 3.0:
            ang = 0.0
        skew = ang
        poly = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        frame_source = "vl_fallback"
    if poly is None:
        raise SystemExit("frame not detected")
    img_r = rotate_image(img, -skew)
    gray_r = cv2.cvtColor(img_r, cv2.COLOR_BGR2GRAY)
    cv2.imwrite(str(ROOT / "data" / "pages" / f"p{page:03d}_deskew.png"), img_r)
    # rotate frame polygon
    m = cv2.getRotationMatrix2D((w / 2, h / 2), -skew, 1.0)
    h2, w2 = img_r.shape[:2]
    m2 = cv2.getRotationMatrix2D((w / 2, h / 2), -skew, 1.0)
    cos, sin = abs(m2[0, 0]), abs(m2[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m2[0, 2] += nw / 2 - w / 2
    m2[1, 2] += nh / 2 - h / 2
    poly_r = [m2 @ np.array([p[0], p[1], 1.0]) for p in poly]
    poly_r = [[int(v[0]), int(v[1])] for v in poly_r]
    # correct the frame bottom when the detector fused it with the first
    # caption line (flush-against-frame books): pull the box back to the
    # real frame line so the figure crop stops at the border and the
    # textband OCR crop starts above the first line
    from lib.common import frame_ink_extent, refine_frame_bottom
    fb0 = [min(p[0] for p in poly_r), min(p[1] for p in poly_r),
           max(p[0] for p in poly_r), max(p[1] for p in poly_r)]
    refined = refine_frame_bottom(gray_r, fb0)
    if refined is not None and abs(refined - fb0[3]) >= 6:
        for p in poly_r:
            if p[1] >= fb0[3] - 2:
                p[1] = refined
        geo_note = {"old_bottom": int(fb0[3]), "new_bottom": int(refined)}
    else:
        geo_note = None
    bands = text_bands(gray_r, poly_r)
    x0 = min(p[0] for p in poly_r); x1 = max(p[0] for p in poly_r)
    y0 = min(p[1] for p in poly_r); y1 = max(p[1] for p in poly_r)
    geo = {
        "page": page,
        "render_size": [w2, h2],
        "skew_deg": round(skew, 3),
        "frame_source": frame_source,
        "frame": {"polygon": poly_r, "bbox": [x0, y0, x1, y1]},
        "text_bands": bands,
        "text_band_union": None,
    }
    if bands:
        ub = [min(b["bbox"][0] for b in bands), min(b["bbox"][1] for b in bands),
              max(b["bbox"][2] for b in bands), max(b["bbox"][3] for b in bands)]
        geo["text_band_union"] = ub
    # visible figure/text gap (white-gap-first ink scan; see
    # lib.common.figure_text_gap). Computed BEFORE the frame_ink crop so
    # its downward scan is capped at the true first-caption ink top (not the
    from lib.common import figure_text_gap
    gap = figure_text_gap(gray_r, geo)
    if gap:
        geo["figure_text_gap"] = gap
    # outer ink extent of the frame (thick/blurry border overhang) — the
    # figure crop, placement and QA all use this box instead of the fitted
    # bbox. The downward scan stops above the first caption ink row so the
    # crop can never swallow text (flush-against-frame pages).
    fb_now = geo["frame"]["bbox"]
    ttop = int(gap["first_text_ink_top_px"]) if gap else (bands[0]["bbox"][1] if bands else None)
    geo["frame_ink_bbox"] = frame_ink_extent(gray_r, fb_now, text_top=ttop)
    if geo_note:
        geo["frame_bottom_refined"] = geo_note
    # The figure crop must never reach the first caption ink: on case-B
    # pages (text flush against the frame) the blurry frame feathering
    # overhangs the text top by a few px, so cap the crop bottom just above
    # the measured text ink top. On normal pages the crop is already above
    # the white gap, so this is a no-op.
    fin = geo["frame_ink_bbox"]
    if gap:
        tt = int(gap["first_text_ink_top_px"])
        if fin[3] > tt - 1:
            fin[3] = tt - 1
    out = ROOT / "data" / "layout" / f"p{page:03d}.geo.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(geo, ensure_ascii=False, indent=2))
    # annotated preview
    (ROOT / "out" / "preview").mkdir(parents=True, exist_ok=True)
    vis = cv2.cvtColor(img_r, cv2.COLOR_GRAY2BGR) if len(img_r.shape) == 2 else img_r.copy()
    cv2.polylines(vis, [np.array(poly_r)], True, (0, 0, 255), 4)
    for b in bands:
        bb = b["bbox"]
        cv2.rectangle(vis, (bb[0] - 6, bb[1] - 6), (bb[2] + 6, bb[3] + 6), (255, 0, 0), 3)
    cv2.imwrite(str(ROOT / "out" / "preview" / f"p{page:03d}_geo.png"), vis)
    print(json.dumps({"page": page, "skew_deg": geo["skew_deg"],
                      "frame_bbox": [x0, y0, x1, y1],
                      "frame_ink_bbox": geo.get("frame_ink_bbox"),
                      "frame_bottom_refined": geo.get("frame_bottom_refined"),
                      "text_lines": len(bands),
                      "text_band_union": geo["text_band_union"],
                      "figure_text_gap_px": (geo.get("figure_text_gap") or {}).get("gap_px")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
