#!/usr/bin/env python3
"""
remove_watermark.py — 斜纹平铺网站水印（如 www.xxxx.com）的去除

原理：水印在页面"白色"区域留下微弱灰度 D(p)（1-9/255）。
  1. build-dmap: 跨所有 B&W 页对"白底"像素取分通道中位数 → D_white 图案图
     （水印在 B&W 页上 100% 重复，中位数即纯水印图案；缺口用 inpaint 填补）。
  2. correct: 对彩色页（封面/封底）逐像素反解水印叠加：
       C = 255 + (C_obs - (255 - r·D)) / (1 - 0.25·r)
     r 随局部亮度连续变化（lo + (1-lo)·clip((L-100)/110, 0, 1)）：
     米白/浅底 r→1.0，深饱和色带（如红带，实际响应只有 ~0.45）r→lo。
     固定 r=1.0 会在深色底上过度反修（水印变深色残影）！
  3. 可选 inpaint: 对 r 仍压不掉的深色平铺色带区，用 D 图笔画 mask 直接 inpaint。

用法：
  # 1) 构建 D 图（跨页中位数，scale 要与后续 correct 一致）
  python remove_watermark.py build-dmap --src 书.pdf --pages all --scale 4 --out work/dwhite_4x.npy

  # 2) 反解彩色页（输出整页 PNG，供 build_pdf 的 image_overrides 使用）
  python remove_watermark.py correct --src 书.pdf --pages 1,62 --scale 4 \
      --dmap work/dwhite_4x.npy --out work/images/override_pNNN.png \
      [--lo 0.3] [--inpaint 150:1050]   # inpaint 仅限 y 带（4x 坐标）
"""
import argparse, json
from pathlib import Path
import cv2, numpy as np
import pymupdf as fitz

ALPHA = 0.25  # 水印白度叠加强度（由白底 D 与笔画振幅反推，~1/4）


def render(doc, i, scale):
    pix = doc[i].get_pixmap(matrix=fitz.Matrix(scale, scale))
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR) if pix.n == 4 else cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def cmd_build_dmap(a):
    d = fitz.open(a.src)
    pages = range(len(d)) if a.pages == "all" else [int(p) - 1 for p in a.pages.split(",")]
    H = W = None
    acc, cnt = None, None
    for i in pages:
        img = render(d, i, a.scale)
        if H is None:
            H, W = img.shape[:2]
            acc = np.zeros((3, H, W), np.float64)
            cnt = np.zeros((3, H, W), np.float64)
        mask = (img > 235).all(axis=2)
        for c in range(3):
            ch = img[:, :, c].astype(np.float64)
            acc[c][mask] += ch[mask]
            cnt[c][mask] += 1
    # 跨页"中位数"用逐像素均值代替：白底像素分布在 252-255 的窄带内（双峰直方图右峰），
    # 均值与中位数误差 <1/255，对水印图案还原可忽略
    med = np.zeros((3, H, W), np.float32)
    for c in range(3):
        with np.errstate(invalid="ignore"):
            med[c] = np.where(cnt[c] > 0, acc[c] / np.maximum(cnt[c], 1), np.nan)
    # 缺口 inpaint（按灰度）
    g = np.nan_to_num(med.mean(axis=0), nan=255.0)
    holes = (np.isnan(med[0]) | np.isnan(med[1]) | np.isnan(med[2])).astype(np.uint8) * 255
    if (holes > 0).any():
        g = cv2.inpaint(g, holes, 7, cv2.INPAINT_TELEA)
        for c in range(3):
            bad = np.isnan(med[c])
            if bad.any():
                med[c] = np.where(bad, g, med[c])
    D = (255.0 - med).clip(0, 255).astype(np.float32)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    np.save(a.out, D.transpose(1, 2, 0))  # 存 (H,W,3)，BGR 顺序
    print(f"D-map saved: {a.out} shape={D.shape[1:][::-1]} max={D.max():.1f}")


def correct_page(img, D, lo=0.3):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    L = cv2.GaussianBlur(g, (101, 101), 0)
    r = lo + (1.0 - lo) * np.clip((L - 100.0) / 110.0, 0, 1)
    rr = r[:, :, None]
    O = 255.0 - rr * D
    C = 255.0 + (img.astype(np.float32) - O) / (1.0 - ALPHA * rr)
    return np.clip(C, 0, 255).astype(np.uint8)


def cmd_correct(a):
    D = np.load(a.dmap)  # (H,W,3) BGR
    d = fitz.open(a.src)
    for p in [int(x) for x in a.pages.split(",")]:
        img = render(d, p - 1, a.scale)
        if img.shape[:2] != D.shape[:2]:
            print(f"p{p}: 尺寸不匹配 img={img.shape[:2]} dmap={D.shape[:2]}，跳过")
            continue
        C = correct_page(img, D, lo=a.lo)
        if a.inpaint:
            y1, y2 = [int(v) for v in a.inpaint.split(":")]
            dm = D[y1:y2, :, 1]
            mask = (dm > 4).astype(np.uint8) * 255
            mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
            C[y1:y2] = cv2.inpaint(C[y1:y2], mask, 7, cv2.INPAINT_TELEA)
        out = Path(a.out.replace("{p}", str(p))) if "{p}" in a.out else Path(f"{a.out}_{p:03d}")
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), C)
        print(f"p{p}: {out}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("build-dmap")
    a1.add_argument("--src", required=True)
    a1.add_argument("--pages", default="all")
    a1.add_argument("--scale", type=float, default=4)
    a1.add_argument("--out", required=True)
    a2 = sub.add_parser("correct")
    a2.add_argument("--src", required=True)
    a2.add_argument("--pages", required=True, help="逗号分隔，1 起")
    a2.add_argument("--scale", type=float, default=4)
    a2.add_argument("--dmap", required=True)
    a2.add_argument("--out", required=True, help="输出 PNG 路径模板，{p} 替换页码")
    a2.add_argument("--lo", type=float, default=0.3, help="深色底 r 下限")
    a2.add_argument("--inpaint", default=None, help="y1:y2（scale 坐标），对该带做 inpaint")
    a = ap.parse_args()
    (cmd_build_dmap if a.cmd == "build-dmap" else cmd_correct)(a)


if __name__ == "__main__":
    main()
