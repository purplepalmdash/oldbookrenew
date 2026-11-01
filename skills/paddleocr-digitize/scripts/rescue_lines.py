#!/usr/bin/env python3
"""S4 低置信字幕行救援（浅灰油墨 → PaddleOCR 乱码）。

自动找出含 score < 阈值 行的页（乱码行的唯一可靠信号是低 score；
字符频率/bigram/墨迹灰度等启发式都无效——乱码恰好由常用字组成），
对字幕条带（插图框底边以下到页底）做 百分位对比度拉伸 + 2x 放大，
经 PaddleOCR MCP 重新识别，替换条带内旧行。

增强配方实测：纯拉伸最优（Otsu 版浅字全灭；CLAHE 行切分略差）。

用法: rescue_lines.py --out digitize [--threshold 0.6]
输出: 更新 <out>/data/pageNNN.json（追加 ocr.rescue 元数据）
"""
import argparse
import json
import os
import re
from pathlib import Path

import cv2
import numpy as np
import asyncio
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


# 工具根目录：默认取本脚本所在目录（yaml 随技能打包在 references/）
DEFAULT_TOOLS = os.environ.get("PADDLEOCR_MCP_TOOLS", str(Path(__file__).resolve().parent))
_SKILL_ROOT = Path(__file__).resolve().parent.parent


def _default_cpu_pipeline():
    c = _SKILL_ROOT / "references" / "ocr_cpu.yaml"
    return str(c) if c.exists() else f"{DEFAULT_TOOLS}/ocr_cpu.yaml"


def stretch(g, lo_p=10, hi_p=99):
    lo, hi = np.percentile(g, lo_p), np.percentile(g, hi_p)
    return np.clip((g.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def skipped_pages(out_dir):
    """keep/special 页不走标准数据流程，不救援。"""
    book_f = out_dir / "book.json"
    book = json.load(open(book_f)) if book_f.exists() else {}
    return set(book.get("keep_pages", [])) | {int(k) for k in book.get("special_pages", {})}


def find_affected(data_dir, threshold, skip):
    affected = []
    for f in sorted(data_dir.glob("page*.json")):
        if not re.match(r"page\d{3}\.json$", f.name):
            continue
        p = int(f.name[4:7])
        if p in skip:
            continue
        d = json.load(open(f))
        # 已救援行（rescued=True）的 score 可能不可靠（小图 OCR 报 0），不再重复标记
        if any(l.get("score", 1.0) < threshold and not l.get("rescued")
               for l in d["ocr"]["lines"]):
            affected.append(p)
    return affected


async def main_async(a):
    out = Path(a.out)
    data, pages_dir, exp = out / "data", out / "work" / "pages", out / "work" / "exp"
    exp.mkdir(parents=True, exist_ok=True)

    skip = skipped_pages(out)
    affected = find_affected(data, a.threshold, skip)
    if not affected:
        print(f"no lines with score < {a.threshold}; nothing to rescue")
        return
    print(f"affected pages: {affected}")

    # 条带小图，CPU 即可（~5s/页）；默认 ocr_cpu.yaml，可用 --pipeline 覆盖
    S = StdioServerParameters(
        command=f"{_find_venv_bin()}/paddleocr_mcp",
        env={
            "PADDLEOCR_MCP_MODEL": "PP-OCRv5",
            "PADDLEOCR_MCP_PPOCR_SOURCE": "local",
            "PADDLEOCR_MCP_PIPELINE_CONFIG": a.pipeline or os.environ.get(
                "PADDLEOCR_MCP_PIPELINE_CPU", _default_cpu_pipeline()),
            "PADDLEOCR_MCP_DEVICE": "cpu",
        },
    )
    async with stdio_client(S) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for p in affected:
                rec = json.load(open(data / f"page{p:03d}.json"))
                geo_f = out / "work" / "images" / f"page{p:03d}.json"
                if not geo_f.exists():
                    print(f"p{p:03d}: no geometry json (run process_page first), skip")
                    continue
                geo = json.load(open(geo_f))
                if not geo["images"]:
                    print(f"p{p:03d}: no images, cannot locate caption strip, skip")
                    continue
                fb = int(geo["images"][0]["frame_center_page"][1]
                         + geo["images"][0]["frame_h"] / 2) - 5
                pg = cv2.imread(str(pages_dir / f"p{p:03d}.png"), cv2.IMREAD_GRAYSCALE)
                strip = stretch(pg[fb:, :])
                enh = cv2.resize(strip, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                f = exp / f"rescue_p{p:03d}.png"
                cv2.imwrite(str(f), enh)

                res = await s.call_tool("ocr", {"input_data": str(f.resolve()), "output_mode": "detailed"})
                if getattr(res, "is_error", False):
                    # 失败安全：OCR 出错时不动旧行，下次运行可重试
                    print(f"p{p:03d}: rescue OCR failed, page left unchanged")
                    continue
                text = "".join(getattr(c, "text", "") for c in res.content)
                d = json.loads(text) if text.strip().startswith("{") and "text_lines" in text \
                    else {"text_lines": []}

                new_lines = []
                for tl in d.get("text_lines", []):
                    t = tl["text"].strip()
                    bx = tl["bbox"]
                    # 2x 放大 + 条带偏移 → 页 2x 坐标
                    quad = [
                        [bx[0] / 2, fb + bx[1] / 2],
                        [bx[2] / 2, fb + bx[1] / 2],
                        [bx[2] / 2, fb + bx[3] / 2],
                        [bx[0] / 2, fb + bx[3] / 2],
                    ]
                    n_cjk = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
                    if n_cjk >= 2 or (n_cjk == 0 and re.fullmatch(
                            r"[\d.,，。！？!?;；:：\-—\s]+", t)):
                        new_lines.append({
                            "text": t,
                            "score": round(float(tl.get("score", tl.get("confidence", 1.0))), 3),
                            "quad": quad,
                            "start_point": [quad[0][0], quad[0][1]],
                            "end_point": [quad[2][0], quad[2][1]],
                            "rescued": True,
                        })

                keep, old_bad = [], []
                for l in rec["ocr"]["lines"]:
                    y1 = min(pt[1] for pt in l["quad"])
                    if y1 > fb - 20:  # 条带内旧行全部换掉
                        if l.get("score", 1.0) < a.threshold:
                            old_bad.append({"text": l["text"], "score": l["score"]})
                        continue
                    keep.append(l)

                rec["ocr"]["lines"] = keep + new_lines
                rec["ocr"]["rescue"] = {
                    "frame_bottom": fb,
                    "old_low_score_lines": old_bad,
                    "new_lines": len(new_lines),
                }
                (data / f"page{p:03d}.json").write_text(
                    json.dumps(rec, ensure_ascii=False, indent=1))
                print(f"p{p:03d}: old_bad={len(old_bad)} new={len(new_lines)} | "
                      + " / ".join(l["text"][:18] for l in new_lines[:3]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--pipeline", default=None, help="官方 server 的 pipeline yaml（默认 ocr_cpu.yaml）")
    a = ap.parse_args()
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
