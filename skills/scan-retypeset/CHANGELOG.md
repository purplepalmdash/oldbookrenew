# Changelog

## 0.7.0 (2026-11-01)

Repo integration (oldbookrenew) + cross-pollination from the sibling
skills `paddleocr-digitize` / `guji-restore` (bread branch) after
reading their 刘秀/孙膑/巴黎圣母院 runs:

- **`09_rescue.py` (new)** — low-confidence caption-line triage + strip
  re-OCR. score < 0.6 (config `rescue.score_threshold`) is the only
  reliable garble signal (garbage lines are built from common
  characters; frequency/gray stats fail). Rescue = textband strip +
  percentile stretch (p10..p99) + 2x INTER_CUBIC + re-OCR via the MCP
  module, replacing all strip lines (fail-safe: empty result keeps the
  old lines); writes the same-schema `data/ocr/pNNN.json` dump +
  `data/rescue/pNNN.json` report, then the normal `run_page.py` rebuild
  picks it up. `--check` runs the book-level strip audit (difflib ratio
  vs the typeset dataset, flags pages < `check_ratio`). `--no-stretch`
  / `rescue.stretch: false` for clean digital scans (upsampling alone
  fragments det lines there).
- **`06_verify` check #11 `chars`** — character-level text audit via
  PyMuPDF `rawdict`: the character multiset of the retypeset page must
  exactly match the dataset (font glyph-mapping variants U+2219/U+2027/
  U+30FB normalized to U+00B7 first). Catches a char lost to font
  subsetting / line rebuilding that the line-level check can't see.
  `pymupdf` added to requirements.
- **`07_special` `retype` `vertical: true`** — upright vertical columns
  for title pages / 题签 (first-char baseline + x, top-to-bottom, right
  to left across columns). Full recipe + pitfalls (spatial vs reading
  order, big-title det misses, show-through ghost lines, char pitch) in
  new `references/vertical.md` (Route A retypeset / B rotation-detection
  / C keep-as-image).
- **New `references/hardware.md`** — hardware tiers: T4 validated
  numbers, CPU-only expectations (×3-5 on inference; split vl/ocr
  phases), remote-GPU pattern (rsync the book, run 08/09 on the GPU
  host, rsync `data/vl`+`data/ocr` back), and how to route between this
  skill and the sibling skills' iGPU/CPU route.
- **`references/watermark.md` §D-map** — tiled/repeating watermarks
  (site watermarks incl. color covers): cross-page pattern averaging
  (no plain-paper page needed) + 2D phase check + un-blend with
  LOCAL-BRIGHTNESS-DEPENDENT response r (fixed r over-corrects dark
  color bands → dark ghost) + TELEA inpaint fallback. Positioned
  alongside `watermark_boxes` / `erase_tmpl` / `erase_ink`.
- **B5 weighted-slot tracking** (`lib/common.py::weighted_gaps`, used by
  `05_typeset` and `07_special::_draw_justified`) — justified lines that
  mix CJK with digits/Latin now get inter-character gaps weighted by
  the adjacent glyph advances (slot ∝ advance, the bread rule): uniform
  tracking gave narrow digits the same gaps as CJK characters and
  visibly over-spaced them. Pure full-width lines keep the historic
  uniform path byte-for-byte; the weighted path draws per character
  (reportlab Tc per char). `placed` rows gain a `wtrack` flag; `track`
  reports the mean gap on weighted lines.
- **`pitfalls.md` +5 (36-40)**: minAreaRect angle metadata (0.5°→89.5°;
  our 02_geometry already uses the safe top-edge atan2 — documented as
  the pattern), dot-glyph QA false alarm (U+00B7→U+2219), rawdict vs
  dict for char-level QA, rescue recipe book-type dependence,
  high-confidence homophone mis-OCRs (孙膑→孙宾) + lexicon step.
- **SKILL.md v0.7.0**: Agent-Skills-standard frontmatter
  (name/description/compatibility/metadata — loads cleanly under both
  pi and Claude Code loaders), workflow step 5b (rescue), `data/rescue/`
  in the output layout, `pymupdf` in prerequisites.

## 0.6.2 (2026-11-01)

Watermark-judgment sub-skill + template un-blending, from the
《刘秀》 下册 scan-time-watermark removal (158 panel pages + colored
banner special page).

