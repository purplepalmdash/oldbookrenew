# Optional typesetting path: Scribus (MCP)

The default pipeline typesets per-page PDFs with **ReportLab** — fast,
batch-parallel, machine-verifiable. But users of this workflow (especially
ones who hand-built a reference edition) often work in **Scribus**, the
open-source desktop-publishing app. Since v0.5 the skill knows how to
engage Scribus instead of — or on top of — ReportLab.

## What exists in the ecosystem (checked 2026-10)

- **`scribus-mcp`** (PyPI, `uvx scribus-mcp`, ~125 MCP tools) — wraps the
  Scribus 1.6/1.7 Python Scripter API. Two backends:
  - *headless*: a fresh `scribus -g -py …` per call (batch/CI);
  - *interactive*: a `.spy` bridge inside an open Scribus window over TCP
    loopback (assist on a live document, user can take over at any time).
  Tools include document/pages/frames/text/styles, `export_pdf`
  (font embed + subset), `render_page_to_image` (inline PNG straight into
  model context — a built-in visual QA loop), `preflight_check`, PDF
  forms, and `install_custom_font` (stage an LXGW TTF into Scribus's
  `ExtraFontDirs` without touching global prefs).
  Verified end-to-end on Windows 11 + Scribus 1.7.3 (125/125) and
  Debian 13 + 1.6.3 (132/134).
- **`scribus-mcp --fetch-appimage`** / `SCRIBUS_MCP_AUTO_APPIMAGE=1`:
  pulls the official Scribus 1.7.3 AppImage (~140 MB, SourceForge) to
  `~/Applications/`. Needs FUSE (`/dev/fuse`) and, on headless servers,
  `xvfb` (`SCRIBUS_MCP_USE_XVFB=1` or `xvfb-run`).
- First-run papercut: Scribus's "New Document" wizard **blocks `-py`
  execution** until dismissed. Launch once, dismiss, then run the repo's
  `scripts/disable-startup-dialog-linux.sh --force` (sets
  `ShowStartupDialog="0"` in `~/.config/scribus/scribusXYZ.rc`).
- **`chrisgliddon/scribus-mcp`** — alternative implementation:
  persistent headless subprocess + NDJSON bridge; similar surface.
- **jablonkai/skills `scribus`** (skills.sh) — a *pi/agent skill* variant:
  no server/bridge, one short-lived `Scribus -g -ns -py` job per page;
  emits `.sla` + PDF so the user can fine-tune in the GUI afterwards.
- Plain **`sla2pdf`** (pip) — headless `.sla` → PDF if you only need the
  export direction.

## When Scribus beats ReportLab here

| Situation | Pick |
|---|---|
| 100+ pages, unattended batch, machine QA gate | **ReportLab** (parallel workers, `06_verify`) |
| User wants an **editable `.sla` source** of the finished book | **Scribus** (emit `.sla`; user opens and tweaks) |
| Human-in-the-loop layout pass (user watches/steers) | **Scribus interactive** (MCP bridge + `render_page_to_image`) |
| CMYK / separations / press ICC | **Scribus** (ReportLab is RGB→Gray only) |
| Font not subsettable by reportlab (exotic OTF) | **Scribus** (its PDF writer handles more) |

## Recommended hybrid for this skill

1. Run the normal pipeline (ReportLab) to convergence — it stays the
   source of truth for the 1:1 dataset and the QA gate.
2. **Optional .sla export**: one Scripter job (or a `scribus-mcp`
   session) walks the same `data/layout/pNNN.json` files and builds the
   book inside Scribus: A4 page, one image frame per figure at mapped
   coordinates, one text frame per line at mapped coordinates, paragraph
   style bound to the embedded CJK font. Save `<stem>.sla` next to the
   PDFs. This gives the user a hand-editable master without re-running
   any image processing.
3. **Optional interactive polish**: `scribus --console -py
   $(scribus-mcp --print-bridge-path)`, wire the MCP, then use
   `render_page_to_image` per page as the visual review loop (replaces
   the `pdftoppm` contact sheets).

## Headless smoke test (minimal, no MCP needed) — VERIFIED on this machine

Scribus 1.7.3 AppImage + `xvfb-run`, `-g -ns` (no GUI, no system install):

