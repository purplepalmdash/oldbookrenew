#!/usr/bin/env python3
"""S5 重建 PDF：插图回原坐标 + 文字按原 quad 排入 + 内嵌 CJK 字体（可选取）。

- keep_pages：insert_pdf 原样插入（保留原尺寸/彩色）
- drop_pages：丢弃（如下载站水印广告页）
- image_overrides：整页替换为处理过的图（如去水印后的彩色封面），
  键页号 → 相对 out 的 PNG 路径，等比居中铺满 A4
- special_pages：按 <out>/data/<special.json> 重建（竖排列/特殊行/插图）
- text_only_pages：只排文字
- 其余：插图 + 文字
页面几何：A4 横向 842×595，S = 595/render_H，水平居中。
字号 = min(宽基, 高基/0.92)×0.98；基线 = 行顶 + 0.88em；
宽间距行（宽基≫高基且以 CJK 为主）逐字等距铺满原宽。
彩色行（special JSON 中 "color": "red"）用独立 TextWriter + color。

用法: build_pdf.py --src book.pdf --out digitize [--font /path/to/CJK.ttf]
      [--page-size 842 595] [--title 书名] [--ink-top 0.88] [--gs]

--ink-top: 基线=行顶+该值×字号（默认 0.88，刘秀双书验证值；换字体/扫描
  风格先用 probe_baseline.py 实测再传此参数）。
--gs: 保存后用 Ghostscript 终优化（/prepress，208 页书可省数倍体积；
  需系统装 gs，缺则报错。参数可用 --gs-args 覆盖）。
"""
import argparse
import json
import shutil
import statistics
import subprocess
from pathlib import Path

import numpy as np
import pymupdf as fitz


