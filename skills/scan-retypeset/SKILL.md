---
name: scan-retypeset
description: >
  Retypeset scanned Chinese books (lianhuanhua / 连环画 / ancient or
  mid-century printed books) into clean, print-ready A4 PDFs: per-page
  figure deskew + de-yellow + sharpen (grayscale, or a "freshly printed"
  vectorized high-contrast black-and-white treatment that turns halftone
  tone into solid black; COLOR figures and pages are kept as-is in their
  original color, no pixel treatment), selectable re-typeset text with
  embedded font, 1:1 coordinate mapping to the original page,
  machine-checked QA per page, then a merged book +
  Ghostscript-optimized output. Use when the user asks to 重排/重编/数字化 a
  scanned book PDF (e.g. 连环画, 古籍, 旧书扫描版) into a modern printable
  edition, or mentions PaddleOCR MCP + PDF reconstruction.
compatibility: >
  Python 3.10+ venv (numpy, opencv-python-headless, pillow, reportlab,
  pymupdf) + poppler-utils + Ghostscript + PaddleOCR MCP server
  (paddlepaddle(-gpu); CPU works, slower — see references/hardware.md)
  + one CJK TTF font. Optional: potrace/rsvg-convert for the `vector`
  figure variant.
metadata:
  version: "0.7.0"
  category: document
  tags: [ocr, paddleocr, pdf, lianhuanhua, retypeset, print, mcp, python, opencv, reportlab, potrace]
  triggers:
    - 古籍重排
    - 连环画重编
    - 扫描书重排
    - 扫描版 PDF 重建
    - scanned book retypeset
    - 连环画数字化
  origin: >
    Distilled from the 龙子太郎 (139p), 哪吒 (70p), 巴黎圣母院 (213p) and
    刘秀 上/下册 (156+164p) full-book retypeset runs, 2026-10/11.
---

# scan-retypeset

Rebuild a scanned book (e.g. a 1980s lianhuanhua) into a modern,
print-ready, A4, text-selectable PDF that stays **1:1 with the original
page layout**: figures sit where they were, every text line lands where it
was, the right-margin design (running-title tab, edge line, owner's red
seal — when the book prints one) is preserved, and each page is
machine-verified.

Produced on the reference book (《龙子太郎》, 139 scanned pages, 134
standard panels): **134/134 pages pass automated QA**, 138-page merged book,
selectable text (~8,900 chars), embedded LXGW WenKai Mono, 98 MB raw /
~55 MB after Ghostscript optimization. Since v0.5 the default figure
look for this book is the **`vector` variant** — freshly-printed
high-contrast black-and-white (solid tone, crisp lines, true 1-bit output,
QA-checked by the `vector_binary` and `ink_coverage` tests). v0.5.1 replaced
the v1 two-scale binarizer with a **local tone-relative model** that keeps
fine detail carved into dark tone (white water waves on dark mountains,
cliff cracks, branch texture) without over-solidifying mid-tone areas —
see `references/workflow.md` §5a. **Output orientation follows the source**:
if the original scan is landscape A4, `config.a4` must be landscape
(841.89 × 595.28) — measure the source page box first, don't assume
portrait. Validated on a second book (《哪吒》,
70 pages, 66 panels, different numbering style + margin seals + a
right-margin design strip on every page): **66/66 pass, ~12 min hands-off
pipeline time, layout-fidelity pass +~15 min, figure/text separation pass +~10 min.**
Validated on a third book (《巴黎圣母院》, 213pp landscape scan, 202 panels
with **dot-style numbers** "31. …" + 6 unnumbered 序幕 pages + ornate
full-frame title page): **208/208 pass, ~84 min wall-clock incl. survey** —
see `examples/` and `references/performance.md`. That book added four
config features now in the skill: `panel_no_format: "dot"`, `no_panel_no`
(numberless pages), `panel_no_exceptions` (original-book number
misprints, kept faithfully), and `retype.draw_images_first` +
`whiteout_boxes` (full-page background image under re-typeset text). See
`examples/lianhuanhua/`, `examples/nezha/` and
`references/performance.md` (measured timings).

## When to use

- The user has a scanned book PDF (yellowed pages, grayscale or color
  art, text under each figure) and wants a clean modern readable/printable
  edition.
