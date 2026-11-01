# Workflow (step by step)

All commands run from the book root (`$BOOK`, contains `pipeline/`).
`PY=$BOOK/.venv/bin/python` (or any venv with the deps).

Timings are from the reference run on a 134-panel book, T4 GPU, 16-core
CPU — see `performance.md` for the measured table.

## 0. Survey the book (agent, ~15-30 min with images)

```bash
pdftoppm -png -r 72 "$SRC" _t          # contact sheet material
# build a labeled contact sheet of samples: p1..p5, p25, p50, ..., last 5
```

Classify every page:
- which pages are panels (the repeating figure+text layout),
- the panel-number rule: read the number off 3+ pages (start, middle,
  end) and fit `panel_no = page - K` → `panel_no_offset = K`,
- number format: bare `9` or parenthesised `（ 9 ）` → `panel_no_format`,
- front/back matter pages (cover, title, abstract, colophon, watermark),
- page count, any rotated/damaged pages, page sizes (some scans mix page
  sizes! e.g. a small cover page — check `pdfinfo` and render sizes).

Record decisions in the book's `MEMORY.md`/`PLAN.md`.

### Watermark judgment (sub-skill — full details in `references/watermark.md`)

After reading through the WHOLE book, decide which marks are later-added
scanner watermarks vs original printed design:

1. **Text repetition**: a short OCR line (≲15 chars) on 3+ pages at the
   same position, with variable readings ⇒ slogan watermark.
2. **Template match**: cut a suspicious patch from one 250 dpi render and
   match it against ALL pages — pixel-identical (recall ≈ 1.0) on most
   pages and ≈ 0 on the sister volume ⇒ scan-time overlay.
3. **Visual contact sheet** of 8–12 pages: recurring marks at fixed
   corners / bottom strips ⇒ candidates.

Classify (URL/site name/personal name/slogan ⇒ later watermark; publisher
seal/running head/binding line/cover art ⇒ original design; the sister
volume is the tie-breaker). **Default: remove watermarks**, keep original
design, and confirm with the user when ambiguous — the client's call wins.
Removal is config-driven: `watermark_boxes` (panel pages, painted out in
03_enhance + line dropped in 04_merge), `whiteout_boxes` (special pages),
`erase_ink` (a watermark on a colored banner/plate image).

## 1. Config

Edit `pipeline/config.json` from `config.example.json`. The only
survey-driven fields are the page ranges, offsets, and `special_pages`
text content.

## 2. Render (all pages, 250 dpi)

```bash
$PY pipeline/01_render.py            # all pages
$PY pipeline/01_render.py --page 10  # single page
```

Output: `data/pages/pNNN.png` (250 dpi) — the source of truth for all
later geometry. ~0.3 s/page.

## 3. Geometry (panel pages)

```bash
for p in $(seq 3 68); do $PY pipeline/02_geometry.py --page $p; done
# or in parallel: xargs -P6 -I{} bash -c '$PY pipeline/02_geometry.py --page {}' < <(seq 3 68)
```

Output: `data/layout/pNNN.geo.json` — frame polygon/bbox, skew angle,
text bands, `pNNN_deskew.png`.

Failures (`frame not detected`): inspect `data/layout/pNNN.geo.json`
plus the page; take the VL image box (`data/vl/pNNN.json` → layout
`image` entry) — but VL runs after geometry… so for these pages run
`08_batch_infer.py vl --pages N` first (or read the layout off a
high-res render), then:

```bash
$PY pipeline/02_geometry.py --page N --vl-frame x0,y0,x1,y1
```

`frame_source` in the geo file records which path was used.

## 4. MCP inference (VL layout + OCR lines, GPU)

```bash
# stop the resident MCP server (single GPU memory):
pkill -f paddleocr_mcp_server || true
$PY_MCP pipeline/08_batch_infer.py all     # PY_MCP = the MCP server venv python
# restart it afterwards (or let the agent session respawn it)
```

The script imports the server module in-process (same model loading as
the MCP tools, no JSON-RPC overhead). Skips pages whose outputs already
exist. ~5.5 s/page VL + ~0.3 s/page OCR on a T4.

## 5. Per-page build (panels)

```bash
./batch_runpages.sh               # all panel pages, 6 workers
./batch_runpages.sh 10 45 94      # specific pages (iteration)
```

Each page: `03_enhance` (crop figure; grayscale scans get de-yellow —
COLOR figures are kept as-is in their original color, no pixel
treatment, `enhance_variant: color_as_is`; per-edge frame restamp)
→ `04_merge` (VL+OCR → per-line dataset, number normalization, panel
anchor, cross-checks) → `05_typeset` (A4 page: figure at mapped position,
lines at mapped positions, per-line font size/tracking to match the
original line width) → `06_verify` (QA report) → preview PNG.

Iterate until `== N/N pages pass ==`. Common fix sites, in order of
frequency: `04_merge.py` (clustering/number/override), `05_typeset.py`
(line width), `03_enhance.py` (frame restamp), `06_verify.py` (figure
bbox). Keep fixes **general** (they are skill code, not book code).

### 5a. Figure treatment: `bw_clear` vs `vector` (and color as-is)

`03_enhance` has two variants, selected by `config.enhance.variant`
— plus an automatic third path:

- **`bw_clear` (default)** — de-yellow, sharpen, per-edge frame restamp,
  output a grayscale PNG. Good for most books; safe, fast.
