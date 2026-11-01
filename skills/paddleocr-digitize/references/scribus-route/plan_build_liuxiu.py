#!/usr/bin/env python3
"""digitize3_shang 构建规划器：刘秀（上）Scribus 路线。

页面映射（源 → 成品）：
  p1 封面(keep) → 1
  p2 免责声明(drop)
  p3 扉页(special 整图+白字) → 2
  p4 内容提要(text_only) → 3
  p5..p154 正文(图+字幕) → 4..153
  p155 版权页(text_only, 手工行+横线) → 154
  p156 水印页(drop)
坐标：2x 渲染 px /2 = pt；mm = pt*25.4/72。
"""
import json, re, cv2, numpy as np, statistics, os
from PIL import ImageFont

OUT = "/path/to/project/digitize3_shang"
SRC = "/path/to/project/test3/025刘秀（上册）（CC下载站）.pdf"
ASSET = f"{OUT}/assets"
os.makedirs(ASSET, exist_ok=True)

PT_PER_PX = 0.5          # 2x 渲染
MM_PER_PT = 25.4 / 72
PAGE_W_PT, PAGE_H_PT = 767.906, 576.0
FONT_R = "LXGW WenKai Regular"
FONT_B = "LXGW WenKai Bold"
_FONT_PX = {
    FONT_R: ImageFont.truetype("/usr/share/fonts/truetype/lxgw-wenkai/LXGWWenKai-Regular.ttf", 100),
    FONT_B: ImageFont.truetype("/usr/share/fonts/truetype/lxgw-wenkai/LXGWWenKai-Bold.ttf", 100),
}
def true_em(t, font=FONT_R):
    return _FONT_PX[font].getlength(t) / 100

def mm(pt): return round(pt * MM_PER_PT, 3)
def pt(px): return px * PT_PER_PX

INK_TOP = 0.86

def text_op(name, page, x_px, y_px, w_px, h_px, text, font, fs, color=None, align="left"):
    return {"op": "text", "name": name, "page": page,
            "x": mm(pt(x_px)), "y": mm(pt(y_px) - (0.928 - INK_TOP) * fs),
            "w": mm(pt(w_px) + 4), "h": mm(pt(h_px) * 2.0),
            "text": text, "font": font, "size": round(fs, 2),
            "color": color, "align": align}

def page_fs(lines, font=FONT_R):
    cands = []
    for l in lines:
        x1 = min(q[0] for q in l["quad"]); x2 = max(q[0] for q in l["quad"])
        em = true_em(l["text"], font)
        cands.append((pt(x2 - x1) / max(em, 0.5), em))
    long_c = [c for c, em in cands if em >= 12]
    return statistics.median(long_c or [c for c, _ in cands])

def norm_line_ops(page, lines, prefix, font=FONT_R, color=None, fs=None):
    if fs is None:
        fs = page_fs(lines, font)
    # 原书是两端对齐（字距均匀撑满行宽）：整行宽 ≥ 页内最宽行 95% 且 n≥12
    # 的行用逐字 slot frame 铺满原宽（复刻均匀字距）；短尾行保持自然宽度。
    ws = []
    for l in lines:
        q = l["quad"]
        ws.append(max(p[0] for p in q) - min(p[0] for p in q))
    max_w = max(ws) if ws else 0
    ops = []
    for i, l in enumerate(lines):
        x1 = min(q[0] for q in l["quad"]); y1 = min(q[1] for q in l["quad"])
        x2 = max(q[0] for q in l["quad"]); y2 = max(q[1] for q in l["quad"])
        w, h, n = x2 - x1, y2 - y1, len(l["text"])
        t = l["text"]
        if n >= 12 and w >= 0.95 * max_w:
            slot = w / n
            for ci, ch in enumerate(t):
                if ch == " ":
                    continue
                cx = x1 + (ci + 0.5) * slot
                ops.append(text_op(f"{prefix}_{i}c{ci}", page, cx - fs * 1.1, y1, fs * 2.2, h, ch, font, fs, color, "center"))
        elif n >= 3 and pt(w) / n > 1.5 * fs:
            slot = w / n
            for ci, ch in enumerate(t):
                cx = x1 + (ci + 0.5) * slot
                ops.append(text_op(f"{prefix}_{i}c{ci}", page, cx - fs * 1.1, y1, fs * 2.2, h, ch, font, fs, color, "center"))
        else:
            # 短尾行（非对齐）：字号按本行原宽拟合（min 页字号，防 det 框偏大）
            em = true_em(t, font)
            fs_line = min(fs, pt(w) / em * 0.99) if em > 0 else fs
            w_px = max(w, fs_line * em * 2 * 1.04)
            ops.append(text_op(f"{prefix}_{i}", page, x1, y1, w_px, h, t, font, fs_line, color))
    return ops

def load(p): return json.load(open(f"{OUT}/data/page{p:03d}.json"))

