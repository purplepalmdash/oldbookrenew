#!/usr/bin/env python3
"""09_rescue: low-confidence caption-line triage + strip re-OCR.

Lesson (absorbed from the paddleocr-digitize S4 recipe): light gray ink
on aged scans makes PaddleOCR read perfectly legible captions as garbage
("具房局路路路堂房市房司") while normal lines score >= 0.62. Score is the
only reliable signal — character-frequency tables and ink-gray stats fail,
because the garbage is built from COMMON characters.

Modes (MCP venv python, resident MCP server stopped, as with 08):
  auto               scan every panel page's OCR dump for lines with
                     score < rescue.score_threshold and rescue those pages
  40 87              rescue the given pages explicitly
  --check [pages]    book-level strip audit (S4.5): re-OCR every text
                     band, difflib-compare against the typeset dataset
                     (each dataset line also scored against concatenations
                     of up to 2 ADJACENT fresh lines, and as a neighbor-
                     concat — rescue OCR splits/merges lines; ds->got only,
                     so intentionally-dropped watermark/out-of-frame lines
                     in the fresh OCR don't false-flag), flag pages with
                     any line ratio < rescue.check_ratio. REPORT ONLY —
                     then rescue the flagged pages and rerun run_page.py.

Rescue recipe (A/B'd in the source skill: plain percentile stretch +
upsample beats Otsu — which kills light ink entirely — and CLAHE, and it
keeps the book's own line segmentation):
  1. strip = the textband crop (same region + coordinate space as the
     original OCR input, so 04_merge maps rescued bboxes identically)
  2. enhance = grayscale + percentile stretch (p10..p99) + 2x INTER_CUBIC
  3. re-OCR via the MCP module (same code path as 08)
  4. replace ALL blocks inside the strip with the new lines (the old
     0.6-0.85 "borderline" lines are replaced too); keep new lines with
     >=2 CJK chars or pure page-number digits
  5. fail-safe: zero new lines -> keep the old blocks (never delete good
     lines because the rescue OCR hiccuped)
  6. write back data/ocr/pNNN.json (same schema as the 08 dump, plus a
     top-level `rescue` record) + data/rescue/pNNN.json report.
     Rerun `run_page.py --page N` afterwards to rebuild the page.

Book-type nuance: for CLEAN digital scans (white paper, crisp ink) pass
--no-stretch — pure upsampling fragments det lines there. Yellowed /
light-ink books use the default stretch + upscale.

Config (pipeline/config.json, all optional):
  "rescue": {"score_threshold": 0.6, "upscale": 2,
             "stretch": true, "check_ratio": 0.9}
"""
import argparse
import asyncio
import difflib
import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.common import (CONFIG, MCP, MAX_OCR_SIDE, PANEL_PAGES, ROOT,  # noqa: E402
                        textband_crop_box)

SERVER_DIR = str(Path(MCP.get("server_dir", "~/paddleocr-mcp")).expanduser())
sys.path.insert(0, SERVER_DIR)
import paddleocr_mcp_server as srv  # noqa: E402

RESCUE = CONFIG.get("rescue") or {}
SCORE_TH = float(RESCUE.get("score_threshold", 0.6))
UPSCALE = int(RESCUE.get("upscale", 2))
STRETCH = bool(RESCUE.get("stretch", True))
CHECK_RATIO = float(RESCUE.get("check_ratio", 0.9))
CJK = re.compile(r"[\u4e00-\u9fff]")
PAGENO = re.compile(r"^[\d\.…\s]+$")


def parse_pages(spec: str):
    out = []
    for part in (spec if isinstance(spec, str) else " ".join(spec)).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [p for p in dict.fromkeys(out) if p in PANEL_PAGES]


def strip_path(page: int) -> Path:
    return ROOT / "data" / "rescue" / f"p{page:03d}_strip.png"