- **`color_as_is` (automatic, not selectable)** — when the figure crop
  has meaningful color (p99 of per-pixel max(R,G,B)−min(R,G,B) > 30,
  `lib.common.is_color_image`), the default variant routes it here:
  the ORIGINAL COLOR crop is placed 1:1 — no grayscale, no whitening,
  no level stretch, no sharpen, no binarize, no frame restamp. The
  `print_fitness` grayscale check in `06_verify` is skipped for
  `enhance_variant == "color_as_is"`. An explicit `variant: vector` on a
  color figure is a deliberate B/W choice and keeps the vector path.

- **`bw_clear` (default)** — de-yellow, sharpen, per-edge frame restamp,
  output a grayscale PNG. Good for most books; safe, fast.
- **`vector`** — the “freshly printed” high-contrast look: the figure is
  converted to true black-and-white with crisp line art, the original
  halftone dot/line texture kept as a pattern (not smoothed away), and
  only the truly dense tone regions rendered solid black. Chosen when the
  user wants the crisp “just printed” look (e.g. they made a manual
  reference with GIMP + Scribus and you want to match or beat it).

**v2 detail-preserving local binarization** (in `03_enhance.vectorize`;
replaced the v1 two-scale pass after a user review of P19 showed v1 lost
water-wave/cliff-crack/branch detail and was “too black”):

1. **Page-level illumination fit** (`page_illumination`): a quadratic 2-D
   polynomial fitted to paper pixels (bright + locally smooth) removes
   the yellowed/vignetted background. (A *local* blur-division instead
   over-flattens large tone regions. Fit the page, not a neighbourhood.)
2. **Local tone-relative binarization** — every pixel is judged against
   its own local tone `tone = blur(flat, 31)`, never against a global
   constant:
   - `solid = tone < T_solid` (150): dense tone → flat black (a new book
     prints a dense tone as solid ink).
   - `bright = flat > tone + bright_dev (45) & tone < bright_tone (185)`:
     light detail carved INTO dark tone (white wavy lines on dark
     mountains, crack highlights on dark rock) — the detail class v1
     lost entirely.
   - `ink = flat < T_dark (190)  |  (flat < tone − dev (25)) & (tone ≥ T_solid)`:
     dark lines/dots on light paper, plus anything locally darker than its
     own tone (halftone dots/lines of a mid-tone region — kept as a dot
     pattern, NOT solidified; this is what stopped “too black”).
   - `bw = (ink | solid) & ~bright`.
3. **Line repair**: 1-px morphological closing reconnects 1-px line
   fragments (water waves render as dashes without it); a 3×3 cross is
   conservative enough not to blob halftone dots. Then drop ink
   components < `speckle_px` (10 px) — the scan-noise removal. NOTE: no
   median pre-filter — median(3) erases 1-px lines.
4. **Vectorize**: `bw` → potrace (`-t 2` — low noise tolerance so small
   dots survive; 5 drops them) → SVG master `data/images/pNNN.svg`.
5. **Rasterize**: `rsvg-convert -d 144 -p 144` (both flags! `-d` alone
   distorts the aspect ratio) → RGBA PNG → alpha-composite on white →
   1-bit `pNNN_bw.png` (rsvg's transparent background turns black if you
   just `.convert("L")`).

Machine-checked by `06_verify` checks `vector_binary` (figure is true
1-bit) and `ink_coverage` (output ink ≈ original ink-equivalent over the
figure region, ratio in [0.55, 1.5] — this is the machine enforcement of
“not too black, not too faded”: a retypeset must carry roughly the same
ink as the scan it came from, and a freshly-printed book is slightly
LIGHTER than its yellowed scan, not darker).

## 6. Special pages

```bash
$PY pipeline/07_special.py all
```

Text for `retype`/`colophon`: get it from the MCP `ocr_image` dump of the
full-page render (save to `data/ocr/spec_pNNN.json`), then transcribe
into the config `layout`/`header`/`left` fields — verify each character
against a 150-200 dpi crop. Seals/emblems: crop regions or largest-ink
component (see handler docs in `07_special.py`).

## 7. Merge + optimize

```bash
# merge (respect drop_pages; editorial order is a decision, document it)
pdfunite out/pages/p001.pdf out/pages/p002.pdf ... out/<stem>_retypeset.pdf

gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.5 -dPDFSETTINGS=/prepress \
   -sColorConversionStrategy=Gray \
   -dDownsampleColorImages=true -dColorImageResolution=200 \
   -dGrayImageResolution=200 -dMonoImageResolution=200 \
   -dAutoRotatePages=/None -dNOPAUSE -dBATCH \
   -sOutputFile=out/<stem>_retypeset_opt.pdf out/<stem>_retypeset.pdf
```

## 8. Final verification

```bash
python test_pages.py all        # expect == N/N pages pass ==
pdfinfo out/<stem>_retypeset_opt.pdf
pdffonts out/<stem>_retypeset_opt.pdf      # font embedded
pdftotext out/<stem>_retypeset_opt.pdf - | wc -m   # sanity: > 90% of OCR chars
# spot check: render 5-8 pages of the final PDF at 100dpi and compare
# with the original contact sheet; read 2-3 full pages of pdftotext.
```

## Timing discipline

When the user asks for timing data, wrap every stage:

```bash
t0=$(date +%s)
<stage command>
echo "stage <name> $(( $(date +%s) - t0 ))s"
```

and append to a `<book>/_timing.log`; distill into
`references/performance.md` with the machine spec (GPU model/cores).