- NOT for: born-digital PDFs, books without a stable per-page layout,
  pure-OCR transcription tasks.

## What you produce

```
<book>/
  pipeline/            # this skill's scripts + config.json (book-specific)
  data/pages/          # 250dpi renders, deskewed renders
  data/layout/         # pNNN.geo.json (frame/text bands/skew) + pNNN.json (1:1 dataset)
  data/vl/ data/ocr/   # PaddleOCR-VL layout + PP-OCR line dumps (GPU)
  data/rescue/         # 09_rescue strip re-OCR reports (low-confidence lines)
  data/images/         # pNNN_bw.png (the figure) + pNNN_margin.png (margin design, when present)
  data/verify/         # pNNN.json QA report per page
  out/pages/           # pNNN.pdf per page (A4)
  out/<stem>_retypeset.pdf        # merged book (raw)
  out/<stem>_retypeset_opt.pdf    # GS-optimized (distribute this)
  out/preview/         # side-by-side original|result comparisons
```

## Prerequisites

1. **Python venv** with: `pillow numpy opencv-python-headless reportlab
   pymupdf opencc-python-reimplemented`. Check existing venvs first
   (`pip list | grep -iE 'reportlab|opencv'`).
2. **poppler-utils** (`pdftoppm pdftotext pdffonts pdfinfo pdfunite`).
3. **PaddleOCR MCP server** (`~/paddleocr-mcp/` by default) with its own
   venv (paddlepaddle(-gpu), paddleocr 3.x, paddlex). GPU strongly
   recommended (T4: ~5-6 s/page VL + ~0.3 s/page OCR). The pipeline can
   also call the MCP tools directly (`mcp__paddleocr__parse_document` /
   `ocr_image`), but the batch script imports the server module in-process
   for speed (134 pages ≈ 14 min on a T4).
4. **Ghostscript** for the final optimization pass.
5. **CJK font** — default LXGW WenKai (`/usr/share/fonts/TTF/LXGWWenKai-Regular.ttf`);
   any TTF with good CJK coverage works; the font name goes into config.
   **Match the user's reference if they made one by hand** (`pdffonts` on
   their file — e.g. `LXGWWenKaiMono`): the mono variant renders digits at
   0.5 em and looks slightly more technical; check both before deciding.
   `05_typeset` measures ASCII widths per font (`c.stringWidth`), so any
   TTF/OTF works.
6. **`vector` figure variant only**: `potrace` + `rsvg-convert`
   (`sudo pacman -S potrace` / `apt install potrace librsvg2-bin`).
   Degrades gracefully to bw_clear if missing.

## Setup (per book)

```bash
BOOK=/path/to/book                       # directory holding the source PDF
mkdir -p "$BOOK/pipeline"
cp -r <skill>/scripts/pipeline/. "$BOOK/pipeline/"   # pipeline scripts
cp <skill>/scripts/test_pages.py "$BOOK/"            # QA test (optional copy)
cp <skill>/scripts/batch_runpages.sh "$BOOK/"
cp <skill>/scripts/pipeline/config.example.json "$BOOK/pipeline/config.json"
# edit $BOOK/pipeline/config.json  (see references/config.md)
```

ROOT resolution: scripts find the book root via `$RETYPESET_ROOT`, else the
grandparent of `pipeline/lib/common.py`, else CWD — the copied-pipeline
layout always works.

## Workflow

Follow `references/workflow.md` end-to-end. In short:

1. **Survey** (agent, manual): render page samples (`pdftoppm -r 72`),
   classify every page type: cover / title / frontispiece / panels /
   colophon / watermark. Establish the **panel number rule**
   (`panel_no = page - panel_no_offset`) and the **number format**
   (bare `9`, parenthesised `（64）`, or dotted `9.` — 《巴黎圣母院》
   prints "31. “救命呀”"; all three are `panel_no_format`). Some books
   have **unnumbered** page runs inside the panel range (序幕; set
   `no_panel_no`) and occasionally **misprinted numbers** (《巴黎圣母院》
   p112 prints "48." where the run says 103; keep the misprint and set
   `panel_no_exceptions`). Check neighbours before declaring a misprint.
   **Watermark judgment (sub-skill, `references/watermark.md`)**: after
   reading through the whole book (contact sheets / low-dpi renders of
   every page), identify which marks are LATER-ADDED scanner watermarks
   (site logos, slogans, “扫描 by X” — pixel-identical page to page, NOT
   present on the sister volume) vs original printed design (publisher
   seals, running heads, binding lines). DEFAULT: remove watermarks
   (`watermark_boxes`); keep original design; confirm with the user when
   ambiguous — the client's decision wins.
