#!/usr/bin/env python3
"""S1+S2 渲染 + MCP 批量识别。

逐页渲染源 PDF（默认 2x），调用 paddleocr-layout(detect_layout) +
paddleocr(ocr) 两个 MCP server，生成每页 JSON（layout 坐标 + 文字行 quad/起止点）。

用法:
  mcp_batch.py --src book.pdf --out digitize [--scale 2.0] [lo hi]

S1.5 deskew（默认开）：逐页从插图框 minAreaRect 顶边估角，
未检出框的页用全部检出角的中位数回退；|角|>0.03° 时整页旋转
（白边填充，画布尺寸不变），坐标全部在水平化后的空间里。
--no-deskew 关闭（扫描已对齐的书）。

环境变量:
  PADDLEOCR_MCP_VENV_BIN     venv bin 目录（不设则从 PATH 找 paddleocr_mcp）
  PADDLEOCR_MCP_PIPELINE     官方 server pipeline yaml（默认用技能 references/ocr_openvino.yaml）
  PADDLEOCR_MCP_LAYOUT_SCRIPT  layout server 脚本路径（默认用技能 scripts/layout_server.py）
  PADDLEOCR_MCP_TOOLS        工具根目录（默认=本脚本所在目录；remote_gpu_server.py 等）
  PADDLEOCR_MCP_REMOTE=1       远程 GPU（PADDLEOCR_MCP_REMOTE_HOST=user@<远程机>，ssh stdio，
                               本地图片自动 scp 到远端 inbox，路径仍传本地绝对路径）；
                               可配 PADDLEOCR_MCP_REMOTE_HOST / PADDLEOCR_MCP_REMOTE_SERVER
已有 JSON 的页自动跳过（断点续跑）。
"""
import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pymupdf as fitz
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

def _find_venv_bin():
    v = os.environ.get("PADDLEOCR_MCP_VENV_BIN")
    if v:
        return v
    import shutil
    p = shutil.which("paddleocr_mcp")
    if p:
        return str(Path(p).resolve().parent)
    raise SystemExit("未设 PADDLEOCR_MCP_VENV_BIN 且 PATH 找不到 paddleocr_mcp："
                     "请 export PADDLEOCR_MCP_VENV_BIN=<paddleocr-mcp venv>/bin")


# 工具根目录：layout_server.py 随技能打包在 scripts/，默认取本脚本所在目录
DEFAULT_TOOLS = os.environ.get("PADDLEOCR_MCP_TOOLS", str(Path(__file__).resolve().parent))
_SKILL_ROOT = Path(__file__).resolve().parent.parent


def _default_pipeline():
    cands = (_SKILL_ROOT / "references" / "ocr_openvino.yaml",
             Path(_find_venv_bin()).parent.parent / "ocr_openvino.yaml")
    for c in cands:
        if c.exists():
            return str(c)
    return str(cands[0])


def server_params(out_dir, pipeline_env=None):
    VENV_BIN = _find_venv_bin()  # 惰性解析：--help 不需要环境就绪
    pipeline = pipeline_env or os.environ.get("PADDLEOCR_MCP_PIPELINE", _default_pipeline())
    if os.environ.get("PADDLEOCR_MCP_REMOTE") == "1":
        # 远程 GPU（PADDLEOCR_MCP_REMOTE_HOST 指向的机器）：本地 wrapper 负责 ssh stdio + scp，
        # 调用方继续传本地绝对路径；yaml/device 由远端 run_*.sh 管理。
        remote_server = os.environ.get(
            "PADDLEOCR_MCP_REMOTE_SERVER", f"{DEFAULT_TOOLS}/remote_gpu_server.py")
        return (
            StdioServerParameters(
                command=f"{VENV_BIN}/python", args=[remote_server, "ocr"]),
            StdioServerParameters(
                command=f"{VENV_BIN}/python", args=[remote_server, "layout"]),
        )
    ocr = StdioServerParameters(
        command=f"{VENV_BIN}/paddleocr_mcp",
        env={
            "PADDLEOCR_MCP_MODEL": "PP-OCRv5",
            "PADDLEOCR_MCP_PPOCR_SOURCE": "local",
            "PADDLEOCR_MCP_PIPELINE_CONFIG": pipeline,
            "PADDLEOCR_MCP_DEVICE": "cpu",
        },
    )
    layout = StdioServerParameters(
        command=f"{VENV_BIN}/python",
        args=[os.environ.get(
            "PADDLEOCR_MCP_LAYOUT_SCRIPT",
            f"{DEFAULT_TOOLS}/layout_server.py",
        )],
    )
    return ocr, layout


