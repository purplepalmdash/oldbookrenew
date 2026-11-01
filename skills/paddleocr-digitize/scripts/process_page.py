#!/usr/bin/env python3
"""S3 插图截取 / 水平对齐 / Otsu 二值化。

对每页 layout 的 image bbox：外扩 PAD → 检测黑色边框（minAreaRect）→
透视变换水平对齐 → 全局 Otsu（去黄、黑白分明、保留网点；不要用 flat-field，
会在木刻黑色填充区引入噪点）。无清晰边框时回退为 layout bbox 直接裁剪。

用法: process_page.py --out digitize [--pages 4-137 | 4,5,135] [--pad 40]
输出: <out>/work/images/pageNNN_{k}.png + pageNNN.json
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def order_points(pts):
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1)
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
        np.float32,
    )


def find_frame(crop_g):
    """检测插图边框：Otsu→反相→闭运算→满足面积/覆盖条件的最大 minAreaRect。"""
    _, bw = cv2.threshold(crop_g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inv = 255 - bw
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    closed = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, k)
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    H, W = crop_g.shape
    best, best_area = None, 0
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.3 * H * W or area <= best_area:
            continue
        rect = cv2.minAreaRect(c)
        (cx, cy), (w, h), _ = rect
        if w > 0.6 * W and h > 0.6 * H:  # 边框应覆盖裁剪区大部分
            best, best_area = rect, area
    return best


def process_image_box(page_g, box, pad, mode="otsu"):
    """截取→对齐→二值化/白化。返回 (meta, final_img)。"""
    x1, y1, x2, y2 = [int(v) for v in box]
    H, W = page_g.shape
    x1c, y1c = max(0, x1 - pad), max(0, y1 - pad)
    x2c, y2c = min(W, x2 + pad), min(H, y2 + pad)
    crop = page_g[y1c:y2c, x1c:x2c]

    rect = find_frame(crop)
    if rect is not None:
        (cx, cy), (w, h), _ = rect
        box4 = order_points(cv2.boxPoints(rect))
        # 倾角从边框顶边算（minAreaRect 的 angle 元数据有版本/宽高比歧义，
        # 0.5° 会被算成 89.5°）
        angle = float(np.degrees(np.arctan2(box4[1][1] - box4[0][1],
                                            box4[1][0] - box4[0][0])))
        wd = int(round(np.linalg.norm(box4[0] - box4[1])))
        hd = int(round(np.linalg.norm(box4[3] - box4[0])))
        M = cv2.getPerspectiveTransform(
            box4, np.array([[0, 0], [wd - 1, 0], [wd - 1, hd - 1], [0, hd - 1]], np.float32)
        )
        aligned = cv2.warpPerspective(crop, M, (wd, hd))
        center = (cx + x1c, cy + y1c)
    else:
        aligned = page_g[y1:y2, x1:x2]  # 回退：不旋转
        center = ((x1 + x2) / 2, (y1 + y2) / 2)
        angle = 0.0
        wd, hd = x2 - x1, y2 - y1

    if mode == "whiten":
        # 数字扫描书：灰度白化保留墨线/网点层次（Otsu 会破坏半色调）
        final = np.where(aligned > 215, 255, aligned).astype(np.uint8)
    else:
        _, final = cv2.threshold(aligned, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return (
        {
            "mode": mode,
            "image_bbox_layout": box,
            "frame_center_page": [round(center[0], 1), round(center[1], 1)],
            "frame_w": wd,
            "frame_h": hd,
            "angle_deg": round(angle, 3),
            "aligned_shape": [hd, wd],
        },
        final,
    )


def parse_pages(spec, total):
    if spec is None:
        return list(range(1, total + 1))
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", default=None, help="如 4-137 或 4,5,135")
    ap.add_argument("--pad", type=int, default=40)
    ap.add_argument("--mode", choices=["otsu", "whiten"], default="otsu",
                    help="otsu=二值化（真扫描/泛黄）; whiten=灰度白化（数字扫描书）")
    a = ap.parse_args()

    out = Path(a.out)
    data, pages_dir, imgs = out / "data", out / "work" / "pages", out / "work" / "images"
    imgs.mkdir(parents=True, exist_ok=True)

    import glob, re
    # total 取最大页号（文件数会把 special JSON 误计入，导致末页漏处理）
    total = max((int(re.search(r"page(\d+)(?:_special)?\.json", f).group(1))
                 for f in glob.glob(str(data / "page*.json"))), default=0)
    for p in parse_pages(a.pages, total):
        jf = data / f"page{p:03d}.json"
        if not jf.exists():
            print(f"p{p:03d}: no recognition json, skip")
            continue
        rec = json.load(open(jf))
        png = pages_dir / f"p{p:03d}.png"
        if not png.exists():
            print(f"p{p:03d}: no rendered page, skip (run mcp_batch first)")
            continue
        page_g = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
        img_boxes = [e["bbox"] for e in rec["layout"] if e["label"] == "image"]
        outrec = {"page": p, "images": []}
        for i, box in enumerate(img_boxes):
            meta, final_img = process_image_box(page_g, box, a.pad, a.mode)
            meta["index"] = i
            outrec["images"].append(meta)
            cv2.imwrite(str(imgs / f"page{p:03d}_{i}.png"), final_img)
        (imgs / f"page{p:03d}.json").write_text(json.dumps(outrec, ensure_ascii=False, indent=1))
        for r in outrec["images"]:
            print(f"p{p:03d}: img{r['index']} {r['frame_w']}x{r['frame_h']} angle={r['angle_deg']}")
        if not outrec["images"]:
            print(f"p{p:03d}: no image (text-only page)")


if __name__ == "__main__":
    main()