def make_strip(page: int, stretch: bool) -> Path:
    """Enhanced textband strip (same crop as the original OCR input)."""
    geo = json.loads(
        (ROOT / "data" / "layout" / f"p{page:03d}.geo.json").read_text())
    box = textband_crop_box(geo)
    x0, y0, x1, y1 = (int(v) for v in box)
    im = Image.open(ROOT / "data" / "pages" / f"p{page:03d}_deskew.png")
    crop = im.crop((x0, y0, x1, y1)).convert("L")
    if stretch:
        arr = np.asarray(crop, dtype=np.float32)
        lo, hi = np.percentile(arr, [10, 99])
        if hi - lo > 1:
            arr = np.clip((arr - lo) * 255.0 / (hi - lo), 0, 255)
            crop = Image.fromarray(arr.astype(np.uint8), "L")
    if UPSCALE > 1:
        crop = crop.resize((crop.width * UPSCALE, crop.height * UPSCALE),
                           Image.BICUBIC)
    p = strip_path(page)
    p.parent.mkdir(parents=True, exist_ok=True)
    crop.save(p)
    return p


def band_scale(w: int, h: int) -> float:
    """Downscale the MCP server applies to the (already upscaled) strip."""
    return min(1.0, MAX_OCR_SIDE / max(w, h))


async def ocr_strip(path: Path):
    res = await srv._tool_ocr(str(path), MCP.get("ocr_language", "ch"))
    return res.model_dump()


def keep_line(text: str) -> bool:
    t = text.strip()
    return len(CJK.findall(t)) >= 2 or bool(PAGENO.match(t))


def low_score_pages():
    bad = []
    for p in PANEL_PAGES:
        f = ROOT / "data" / "ocr" / f"p{p:03d}.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text())
        n = sum(1 for b in d.get("blocks", [])
                if float(b.get("score", 1.0)) < SCORE_TH)
        if n:
            bad.append((p, n))
    return bad


