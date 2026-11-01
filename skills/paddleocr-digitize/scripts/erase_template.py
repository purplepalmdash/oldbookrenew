#!/usr/bin/env python3
"""erase_template: 彩色图上扫描期水印的模板反混合（un-blend）。

适用场景（三条件同时满足才用本脚本）：
  1. 水印坐在**彩色图**上（Otsu 黑白化天然抑制的浅灰水印不需要它——
     见 SKILL 水印节；平铺斜纹水印用 remove_watermark.py 的 D-map 法）；
  2. 水印是**扫描期叠加**：同一水印图案在书内每一页**像素一致**
     （scanner 空间固定，与纸质倾斜无关）——用同图案两页互相关验证；
  3. 书内有一页水印落在**白纸区**（模板页，水印区不压其他内容）。

模型（乘性吸墨，2026-11 刘秀下册 p2 彩色横幅实测）：
  observed = base * (1 - a(x))
  模板页上 a(x) = (P - T(x)) / P（P=纸色，逐通道，取自模板框外亮环中位数）
  base = observed / (1 - a) 即把水印笔画下的底图算回来。
  只去水印形状，横幅画保留。

**必须在摆正（deskew）之前**对原始 2x 渲染图操作——水印在 scanner
空间固定，两页的纸倾斜不同不影响水印同位；摆正后纸质坐标变了反而
不同相。处理完的图走 book.json 的 image_overrides 整页替换。

用法:
  erase_template.py --render work/pages/p002.png --tmpl work/pages/p007.png \
      --box 0.05 0.05 0.95 0.45 [--box ...] --out work/images/clean_p002.png

  --box 为页面归一化比例 (x0 y0 x1 y1)，模板页与目标页用同一组 box。
  吸收场 a 逐通道 clip 到 [0, 0.97] 后 5x5 高斯平滑（抗噪），
  仅 a>0.05 的像素参与反混合（a 极小处不动，防噪声放大）。
"""
import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def unblend(a: np.ndarray, tsub: np.ndarray, P: np.ndarray) -> np.ndarray:
    """单框反混合。a: 目标页裁剪区, tsub: 模板页同区, P: 纸色 (3,)。"""
    abs_ = np.clip((P[None, None, :] - tsub) / P[None, None, :], 0.0, 0.97)
    abs_ = cv2.GaussianBlur(abs_, (5, 5), 1.2)
    f = 1.0 - abs_
    keep = abs_ > 0.05
    out = a.copy()
    for cc in range(3):
        k = keep[:, :, cc]
        out[k, cc] = np.clip(a[k, cc] / f[k, cc], 0.0, 255.0)
    return out


def paper_color(t: np.ndarray, tx0, ty0, tx1, ty1, m: int = 30) -> np.ndarray:
    """模板页上水印框外一圈亮环的中位纸色（滤掉环上偶发水印笔画）。"""
    th, tw = t.shape[:2]
    rx0, ry0 = max(0, tx0 - m), max(0, ty0 - m)
    rx1, ry1 = min(tw, tx1 + m), min(th, ty1 + m)
    ring = np.concatenate([
        t[ry0:ry0 + 1, rx0:rx1].reshape(-1, 3),
        t[ry1 - 1:ry1, rx0:rx1].reshape(-1, 3),
        t[ry0:ry1, rx0:rx0 + 1].reshape(-1, 3),
        t[ry0:ry1, rx1 - 1:rx1].reshape(-1, 3),
    ])
    bright = ring[ring.min(axis=1) > 120]
    P = np.median(bright, axis=0) if bright.size else np.median(ring, axis=0)
    return np.maximum(P.astype(np.float32), 40.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", required=True, help="目标页 2x 渲染图（摆正前）")
    ap.add_argument("--tmpl", required=True,
                    help="模板页 2x 渲染图（水印落在白纸区的那页，摆正前）")
    ap.add_argument("--box", nargs=4, type=float, action="append", required=True,
                    metavar=("X0", "Y0", "X1", "Y1"),
                    help="页面归一化比例框，可多次；模板/目标页用同一组")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    timg = Image.open(a.tmpl)
    t = np.array(timg.convert("RGB")).astype(np.float32)
    th, tw = t.shape[:2]
    img = Image.open(a.render)
    if img.size != timg.size:
        raise SystemExit(f"尺寸不一致：render={img.size} tmpl={timg.size}"
                         "（必须是同一扫描源的 2x 渲染）")
    canvas = np.array(img.convert("RGB")).astype(np.float32)
    chh, cww = canvas.shape[:2]

    for (nx0, ny0, nx1, ny1) in a.box:
        tx0, ty0 = int(nx0 * tw), int(ny0 * th)
        tx1, ty1 = int(nx1 * tw), int(ny1 * th)
        tsub = t[ty0:ty1, tx0:tx1]
        if tsub.size == 0:
            continue
        P = paper_color(t, tx0, ty0, tx1, ty1)
        px0, py0 = int(nx0 * cww), int(ny0 * chh)
        px1, py1 = int(nx1 * cww), int(ny1 * chh)
        if px1 <= px0 or py1 <= py0:
            continue
        sub = canvas[py0:py1, px0:px1]
        if tsub.shape[:2] != sub.shape[:2]:
            tsub = cv2.resize(tsub, (px1 - px0, py1 - py0))
        canvas[py0:py1, px0:px1] = unblend(sub, tsub, P)
        print(f"box ({nx0:.3f},{ny0:.3f})-({nx1:.3f},{ny1:.3f}) "
              f"paper={P.round(0).tolist()}")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(canvas.astype(np.uint8), "RGB").save(out)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