- **New `references/watermark.md` sub-skill**: run after reading the
  whole book, before final config — identify later scanner watermarks
  (repeated short OCR lines in a fixed corner, template match vs a
  plain-paper page, sister-volume cross-check, contact sheets),
  distinguish them from original printed design, **default REMOVE,
  client opinion wins**; per-removal-mechanism guidance + pitfalls.
- **`07_special.py` `erase_tmpl` (template un-blending)** — preferred
  over `erase_ink` for watermarks sitting on colored art: measures the
  per-pixel watermark absorption field `a = (P − T)/P` on a plain-paper
  template page (same scan-time overlay), then restores the underlying
  paint as `base = observed / (1 − a)`. `erase_ink` remains the
  fallback when no plain-paper page exists. 1-px resize guard for
  integer-rounding shape drift between template and target crops.
- SKILL.md: sub-skill entry in Survey, `erase_tmpl`-first removal rule,
  and a fix to the "left-margin running design" entry (a left-margin
  mark is NOT automatically original design — judge it). workflow.md:
  new "Watermark judgment" section under Survey.
- `config.example.json` + `config.md`: `watermark_boxes` help now lists
  `erase_tmpl` (preferred) / `erase_ink` (fallback) for watermarks on
  colored banner images.
- `examples/liuxiu/` case study (config + notes): heavy-yellowing
  `bw_clear` + `color_auto: off`, `watermark_boxes`, `erase_tmpl`.

## 0.6.1 (2026-10-31)

Scan-time watermark removal + multi-book session rules, from the
《刘秀》 上册/下册 two-volume session (156 + 164 pages in one session).

- **`watermark_boxes`** (config, normalized page boxes): later
  scanner watermarks painted out of the deskewed page in `03_enhance`
  **before** all crops — median ring fill + vertical-line-column
  restore (a page-edge design line inside the box survives). The
  watermark's OCR line is dropped in `04_merge` by a strict rule: line
  centre inside the box AND line left edge inside the box's left 60%
  (real caption lines ending in the same corner start further right).
  `lib/common.py` exposes `WATERMARK_BOXES`; stale
  `pNNN_margin_left.png` strips are unlinked when a box removes the
  margin design.
- **`07_special.py` `erase_ink`**: pixel-level dark-ink replacement
  with the local non-ink mean for watermarks on colored banner/special
  images (fallback when template un-blending is impossible). On
  plain-canvas pages `whiteout_boxes` now runs before text/images so
  it cannot erase just-placed crops.
- **Multi-book sessions** (SKILL.md section + rules): one session,
  one survey, per-book config — pipeline stays 100% config-driven, no
  book-specific code; stop the resident MCP server before
  `08_batch_infer` (one GPU model set at a time); merge from the common
  parent with command substitution (`pdfunite $(cat …)`); post-GS font
  QA via `pdffonts` (subset prefixes are unstable); `out/pages/` hold
  PDFs — render with `pdftoppm` for visual checks.
- `references/pitfalls.md` +5 (31–35): special-page coordinate bias
  (use pixel projection on rendered originals, not OCR bboxes),
  `_place_image` top-center anchor, 4-tuple layout colors, `@file`/
  `xargs -o` merge failures, stale `wc -l` on trailing-newline-less
  merge lists.
- `references/config.md`: `background` key for full-page plates
  (replaces `images` + `draw_images_first`), `force_gray`, top-center
  anchor semantics, `erase_ink`.

## 0.6.0 (2026-10-31)

Heavily-yellowed / multi-quality scan support, from the 《刘秀》 survey.

- **`enhance.color_auto`** (`auto` default / `off` / `on`): per-figure
  color routing override. REQUIRED `off` for heavily yellowed grayscale
  scans — the yellow cast alone (paper RGB ≈ (236,220,167), chroma
  ~70) trips the p99-chroma color test and mis-routes every figure to
  color-as-is.