async def rescue_page(page: int, force: bool = False) -> dict:
    old = json.loads((ROOT / "data" / "ocr" / f"p{page:03d}.json").read_text())
    strip = make_strip(page, STRETCH)
    t0 = time.time()
    try:
        fresh = await ocr_strip(strip)
    except Exception as e:  # fail-safe: keep the old blocks
        rep = {"page": page, "status": f"ocr_failed: {e}",
               "kept_old": len(old.get("blocks", []))}
        (ROOT / "data" / "rescue" / f"p{page:03d}.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=1))
        print(f"RESCUE p{page:03d} FAIL {e} (old lines kept)", flush=True)
        return rep

    w, h = strip.size
    d = band_scale(w, h)
    new_blocks = []
    for b in fresh.get("blocks", []):
        text = (b.get("text") or "").strip()
        if not keep_line(text):
            continue
        bb = [int(v / (d * UPSCALE)) for v in b["bbox"][:4]]
        new_blocks.append({"text": text,
                           "score": round(float(b.get("score", 1.0)), 4),
                           "bbox": bb})
    if not new_blocks and old.get("blocks"):
        rep = {"page": page, "status": "empty_new_kept_old",
               "kept_old": len(old["blocks"]),
               "elapsed_s": round(time.time() - t0, 1)}
    else:
        out = {k: v for k, v in old.items() if k != "rescue"}
        out["blocks"] = new_blocks
        out["rescue"] = {"ts": time.strftime("%F %T"),
                         "mode": "stretch+2x" if STRETCH else "raw+2x",
                         "replaced": len(old.get("blocks", [])),
                         "new": len(new_blocks),
                         "elapsed_s": round(time.time() - t0, 1)}
        old_path = ROOT / "data" / "ocr" / f"p{page:03d}.json"
        old_path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
        rep = {"page": page, "status": "rescued",
               "old": [b["text"] for b in old.get("blocks", [])],
               "new": [(b["text"], b["score"]) for b in new_blocks],
               **out["rescue"]}
    (ROOT / "data" / "rescue" / f"p{page:03d}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1))
    print(f"RESCUE p{page:03d} {rep['status']} "
          f"{len(new_blocks)}/{len(old.get('blocks', []))} lines "
          f"{time.time()-t0:.1f}s", flush=True)
    return rep


def line_audit_ratio(ds_lines: list, got_lines: list) -> float:
    """Worst per-line difflib ratio of the typeset dataset lines against the
    fresh OCR lines, tolerant to block SPLIT / MERGE (ds -> got only).

    Rescue OCR re-segments the strip: a dataset line may come back as two
    blocks (SPLIT) and two dataset lines may merge into one block (MERGE);
    plain per-line best-match scores those ~0.5-0.8 and false-flags the
    page. So each dataset line is scored against the fresh lines AND
    concatenations of up to 2 adjacent fresh lines (split tolerance), and
    the dataset line itself is also scored as a concat with its neighbor
    (merge tolerance).

    ds -> got only, NOT got -> ds: the fresh strip OCR legitimately sees
    lines the dataset intentionally DROPS (a scan-time watermark slogan, an
    out-of-frame low-confidence block filtered by 04_merge); scoring the
    extra got lines would false-flag exactly the watermark pages this audit
    exists to serve. (A genuinely missing content line in the dataset is
    caught downstream by 06_verify, not here.) Greedy neighborhood
    allowance, not full sequence alignment (a 3-way split can still flag —
    harmless: rescue is idempotent). Returns the min over dataset lines;
    1.0 when the dataset has no lines.
    """
    def pairs(lines):
        return [lines[i] + lines[i + 1] for i in range(len(lines) - 1)]

    if not ds_lines:
        return 1.0
    got_c = got_lines + pairs(got_lines)  # split tolerance
    worst = 1.0
    for i, t in enumerate(ds_lines):
        nxt = ds_lines[i + 1] if i + 1 < len(ds_lines) else ""
        prv = ds_lines[i - 1] if i > 0 else ""
        ext = [t, t + nxt, prv + t]  # merge tolerance
        best = max((difflib.SequenceMatcher(None, x, y).ratio()
                    for x in ext for y in got_c), default=0.0)
        worst = min(worst, best)
    return worst


async def check_pages(pages) -> list:
    """S4.5 book-level strip audit: re-OCR strips, difflib vs dataset."""
    flagged = []
    for p in pages:
        ds_f = ROOT / "data" / "layout" / f"p{p:03d}.json"
        if not ds_f.exists():
            continue
        ds = json.loads(ds_f.read_text())
        ds_lines = [l["simplified"] for l in ds.get("text", {}).get("lines", [])]
        strip = make_strip(p, STRETCH)
        try:
            fresh = await ocr_strip(strip)
        except Exception as e:
            print(f"CHECK p{p:03d} OCR FAIL {e}", flush=True)
            continue
        got = [(b.get("text") or "").strip() for b in fresh.get("blocks", [])]
        worst = line_audit_ratio(ds_lines, got)
        status = "ok" if worst >= CHECK_RATIO else "FLAG"
        if worst < CHECK_RATIO:
            flagged.append(p)
        print(f"CHECK p{p:03d} {status} worst={worst:.2f}", flush=True)
    return flagged


async def amain(args):
    if args.check:
        pages = parse_pages(args.pages) if args.pages else PANEL_PAGES
        flagged = await check_pages(pages)
        print(f"== check done: {len(flagged)}/{len(pages)} flagged "
              f"-> rescue them: 09_rescue.py {' '.join(map(str, flagged))}",
              flush=True)
        return
    if not args.pages or args.pages == ["auto"]:
        bad = low_score_pages()
        print(f"== low-score (<{SCORE_TH}) pages: "
              f"{[(p, n) for p, n in bad] or 'none'}", flush=True)
        pages = [p for p, _ in bad]
    else:
        pages = parse_pages(args.pages)
    if not pages:
        print("== nothing to rescue", flush=True)
        return
    for p in pages:
        await rescue_page(p)
    print(f"== rescue done: {len(pages)} pages. "
          f"Rerun run_page.py for: {' '.join(map(str, pages))}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="*", default=["auto"],
                    help="page list/ranges, 'auto' (default), or --check")
    ap.add_argument("--check", action="store_true",
                    help="book-level strip audit (report only)")
    ap.add_argument("--no-stretch", action="store_true",
                    help="clean digital scans: skip percentile stretch")
    args = ap.parse_args()
    if args.no_stretch:
        STRETCH = False
    asyncio.run(amain(args))
