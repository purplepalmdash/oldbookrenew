#!/usr/bin/env python3
"""digitize1 构建规划器：OCR/几何 JSON → Scribus MCP 操作计划 build_plan.json。

页面：114 页（源 p1-p114，p115 丢弃）。坐标：2x 渲染 px /2 = pt；mm = pt*0.352778。
"""
import json, re, cv2, numpy as np, pymupdf, statistics
from PIL import ImageFont

OUT = "/path/to/project/digitize1"
SRC = "/path/to/project/test1/052孙膑和庞涓（CC下载站）.pdf"
ASSET = f"{OUT}/assets"
import os; os.makedirs(ASSET, exist_ok=True)

PT_PER_PX = 0.5          # 2x 渲染
MM_PER_PT = 25.4 / 72
PAGE_W_PT, PAGE_H_PT = 767.906, 576.0
FONT_R = "LXGW WenKai Regular"
FONT_B = "LXGW WenKai Bold"
_FONT_PX = {
    FONT_R: ImageFont.truetype("/usr/share/fonts/truetype/lxgw-wenkai/LXGWWenKai-Regular.ttf", 100),
    FONT_B: ImageFont.truetype("/usr/share/fonts/truetype/lxgw-wenkai/LXGWWenKai-Bold.ttf", 100),
}
def text_len_100(t, font=FONT_R):
    """100pt 下的精确文本宽度（px）"""
    return _FONT_PX[font].getlength(t)

def true_em(t, font=FONT_R):
    """真实字体（LXGW）下的有效 em 数：PIL 实测 advance，与 Scribus 渲染一致
    （实测校准：CJK/全角标点 1.0em，数字 0.6em，空格 0.35em；
     旧 adj_len_100 的 0.55/0.30 系统性低估 ~1%，导致行尾折行溢出。"""
    return _FONT_PX[font].getlength(t) / 100

def adj_len_100(t):
    """Scribus 1.6 CJK 环境宽度估计（100pt 基准 px）：
    与汉字紧邻的数字/字母被渲染成全角 1em，其余数字 0.55em。"""
    L = 0.0
    for i, c in enumerate(t):
        prev = t[i-1] if i > 0 else ""
        nxt = t[i+1] if i+1 < len(t) else ""
        cjk_near = (prev and "\u4e00" <= prev <= "\u9fff") or (nxt and "\u4e00" <= nxt <= "\u9fff")
        if "\u4e00" <= c <= "\u9fff" or c in "，。：；！？、""''（）《》—…·—":
            L += 100
        elif c == " ":
            L += 30
        elif c.isdigit() or c.isalpha():
            L += 100 if cjk_near else 55
        else:
            L += 55
    return L

def mm(pt): return round(pt * MM_PER_PT, 3)
def pt(px): return px * PT_PER_PX

# ---------- 资产 ----------
# p1 封面 3x 渲染
doc = pymupdf.open(SRC)
pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(3, 3))
cover = f"{ASSET}/cover_p001.png"; pix.save(cover)