- **`crop_margin_strip` `side`**: left AND right margin running-design
  strips (`pNNN_margin.png` / `pNNN_margin_left.png`), both redrawn 1:1
  by `05_typeset` (the books' left-edge vertical line lives here).
- **`07_special.py` `force_gray`**: de-yellowed crops on special pages
  (a yellow full-page plate can pass `is_color_image()` on paper
  chroma alone).
- `config.example.json` / `config.md` / SKILL.md updated.

## 0.5.1 (2026-10-30)

Detail-preserving vector v2 binarization + landscape / measured special
pages, driven by user review of the 《龙子太郎》 v1 output (P19: water
waves, mountain paths and cliff-crack detail lost; mid-tones over-
solidified → "too black").

- **`vector` v2 local tone-relative binarization** (`03_enhance`,
  `config.enhance.vector`): every pixel is judged against its local tone
  (`tone = blur(flat, tone_kernel=31)`):
  - `solid = tone < T_solid(150)` → dense tone becomes flat black;
  - `bright = flat > tone + bright_dev(45) & tone < bright_tone(185)` →
    light detail carved into dark tone is preserved (white water waves on
    dark mountains, crack highlights);
  - `ink = flat < T_dark(190) | (flat < tone − dev(25)) & tone ≥ T_solid`
    → dark lines/dots kept, mid-tone halftone kept as pattern, not
    solidified;
  - denoise AFTER binarization: 1-px morphological close + drop ink
    components < `speckle_px(10)`. **No median pre-filter** — median(3)
    erases 1-px detail lines.
  - `potrace_t` default 5 → **2** (tolerance 5 drops isolated halftone
    dots from the SVG master).
- **`06_verify` check #10 `ink_coverage`**: output figure ink vs the
  original scan's ink-equivalent (`mean(255−gray)/255` over the figure
  crop). Pass band 0.55–1.5; a fresh book is slightly lighter than the
  aged scan (typical 0.75–0.9). The machine check for "not too black".
- **Output orientation follows the source**: `config.a4` must match the
  source scan's orientation (this book + the user's manual reference are
  landscape A4 841.89 × 595.28). `lib/common.py` reads width/height
  generically — no code change needed; the trap is a silently wrong
  portrait config. `config.md` documents both A4 boxes.
- **Special pages by measurement, not by proportion**: render the
  original page at 300 dpi, locate text lines / box borders / tabs with
  OpenCV row/column projections (HSV hue-window for colored print),
  convert to A4 pt by the measured page-box factor, hard-code the result
  in `07_special.py`. Slanted borders are checked at 3+ x-positions and
  drawn as explicit paths; a tab straddling a border line is drawn over
  it with a white fill. (《龙子太郎》 P2 title, P3 synopsis, P138
  colophon all rebuilt this way; the colophon box is a slightly slanted
  quadrilateral.)
- **Reference book final state**: 134/134 panel pages pass (v2 figures,
  landscape A4), 138-page book, ~71 MB after GS; special pages
  side-by-side verified against the original at 300 dpi.

## 0.5.0 (2026-10-30)

High-contrast "freshly printed" figure treatment + Scribus awareness,
driven by the 《龙子太郎》 v0.5 rebuild (user's manual GIMP+Scribus
reference as the quality target).

