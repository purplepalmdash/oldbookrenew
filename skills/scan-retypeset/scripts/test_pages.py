#!/usr/bin/env python3
"""Per-page reconstruction test case (worktips item 3).

Asserts the page fully reproduces the original:
  - coordinate-proportional alignment (figure + every text line)
  - text content / start-end chars / panel-number anchor
  - print fitness (grayscale, white paper)
Run: .venv/bin/python tests/test_pages.py 10
     .venv/bin/python tests/test_pages.py all
"""
import json
import re
import sys
from pathlib import Path

_here = Path(__file__).resolve()
# book root = directory containing pipeline/config.json (this file may sit at
# the book root or in a subdirectory like tests/)
ROOT = _here.parent if (_here.parent / "pipeline" / "config.json").exists() \
    else _here.parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from lib.common import ROOT as CROOT, load_json  # noqa: E402


def check_page(page: int) -> list[str]:
    fails = []
    rep = load_json(CROOT / "data" / "verify" / f"p{page:03d}.json")
    ds = load_json(CROOT / "data" / "layout" / f"p{page:03d}.json")
    if not rep.get("pass"):
        fails.append(f"verify pass=False flags={ds.get('qa', {}).get('flags')}")
    for name, chk in rep.get("checks", {}).items():
        if not chk.get("pass"):
            fails.append(f"check[{name}] failed")
    # panel anchor
    pno = ds["text"]["panel_no_check"]
    if not pno["pass"]:
        fails.append(f"panel_no {pno}")
    # figure layout present (worktips 2.2: 1:1 dataset)
    fig = ds["layout"]["figure"]
    if not (fig.get("bbox") and fig.get("polygon")):
        fails.append("figure layout bbox/polygon missing")
    # every line has start/end char + bbox (worktips 2.3)
    for l in ds["text"]["lines"]:
        if not (l.get("start_char") and l.get("end_char") and l.get("line_bbox_in_render")):
            fails.append(f"line {l['idx']} missing start/end/bbox")
    return fails


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "all":
        verify_dir = CROOT / "data" / "verify"
        pages = sorted(int(m.group(1)) for f in verify_dir.glob("p*.json")
                       if (m := re.match(r"p(\d{3,})\.json", f.name)))
    else:
        pages = [int(a) for a in sys.argv[1:]]
    allfail = 0
    for p in pages:
        fails = check_page(p)
        status = "PASS" if not fails else "FAIL"
        print(f"[{status}] page {p:03d} {(' '.join(fails)) if fails else ''}")
        allfail += 1 if fails else 0
    print(f"== {len(pages)-allfail}/{len(pages)} pages pass ==")
    sys.exit(1 if allfail else 0)


if __name__ == "__main__":
    main()
