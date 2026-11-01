#!/usr/bin/env python3
"""全书正文页（4-113）字幕条带并行重识别，结果权威替换 data JSON 条带内行。"""
import json, re, asyncio, os
from pathlib import Path
import cv2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

OUT = Path("/path/to/project/digitize1")
WORK = Path("/path/to/project/work1/strips_all")
WORK.mkdir(parents=True, exist_ok=True)
PAGES = list(range(4, 114))
WORKERS = 4

def norm_caption(t):
    return re.sub(r"^(\d{1,3})[.、]?\s*(?=[\u4e00-\u9fff])", r"\1 ", t.strip())

async def worker(q):
    S = StdioServerParameters(
        command="/path/to/paddleocr-mcp/venv/bin/paddleocr_mcp",
        env={
            "PADDLEOCR_MCP_MODEL": "PP-OCRv5",
            "PADDLEOCR_MCP_PPOCR_SOURCE": "local",
            "PADDLEOCR_MCP_PIPELINE_CONFIG": "/path/to/paddleocr-mcp/ocr_openvino.yaml",
            "PADDLEOCR_MCP_DEVICE": "cpu",
        },
    )
    async with stdio_client(S) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            while True:
                p = await q.get()
                if p is None:
                    return
                fp = WORK / f"strip_p{p:03d}.png"
                fb = int(json.load(open(fp.with_suffix('.fb')))["fb"])
                res = await s.call_tool("ocr", {"input_data": str(fp), "output_mode": "detailed"})
                if getattr(res, "is_error", False):
                    print(f"p{p}: OCR error"); continue
                text = "".join(getattr(c, "text", "") for c in res.content)
                d = json.loads(text) if text.strip().startswith("{") and "text_lines" in text else {"text_lines": []}
                new_lines = []
                for tl in d.get("text_lines", []):
                    t = norm_caption(tl["text"])
                    b = tl["bbox"]
                    if len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
                        x1, y1, x2, y2 = b
                    else:
                        xs = [q_[0] for q_ in b]; ys = [q_[1] for q_ in b]
                        x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
                    n_cjk = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
                    if n_cjk < 2 and not re.fullmatch(r"[\d.,，。！？!?;；:：\-—\s]+", t):
                        continue
                    quad = [[x1, fb+y1], [x2, fb+y1], [x2, fb+y2], [x1, fb+y2]]
                    new_lines.append({"text": t, "score": round(float(tl.get("score", 1.0)), 3),
                                       "quad": quad, "start_point": [x1, fb+y1],
                                       "end_point": [x2, fb+y2], "stripall": True})
                jf = OUT / "data" / f"page{p:03d}.json"
                rec = json.load(open(jf))
                keep = [l for l in rec["ocr"]["lines"] if min(q_[1] for q_ in l["quad"]) <= fb - 20]
                rec["ocr"]["lines"] = keep + new_lines
                rec["ocr"]["stripall"] = {"fb": fb, "new_lines": len(new_lines)}
                jf.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
                print(f"p{p:03d}: new={len(new_lines)} last_y={max((q[1] for l in new_lines for q in l['quad']), default=0):.0f}", flush=True)

async def main():
    # 准备全部条带
    fbs = {}
    for p in PAGES:
        img = cv2.imread(str(OUT / f"work/pages/p{p:03d}.png"), cv2.IMREAD_GRAYSCALE)
        geo = json.load(open(OUT / f"work/images/page{p:03d}.json"))
        fb = int(geo["images"][0]["frame_center_page"][1] + geo["images"][0]["frame_h"]/2) + 8
        strip = img[fb:, :]
        fp = WORK / f"strip_p{p:03d}.png"
        cv2.imwrite(str(fp), strip)
        (fp.with_suffix(".fb")).write_text(json.dumps({"fb": fb}))
        fbs[p] = fb
    print(f"strips ready: {len(fbs)}")
    q = asyncio.Queue()
    for p in PAGES:
        await q.put(p)
    for _ in range(WORKERS):
        await q.put(None)
    await asyncio.gather(*[worker(q) for _ in range(WORKERS)])
    print("ALL DONE")

asyncio.run(main())