- **`vector` figure variant** (`03_enhance`, `config.enhance.variant`:
  - **Page-level illumination fit**: a quadratic 2-D polynomial fit to
    paper pixels corrects vignette/yellowing without erasing halftone tone
    (local blur-division over-flattens dense tone regions — see
    references/pitfalls.md "Vector variant").
  - **Two-scale binarization**: `fine = flat < fine_thr` (line art + dots)
    OR `coarse = blur19(flat) < coarse_thr` (dense tone solidified to flat
    black). Defaults 160 / 195.
  - **potrace SVG master** per figure (`data/images/pNNN.svg`, the
    editable vector source of truth) + **rsvg 144 dpi 1-bit raster** for
    embedding (RGBA composited on white first).
  - New `06_verify` check `vector_binary`: figure must be true 1-bit
    (values ⊆ {0,255}) with sane ink fraction.
  - Degrades gracefully to `bw_clear` when potrace/rsvg are missing.
- **Font-accurate ASCII width** (`05_typeset`): per-glyph measurement via
  `reportlab.stringWidth` replaces the hardcoded 0.55 em — correct for
  proportional CJK fonts and the LXGWWenKai **Mono** variant (digits at
  0.5 em). Skill now matches a user's manual reference exactly when the
  reference used Mono (`pdffonts` is the diagnostic).
- **`config.example.json`**: `enhance.vector` parameter block documented.
- **《龙子太郎》 reference run v0.5**: all 134 panel pages re-passed with
  `vector` + `LXGWWenKaiMono`; merged book 98 MB raw / ~55 MB after GS
  (1-bit figures compress much better than the old grayscale halftone:
  340 MB → 98 MB raw).
- **New `references/scribus.md`**: ecosystem survey (scribus-mcp on PyPI,
  ~125 tools, headless + interactive backends, AppImage auto-fetch,
  xvfb/FUSE requirements, startup-dialog papercut) + when Scribus beats
  ReportLab (editable .sla source, human-in-the-loop layout, CMYK) +
  hybrid workflow (ReportLab stays the QA source of truth; optional .sla
  export from the same layout JSON; optional interactive polish).

# Changelog

## 0.4.0 (2026-10-30)

Layout-consistency pass: stop the figure crop from swallowing body text,
stop duplicated panel numbers, and use one font size per page. Both
regressions were found by side-by-side review of 《哪吒》 against the
original and are now caught by two new machine checks.

- **Figure/text separation.** The fitted frame bbox bottom could sit
  inside the blurry frame stroke or fuse the first caption line into the
  box, so the figure crop cut into the caption (p20/p22/p25/p27/p28/p33).
  `refine_frame_bottom` recovers the true frame bottom from the white gap
  + text-band shape; `frame_ink_extent` is capped at the gap's
  `first_text_ink_top_px` (computed BEFORE the crop — the projection band
  top can sit above the real text and truncate a blurry stroke); the crop
  is post-capped at `text_top - 1`. `gap_scan` is now white-gap-first
  (sustained white gap, then a rising-coverage text top via `_text_rising`)
  so blurry frame strokes and dark figure corners no longer give false
  case-B. Original-side and output-side gap measurement use the identical
  `gap_scan` (60px white-gap search, 160px text search; output scaled by
  `k=page_pt/page_px`).
- **Panel-number ordering.** `04_merge` sorts a y-cluster by sub-row
  (vertical-overlap grouping, top-down then left-right) instead of raw
  `(y0, x0)` — the number block's bbox top can sit below the text it
  prefixes, duplicating it at the line end ("（23）…(23)").
- **Unified per-page font size.** `05_typeset` uses one font size per page
  (median of per-line natural sizes, capped by the smallest full-line fit,
  floored at 0.6× median ink size); per-line independent sizing made the
  first line render smaller than the rest.
- **New machine checks in `06_verify`:** `figure_text_sep` (crop bottom <=
  first caption ink top + 2px, +6px for case-B flush pages) and
  `panel_no_single` (panel number at most once, at the start of line 0).
  Both run per page in the batch.
- **Textband crop/mapping consistency:** `00_batch_prepare` and
  `ocr_to_page_scale` both use `textband_crop_box` (was union±30px, which
  drifted OCR bboxes up to ~70px in x).
- **`02_geometry` self-contained VL fallback:** if OpenCV frame detection
  fails and no `--vl-frame` is given, it reads the largest `label="image"`
  box from `data/vl/pNNN.json` (previously such a page failed the batch
  with "frame not detected").
- Result on 《哪吒》: 66/66 pass (including the new checks), figure↔text
  gap median 0.19 pt (max 1.06 pt), opt book ~34 MB.

## 0.3.0 (2026-10-30)

Layout-fidelity pass: the right-margin design strip (《哪吒》 prints a
running-title tab, a full-height page-edge line, notch bars and an
owner's red seal in the right margin of every panel page) is now
cropped, cleaned and redrawn 1:1, so the retypeset page matches the
original page layout, not just frame+figure+text.

- **`03_enhance.crop_margin_strip`** (new, config `margin_strip`,
  default on): crops the strip from just past the figure's outer ink
  extent (`frame_ink_bbox[2] + 2`, zero overlap with the figure) to the
  page edge of the color deskewed render; per-channel illumination
  division + level stretch removes the yellow cast while the red seal
  keeps its color. Auto-detects blank margins (no ink, no saturated
  color → skip), so it is safe to leave on for books without margin
  design.
- **`05_typeset`**: redraws the strip 1:1 from `geo.margin_crop_box`
  (draw order figure → margin → text, so text overhanging the frame
  stays on top).
- **`06_verify` `print_fitness` fix**: the expected/measured figure
  boxes are `(x0, y0, x1, y1)`, not `(x, y, w, h)` — the old unpacking
  silently extended the QA region past the figure's right edge, which
  was harmless while that zone was blank page but failed (`channel
  spread` 9–30 > 8) on ~half the book once the color margin strip
  landed there. The check now slices the correct box and measures
  against the expected box.
- Merge/GS robustness: `/Matrix` patching is in-place on the single-page
  PDFs (patch → pdfunite → gs); GS `pdls` device is not in stock builds
  — `pdfwrite` + prepress + the downsample flags is the portable path.
- Docs: `references/pitfalls.md` +6 items (25–30: margin strip, crop
  origin, box semantics, W/H slice bug, patch order, pdls);
  `references/performance.md` margin-fidelity stage table (~15 min);
  `references/config.md` + `margin_strip`; `config.example.json` updated;
  `examples/nezha/CASE.md` issue #6 + updated result numbers.
- Result (《哪吒》): 66/66 held, gap median error unchanged (0.19 pt /
  max 1.15 pt), opt book 35.7 MB (color margin strips add ~3 MB).

## 0.2.0 (2026-10-29)

Second-book validation (《哪吒》, 70 pages / 66 panels) and the four
fixes it surfaced. All general — no book-specific constants.

- **Margin-seal handling** (red owner's seals in the page margin get OCR'd
  as short low-confidence garble and corrupt lines):
  - `lib/common.py`: new `textband_crop_box(geo)` — the textband OCR crop
    x-range is clamped to 65 px beyond the frame edge when the geometry
    union overhangs the frame by >80 px. `ocr_to_page_scale` now uses the
    same box, so the MCP→page coordinate mapping matches the crop that
    was actually sent to OCR.
  - `04_merge.py`: drops low-confidence (<0.85) blocks sitting outside
    the frame's x span (real overhanging text reads ≥0.97 and survives).
- **Figure QA rewrite** (`06_verify.output_figure_bbox`): measures ink
  inside the expected mapped frame box instead of "everything above the
  first text line" (text can sit flush against the frame bottom), and
  extends the box to strong full-span dark rows/columns — the restamped
  frame border is too thin for percentiles alone (failed on light
  line-drawings: bottom edge 4–6.5 % short).
- **`07_special._place_image`**: unique content-hash temp path per PIL
  image — reportlab caches embedded images by file path, so two images
  through one path silently re-embed the first (wrong image + wrong
  aspect ratio).
- Docs: `examples/nezha/` case study; `references/pitfalls.md` +4 items
  (14a, 14b, 20a, and the seal notes); `references/performance.md` gains
  the measured Nezha stage table (hands-off pipeline ≈ 12 min on T4);
  SKILL.md/README updated.

## 0.1.0 (2026-10-29)

Initial release, extracted from the 《龙子太郎》 full-book retypeset run
(139 scanned pages → 138-page A4 print-ready book, 134/134 panel pages
pass machine QA).

- Config-driven pipeline (`pipeline/config.json`): source PDF, page
  ranges, panel-number offset + format (`bare` / `paren`), A4 geometry,
  font + measured ink metrics, enhance options, MCP server location,
  special-page handlers.
- `01` render / `02` geometry (OpenCV frame + skew + text bands,
  `--vl-frame` fallback) / `03` figure enhance (de-yellow whitening,
  per-edge frame restamp) / `04` VL+OCR merge into the 1:1 per-page
  dataset (line clustering by the y0 rule, over-tall-line splitting,
  paren/bare number normalization, conservative VL override) / `05`
  A4 typeset at original coordinates (per-line font fit + tracking) /
  `06` machine QA / `07` special pages (cover_as_is, retype,
  title_page, colophon) / `08` in-process GPU batch inference.
- `run_page.py` per-page contract, `batch_runpages.sh` parallel driver,
  `test_pages.py` full-book QA test.
- Skill documentation: SKILL.md, references/{workflow,config,pitfalls,
  performance}.md.
- Reference case: examples/lianhuanhua (config + case notes).
