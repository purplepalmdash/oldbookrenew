#!/usr/bin/env python3
"""margin_ink_check: 框外墨检测——重建前的页边设计丢失报警（S5 前置门）。

本路线的重建模型是 页面=白底+插图(frame 裁剪)+文字层（quad）：
**插图框外**的墨（版心签条/边线/藏书印/页边小徽记）不会被任何层保留，
且 test_page.py 的 QA 查不出来（它比的是框内图与文字行）→ 静默丢失。
本脚本在 S5 重建前把这件事变成显式信号。

检测：对每页 2x 渲染图 Otsu 二值化，扣除
  (a) 插图框（geo frame，残角旋转后的轴对齐 bbox，**外扩 10px**——
      原书印的边框线本身属于插图层，边界 1-2px 的 Otsu 化残墨不算框外）
  (b) 所有 OCR 行 quad 的外扩框（字幕是文字层，本来就该在框外）
  (c) book.json `margin_ink_ignored` 记录的已判定水印区（2x px 框列表；
      判定子技能定过归属的元素记在这里，检测器尊重判定证据）
剩下的墨 = 疑似页边设计。占页面积 > --min-frac（默认 0.1%）→ WARN，
并输出 work/margin_ink/pNNN.png 高亮图供目检。

有 WARN 时的处置（按序）：
  1. 确认为页边 running design 且需 1:1 保留 → 本书该页转
     scan-retypeset 路线（margin_strip），或手工几何进 special_pages；
  2. 确认为噪声/装订残影 → 忽略并在 book.json 记 "margin_ink_ignored"。
无 WARN → 本书无框外设计，直接 S5。

用法: margin_ink_check.py --out digitize3_xia [--pages 4-163] [--min-frac 0.001]
退出码: 0=全干净, 1=有 WARN（可作脚本门）
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def otsu(gray: np.ndarray) -> int:
    hist, _ = np.histogram(gray, 256, [0, 256])
    total = gray.size
    sum_all = np.dot(np.arange(256), hist)
    sum_b, w_b, best, thr = 0.0, 0, 0.0, 127
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best:
            best, thr = var, t
    return thr


def frame_bbox(geo_im: dict) -> list:
    """frame 残角旋转后的轴对齐 bbox（渲染像素）。"""
    cx, cy = geo_im["frame_center_page"]
    w, h = geo_im["frame_w"], geo_im["frame_h"]
    th = np.deg2rad(geo_im.get("angle_deg", 0.0))
    w2 = w * abs(np.cos(th)) + h * abs(np.sin(th))
    h2 = w * abs(np.sin(th)) + h * abs(np.cos(th))
    return [cx - w2 / 2, cy - h2 / 2, cx + w2 / 2, cy + h2 / 2]


def check_page(out: Path, p: int, min_frac: float, text_pad: int,
               ignored: list):
    geo_f = out / "work" / "images" / f"page{p:03d}.json"
    rec_f = out / "data" / f"page{p:03d}.json"
    render_f = out / "work" / "pages" / f"p{p:03d}.png"
    if not (geo_f.exists() and rec_f.exists() and render_f.exists()):
        return "skip"
    geo = json.load(open(geo_f))
    if not geo.get("images"):
        return "skip"
    rec = json.load(open(rec_f))
    gray = np.asarray(Image.open(render_f).convert("L"))
    H, W = gray.shape
    ink = gray < otsu(gray)

    keep = np.ones_like(ink, bool)
    # 框外墨判定：frame bbox 外扩 10px（2x 空间）——原书印的边框线属于
    # 插图层（frame 裁剪已含），边界外 1-2px 的 Otsu 残墨不是页边设计
    fx0, fy0, fx1, fy1 = [int(v) for v in frame_bbox(geo["images"][0])]
    keep[fy0 - 10:fy1 + 10, fx0 - 10:fx1 + 10] = False
    for line in rec["ocr"]["lines"]:
        q = line["quad"]
        xs, ys = [v[0] for v in q], [v[1] for v in q]
        x0, x1 = max(0, int(min(xs)) - text_pad), min(W, int(max(xs)) + text_pad)
        y0, y1 = max(0, int(min(ys)) - text_pad), min(H, int(max(ys)) + text_pad)
        keep[y0:y1, x0:x1] = False
    for (ix0, iy0, ix1, iy1) in ignored:
        x0, x1 = max(0, int(ix0)), min(W, int(ix1))
        y0, y1 = max(0, int(iy0)), min(H, int(iy1))
        keep[y0:y1, x0:x1] = False

    rem = ink & keep
    frac = rem.mean()
    status = "WARN" if frac > min_frac else "ok"
    if status == "WARN":
        dbg = out / "work" / "margin_ink"
        dbg.mkdir(parents=True, exist_ok=True)
        viz = np.stack([gray] * 3, axis=-1)
        viz[rem] = (255, 0, 0)
        viz[~keep & ink] = (0, 128, 0)  # 已覆盖区（框内墨+文字墨）标绿
        Image.fromarray(viz, "RGB").save(dbg / f"p{p:03d}.png")
    return (status, int(rem.sum()), round(frac, 6),
            f"{dbg / f'p{p:03d}.png'}" if status == "WARN" else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", default=None, help="页号/区间，默认全部")
    ap.add_argument("--min-frac", type=float, default=0.001)
    ap.add_argument("--text-pad", type=int, default=12)
    a = ap.parse_args()
    out = Path(a.out)
    book = json.load(open(out / "book.json")) if (out / "book.json").exists() else {}
    ignored = book.get("margin_ink_ignored", [])  # 2x px 框，已判定元素

    pages = []
    if a.pages:
        for part in a.pages.split(","):
            part = part.strip()
            if "-" in part:
                s, e = part.split("-")
                pages.extend(range(int(s), int(e) + 1))
            elif part:
                pages.append(int(part))
    else:
        pages = sorted(int(f.stem[4:]) for f in (out / "work" / "pages").glob("p*.png"))

    warns = 0
    for p in pages:
        r = check_page(out, p, a.min_frac, a.text_pad, ignored)
        if r == "skip":
            continue
        status, n, frac, dbg = r
        if status == "WARN":
            warns += 1
            print(f"WARN p{p:03d} 框外墨 {n}px ({frac:.4%} 页面积) 目检 {dbg}")
        else:
            print(f"ok   p{p:03d} 框外墨 {n}px ({frac:.4%})")
    print(f"== margin_ink_check: {warns} WARN / {len(pages)} pages")
    sys.exit(1 if warns else 0)


if __name__ == "__main__":
    main()