def load_book(out):
    f = out / "book.json"
    return json.load(open(f)) if f.exists() else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--font", default="/usr/share/fonts/truetype/lxgw-wenkai/LXGWWenKai-Regular.ttf")
    ap.add_argument("--page-size", nargs=2, type=float, default=[842.0, 595.0])
    ap.add_argument("--title", default=None)
    ap.add_argument("--jpeg-quality", type=int, default=0,
                    help="插图重编码为 JPEG 灰度（0=保持 PNG；**仅适用灰度网点图**："
                         "whiten 插图 80-88，巴黎案例 211 页 106→63MB。"
                         "⚠ Otsu 1-bit 图不要开：实测 43.7→91.2MB 反而变大）")
    ap.add_argument("--ink-top", type=float, default=0.88,
                    help="基线=行顶+该值×字号（默认 0.88；换字体先用 "
                         "probe_baseline.py 实测）")
    ap.add_argument("--gs", action="store_true",
                    help="保存后 Ghostscript 终优化（/prepress，减体积）")
    ap.add_argument("--gs-args",
                    default="-dCompatibilityLevel=1.5 -dPDFSETTINGS=/prepress",
                    help=argparse.SUPPRESS)
    a = ap.parse_args()

    out = Path(a.out)
    data, imgs = out / "data", out / "work" / "images"
    book = load_book(out)
    keep = set(book.get("keep_pages", []))
    drop = set(book.get("drop_pages", []))
    overrides = {int(k): (out / v) for k, v in book.get("image_overrides", {}).items()}
    special = {int(k): v for k, v in book.get("special_pages", {}).items()}
    text_only = set(book.get("text_only_pages", []))
    out_pdf = out / (book.get("out_name") or "数字化.pdf")

    FONT = fitz.Font(fontfile=a.font)
    PW, PH = a.page_size
    INK_TOP, INK_CENTER = a.ink_top, 0.40

    # 渲染几何（从第一个**标准** data JSON 读；special JSON 无 width 字段）
    first = next(f for f in sorted(data.glob("page*.json"))
                 if "width" in json.load(open(f)))
    render = json.load(open(first))
    RW, RH = render["width"], render["height"]
    S = PH / RH
    OX = (PW - RW * S) / 2

    def to_pt(x, y):
        return fitz.Point(x * S + OX, y * S)

    def place_hline(tw, quad, text, fs_page=None, force_slot=False):
        xs = [pt[0] for pt in quad]
        ys = [pt[1] for pt in quad]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        p1 = to_pt(x1, y1)
        w_pt, h_pt = (x2 - x1) * S, (y2 - y1) * S
        lref = FONT.text_length(text, fontsize=100)
        fs_w = w_pt * 100.0 / lref if lref > 0 else 10
        fs_h = h_pt / 0.92
        # 排版类书（密集正文）：det 行框高≈行距，h_pt/0.92 会低于宽基、
        # 把字号压小导致行宽铺不满 → 用每页统一宽基字号（fs_page），
        # 仅保留 1.25× 高限作溢出保护
        if fs_page is not None:
            fs = min(fs_page, fs_h * 1.25)
        else:
            fs = max(1.0, min(fs_w, fs_h) * 0.98)
        baseline = p1.y + INK_TOP * fs
        n = len(text)
        n_cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        # 宽间距/两端对齐：逐字等距铺满原宽。force_slot=纯文字排版页
        # （整行都该铺满，不受 fs_w>fs_h*1.15 的宽间距判据限制）
        slot_mode = (n >= 2 and n_cjk / max(n, 1) >= 0.8 and
                     (fs_w > fs_h * 1.15 or (force_slot and n >= 4)))
        if slot_mode:
            slot = w_pt / n
            for i, ch in enumerate(text):
                # append 的点是 advance 左缘（不是中心）！
                cx = p1.x + (i + 0.5) * slot - fs * 0.5
                tw.append(fitz.Point(cx, baseline), ch, font=FONT, fontsize=fs)
        else:
            tw.append(fitz.Point(p1.x, baseline), text, font=FONT, fontsize=fs)

    def place_vcol(tw, col):
        text, n = col["text"], col["n_chars"]
        x1, x2, y1, y2 = col["x1"], col["x2"], col["y1"], col["y2"]
        fs = min((x2 - x1), (y2 - y1) / n) * S * 0.98
        xc_pt = ((x1 + x2) / 2) * S + OX
        for i, ch in enumerate(text):
            yc = y1 + (i + 0.5) * (y2 - y1) / n
            tw.append(fitz.Point(xc_pt - fs * 0.5, yc * S + INK_CENTER * fs),
                      ch, font=FONT, fontsize=fs)

    def place_image(page, im, imgf):
        cx, cy = im["frame_center_page"]
        w, h = im["frame_w"] * S, im["frame_h"] * S
        c = to_pt(cx, cy)
        rect = fitz.Rect(c.x - w / 2, c.y - h / 2, c.x + w / 2, c.y + h / 2)
        if a.jpeg_quality > 0 and str(imgf).endswith(".png"):
            # 白化灰度图重编码 JPEG（保灰调层次，体积大降）
            pm = fitz.Pixmap(str(imgf))
            if pm.alpha or pm.colorspace.n > 1:
                pm = fitz.Pixmap(fitz.csGRAY, pm)
            page.insert_image(rect, stream=pm.tobytes("jpeg",
                              jpg_quality=a.jpeg_quality))
        else:
            page.insert_image(rect, filename=str(imgf))

    def add_special(doc, p, jf):
        sp = json.load(open(jf))
        page = doc.new_page(width=PW, height=PH)
        if sp.get("image"):
            place_image(page, sp["image"], imgs / f"page{p:03d}_{sp['image'].get('index',0)}.png")
        # 整页图特殊页（图已含文字，如扉页）：text_invisible=true 时文字层
        # 白色输出（可选取/可检索但不可见），避免黑字叠图重影
        txt_invisible = bool(sp.get("text_invisible"))
        tw = fitz.TextWriter(page.rect)
        tw_red = fitz.TextWriter(page.rect)
        for col in sp.get("vertical_columns", []):
            place_vcol(tw, col)
        for line in sp.get("horizontal_lines", []):
            if line.get("color") == "red":
                place_hline(tw_red, line["quad"], line["text"])
            else:
                place_hline(tw, line["quad"], line["text"])
        tw.write_text(page, color=(1.0, 1.0, 1.0) if txt_invisible else None)
        tw_red.write_text(page, color=(0.75, 0.05, 0.05))

    def rot_quad(quad, center, angle_deg):
        """quad 绕 center 旋转 angle_deg（图像坐标 y 向下）。用于把
        OCR 行 quad（deskew 后页面空间）转回水平化插图空间。"""
        if abs(angle_deg) < 0.02:
            return quad
        th = np.deg2rad(angle_deg)
        c = np.array(center, np.float64)
        R = np.array([[np.cos(th), np.sin(th)], [-np.sin(th), np.cos(th)]])
        pts = np.array(quad, np.float64)
        return (pts - c) @ R.T + c

    def add_processed(doc, p):
        geo = json.load(open(imgs / f"page{p:03d}.json"))
        rec = json.load(open(data / f"page{p:03d}.json"))
        page = doc.new_page(width=PW, height=PH)
        for im in geo["images"]:
            place_image(page, im, imgs / f"page{p:03d}_{im['index']}.png")
        tw = fitz.TextWriter(page.rect)
        # 注意：不要再用 layout bbox 二次过滤行（layout 框比真实插图框松，
        # 会误杀紧贴框下的字幕行）
        # 插图水平化后，行 quad 绕帧中心反向旋转同一残角，文字才与图平齐
        ang = geo["images"][0].get("angle_deg", 0.0) if geo["images"] else 0.0
        ctr = geo["images"][0]["frame_center_page"] if geo["images"] else None
        for line in rec["ocr"]["lines"]:
            quad = line["quad"]
            if ctr:
                quad = rot_quad(quad, ctr, ang)
            place_hline(tw, quad, line["text"])
        tw.write_text(page)

    def add_text_only(doc, p):
        rec = json.load(open(data / f"page{p:03d}.json"))
        page = doc.new_page(width=PW, height=PH)
        tw = fitz.TextWriter(page.rect)
        lines = rec["ocr"]["lines"]
        # 排版类纯文字页：每页统一字号 = 长行（n>=12）宽基比的中位数
        # （原书单一字号排版；逐行 min 会让字号随行宽漂移、铺不满行宽）
        ratios = []
        for l in lines:
            q = l["quad"]
            w = (max(q_[0] for q_ in q) - min(q_[0] for q_ in q)) * S
            lref = FONT.text_length(l["text"], fontsize=100)
            if lref > 0 and len(l["text"]) >= 12:
                ratios.append(w * 100.0 / lref)
        fs_page = statistics.median(ratios) * 0.99 if ratios else None
        for line in lines:
            n = len(line["text"])
            place_hline(tw, line["quad"], line["text"],
                        fs_page if n >= 12 else None, force_slot=(n >= 4))
        tw.write_text(page)

    def add_override(doc, p, imgf):
        """整页替换图（去水印封面等）：等比居中，尽量铺满。"""
        page = doc.new_page(width=PW, height=PH)
        iw, ih = fitz.Pixmap(str(imgf)).width, fitz.Pixmap(str(imgf)).height
        s = min(PW / iw, PH / ih)
        r = fitz.Rect((PW - iw * s) / 2, (PH - ih * s) / 2,
                      (PW + iw * s) / 2, (PH + ih * s) / 2)
        page.insert_image(r, filename=str(imgf))

    src = fitz.open(a.src)
    doc = fitz.open()
    for p in range(1, len(src) + 1):
        if p in drop:
            continue
        if p in overrides:
            add_override(doc, p, overrides[p])
        elif p in keep:
            doc.insert_pdf(src, from_page=p - 1, to_page=p - 1)
        elif p in special:
            add_special(doc, p, data / special[p])
        elif p in text_only:
            add_text_only(doc, p)
        else:
            if not (data / f"page{p:03d}.json").exists():
                raise SystemExit(f"p{p}: no data/page{p:03d}.json (run mcp_batch)")
            add_processed(doc, p)
    meta = {
        "title": a.title or book.get("title") or "",
        "author": book.get("author", ""),
        "subject": book.get("subject", ""),
        "creator": "paddleocr-mcp digitization pipeline",
    }
    doc.set_metadata({k: v for k, v in meta.items() if v})
    doc.subset_fonts()
    doc.save(str(out_pdf), garbage=4, deflate=True)
    print(f"saved {out_pdf} pages={len(doc)}")
    if a.gs:
        gs = shutil.which("gs")
        if not gs:
            raise SystemExit("--gs 需要系统安装 Ghostscript（gs）")
        # 文字层回归门：抽 3 页比对 gs 前后提取文本。部分 gs 版本（实测
        # 10.02.1）对 CID TrueType 子集重嵌会弄坏 ToUnicode 映射（字体表
        # uni=yes 但提取出乱码）→ 检出即弃 gs 版、保原版，并提示改走
        # --jpeg-quality 减容。宁可不压，不可交乱码文字层。
        import random as _r
        sample = _r.Random(7).sample(range(1, len(doc) + 1),
                                     k=min(3, len(doc)))
        before = [doc[p - 1].get_text() for p in sample]
        tmp = out_pdf.with_suffix(".gs.pdf")
        subprocess.run([gs, "-dBATCH", "-dNOPAUSE", "-dQUIET",
                        "-sDEVICE=pdfwrite", a.gs_args,
                        f"-sOutputFile={tmp}", str(out_pdf)], check=True)
        after = None
        try:
            chk = fitz.open(str(tmp))
            after = [chk[p - 1].get_text() for p in sample]
            chk.close()
        except Exception:
            after = None
        if after == before:
            b_sz, a_sz = out_pdf.stat().st_size, tmp.stat().st_size
            tmp.replace(out_pdf)
            print(f"gs: {b_sz/1e6:.1f}MB -> {a_sz/1e6:.1f}MB")
        else:
            tmp.unlink(missing_ok=True)
            print("gs: 文字层回归失败（此 gs 版本损坏 CJK 文字层映射，"
                  "已弃用 gs 版、保留原 PDF）——减容改走 --jpeg-quality")


if __name__ == "__main__":
    main()
