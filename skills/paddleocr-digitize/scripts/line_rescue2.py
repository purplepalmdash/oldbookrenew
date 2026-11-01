#!/usr/bin/env python3
"""逐行乱码字幕救援（半色调/淡点底扫描书，rescue_lines 条带配方的补充路线）。

适用：条带 stretch+2x 配方失效的页——
  a) 行距过窄（~7px），灰度投影粘连成 2 个"大行"（条带版行切分与原书不符）；
  b) 宽行（>~600px@2x）det 碎成多段，文本丢失；
  c) 条带重识别整行丢失。
配方：整页 3x 渲染的 det 行框（原书行切分，框比墨迹宽但 y 准）
      → 逐框 crop（±10px pad，2x）→ 送 MCP rec → 按原顺序回填。
判据：密集网点页 OCR 反复失败（score<0.6 或乱码）→ 停止自动救援，
      转人工逐行对照源图校对（rescued: "manual-proofread"），见 SKILL.md S4。

用法:
    python line_rescue2.py --out <out> --src <src> --pages 42 191 [--mcp <serve.py>]
    # 渲染图取 <out>/work/pages/pNNN.png（3x，render.py 产出）；
    # 行框取 data/pageNNN.json 的 ocr.lines（需已跑过 mcp_batch）。
    # --pages 缺省 = 自动扫 score<0.6 的页（未 rescue 过的）。
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

import cv2
from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CJK = lambda c: '\u4e00' <= c <= '\u9fff'


def ocr_call(mcp, path):
    with stdio_client(StdioServerParameters(
            command=sys.executable, args=[mcp],
            env={"PYTHONPATH": str(Path(mcp).resolve().parent)})) as (r, w):
        with ClientSession(r, w) as s:
            s.initialize()
            return s.call_tool("ocr", {"image_path": str(path)})


def get_lines(res):
    for c in res.content:
        if getattr(c, "text", None):
            for r in (json.loads(c.text).get("results") or []):
                for l in r.get("text_lines", []):
                    yield l
            return
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--src", required=True)  # 3x 渲染源 PDF
    ap.add_argument("--pages", nargs="*", default=[])
    ap.add_argument("--mcp", default=os.environ.get(
        "PADDLEOCR_MCP_SERVE",
        f"{os.environ.get('PADDLEOCR_MCP_TOOLS', str(Path(__file__).resolve().parent))}/serve.py"))
    a = ap.parse_args()
    OUT = Path(a.out).resolve()
    PAGES = Path(a.src).resolve().parent  # render.py 输出的 pages/ 所在目录

    if a.pages:
        pages = [int(p) for p in a.pages]
    else:
        pages = []
        for f in sorted(OUT.glob("data/page*.json")):
            if "_special" in f.name:
                continue
            rec = json.load(open(f))
            if any(l.get("score", 1) < 0.6 for l in rec["ocr"]["lines"]):
                if not any("rescued" in l for l in rec["ocr"]["lines"]):
                    pages.append(rec["page"])
    print(f"pages: {pages}")

    for p in pages:
        rec = json.load(open(OUT / f"data/page{p:03d}.json"))
        lines = rec["ocr"]["lines"]
        # 低分行（或全部行——det 框即原书行切分，逐框重识别更忠实）
        low = [l for l in lines if l.get("score", 1) < 0.6 or l.get("rescued")]
        targets = low if low else lines
        # 最长片段过滤：宽行被 det 拆成多段时只认最长段（其余是碎片/邻行）
        boxes = sorted(targets, key=lambda l: (l["quad"][0][1], l["quad"][0][0]))
        im = cv2.imread(str(PAGES / f"p{p}.png"), cv2.IMREAD_GRAYSCALE)
        new_lines = []
        for l in boxes:
            q = l["quad"]
            x1 = min(v[0] for v in q) - 10
            y1 = min(v[1] for v in q) - 10
            x2 = max(v[0] for v in q) + 10
            y2 = max(v[1] for v in q) + 10
            crop = im[max(0, y1):y2, max(0, x1):x2]
            if crop is None or crop.size < 5000:
                continue
            crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            tmp = OUT / f"work/lr2_{p}_{y1}_{x1}.png"
            cv2.imwrite(str(tmp), crop)
            res = ocr_call(a.mcp, tmp)
            ls = list(get_lines(res))
            cand = [l for l in ls if sum(map(CJK, l["text"])) >= 2]
            if not cand:
                print(f"  p{p} 行未识别 y={y1}: 保留原样")
                new_lines.append(l)
                continue
            best = max(cand, key=lambda l: len(l["text"]))
            if best["score"] < 0.6 and not CJK(best["text"][0]):
                print(f"  p{p} 行可疑 y={y1}: {best['text'][:20]} (score={best['score']}) — 人工复核")
                new_lines.append(l)
                continue
            # 保留原 quad 坐标，只换文本；行切分跟随原书 det 框
            nl = dict(l)
            nl["text"] = best["text"]
            nl["score"] = best["score"]
            nl["rescued"] = "line-crop-2x"
            new_lines.append(nl)
        # 非目标行原样保留
        keep = [l for l in lines if l not in targets]
        merged = keep + new_lines
        merged.sort(key=lambda l: (l["quad"][0][1], l["quad"][0][0]))
        rec["ocr"]["lines"] = merged
        rec["ocr"]["text"] = "\n".join(l["text"] for l in merged)
        json.dump(rec, open(OUT / f"data/page{p:03d}.json", "w"),
                  ensure_ascii=False, indent=1)
        print(f"p{p}: " + " | ".join(l["text"][:24] for l in new_lines))
    print("done — 重跑 build_pdf + test_page")


if __name__ == "__main__":
    main()