2. **Config**: write `pipeline/config.json` (page ranges, offsets, font,
   special-page handlers with the exact text you read off the pages).
3. **Render**: `python pipeline/01_render.py` (all pages, 250 dpi).
4. **Geometry**: `python pipeline/02_geometry.py --page N` per panel page
   (frame polygon, skew, text bands). Pages where OpenCV fails:
   `--vl-frame x0,y0,x1,y1` from the VL image box.
5. **MCP inference** (GPU): stop the resident MCP server (single-GPU
   memory), then `python pipeline/08_batch_infer.py all` →
   `data/vl/pNNN.json` + `data/ocr/pNNN.json` for every panel page.
   Restart the MCP server afterwards.
5b. **Low-confidence rescue** (`python pipeline/09_rescue.py auto`,
   MCP venv python, resident MCP stopped — same as step 5): finds caption
   lines OCR'd as garble (score < 0.6 is the only reliable signal),
   re-OCRs the enhanced text-band strip (percentile stretch + 2x) and
   patches the OCR dump; fail-safe keeps old lines when the rescue OCR
   comes back empty. For a book-wide audit first, `09_rescue.py --check`
   (difflib strip re-OCR vs the typeset dataset, flags drift pages). Then
   rerun the affected pages through `batch_runpages.sh` / `run_page.py`.
   Clean digital scans: set `rescue.stretch: false` (upsampling alone
   fragments det lines there).
6. **Per-page build**: `./batch_runpages.sh` (parallel; each page:
   enhance → merge → typeset → verify → preview). Expect to iterate:
   rerun only failing pages. Convergence on the reference book:
   93 → 104 → 106 → 131 → 134 / 134 across 5 rounds while fixing
   general classes of issues (line clustering, over-tall lines, line
   width, frame restamp, figure QA percentile).
7. **Special pages**: `python pipeline/07_special.py all` using the
   `special_pages` config (cover_as_is / retype / title_page / colophon).
   For `retype`/`colophon` text, first OCR the page with the MCP
   `ocr_image` (or read it off a high-res render) so the config text is
   faithful; verify every character, especially numbers (书号, 印数).
   **Reproduce the layout by measurement, not by guessing proportions**:
   render the original page at 300 dpi, locate each text line / box edge /
   tab border with OpenCV row- and column-projections (ink mask or HSV
   hue-window for colored print), get y-centres and x-starts in the source
   pt space, then scale to A4 by the measured page-box factor and hard-code
   the A4 coordinates in the handler. Non-axis-aligned elements (a slanted
   box border, a tab straddling the border line) are drawn as explicit
   paths / lines at the measured corners — check borders at 3+ x-positions
   for slope before assuming they are horizontal.
8. **Full QA**: `python test_pages.py all` → expect `== N/N pages pass ==`.
9. **Merge**: `pdfunite out/pages/pNNN.pdf ... out/<stem>_retypeset.pdf`
   (respect `drop_pages`; editorial order — e.g. cover first — is your call).
10. **Optimize**: Ghostscript (see command in references/workflow.md;
    string options need `-s`, not `-d`).
11. **Final review** (agent, manual): spot-check a contact sheet of the
    merged book against the original (panel text, figure position, special
    pages); check `pdftotext`/`pdffonts`; fix regressions, rerun.

## Multi-book sessions (different scan quality in one run)

Several scanned books of DIFFERENT quality (e.g. 泛黄/黄纸 vs 洁净白底) can
be processed in one session — the pipeline is 100% config-driven, so each
book gets its own root directory + `config.json` and the skill scripts are
shared read-only. Practical rules (learned on the 《刘秀》 pair, 024 下册
heavily yellowed + du1001 watermark vs 025 上册 clean):