async def call(session, tool, args, timeout=300):
    t0 = time.time()
    res = await asyncio.wait_for(session.call_tool(tool, args), timeout)
    if getattr(res, "is_error", False):  # mcp 2.3.0: is_error，不是 isError
        raise RuntimeError(f"tool {tool} error: {res.content}")
    text = "".join(getattr(c, "text", "") for c in res.content)
    return text, time.time() - t0


def _norm_quad(bbox):
    """扁平 [x1,y1,x2,y2] 或四点多边形 → [[x,y]×4]。"""
    if len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
        x1, y1, x2, y2 = bbox
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    return bbox


def estimate_frame_angle(gray):
    """插图框顶边角（度）。检不出框返回 None。
    坑：minAreaRect 的 cv angle 在 0.5° 倾斜时会报 89.5°，一律从
    box4 顶边算。"""
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inv = 255 - bw
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    closed = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, k)
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    H, W = gray.shape
    best, ba = None, 0
    for c in contours:
        a = cv2.contourArea(c)
        if a < 0.3 * H * W or a <= ba:
            continue
        rect = cv2.minAreaRect(c)
        (cx, cy), (w, h), _ = rect
        if w > 0.6 * W and h > 0.6 * H:
            best, ba = rect, a
    if not best:
        return None
    box = cv2.boxPoints(best)
    ss = box.sum(axis=1)
    dd = np.diff(box, axis=1)
    tl, tr, br, bl = box[np.argmin(ss)], box[np.argmin(dd)], box[np.argmax(ss)], box[np.argmax(dd)]
    return float(np.degrees(np.arctan2(tr[1] - tl[1], tr[0] - tl[0])))


def deskew_page(png_path, angle, min_deg=0.03):
    """整页旋转（围绕中心，不扩画布，白边填充）。返回实际使用的角。"""
    if abs(angle) < min_deg:
        return 0.0
    img = cv2.imread(png_path)
    H, W = img.shape[:2]
    M = cv2.getRotationMatrix2D((W / 2, H / 2), angle, 1.0)
    out = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    cv2.imwrite(png_path, out)
    return angle


