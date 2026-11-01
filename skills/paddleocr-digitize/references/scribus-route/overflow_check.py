#!/usr/bin/env python3
"""帧级换行溢出检测：对每个数据行，锚定 PDF 同行，检查行尾字符是否
折到下一 PDF 行（x 回到行首、y 下移一行）。宽间距逐字行跳过。
用法: overflow_check.py [pdf路径]
"""
import json, sys, fitz

OUT = "/path/to/project/digitize1"
pdf_path = sys.argv[1] if len(sys.argv) > 1 else f"{OUT}/孙膑和庞涓_数字化.pdf"
d = fitz.open(pdf_path)

def pdf_lines(p):
    rd = d[p].get_text("rawdict")
    out = []
    for b in rd["blocks"]:
        for l in b.get("lines", []):
            t = "".join(ch["c"] for sp in l["spans"] for ch in sp["chars"])
            if t.strip():
                fs = l["spans"][0]["size"]
                out.append((l["bbox"][1], l["bbox"][0], fs, t))
    return sorted(out)

bad = 0
for pn in range(2, 115):
    if pn == 114:  # 手工版式页，数据行为旧碎片，单独目检
        continue
    data = json.load(open(f"{OUT}/data/page{pn:03d}.json"))
    lines = data["ocr"]["lines"]
    plines = pdf_lines(pn - 1)
    if not plines:
        continue
    for i, l in enumerate(lines):
        t = l["text"]
        x1 = min(q[0] for q in l["quad"]) / 2
        y1 = min(q[1] for q in l["quad"]) / 2
        x2 = max(q[0] for q in l["quad"]) / 2
        w, n = x2 - x1, len(t)
        # 锚定 PDF 行：y 最接近且 x 起点接近
        cands = [j for j, (y, x, fs, txt) in enumerate(plines)
                 if abs(y - y1) < 8 and abs(x - x1) < 10]
        if not cands:
            continue
        j = cands[0]
        y, x, fs, txt = plines[j]
        if n >= 3 and w / n > 1.5 * fs:
            continue  # 宽间距逐字行
        if txt == t:
            continue  # 完整一行 ✓
        if t.startswith(txt) and len(txt) < n:
            # 剩余字符是否出现在下一 PDF 行开头（折行溢出）
            rest = t[len(txt):]
            nxt = [q for q in plines[j+1:j+3]
                   if q[0] - y > 0.5*fs and abs(q[1] - x1) < 10]
            if nxt and rest.startswith(nxt[0][3]):
                bad += 1
                print(f"WRAP p{pn} line{i}: {txt[-6:]!r}… 折行 {nxt[0][3][:8]!r}")
            else:
                # 逐字 frame 布局（标题等）：剩余字符各自独立成行
                band = [q[3] for q in plines if y <= q[0] < y + 1.5*fs]
                if all(c in band for c in rest):
                    continue
                print(f"DIFF p{pn} line{i}: pdf={txt!r} data={t!r}")
        else:
            print(f"DIFF p{pn} line{i}: pdf={txt!r} data={t!r}")
print("wrapped lines:", bad)
