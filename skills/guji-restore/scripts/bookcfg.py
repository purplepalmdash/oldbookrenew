"""Shared config & helpers for the guji-restore pipeline (book-agnostic).

Per-book configuration lives in <project>/book.json (parent of this src/
dir). Field reference: the skill's references/book.example.json.

Convention: run scripts from the src/ dir:
    cd <project>/src && python s2_segment.py 4
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
PAGES = WORK / "pages"
OUT = ROOT / "out"
CFG_PATH = ROOT / "book.json"

# book.json defaults (any field may be omitted)
DEFAULTS = {
    "render_dpi": 200,       # processing render resolution (grayscale)
    "margin_mm": 20,         # output page margin
    "keep_pages": [],        # inserted as-is (color cover ...), contain-fit
    "drop_pages": [],        # excluded from the output (ad pages ...)
    "no_frame_pages": [],    # skip auto frame detection (text-only / special)
    "whiten_params": {},     # book-level S3 overrides (see s3_whiten)
    "page_whiten": {},       # per-page S3 overrides, e.g. {"2": {"remove_red": true}}
}

_cache: dict | None = None


def cfg() -> dict:
    """Load book.json once, filled with defaults."""
    global _cache
    if _cache is None:
        if not CFG_PATH.exists():
            raise SystemExit(
                f"book.json not found at {CFG_PATH}\n"
                "copy the skill's references/book.example.json and edit it")
        d = json.loads(CFG_PATH.read_text())
        for k, v in DEFAULTS.items():
            d.setdefault(k, v)
        _cache = d
        if not d.get("src_page_pt"):
            # note: use the raw value - src_pdf() would re-enter cfg()
            import pymupdf
            p = Path(d["src_pdf"]).expanduser()
            if not p.is_absolute():
                p = ROOT / p
            doc = pymupdf.Document(str(p.resolve()))
            r = doc[0].rect
            d["src_page_pt"] = [round(r.width, 2), round(r.height, 2)]
            doc.close()
    return _cache


def src_pdf() -> Path:
    return ROOT / cfg()["src_pdf"]


def out_pdf() -> Path:
    return ROOT / cfg()["out_pdf"]


def font_path() -> Path:
    fp = Path(cfg()["font"]).expanduser()
    return fp if fp.is_absolute() else ROOT / fp


def page_count() -> int:
    import pymupdf
    doc = pymupdf.Document(str(src_pdf().resolve()))
    n = doc.page_count
    doc.close()
    return n


def keep_pages() -> set[int]:
    return set(cfg()["keep_pages"])


def drop_pages() -> set[int]:
    return set(cfg()["drop_pages"])


def no_frame_pages() -> set[int]:
    return set(cfg()["no_frame_pages"])


def process_pages() -> list[int]:
    """Pages going through the full S2..S5 pipeline (original order)."""
    c = cfg()
    if c.get("process_pages"):
        return sorted(c["process_pages"])
    keep, drop = keep_pages(), drop_pages()
    return [n for n in range(1, page_count() + 1)
            if n not in keep and n not in drop]


def pnn(n: int) -> str:
    return f"{n:03d}"


def render_name(n: int, color: bool = False) -> str:
    """File name of a page render, e.g. '004_200.png' / '004_200c.png'."""
    return f"{pnn(n)}_{cfg()['render_dpi']}{'c' if color else ''}.png"


def ensure_dirs() -> None:
    (WORK / "render50").mkdir(parents=True, exist_ok=True)
    PAGES.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)


def load_page_json(n: int) -> dict:
    return json.loads((PAGES / f"{pnn(n)}.json").read_text())


def save_page_json(n: int, obj: dict) -> None:
    (PAGES / f"{pnn(n)}.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=1))


def load_params() -> dict:
    """Frozen S3 params (work/params.json) - the calibrated set.
    Wins over book.json whiten_params (that is what 'frozen' means)."""
    p = WORK / "params.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_params(params: dict) -> None:
    (WORK / "params.json").write_text(
        json.dumps(params, ensure_ascii=False, indent=1))


def margin_box() -> tuple:
    """(page_w, page_h, margin_pt, box_w, box_h) in pt, from book.json."""
    w, h = cfg()["out_page_pt"]
    m = cfg()["margin_mm"] / 25.4 * 72.0
    return w, h, m, w - 2 * m, h - 2 * m
