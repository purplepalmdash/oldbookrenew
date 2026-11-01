#!/usr/bin/env python3
"""probe_baseline: 实测字体整行墨顶偏移（ink-top em），给 build_pdf.py --ink-top 供数。

build_pdf.py 的基线公式 `baseline = 行顶 + INK_TOP * fs` 里 INK_TOP 默认 0.88
（CJK 墨顶≈基线上方 0.88em，刘秀双书 500+ 行 0 位置偏差验证值）。换字体、
换扫描风格（描边粗体/细宋）时墨顶偏移会变——本脚本按"原始墨行实测"原则
直接渲染目标字体量出该常数，避免凭感觉调 0.86-0.90。

方法：PIL 在白色画布上渲染样字行（逐字 advance 放置，字间 0.1em），
anchor la 下 baseline = 原点 y + ascent，**整行**墨顶 = 首个密集墨行
（行墨数 >= max(1, 1% 峰值)，抗单像素毛刺），
ink_top_em = (baseline - 整行墨顶) / 字号。

注意是**整行**不是逐字：不同 CJK 字墨顶差异大（“一”只有 ~0.5em，
“国/十”有顶横），而行内墨顶由最高笔画的字决定——build_pdf 的基线公式
对整行生效，探的也必须是整行。样字默认含顶横类字（国/十）。

**与 build_pdf 常数的关系**：本脚本量的是**字体自身**墨顶；build_pdf 的
INK_TOP 锚定的是 **det quad 顶**（quad 顶比真实墨顶高几 px padding），所以
build_pdf 常数 ≈ 本值 + quad 顶 padding。LXGW Regular + PP-OCRv6 det 实测：
字体 0.80 / build_pdf 验证值 0.88 → padding ≈ 0.08em。**换字体时用 Δ 相对
校正**（新常数 ≈ 旧常数 + 新字体墨顶 − 旧字体墨顶），换后先 QA 基准页确认，
不要直接把本值当常数用。

用法:
  probe_baseline.py --font /path/to/CJK.ttf [--pt 200] [--samples 国一二三四五七八九十]
输出:
  {"font": "...", "pt": 200, "ink_top_em": 0.80, "per_char_max": 0.80}
  → 换字体：build_pdf.py --ink-top (旧常数 + 本值 − 旧字体本值)；QA 基准页
"""
import argparse
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def dense_top(arr: np.ndarray):
    """首个密集墨行的 y（行墨数 >= max(1, 1% 峰值)），无则 None。"""
    row_ink = (arr < 140).sum(axis=1)
    if row_ink.max() == 0:
        return None
    peak = row_ink.max()
    for y in range(len(row_ink)):
        if row_ink[y] >= max(1, 0.01 * peak):
            return y
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", required=True)
    ap.add_argument("--pt", type=int, default=200,
                    help="渲染字号（越大测量越稳，默认 200）")
    ap.add_argument("--samples", default="国一二三四五七八九十",
                    help="样字行（应含顶横类字：国/十/目）")
    a = ap.parse_args()

    font = ImageFont.truetype(a.font, a.pt)
    ascent, _ = font.getmetrics()
    canvas_h = int(a.pt * 2.2)

    # 整行渲染：逐字放置，advance = 字宽 + 0.1em 间隙。
    # 所有字共享同一 baseline：anchor la 下 baseline = 原点 y + ascent，
    # 故原点 y 取常数 2（与 t 无关！若按 2-t 放置会变成墨顶对齐、
    # 破坏行结构）；baseline = 2 + ascent，行墨顶 = 2 + min(t_i)
    bboxes = [font.getbbox(ch) for ch in a.samples]
    gap = a.pt // 10
    W = int(sum(r - l for (l, t, r, b) in bboxes)) + gap * (len(a.samples) - 1) + 8
    img = Image.new("L", (W, canvas_h), 255)
    d = ImageDraw.Draw(img)
    x = 4
    for ch, (l, t, r, b) in zip(a.samples, bboxes):
        d.text((x - l, 2), ch, font=font, fill=0)
        x += (r - l) + gap
    baseline = 2 + ascent
    top = dense_top(np.asarray(img))
    if top is None or top >= baseline:
        raise SystemExit("no measurable ink above baseline")
    ink_top_em = (baseline - top) / a.pt

    # 逐字墨顶（参考信息：行内最高单字的 em 值）
    per_char = []
    for ch, (l, t, r, b) in zip(a.samples, bboxes):
        w1 = max(r - l + 4, 8)
        img1 = Image.new("L", (w1, canvas_h), 255)
        d1 = ImageDraw.Draw(img1)
        d1.text((2 - l, 2), ch, font=font, fill=0)
        top1 = dense_top(np.asarray(img1))
        if top1 is not None and top1 < 2 + ascent:
            per_char.append((2 + ascent - top1) / a.pt)
    print(json.dumps({"font": a.font, "pt": a.pt,
                      "ink_top_em": round(ink_top_em, 4),
                      "per_char_max": round(max(per_char), 4) if per_char else None,
                      "samples": a.samples}, ensure_ascii=False))


if __name__ == "__main__":
    main()