def crop_seal(page_png, x1, y1, x2, y2, name, red=False):
    img = cv2.imread(page_png)
    crop = img[y1:y2, x1:x2].copy()
    if red:
        b, g, r = cv2.split(crop)
        mask = (r.astype(int) - b.astype(int) > 25)
    else:
        mask = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) < 150
    ys, xs = np.where(mask)
    if len(xs) == 0:
        print("seal not found", name); return None
    m = 8
    cx1, cx2 = max(xs.min()-m, 0), min(xs.max()+m, crop.shape[1])
    cy1, cy2 = max(ys.min()-m, 0), min(ys.max()+m, crop.shape[0])
    out = crop[cy1:cy2, cx1:cx2]
    # 二值化清底：保留墨迹原色，背景纯白
    if red:
        thr = cv2.threshold(cv2.split(out)[2], 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[0]
    else:
        gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[0]
    ink = (gray if not red else cv2.split(out)[2]) < thr
    out = np.where(ink[..., None], out, 255).astype(np.uint8)
    fp = f"{ASSET}/{name}.png"
    cv2.imwrite(fp, out)
    return fp, (cx1, cy1, cx2, cy2)

seal2 = crop_seal(f"{OUT}/work/pages/p002.png", 1250, 900, 1536, 1152, "seal_p002")
seal114 = crop_seal(f"{OUT}/work/pages/p114.png", 40, 40, 260, 340, "seal_p114", red=True)
print("seals:", seal2, seal114)

# ---------- 文本工具 ----------
def eff_n(t):
    return len(t)  # 兼容占位

INK_TOP = 0.86  # CJK 墨迹顶 ≈ 基线上方 0.86em（LXGW）

def text_op(name, page, x_px, y_px, w_px, h_px, text, font, fs, color=None, align="left"):
    """x/y/w/h 均为 2x 渲染 px；fs 单位 pt。frame 顶 = 墨迹顶（ascent 0.928 vs 墨迹 0.86 补偿）。"""
    return {"op": "text", "name": name, "page": page,
            "x": mm(pt(x_px)), "y": mm(pt(y_px) - (0.928 - INK_TOP) * fs),
            "w": mm(pt(w_px) + 4), "h": mm(pt(h_px) * 2.0),
            "text": text, "font": font, "size": round(fs, 2),
            "color": color, "align": align}

def page_fs(lines, font=FONT_R):
    """每页统一字号 = 各行（宽/em）比的中位数（长行 em>=12 定标）。
    em 用真实字体 PIL 实测（与渲染一致），抗 det 框噪声。"""
    cands = []
    for l in lines:
        x1 = min(q[0] for q in l["quad"]); x2 = max(q[0] for q in l["quad"])
        em = true_em(l["text"], font)
        cands.append((pt(x2-x1) / max(em, 0.5), em))
    long_c = [c for c, em in cands if em >= 12]
    return statistics.median(long_c or [c for c, _ in cands])

def norm_line_ops(page, lines, prefix, font=FONT_R, color=None, fs=None):
    """每行一个 frame，全页统一字号；宽间距行（slot>1.5*fs）逐字 frame。
    帧宽 = max(quad宽, 真实字体文本宽 * 1.04)——PIL 实测宽与 Scribus 渲染
    一致，4% 余量吸收换行算法/空格压缩差异，防行尾单字折行溢出。"""
    if fs is None:
        fs = page_fs(lines, font)
    ops = []
    for i, l in enumerate(lines):
        x1 = min(q[0] for q in l["quad"]); y1 = min(q[1] for q in l["quad"])
        x2 = max(q[0] for q in l["quad"]); y2 = max(q[1] for q in l["quad"])
        w, h, n = x2-x1, y2-y1, len(l["text"])
        t = l["text"]
        if n >= 3 and pt(w)/n > 1.5 * fs:
            slot = w / n
            for ci, ch in enumerate(t):
                cx = x1 + (ci + 0.5) * slot
                ops.append(text_op(f"{prefix}_{i}c{ci}", page, cx - fs*1.1, y1, fs*2.2, h, ch, font, fs, color, "center"))
        else:
            w_px = max(w, fs * true_em(t, font) * 2 * 1.04)
            ops.append(text_op(f"{prefix}_{i}", page, x1, y1, w_px, h, t, font, fs, color))
    return ops

def load(p): return json.load(open(f"{OUT}/data/page{p:03d}.json"))

plan = []
# ---------- 文档 ----------
plan.append({"op": "document", "w": mm(PAGE_W_PT), "h": mm(PAGE_H_PT), "pages": 114})

# ---------- p1 封面 ----------
plan.append({"op": "page", "n": 1})
plan.append({"op": "image", "name": "img_p001", "page": 1,
             "x": mm(0), "y": mm(0), "w": mm(PAGE_W_PT), "h": mm(PAGE_H_PT),
             "path": cover})

# ---------- p2 扉页 ----------
d2 = load(2)
plan.append({"op": "page", "n": 2})
# 标题 5 字
tl = [l for l in d2["ocr"]["lines"] if l["text"] == "孙膑和庞涓"][0]
tx1 = min(q[0] for q in tl["quad"]); ty1 = min(q[1] for q in tl["quad"])
tx2 = max(q[0] for q in tl["quad"]); ty2 = max(q[1] for q in tl["quad"])
th = pt(ty2-ty1) / 0.92 * 0.98
slot = (tx2-tx1) / 5
for ci, ch in enumerate("孙膑和庞涓"):
    cx = tx1 + (ci + 0.5) * slot
    plan.append(text_op(f"t2_c{ci}", 2, cx - th*1.1, ty1, th*2.2, ty2-ty1, ch, FONT_B, th, "Black", "center"))
# 王振民 / 改编 / 秀公… / 人民美术出版社（宽排行自动逐字）
rest2 = [l for l in d2["ocr"]["lines"] if l["text"] != "孙膑和庞涓"]
plan += norm_line_ops(2, rest2, "t2")
if seal2:
    plan.append({"op": "image", "name": "img_seal_p002", "page": 2,
                 "x": mm(pt(1250+seal2[1][0])), "y": mm(pt(900+seal2[1][1])),
                 "w": mm(pt(seal2[1][2]-seal2[1][0])), "h": mm(pt(seal2[1][3]-seal2[1][1])),
                 "path": seal2[0]})

# ---------- p3 内容说明 ----------
d3 = load(3)
plan.append({"op": "page", "n": 3})
title3 = [l for l in d3["ocr"]["lines"] if l["text"] == "内容说明"][0]
x1 = min(q[0] for q in title3["quad"]); y1 = min(q[1] for q in title3["quad"])
x2 = max(q[0] for q in title3["quad"]); y2 = max(q[1] for q in title3["quad"])
fs3 = pt(y2-y1) / 0.92 * 0.98
slot = (x2-x1) / 4
for ci, ch in enumerate("内容说明"):
    cx = x1 + (ci + 0.5) * slot
    plan.append(text_op(f"t3_c{ci}", 3, cx - fs3*1.1, y1, fs3*2.2, y2-y1, ch, FONT_B, fs3, "Black", "center"))
body3 = [l for l in d3["ocr"]["lines"] if l["text"] != "内容说明"]
plan += norm_line_ops(3, body3, "t3")

# ---------- p4-p113 正文 ----------
for p in range(4, 114):
    d = load(p)
    geo = json.load(open(f"{OUT}/work/images/page{p:03d}.json"))
    plan.append({"op": "page", "n": p})
    im = geo["images"][0]
    cx, cy = im["frame_center_page"]; fw, fh = im["frame_w"], im["frame_h"]
    plan.append({"op": "image", "name": f"img_p{p:03d}", "page": p,
                 "x": mm(pt(cx) - fw*PT_PER_PX/2), "y": mm(pt(cy) - fh*PT_PER_PX/2),
                 "w": mm(pt(fw)), "h": mm(pt(fh)),
                 "path": f"{OUT}/work/images/page{p:03d}_0.png"})
    plan += norm_line_ops(p, d["ocr"]["lines"], f"t{p}")

# ---------- p114 版权页 ----------
plan.append({"op": "page", "n": 114})
# 红印
if seal114:
    plan.append({"op": "image", "name": "img_seal_p114", "page": 114,
                 "x": mm(pt(40+seal114[1][0])), "y": mm(pt(40+seal114[1][1])),
                 "w": mm(pt(seal114[1][2]-seal114[1][0])), "h": mm(pt(seal114[1][3]-seal114[1][1])),
                 "path": seal114[0]})
# 蓝线（版权块上下）
img114 = cv2.imread(f"{OUT}/work/pages/p114.png")
# 找蓝色水平线：行均值蓝色且长
def blue_rows(img):
    b, g, r = cv2.split(img)
    blue = ((b.astype(int) - r.astype(int) > 40) & (b > 90)).astype(np.uint8)
    rowsum = blue.sum(axis=1)
    return rowsum
rs = blue_rows(img114)
rows = [y for y in range(560, 1100) if rs[y] > 500]
# 分组
groups, cur = [], []
for y in rows:
    if cur and y - cur[-1] > 3: groups.append(cur); cur = []
    cur.append(y)
if cur: groups.append(cur)
line_ys = [int(np.mean(g)) for g in groups]
print("p114 blue lines at y:", line_ys)
for k, ly in enumerate(line_ys[:2]):
    plan.append({"op": "line", "name": f"ln114_{k}", "page": 114,
                 "x1": mm(pt(648)), "y1": mm(pt(ly)), "x2": mm(pt(1484)), "y2": mm(pt(ly)),
                 "color": "ColophonBlue", "width_pt": 1.2})
# 手工行结构（合并碎片 + 补全"全"字）
P114 = [
    ("孙膑和庞涓", (834,638,1303,692), "wide", FONT_B),
    ("编者：王振民", (648,704,1048,746), "wide", FONT_R),
    ("出版者：人民美术出版社", (1094,707,1483,746), "normal", FONT_R),
    ("（北京北总布胡同32号）", (1223,746,1455,779), "normal", FONT_R),
    ("绘者：秀公、新昌、新国", (647,770,1047,815), "wide", FONT_R),
    ("发行者：新华书店北京发行所", (1094,793,1484,833), "normal", FONT_R),
    ("封面绘者：许全群", (656,843,1051,884), "wide", FONT_R),
    ("印刷者：人民美术出版社印刷厂", (1094,844,1483,880), "normal", FONT_R),
    ("1982年1月第1版第1次印刷", (736,892,1102,928), "normal", FONT_R),
    ("印数：1—800,000", (1149,890,1395,927), "normal", FONT_R),
    ("开本：787×1092毫米1/64", (738,937,1077,974), "normal", FONT_R),
    ("印张：1 3/4", (1150,934,1325,973), "normal", FONT_R),
    ("统一书号：8027·8023", (737,981,1025,1017), "normal", FONT_R),
    ("定价：0.14元", (1152,980,1347,1015), "normal", FONT_R),
]
def line_color(box):
    x1,y1,x2,y2 = box
    crop = img114[int(y1):int(y2), int(x1):int(x2)]
    gsum = crop.sum(axis=2)
    mask = gsum < 450
    if mask.sum() < 10: return "Black"
    m = np.median(crop[mask], axis=0).astype(int)  # BGR
    rgb = (int(m[2])/255, int(m[1])/255, int(m[0])/255)
    return {"type": "linecolor", "rgb": rgb}
# 正文统一字号 = normal 行（宽/em）比的中位数；标题行(i==0)自定
_cands = [pt(b[2]-b[0]) / max(true_em(t, font), 0.5)
          for t, b, kind, font in P114[1:] if kind == "normal"]
_FS114 = statistics.median(_cands)
for i, (t, box, kind, font) in enumerate(P114):
    x1,y1,x2,y2 = box
    w, h = x2-x1, y2-y1
    color = line_color(box)
    colname = None
    if isinstance(color, dict):
        colname = f"CB{i}"
        plan.append({"op": "color", "name": colname, "rgb": color["rgb"]})
    else:
        colname = "Black"
    fs_h = pt(h) / 0.92 * 0.98
    fs = fs_h if i == 0 else _FS114  # 标题行自定，正文统一
    if kind == "wide":
        slot = w / len(t)
        for ci, ch in enumerate(t):
            cx = x1 + (ci + 0.5) * slot
            plan.append(text_op(f"t114_{i}c{ci}", 114, cx - fs*1.1, y1, fs*2.2, h, ch, font, fs, colname, "center"))
    else:
        w_px = max(w, fs * true_em(t, font) * 2 * 1.04)
        plan.append(text_op(f"t114_{i}", 114, x1, y1, w_px, h, t, font, fs, colname))

# 文档默认蓝色（线用）
plan.insert(1, {"op": "color", "name": "ColophonBlue", "rgb": (0.18, 0.43, 0.64)})

json.dump(plan, open(f"{OUT}/build_plan.json", "w"), ensure_ascii=False, indent=1)
n_img = sum(1 for o in plan if o["op"]=="image")
n_txt = sum(1 for o in plan if o["op"]=="text")
print(f"plan: {len(plan)} ops ({n_img} images, {n_txt} text frames, {sum(1 for o in plan if o['op']=='line')} lines)")