1. **Quantify yellowing before choosing the variant.** Sample the page
   margin strips (left/right/bottom, outside the figure): paper RGB +
   `R-B` delta + chroma percentiles. `R-B ≳ 30` and chroma p50 ≳ 40 →
   yellowed → `variant: "bw_clear"` + `enhance.color_auto: "off"`
   (prevents the yellowed-gray scan from being misclassified as a COLOR
   figure and skipped). Near-white (`R-B < 5`, chroma < 15) → clean →
   `variant: "vector"` (or bw_clear) with the default color detection.
   This is the ONE knob that must differ between the two books; everything
   else (font, layout, QA) is shared.
2. **Run the two books as independent projects.** Each has its own
   `src.pdf`, `pipeline/`, `data/`, `out/`, `.venv`, `batch_runpages.sh`.
   The skill's scripts are invoked from the book's `pipeline/` dir, so
   paths never collide. Render both, then run geometry + batch inference
   for both (they're CPU/GPU-bound, so parallelize the batch inference and
   the per-page build across the two books — they share one GPU, so keep
   the resident MCP server stopped for the whole inference phase).
3. **Watermarks / emblems are per-book, not per-page.** A corner emblem
   or a stamped slogan (e.g. the du1001 horse + “天岸马奇通人中龙”)
   that appears on one book's panel pages is a property of that SCAN,
   not the book. Detect it once (a fixed-region crop on 2–3 sample pages,
   or a template match of the watermark patch across ALL pages — a
   scan-time overlay is pixel-identical page to page), and remove it with
   `watermark_boxes`: the box(es) are painted out of the deskewed page in
   `03_enhance` (so figure + margin-strip crops come out clean) and the
   watermark's OCR line is dropped in `04_merge` (centre-in-box + left-edge
   rule, so short caption lines ending in the same corner survive). Add the
   same box to a special page's `whiteout_boxes`; a watermark ON a colored
   banner crop goes in that image's `erase_tmpl` (preferred: template
   un-blending from a plain-paper page — preserves the banner art) with
   `erase_ink` as the fallback when no plain-paper page exists. Do NOT
   assume the sister volume has the same watermark. 024 下册 had the horse
   on every panel page + p3/p4/p2 banner; 025 上册 had none.
4. **Panel numbers & layout can still differ.** Even two halves of one
   series may renumber, reflow, or drop a page. Always re-establish
   `panel_no_offset`, `panel_no_format`, and the page inventory per book
   from its OWN renders; never copy the config from the sister volume.
5. **Special pages (cover/title/synopsis/colophon) are always re-measured
   per book** — the text and the exact coordinates differ even for the two
   halves of one series. Use the measurement workflow (step 7), not a
   scaled copy.

## Hard QA rules (worktips contract)

- One page at a time: every panel page gets a `data/verify/pNNN.json`
  (figure bbox drift < 4 %, line content/position vs dataset, panel number
  anchor, font embedding, selectable text, grayscale + paper whiteness —
  skipped for `color_as_is` figures).
- `data/layout/pNNN.json` is the 1:1 dataset: figure contour polygon +
  every text line with `start_char`/`end_char`/`line_bbox_in_render`.
  Never lose page↔content correspondence.
- Do not "fix" OCR text by guessing: cross-check against the VL markdown
  dump; only override low-confidence OCR when band/line counts match.

## Key pitfalls (details in references/pitfalls.md)

- OCR bboxes are in the MCP server's ≤1920 px preprocessing coordinate
  system — map back via `lib.common.ocr_bbox_to_page` (geo carries the
  scale). Never mix coordinate systems.
- VL markdown may merge two visual lines into one; keep the override
  conservative (counts must match exactly AND OCR min-score < 0.85).
- Line clustering: a block joins the current line iff its y0 is within
  half the cluster height (y0 rule, not center-distance). Split lines whose
  height exceeds 1.3× the median line height (OCR fused two lines).
- Typeset line width: target = the original line's mapped width; shrink
  font to fit (floor 0.60×) then add tracking (cap 0.08 em) only when
  under target. This reproduces the original rag/width per line.
- Restamp only *faded* frame edges (per-edge), never the whole box —
  full restamps make frames double-thick and fool the figure QA.