```bash
cp scribus-1.7.3-linux-x86_64.AppImage ~/Applications/ && chmod +x ~/Applications/*
xvfb-run -a ~/Applications/Scribus-1.7.3-x86_64.AppImage -g -ns -py /tmp/scribus_smoke.py
```

`/tmp/scribus_smoke.py` (runs inside Scribus's embedded Python;
**1.7 ScripterNG signatures** — verified 2026-10-30):

```python
import scribus
# A4 portrait, 56.7pt margins, points, 1 page  (8 positional args!)
assert scribus.newDocument(
    (595.28, 841.89), (56.7, 56.7, 56.7, 56.7), scribus.PORTRAIT, 1,
    scribus.UNIT_PT, scribus.NOFACINGPAGES, scribus.FIRSTPAGERIGHT, 1)

f = scribus.createText(56.7, 700, 481.88, 60, "t1")   # x,y,w,h in pt
scribus.setText("132  这时候，阿雅牵着白马来到了这里。", f)
scribus.setFont("LXGWWenKaiMono", f)                  # system fontconfig name
scribus.layoutText(f)

scribus.placeSVG("/path/to/pNNN.svg", 56.7, 56.7)     # potrace master in-place
# NOTE: placeSVG puts the SVG at its NATURAL size — for a real book build,
# wrap each figure in a createImage frame and scale, or post-scale the
# placed object (getBoundingBox + resize) to the mapped 1:1 rect.

scribus.saveDocAs("/path/to/book.sla")                # editable master
pdf = scribus.PDFfile()
pdf.file = "/path/to/book.pdf"                         # attr is .file, not .filename
pdf.fontEmbedding = 1
pdf.embedPDF = 1
pdf.save()
```

Verified result: A4 PDF, LXGW font embedded (`pdffonts` clean), SVG figure
rendered. If the smoke test works, the full book build is a loop over the
layout JSON files (`newPage` between pages). Budget ~2–5 s/page for the
Scripter build (Qt main-thread serialization — no parallelism inside one
Scribus process).

**API gotchas (1.7.3 experimental bundle, ScripterNG):**
- `newDocument` takes **8 positional args** (size, margins, orientation,
  firstPageNumber, unit, pagesType, firstPageOrder, numPages) — not the
  1.6-era `(name, w, h)` form. `newDoc` (7 args) is the obsolete alias.
- `createText`/`createImage` return the frame NAME (string); reference all
  later calls by that name.
- PDF export is the `PDFfile()` class: attrs are `file`, `fontEmbedding`,
  `embedPDF`, `compress`, `quality`, `thumbnails`, `save()` — there is no
  module-level `exportPDF` and no `pdf.filename`.
- Font names are fontconfig names (`getFontNames()`); check the exact
  family name before `setFont` (it raises `ValueError: Font not found`).

## Known constraints

- **`scribus-mcp` 1.3.1 breaks on `mcp` 2.x** (`ModuleNotFoundError:
  mcp.server.fastmcp` — FastMCP was renamed to MCPServer in mcp 2.0).
  Workaround: `uvx --with "mcp<2" scribus-mcp …` (verified). Upstream is
  expected to pin/allow both; check before upgrading the MCP.
- **SourceForge AppImage download is flaky**: the downloader can receive a
  ~12 MB error page (the MCP's ≥50 MB sanity check rejects it — good).
  Retry, or download manually and put it at
  `~/Applications/Scribus-1.7.3-x86_64.AppImage` (chmod +x); the MCP
  probes that path first and skips re-download.

- **Qt main thread**: Scripter must run on Scribus's Qt main thread; the
  interactive bridge dispatches via QTimer. Headless = one process per
  job (caewa) or one persistent process (chrisgliddon).
- **System Python on Linux**: Scribus uses the distro's Python for
  Scripter; the bridge needs 3.11+ (Arch/Debian 12+ fine; Ubuntu 22.04 →
  use the AppImage which bundles Python).
- **Fonts**: register the TTF with `install_custom_font` (per-spawn temp
  prefs, no global change) or drop it in a dir passed via
  `SCRIBUS_MCP_EXTRA_FONT_PATHS`.
- The AppImage needs FUSE; without it run with `--appimage-extract`.