async def recognize_page(s_ocr, s_layout, page_idx, png_path, render_scale, W, H, deskew_deg=0.0):
    # 官方 OCR server 拒绝相对路径，必须 resolve
    png_abs = str(Path(png_path).resolve())
    layout_raw, t1 = await call(s_layout, "detect_layout", {"image_path": png_abs})
    ocr_raw, t2 = await call(s_ocr, "ocr", {"input_data": png_abs, "output_mode": "detailed"})
    layout = json.loads(layout_raw)
    ocr = json.loads(ocr_raw) if ocr_raw.strip().startswith("{") and "text_lines" in ocr_raw \
        else {"text": "", "confidence": 0, "text_lines": []}
    img_boxes = [e["bbox"] for e in layout.get("elements", []) if e["label"] == "image"]

    def _in_image(quad, m=4):
        """行**中心点**在 image bbox 内才算图内（layout 框比真实插图框松，
        用角点/起点会误杀紧贴框下的字幕行）。"""
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        return any(ax1 - m <= cx <= ax2 + m and ay1 - m <= cy <= ay2 + m
                   for (ax1, ay1, ax2, ay2) in img_boxes)

    raw_lines, lines = [], []
    for tl in ocr.get("text_lines", []):
        q = _norm_quad(tl["bbox"])
        xs = [p[0] for p in q]
        ys = [p[1] for p in q]
        rec_line = {
            "text": tl["text"],
            "score": tl.get("score", tl.get("confidence", 0)),
            "quad": q,
            "start_point": [round(min(xs), 1), round(min(ys), 1)],
            "end_point": [round(max(xs), 1), round(max(ys), 1)],
        }
        raw_lines.append(rec_line)
        if not _in_image(q):
            lines.append(rec_line)

    return {
        "page": page_idx + 1,
        "render_scale": render_scale,
        "deskew_deg": round(deskew_deg, 3),
        "width": W, "height": H,
        "layout": layout.get("elements", []),
        "ocr": {
            "text": "\n".join(l["text"] for l in lines),
            "confidence": ocr.get("confidence", 0),
            "lines": lines,
            "dropped_in_image_lines": [l["text"] for l in raw_lines if l not in lines],
        },
        "timings": {"layout_s": round(t1, 1), "ocr_s": round(t2, 1)},
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--no-deskew", action="store_true",
                    help="跳过 S1.5 整页水平化（扫描已对齐时用）")
    ap.add_argument("lo", nargs="?", type=int, default=1)
    ap.add_argument("hi", nargs="?", type=int, default=None)
    a = ap.parse_args()

    out = Path(a.out)
    data_dir, page_dir = out / "data", out / "work" / "pages"
    data_dir.mkdir(parents=True, exist_ok=True)
    page_dir.mkdir(parents=True, exist_ok=True)

    src = fitz.open(a.src)
    total = len(src)
    hi = a.hi or total

    book_f = out / "book.json"
    book = json.load(open(book_f)) if book_f.exists() else {}
    skip = (set(book.get("keep_pages", []))
            | set(book.get("drop_pages", []))
            | {int(k) for k in book.get("special_pages", {})}
            | {int(k) for k in book.get("image_overrides", {})})

    todo = []
    for i in range(a.lo - 1, min(hi, total)):
        if (i + 1) in skip:
            continue  # 原样页/特殊页不走标准识别
        jf = data_dir / f"page{i+1:03d}.json"
        png = page_dir / f"p{i+1:03d}.png"
        if jf.exists():
            continue
        # 没有 JSON 就重新渲染：deskew 是原地旋转，复用旧 PNG 会二次旋转
        pix = src[i].get_pixmap(matrix=fitz.Matrix(a.scale, a.scale))
        pix.save(str(png))
        todo.append((i, png, src[i].rect.width * a.scale, src[i].rect.height * a.scale))
    print(f"pages {a.lo}-{hi}: {len(todo)} to recognize ({total} total)")
    if not todo:
        return

    # S1.5 deskew：先全部估角，未检出框的页用中位数回退
    deskew = {}
    if not a.no_deskew:
        found = []
        for i, png, W, H in todo:
            g = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
            ang = estimate_frame_angle(g)
            if ang is not None:
                found.append(ang)
            deskew[i] = ang
        fallback = statistics.median(found) if found else 0.0
        n_rot = 0
        for i, png, W, H in todo:
            ang = deskew[i] if deskew[i] is not None else fallback
            used = deskew_page(str(png), ang)
            if used:
                n_rot += 1
            deskew[i] = used if used else (deskew[i] or 0.0)
        med_all = statistics.median([v for v in deskew.values() if v]) if any(deskew.values()) else 0.0
        print(f"deskew: {n_rot}/{len(todo)} pages rotated; "
              f"detected={len(found)} fallback_median={fallback:.2f}°")

    ocr_s, layout_s = server_params(out)
    async with stdio_client(ocr_s) as (ro, wo), stdio_client(layout_s) as (rl, wl):
        async with ClientSession(ro, wo) as s_ocr, ClientSession(rl, wl) as s_layout:
            await s_ocr.initialize()
            await s_layout.initialize()
            ok, fail = 0, []
            for i, png, W, H in todo:
                jf = data_dir / f"page{i+1:03d}.json"
                t0 = time.time()
                try:
                    rec = await recognize_page(s_ocr, s_layout, i, png, a.scale,
                                               int(W), int(H), deskew.get(i, 0.0))
                    jf.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
                    ok += 1
                    txt = rec["ocr"]["text"].replace("\n", " ")[:40]
                    print(f"  p{i+1:03d} [{time.time()-t0:5.1f}s] "
                          f"img={sum(1 for e in rec['layout'] if e['label']=='image')} "
                          f"lines={len(rec['ocr']['lines'])} | {txt}")
                except Exception as e:
                    fail.append((i + 1, str(e)[:120]))
                    print(f"  p{i+1:03d} FAILED: {str(e)[:120]}")
            print(f"done: ok={ok} fail={len(fail)}")
            if fail:
                (data_dir / "_failed.json").write_text(json.dumps(fail, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