- Figure QA: percentile (0.2/99.8) bbox of non-white pixels measured
  **inside the expected mapped frame box**, extended to strong full-span
  dark rows/cols (the restamped border is too thin for percentiles alone);
  do not Otsu-contour, do not cut at "top of first text line" (text can
  sit flush against the frame bottom).
- Margin seals (red owner's seals in the page margin, common on older
  scans): clamp the OCR crop to frame+65 px when the band union
  overhangs the frame >80 px (`textband_crop_box`), and drop low-confidence
  blocks outside the frame x-span in `04_merge`. Text can overhang the
  frame by ~50 px — never clamp to the frame edge itself.
- Margin design strip: some books print a running design in the right
  margin (title tab, edge line, red seal). `03_enhance` auto-detects and
  crops it from just past the figure ink extent (zero overlap — color
  noise in the figure QA region fails `print_fitness`), whitens
  per-channel (red seal stays red), and `05_typeset` redraws it 1:1
  (figure → margin → text). Config `margin_strip` (default on); blank
  margins are skipped automatically.
- Figure/text separation: the fitted frame bbox bottom can sit inside the
  blurry frame stroke or fuse the first caption line into the box, so the
  figure crop swallows body text. `refine_frame_bottom` recovers the true
  frame bottom from the white gap + text-band shape, `frame_ink_extent`
  is capped at the gap's `first_text_ink_top_px` (computed BEFORE the crop
  — the projection band top can sit above the real text), and the crop is
  post-capped at `text_top - 1`. `gap_scan` is white-gap-first (sustained
  white gap, then a rising-coverage text top) so blurry frame strokes and
  dark figure corners don't produce false case-B. Machine-checked by
  `figure_text_sep` (crop bottom <= text top + 2 px, +6 px for case-B).
- Panel numbers: sort a y-cluster by sub-row (vertical-overlap grouping,
  top-down then left-right), not by raw `(y0, x0)` — the number block's
  bbox top can sit below the text it prefixes, duplicating it at the line
  end. Machine-checked by `panel_no_single` (number appears at most once,
  at the start of line 0).
- Font size: use ONE font size per page (median of per-line natural sizes,
  capped by the smallest full-line fit, floored at 0.6× median ink size);
  per-line independent sizing makes the first line render smaller than the
  rest.
- **Output orientation**: measure the source page box and the user's
  reference PDF before setting `config.a4`. `lib/common.py` reads
  `a4.width_pt`/`height_pt` generically, so landscape books need no code
  change — but a portrait config on a landscape source silently produces a
  wrong-layout book. Special-page handlers must use measured coordinates,
  not `A4_W * k` proportions, or the same trap repeats.
- reportlab caches images by file path: two PIL images through one temp
  path silently re-embed the first (wrong-image/wrong-aspect bug). Use
  content-hash temp names (`07_special._place_image`); sanity-check by
  counting `/Subtype /Image` XObjects per page PDF.
- **reportlab charSpace leaks across text objects.** `setCharSpace(0)`
  is a no-op (Tc is emitted only for non-zero values), so a justified
  line's tracking stretches every following line's cells to `pt +
  leaked_track` (《孙膑和庞涓》 p114: title tracking turned the 15 pt
  colophon columns into 2.9-em cells that intruded into the right
  column). Always `setCharSpace(track if track else -0.001)` in every
  text path (`05_typeset`, `07_special._draw_justified`), and route
  `drawString`-style lines through a `textOut` wrapper with the same
  sentinel. Invisible to position QA (only the right edge drifts) —
  detect via `pdftotext -bbox`: cell width vs font size != 1.0 on a
  non-tracked line.
- The dataset line field is `simplified`/`raw`, not `text` — verify/merge
  code reading `ln["text"]` raises `KeyError`.
- **vector v2**: do not median-blur before binarization (median(3) erases
  1-px detail lines); denoise AFTER binarization by dropping small ink
  components. Judge every pixel against its local tone: `solid` for dense
  tone → flat black; `bright = flat > tone + bright_dev` preserves light
  detail inside dark tone; `ink = flat < T_dark | (flat < tone - dev)`
  keeps lines + halftone pattern. `potrace -t 2` (not 5) or isolated
  halftone dots vanish from the SVG. `ink_coverage` QA: output ink must be
  ~0.55–1.5× the original scan's ink-equivalent; a fresh book is slightly
  lighter than the aged scan (typical ~0.75–0.9).
