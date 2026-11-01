#!/usr/bin/env python3
"""08_batch_infer: batch GPU inference via the PaddleOCR MCP server module.

Loads the MCP server module directly (same code path as the MCP tools) and
runs VL layout parsing + line OCR for every panel page. This avoids 2N MCP
tool round-trips and keeps one GPU model set alive.

Run with the MCP venv python (the one with paddle + paddlepaddle(-gpu)):
  <mcp-venv>/bin/python 08_batch_infer.py [vl|ocr|all]

Config keys (pipeline/config.json):
  mcp.server_dir  : directory containing paddleocr_mcp_server.py
  mcp.ocr_language: OCR language, default "ch"

Results -> data/vl/pNNN.json, data/ocr/pNNN.json.
Skip logic: pages whose output already exists are not re-run.
NOTE: stop the resident MCP server process before running (single GPU:
two resident model sets may OOM); restart it afterwards.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.common import MCP, PANEL_PAGES, ROOT  # noqa: E402

SERVER_DIR = str(Path(MCP.get("server_dir", "~/paddleocr-mcp")).expanduser())
sys.path.insert(0, SERVER_DIR)
import paddleocr_mcp_server as srv  # noqa: E402

VL_DIR = ROOT / "data" / "vl"
OCR_DIR = ROOT / "data" / "ocr"


def existing_pages():
    return [p for p in PANEL_PAGES
            if (ROOT / "data" / "pages" / f"p{p:03d}.png").exists()]


def ensure_textband(page: int):
    """Crop the text band (union of geometry text bands, padded) from the
    deskewed render. This is the image the line OCR runs on.

    The geometry union spans the full projection width, so page margins can
    end up in the crop (e.g. the red owner's seal sitting in the right
    margin of every panel page). When the band overhangs the frame box by a
    clear margin, clamp the crop to the frame so seals are excluded.
    """
    out = ROOT / "data" / "layout" / f"p{page:03d}_textband.png"
    if out.exists():
        return out
    from PIL import Image
    from lib.common import textband_crop_box
    geo_path = ROOT / "data" / "layout" / f"p{page:03d}.geo.json"
    if not geo_path.exists():
        raise FileNotFoundError(geo_path)
    geo = json.loads(geo_path.read_text())
    if not geo.get("text_band_union"):
        return None
    deskew = Image.open(ROOT / "data" / "pages" / f"p{page:03d}_deskew.png")
    x0, y0, x1, y1 = textband_crop_box(geo)
    deskew.crop((x0, y0, x1, y1)).save(out)
    return out


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    pages = existing_pages()
    print(f"pages={len(pages)} mode={mode} device={getattr(srv, 'DEVICE', '?')}",
          flush=True)
    if mode in ("vl", "all"):
        t0 = time.time()
        n = 0
        for i, p in enumerate(pages):
            out = VL_DIR / f"p{p:03d}.json"
            if out.exists():
                continue
            t = time.time()
            try:
                res = await srv._tool_parse(
                    str(ROOT / "data" / "pages" / f"p{p:03d}.png"))
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(res.model_dump(),
                                          ensure_ascii=False, indent=1))
                n += 1
                print(f"VL  p{p:03d} ok {time.time()-t:6.1f}s  ({i+1}/{len(pages)})",
                      flush=True)
            except Exception as e:
                print(f"VL  p{p:03d} FAIL {type(e).__name__}: {e}", flush=True)
        print(f"VL phase done: {n} pages in {time.time()-t0:.0f}s", flush=True)
    if mode in ("ocr", "all"):
        t0 = time.time()
        done = 0
        for p in pages:
            out = OCR_DIR / f"p{p:03d}.json"
            if out.exists():
                continue
            try:
                crop = ensure_textband(p)
            except FileNotFoundError:
                print(f"OCR p{p:03d} SKIP no geo yet", flush=True)
                continue
            if crop is None:
                print(f"OCR p{p:03d} SKIP no text_band_union in geo", flush=True)
                continue
            t = time.time()
            try:
                res = await srv._tool_ocr(str(crop), MCP.get("ocr_language", "ch"))
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(res.model_dump(),
                                          ensure_ascii=False, indent=1))
                done += 1
                print(f"OCR p{p:03d} ok {time.time()-t:5.1f}s  ({done})",
                      flush=True)
            except Exception as e:
                print(f"OCR p{p:03d} FAIL {type(e).__name__}: {e}", flush=True)
        print(f"OCR phase done: {done} pages in {time.time()-t0:.0f}s",
              flush=True)


if __name__ == "__main__":
    asyncio.run(main())