# ---------- 资产 ----------
import pymupdf
doc = pymupdf.open(SRC)
pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(3, 3))
cover = f"{ASSET}/cover_p001.png"
pix.save(cover)

plan = []
# ---------- 文档：154 页 ----------
plan.append({"op": "document", "w": mm(PAGE_W_PT), "h": mm(PAGE_H_PT), "pages": 154})

# ---------- 成品 p1 = 源 p1 封面 ----------
plan.append({"op": "page", "n": 1})
plan.append({"op": "image", "name": "img_p001", "page": 1,
             "x": mm(0), "y": mm(0), "w": mm(PAGE_W_PT), "h": mm(PAGE_H_PT),
             "path": cover})

# ---------- 成品 p2 = 源 p3 扉页（整图 + 白色不可见文字） ----------
sp = json.load(open(f"{OUT}/data/page003_special.json"))
plan.append({"op": "page", "n": 2})
plan.append({"op": "image", "name": "img_p003", "page": 2,
             "x": mm(0), "y": mm(0), "w": mm(PAGE_W_PT), "h": mm(PAGE_H_PT),
             "path": f"{OUT}/work/images/page003_0.png"})
WHITE = "Paper"
for col in sp["vertical_columns"]:
    text, n = col["text"], col["n_chars"]
    x1, x2, y1, y2 = col["x1"], col["x2"], col["y1"], col["y2"]
    xc = (x1 + x2) / 2
    for i, ch in enumerate(text):
        yc = y1 + (i + 0.5) * (y2 - y1) / n
        fs = min(x2 - x1, (y2 - y1) / n) * PT_PER_PX * 0.98
        plan.append(text_op(f"t2_v{i}", 2, xc - fs * 1.1, yc - (y2 - y1) / n / 2,
                            fs * 2.2, (y2 - y1) / n, ch, FONT_B, fs, WHITE, "center"))
for i, line in enumerate(sp["horizontal_lines"]):
    q = line["quad"]
    x1, y1 = min(p[0] for p in q), min(p[1] for p in q)
    x2, y2 = max(p[0] for p in q), max(p[1] for p in q)
    t = line["text"]
    fs = pt(x2 - x1) / max(true_em(t), 0.5) * 0.98
    # 白色不可见文字：帧放宽防换行
    plan.append(text_op(f"t2_l{i}", 2, x1, y1, (x2 - x1) * 2, y2 - y1, t, FONT_R, fs, WHITE))

# ---------- 成品 p3 = 源 p4 内容提要 ----------
d4 = load(4)
plan.append({"op": "page", "n": 3})
plan += norm_line_ops(3, d4["ocr"]["lines"], "t3")

# ---------- 成品 p4..p153 = 源 p5..p154 正文 ----------
for src_p in range(5, 155):
    fin = src_p - 1
    d = load(src_p)
    geo = json.load(open(f"{OUT}/work/images/page{src_p:03d}.json"))
    plan.append({"op": "page", "n": fin})
    if geo["images"]:
        im = geo["images"][0]
        cx, cy = im["frame_center_page"]; fw, fh = im["frame_w"], im["frame_h"]
        plan.append({"op": "image", "name": f"img_p{src_p:03d}", "page": fin,
                     "x": mm(pt(cx) - fw * PT_PER_PX / 2), "y": mm(pt(cy) - fh * PT_PER_PX / 2),
                     "w": mm(pt(fw)), "h": mm(pt(fh)),
                     "path": f"{OUT}/work/images/page{src_p:03d}_{im['index']}.png"})
    plan += norm_line_ops(fin, d["ocr"]["lines"], f"t{fin}")

# ---------- 成品 p154 = 源 p155 版权页（单元格组合行框 + 手工文本） ----------
# OCR 把表格拆成单元格框；每行 = 若干 (文本, x1, x2) 单元格；
# 文本已对 150dpi 源图校对（人氏美街→人民美术、新華→新华 等）。
P154 = [
    # (cells, y1, y2, fs_ink, tag?)  fs_ink = 原行墨高/0.88（实测于 2x 灰度图）
    ([("刘", 1039, 1103), ("秀", 1242, 1299)], 307, 366, 20.0),
    ([("上册", 1121, 1224)], 368, 420, 16.0),
    ([("编文", 926, 1042), ("义", 1098, 1153), ("俊", 1167, 1241), ("志哲", 1257, 1412)], 438, 490, 18.2, "rule"),
    ([("绘画", 925, 1045), ("陈", 1089, 1144), ("宁", 1363, 1413)], 500, 553, 18.2, "rule"),
    ([("封面画", 925, 1040), ("李", 1089, 1142), ("世", 1209, 1282), ("南", 1358, 1412)], 562, 613, 18.2, "rule"),
    ([("上海人民美术出版社出版", 936, 1409)], 631, 673, 17.6),
    ([("上海长乐路672弄33号", 1056, 1289)], 672, 705, 10.2),
    ([("新华书店上海发行所发行", 940, 1408)], 723, 767, 18.8, "rule"),
    ([("上海市印刷七厂印刷", 935, 1409)], 788, 833, 17.6, "rule"),
    ([("开本787×1092 1/64 印张2 12/32", 934, 1409)], 853, 890, 14.8),
    ([("1982年6月第1版 1982年6月第1次印刷", 895, 1445)], 894, 934, 16.5),
    ([("印数 000,001——700,000", 981, 1362)], 938, 974, 14.2),
    ([("统一书号：8081·12954", 1008, 1352)], 995, 1032, 17.6),
    ([("定价：0.17元", 1063, 1277)], 1041, 1081, 17.0),
]