- **Color images stay as-is.** A color figure or page (p99 of
  per-pixel max(R,G,B)−min(R,G,B) > 30 — `lib.common.is_color_image`)
  gets NO pixel treatment: no grayscale, no illumination whitening, no
  level stretch, no sharpen, no binarize. It is placed 1:1 in its
  original color (`enhance_variant: "color_as_is"`), and the
  `print_fitness` grayscale/whiteness check is skipped for it. Gray
  scans (p99 chroma ~0-3) keep the bw_clear path. Applies to
  `03_enhance` figure crops, `07_special.enhance_full` (mode `auto`),
  and retype/title/colophon image crops. Do not "clean" a color
  illustration into grayscale — the color IS the content (《孙膑和庞涓》
  p1 cover: a color plate that bw_clear turned gray).
- GS string options: `-sColorConversionStrategy=Gray` (not `-d...`).
- One GPU: stop the resident MCP server before `08_batch_infer.py`.
- Figure/text gap: the fitted contour bbox can sit a few px BELOW the
  frame's strong bottom line (faint band/gradient fused into the contour).
  Blindly scanning down from the bbox swallows that band and pushes the
  crop bottom into the visible figure/text gap. `frame_ink_extent` stops
  the down-scan at the first sustained faint run (>=3 rows <10% dark) and,
  when the row just below the bbox is already faint, pins the crop bottom
  to the strong line's bottom (lowest >=30% row scanning up, + short
  anti-alias tail). Check `frame_ink_bbox` vs the strong line on pages
  that fail `figure_text_gap`.
- Dense type lines: when the inter-line dip never drops below the ink
  threshold (large strokes whose tips reach into the gap), the ink-bbox
  chain clips every following line's top low and lines drift off the page
  (QA: `n_got < n_expected` in the text check). `04_merge` detects the dip
  as the coverage local minimum (min < 0.5 × P75 of the window) inside the
  bbox overlap of consecutive lines and clips the ink extents at it.
- Figure QA in source (1:1) mode: judge corners by error RELATIVE TO THE
  BOX SPAN (width/height), not relative to coordinate magnitude — a box
  near the page edge makes 4%-of-coordinate a sub-pixel demand that
  quantized 150 dpi measurement trips.
- **Near-black figures fuse the frame**: on a near-pitch-black plate
  (《巴黎圣母院》 p211) the contour detector finds no frame because the
  frame stroke merges with the figure ink. `02_geometry` falls back to
  the VL image box automatically — but that needs `data/vl/pNNN.json`
  first: run the VL phase (or one MCP `parse_document` call) BEFORE
  geometry on such pages, or re-run `02_geometry --page N` afterwards.
- **Yellowed gray scans are NOT color** — a heavy yellow cast (e.g. 刘秀
下册: paper RGB ≈ (236,220,167)) pushes per-pixel chroma to ~60-70,
far above the 30 threshold, so with `color_auto: "auto"` (default) every
figure gets mis-routed to `color_as_is` and keeps its yellow. Set
`enhance.color_auto: "off"` for yellowed grayscale books (and `"on"` to
force the color path for subtle color below the threshold). Measure the
paper R−B / chroma during the survey (four margin strips, bright
pixels): R−B > ~20 means de-yellowing is mandatory.

