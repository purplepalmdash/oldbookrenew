#!/usr/bin/env python3
"""S6 逐页比对测试（文本保真 + 坐标严格性）+ 并排对比图。

校验项：
  1. 页数；keep 页保持原尺寸（book.json keep_sizes，若给出）
  2. 每页文字可选取（非 keep/special 页）
  3. 文本保真：原 OCR JSON 每行文字都在重建页中（逐字匹配）
  4. 坐标严格性：匹配字符并集 bbox 与原 quad（换算 pt）吻合
  5. 插图存在性
  6. 并排对比图 work/qa/compare_pNNN.png（供目检）

字符级匹配要点（行级 get_text("dict") 会把同基线相邻行合并 → 误报）：
  - rawdict 逐 char 序列，先剔除空白（"" 会打乱 probe 窗口）
  - 6 字 probe；多候选取并集 bbox 最接近原 quad 的（probe 可能歧义，
    如页内重复短语——重复短语可能真是书本文字，先目检再怀疑 OCR）
  - 容差：x/y ±14pt；宽度 max(16pt, 0.45×fs_est)；≤3 字短行跳过宽度检查
    （页码 det 框含 padding，墨迹窄于框是度量伪影）

用法: test_page.py --src book.pdf --out digitize [--pages 135]
      [--compare 2,5,50] [--page-size 842 595]
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf as fitz

TOL_PT = 14.0


def norm(t):
    # 点号变体归一：LXGW 字体把 U+00B7 写出的字形反查为 U+2219，
    # 提取文本与数据文本的点号代码位不一致会误报“文本缺失”
    for ch in "\u2219\u2027\u30fb":
        t = t.replace(ch, "\u00b7")
    return t.replace(" ", "").replace("\u3000", "")


def quad_to_pt(quad, S, OX):
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return (min(xs) * S + OX, min(ys) * S, max(xs) * S + OX, max(ys) * S)


def page_chars(doc, i):
    d = doc[i - 1].get_text("rawdict")
    out = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                for ch in sp.get("chars", []):
                    out.append((ch["c"], fitz.Rect(ch["bbox"])))
    return out


def match_line(chars, text, expect=None):
    seq_raw = [norm(c) for c, _ in chars]
    seq = [c for c in seq_raw if c]
    rects = [r for c, r in zip(seq_raw, [r for _, r in chars]) if c]
    target = norm(text)
    if not target:
        return None
    probe = list(target[: max(3, min(6, len(target)))])
    cands = []
    for i in range(len(seq) - len(probe) + 1):
        if seq[i : i + len(probe)] == probe:
            r = None
            for j in range(i, min(i + len(target), len(seq))):
                r = rects[j] if r is None else r | rects[j]
            cands.append(r)
    if not cands:
        return None
    if expect is not None:
        def dist(r):
            return abs(r.x0 - expect.x0) + abs(r.y0 - expect.y0) + abs(r.width - expect.width)
        cands.sort(key=dist)
    return cands[0]


def pm_bgr(pm):
    arr = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n)
    src = cv2.COLOR_RGBA2BGR if pm.n == 4 else cv2.COLOR_RGB2BGR
    return cv2.cvtColor(arr, src)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", default=None)
    ap.add_argument("--compare", default=None, help="生成对比图的页号，如 2,5,50")
    ap.add_argument("--page-size", nargs=2, type=float, default=[842.0, 595.0])
    a = ap.parse_args()

    out = Path(a.out)
    data, qa = out / "data", out / "work" / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    book_f = out / "book.json"
    book = json.load(open(book_f)) if book_f.exists() else {}
    keep = set(book.get("keep_pages", []))
    drop = set(book.get("drop_pages", []))
    special = {int(k) for k in book.get("special_pages", {})}
    keep_sizes = {int(k): tuple(v) for k, v in book.get("keep_sizes", {}).items()}
    out_name = book.get("out_name") or "数字化.pdf"

    src = fitz.open(a.src)
    pdf = out / out_name
    if not pdf.exists():
        raise SystemExit(f"未找到 {pdf}（先运行 build_pdf.py）")
    doc = fitz.open(str(pdf))

    PW, PH = a.page_size
    first = next(f for f in sorted(data.glob("page*.json"))
                 if "width" in json.load(open(f)))
    render = json.load(open(first))
    RW, RH = render["width"], render["height"]
    S = PH / RH
    OX = (PW - RW * S) / 2

    pages = ([int(x) for x in a.pages.split(",")] if a.pages
             else list(range(1, len(src) + 1)))
    cmps = [int(x) for x in a.compare.split(",")] if a.compare else []

    fails = []
    n_total = n_ok = n_bad = 0

    if len(doc) != len(src) - len(drop):
        fails.append(f"页数 {len(doc)} != {len(src) - len(drop)}（源 {len(src)} 丢 {len(drop)}）")
    for p, size in keep_sizes.items():
        di = p - 1 - sum(1 for q in drop if q < p)
        r = doc[di].rect
        if abs(r.width - size[0]) > 1 or abs(r.height - size[1]) > 1:
            fails.append(f"p{p}: keep 页尺寸 {r.width:.0f}x{r.height:.0f} != {size}")

    for i in pages:
        jf = data / f"page{i:03d}.json"
        if not jf.exists():
            continue
        if i in drop:
            continue
        # 源页号 → 重建 PDF 页索引（扣除前面的 drop 页）
        di = i - 1 - sum(1 for q in drop if q < i)
        rec = json.load(open(jf))
        recon_chars = page_chars(doc, di + 1)
        seq_text = norm("".join(c for c, _ in recon_chars))

        if i in keep or i in special:
            continue
        # 页面尺寸（应为重建尺寸）
        if abs(doc[di].rect.width - PW) > 1:
            fails.append(f"p{i}: 页宽 {doc[di].rect.width:.0f} != {PW:.0f}")
        # 文字可选取
        if len(seq_text) < 4:
            fails.append(f"p{i}: 重建页文字过少({len(seq_text)})")

        for line in rec["ocr"]["lines"]:
            orig = norm(line["text"])
            qx1, qy1, qx2, qy2 = quad_to_pt(line["quad"], S, OX)
            r = match_line(recon_chars, line["text"], fitz.Rect(qx1, qy1, qx2, qy2))
            n_total += 1
            if r is None:
                fails.append(f"p{i}: 文本缺失 {line['text'][:20]}")
                continue
            n_ok += 1
            fs_est = (qy2 - qy1) / 0.92
            dx = abs(r.x0 - qx1)
            dy = abs(r.y0 - qy1)
            dw = abs(r.width - (qx2 - qx1))
            dw_ok = True if len(orig) <= 3 else dw <= max(16.0, 0.45 * fs_est)
            if dx > TOL_PT or dy > TOL_PT or not dw_ok:
                n_bad += 1
                fails.append(f"p{i}: 行偏差大 {line['text'][:12]} dx={dx:.0f} "
                             f"dy={dy:.0f} dw={dw:.0f}pt")

        if any(e["label"] == "image" for e in rec.get("layout", [])) \
                and not doc[di].get_images():
            fails.append(f"p{i}: 缺少插图")

        if i in cmps:
            pix = doc[di].get_pixmap(matrix=fitz.Matrix(1.1, 1.1))
            spix = src[i - 1].get_pixmap(matrix=fitz.Matrix(1.1, 1.1))
            x, y = pm_bgr(spix), pm_bgr(pix)
            h = min(x.shape[0], y.shape[0])
            x = cv2.resize(x, (int(x.shape[1] * h / x.shape[0]), h))
            y = cv2.resize(y, (int(y.shape[1] * h / y.shape[0]), h))
            bar = 255 * np.ones((h, 8, 3), np.uint8)
            cv2.imwrite(str(qa / f"compare_p{i:03d}.png"), cv2.hconcat([x, bar, y]))

    print(f"lines: {n_ok}/{n_total} 文本匹配, 位置偏差超标 {n_bad}")
    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS ✓")


if __name__ == "__main__":
    main()