# 版权页横线：在 x∈[926,1412] 内找细暗行（原灰线可能浅于 Otsu，用原始灰度）
img155g = cv2.imread(f"{OUT}/work/pages/p155.png", cv2.IMREAD_GRAYSCALE)
seg = img155g[:, 926:1412].astype(int)
dark = (seg < 215).sum(axis=1)
cand = [y for y in range(420, 950) if dark[y] > 350]
groups, cur = [], []
for y in cand:
    if cur and y - cur[-1] > 3:
        groups.append(cur); cur = []
    cur.append(y)
if cur:
    groups.append(cur)
rule_ys = [int(np.mean(g)) for g in groups]
print("p155 rules at y:", rule_ys)

def eff_em(t, font=FONT_R):
    """Scribus 渲染时 CJK↔数字直连处会多 ~0.25em 空隙（词间距伪影），计入有效宽度。"""
    em = true_em(t, font)
    cat, extra = None, 0.0
    for ch in t:
        c = "d" if ch.isdigit() else ("s" if ch == " " else "c")
        if cat is not None and {cat, c} == {"c", "d"}:
            extra += 0.25
        cat = c
    return em + extra


def col_cell_ops(i, k, t, cx1, cx2, y1, y2, fs_ink):
    """版权页单元格：字号按原墨高；≤2 字居中；n≥3 按字 advance 加权 slot 逐字定位，
    行宽精确等于原 quad 宽，且逐字独立 frame 可规避 Scribus 词间距伪影。"""
    h = y2 - y1
    if len(t) == 1:
        return [text_op(f"t154_{i}c{k}", 154, cx1, y1, cx2 - cx1, h, t, FONT_R, fs_ink, align="center")]
    advs = [max(true_em(ch, FONT_R), 0.3) for ch in t]
    tot = sum(advs)
    slot0 = (cx2 - cx1) / tot  # px / em
    # CJK 字墨宽不得超过其 slot：必要时缩小字号
    cjk_slots = [a * slot0 for a in advs if a >= 0.9]
    if cjk_slots:
        fs = min(fs_ink, min(cjk_slots) * 0.5 * 0.95 / 0.88)
    else:
        fs = fs_ink
    ops, x = [], float(cx1)
    for ci, ch in enumerate(t):
        slot = advs[ci] * slot0
        if ch == " ":
            x += slot
            continue
        cx = x + slot / 2
        ops.append(text_op(f"t154_{i}c{k}s{ci}", 154, cx - fs * 1.1, y1, fs * 2.2, h, ch, FONT_R, fs, align="center"))
        x += slot
    return ops


plan.append({"op": "page", "n": 154})
# 表格顶线（编文行上方）
if rule_ys and rule_ys[0] < 440:
    plan.append({"op": "line", "name": "ln154_top", "page": 154,
                 "x1": mm(pt(926)), "y1": mm(pt(rule_ys[0])),
                 "x2": mm(pt(1412)), "y2": mm(pt(rule_ys[0])),
                 "color": "Black", "width_pt": 0.8})
for i, item in enumerate(P154):
    cells, y1, y2, fs_ink = item[0], item[1], item[2], item[3]
    tag = item[4] if len(item) > 4 else None
    h = y2 - y1
    for k, (t, cx1, cx2) in enumerate(cells):
        plan += col_cell_ops(i, k, t, cx1, cx2, y1, y2, fs_ink)
    if tag == "rule":
        ry = min(rule_ys, key=lambda y: abs(y - (y2 + 3))) if rule_ys else y2 + 3
        plan.append({"op": "line", "name": f"ln154_{i}", "page": 154,
                     "x1": mm(pt(926)), "y1": mm(pt(ry)),
                     "x2": mm(pt(1412)), "y2": mm(pt(ry)),
                     "color": "Black", "width_pt": 0.8})

json.dump(plan, open(f"{OUT}/build_plan.json", "w"), ensure_ascii=False, indent=1)
n_img = sum(1 for o in plan if o["op"] == "image")
n_txt = sum(1 for o in plan if o["op"] == "text")
print(f"plan: {len(plan)} ops ({n_img} images, {n_txt} text frames, {sum(1 for o in plan if o['op']=='line')} lines)")