**Left-margin running design**: some books print a small original design
in the LEFT margin (running title, edge line, publisher mark).
`margin_strip` (default on) auto-detects and crops both sides per page —
left strip is `pNNN_margin_left.png` (`margin_crop_box_left`), whitened
per channel like the right strip, drawn 1:1 by `05_typeset`. Blank
margins are skipped, so a series with per-volume differentia needs no
per-volume config. CAUTION: a mark in the left margin is NOT
automatically original design — the 刘秀 下册 scan-time horse + slogan
stamp also sits in the left margin. Judge it with the watermark sub-skill
(`references/watermark.md`) FIRST: a later watermark is removed by
`watermark_boxes` (the margin strip then auto-skips the now-blank
margin), while original design is redrawn 1:1 by the strip.
- **Watermark judgment is a sub-skill (run during survey; default REMOVE).**
  After reading through the whole book, judge which marks are LATER-ADDED
  scanner watermarks vs original printed design — a scan-time overlay is
  pixel-identical on every page it appears on (template-match a candidate
  patch against ALL pages, recall ≈ 1.0; a short OCR line repeating at the
  same corner position on 3+ pages with variable readings = a slogan
  stamp; the sister volume / a different scan of the same book is the
  tie-breaker — the watermark is usually NOT there). URL / site name /
  personal name / slogan ⇒ later watermark ⇒ remove by default
  (`watermark_boxes` → painted out in 03_enhance + line dropped in
  04_merge; `whiteout_boxes` on special pages; `erase_tmpl` (preferred:
  template un-blending from a plain-paper page) or `erase_ink` fallback
  for a watermark sitting ON a colored banner image). Publisher seals,
  running heads,
  binding lines, cover art ⇒ original design ⇒ keep. When ambiguous, ASK
  the client — the client's call wins over the default. Full procedure:
  `references/watermark.md`.

**MCP VL layout bboxes are ORIGINAL-image pixels.** `_tool_parse`
  passes the original file straight to PaddleOCR-VL and the model
  rescales its boxes back to the input image — no ≤1920px rescale
  factor (the OCR textband path is the one that needs
  `ocr_bbox_to_page`). Comparing VL image boxes against geometry frame
  boxes (page px) works directly; do not "fix" a healthy 0.99 IoU.
- **retype uniform text blocks: ONE size for the whole block.** In
  strict-fidelity retype, deriving each line's `pt` from its own
  measured ink height amplifies per-line ink jitter: a row whose glyphs
  run tall (副/洛/·-heavy rows measure ~8-10% taller — 《巴黎圣母院》
  p3 line "…副主教克洛德·孚罗洛": 55 px band vs 51 px median → 16.6 pt
  vs 15.4 pt, visibly larger) renders as a size step across the block.
  Measure the median ink height of the block and use ONE `pt` for all
  body lines; keep the per-line x/width/baseline (width matching and
  placement stay line-accurate, size becomes uniform). Same rule the
  panel path already follows (one size per page in 05_typeset).

## Performance reference

Measured on the reference machine (see `references/performance.md`):
render 139 pages ≈ 1 min; geometry ≈ 4 min; VL+OCR 134 pages on a T4 ≈
14 min; per-page build 134 pages @ 6 workers ≈ 10 min; merge + GS ≈ 4 min.
Plan: a 100-panel book is a **~1 hour** job wall-clock, mostly waiting on
GPU inference.

## Extending

- New special-page layout? Add a handler to `07_special.py` (each handler
  is a small function of `(page, cfg)` drawing on an A4 canvas).
- Different figure treatment? `03_enhance.py` variants: `bw_clear`
  (grayscale cleaned scan, default for grayscale scans) or `vector`
  ("freshly printed" high-contrast B/W: page-level illumination fit +
  two-scale binarize + potrace SVG master + 1-bit high-res raster — see
  references/workflow.md §5). **Color-figure rule**: with the default
  variant, a color figure (p99 per-pixel chroma > 30) is kept AS-IS in
  its original color — no grayscale, no whitening, no stretch, no
  sharpen, no binarize — and placed 1:1 (`enhance_variant:
  "color_as_is"`); the `print_fitness` grayscale check is skipped for
  such pages. Same rule for special pages: `enhance_full` auto-places a
  color page as-is, and retype/title/colophon image crops keep their
  color even when `color: false`.
- **Typesetting engine**: the pipeline emits per-page PDFs via ReportLab
  (fast, batch-parallel, machine-verifiable). If the user works in
  **Scribus** (or wants an editable `.sla` source), an optional path
  exists: `scribus-mcp` (PyPI) drives Scribus headless/interactive with
  ~125 tools incl. `render_page_to_image` for visual QA — see
  `references/scribus.md` for when it wins over ReportLab.
- The per-page contract is `run_page.py` (03→04→05→06); keep it idempotent.
