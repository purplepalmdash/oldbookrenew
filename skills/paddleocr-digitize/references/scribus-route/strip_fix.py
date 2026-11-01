#!/usr/bin/env python3
"""对本书（清晰数字扫描，无浅墨）的字幕条带直接原分辨率 OCR 修复。

与技能 rescue_lines.py 的区别：不做拉伸/2x 放大（浅墨配方），
清晰数字扫描上纯放大反而让 det 碎片化；原条带 det 直接出完整行。

合并规则：条带内（y1 > fb-20）的旧行全部替换为条带重识别行。
"""
import json, re, asyncio, os
from pathlib import Path
import cv2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

VENV_BIN = "/path/to/paddleocr-mcp/venv/bin"
OUT = Path("/path/to/project/digitize1")
PAGES = [4,11,12,14,28,32,35,54,61,68,70,77,85,88,89,104,112]
FIXES = [
    (re.compile(r"孙宾"), "孙膑"),
    (re.compile(r"孙瘸"), "孙膑"),
    (re.compile(r"庞消"), "庞涓"),
    (re.compile(r"人氏美街出版社"), "人民美术出版社"),
    (re.compile(r"人民美街出版社"), "人民美术出版社"),
    (re.compile(r"孙膑子"), "孙膑"),  # 安全网：若出现
]

def fix_text(t):
    for pat, rep in FIXES:
        t = pat.sub(rep, t)
    return t

def norm_caption(t):
    # 行首编号与正文之间补一个空格；去掉误识别的点号
    t = re.sub(r"^(\d{1,3})[.、]?\s*(?=[\u4e00-\u9fff])", r"\1 ", t)
    return t

async def main():
    S = StdioServerParameters(
        command=f"{VENV_BIN}/paddleocr_mcp",
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
            for p in PAGES:
                strip_f = f"/path/to/project/work1/strips/strip_p{p:03d}.png"
                if not os.path.exists(strip_f):
                    print(f"p{p}: strip missing, skip"); continue
                fb = json.load(open("/path/to/project/work1/strips/fb.json"))[str(p)]
                res = await s.call_tool("ocr", {"input_data": strip_f, "output_mode": "detailed"})
                if getattr(res, "is_error", False):
                    print(f"p{p}: OCR error, left unchanged"); continue
                text = "".join(getattr(c, "text", "") for c in res.content)
                d = json.loads(text) if text.strip().startswith("{") and "text_lines" in text else {"text_lines": []}
                new_lines = []
                for tl in d.get("text_lines", []):
                    t = fix_text(norm_caption(tl["text"].strip()))
                    b = tl["bbox"]
                    if len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
                        x1, y1, x2, y2 = b
                    else:
                        xs = [q[0] for q in b]; ys = [q[1] for q in b]
                        x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
                    n_cjk = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
                    if n_cjk < 2 and not re.fullmatch(r"[\d.,，。！？!?;；:：\-—\s]+", t):
                        continue
                    quad = [[x1, fb+y1],[x2, fb+y1],[x2, fb+y2],[x1, fb+y2]]
                    new_lines.append({"text": t, "score": round(float(tl.get("score", 1.0)), 3),
                                       "quad": quad, "start_point": [x1, fb+y1],
                                       "end_point": [x2, fb+y2], "rawstrip": True})
                jf = OUT / "data" / f"page{p:03d}.json"
                rec = json.load(open(jf))
                keep = [l for l in rec["ocr"]["lines"] if min(q[1] for q in l["quad"]) <= fb - 20]
                rec["ocr"]["lines"] = keep + new_lines
                rec["ocr"]["stripfix"] = {"fb": fb, "new_lines": len(new_lines)}
                jf.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
                print(f"p{p:03d}: new={len(new_lines)} | " + " / ".join(l["text"][:20] for l in new_lines[:2]))

    # 全局人名订正（所有页）
    n_fix = 0
    for f in sorted((OUT / "data").glob("page*.json")):
        if not re.match(r"page\d{3}\.json$", f.name): continue
        rec = json.load(open(f))
        changed = False
        for l in rec["ocr"]["lines"]:
            nt = fix_text(l["text"])
            if nt != l["text"]:
                l["text"] = nt; changed = True; n_fix += 1
        rec["ocr"]["text"] = "\n".join(l["text"] for l in rec["ocr"]["lines"])
        if changed or rec["ocr"]["text"]:
            f.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    print(f"global fixes applied to {n_fix} lines")

    # p2: 删除印章乱码行（低 score 且位于右下印章区）
    rec = json.load(open(OUT / "data" / "page002.json"))
    before = len(rec["ocr"]["lines"])
    rec["ocr"]["lines"] = [l for l in rec["ocr"]["lines"]
                           if not (l["score"] < 0.6 and min(q[0] for q in l["quad"]) > 1000)]
    print(f"p2: dropped {before - len(rec['ocr']['lines'])} seal lines")
    (OUT / "data" / "page002.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))

asyncio.run(main())
