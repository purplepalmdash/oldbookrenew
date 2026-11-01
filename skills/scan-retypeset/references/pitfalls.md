# Pitfalls & hard-won lessons

Distilled from the reference run (《龙子太郎》, 134 panels, 5 batch rounds
93→104→106→131→134). Each item is a class of failure that actually
happened; keep the fixes general.

## Coordinate systems

1. **OCR bbox coordinate system.** `mcp__paddleocr__ocr_image` returns
   bboxes in the server's preprocessing space (long side ≤ 1920 px,
   `PADDLEOCR_MAX_IMAGE_SIZE`), NOT page pixels. Always map back with
   `lib.common.ocr_bbox_to_page(bbox, geo)` (uses `render_size` +
   `ocr_to_page_scale`). Mixing systems silently shifts every line by the
   scale factor.
2. **Render DPI consistency.** Everything downstream (geometry, crop,
   map) assumes `data/pages/pNNN.png` is at `render_dpi`. If you
   re-render at a different DPI, delete `data/layout/*.geo.json` and
   re-run geometry (stale geo = wrong frame + wrong skew).
3. **Mixed page sizes in one PDF.** `pdfinfo` reports one "Page size";
   individual pages can differ (a small scanned cover). Check render
   sizes (`data/pages/pNNN.png` dimensions) for special pages; the
   pipeline handles per-page sizes, but your crop boxes in
   `special_pages` are fractions of that page's own render — verify
   against the actual render, not the nominal page size.

3a. **Textband crop box must match the OCR mapping box.** `00_batch_prepare`
    and `ocr_to_page_scale` both derive the textband crop from
    `textband_crop_box(geo)` (frame-clamped, seal-excluding). If the
    cropper uses a different rule (e.g. naive `text_band_union ± 30px`),
    the x-range of the crop actually made differs from the one the scale
    assumes, and every OCR bbox drifts by up to ~70 px in x (《哪吒》: the
    union overhung the frame by >80 px on some pages). Always crop with
    `textband_crop_box` and map with the identical function.

## OCR / VL data quality

4. **VL markdown merges lines.** PaddleOCR-VL sometimes emits two visual
   text lines as one markdown line (and vice versa). The override in
   `04_merge` therefore only fires when `n_vl_lines == n_bands ==
   n_ocr_lines` AND `ocr_min_score < 0.85`. Anything else: trust the
   per-line OCR. (Failure mode if you loosen this: a 3-band page renders
   as 1 giant line, verify FAILs on line count.)
5. **OCR splits multi-digit numbers across blocks** ("13" + "2…").
   `04_merge` concatenates when the previous block is a pure number token
   and the next starts with a digit. Keep `_is_number_token` tolerant of
   parenthesised tokens (`（13）`) — the same split happens around
   `（1）`.
6. **OCR drops the small leading panel number** on some pages. The
   fallback prepends it only when the VL text contains the expected
   number; otherwise `panel_no_check.pass=false` and the page FAILs —
   that is correct behavior, fix the page (or the VL/OCR data), don't
   silence the check.
7. **OCR fuses two visual lines into one tall block** (p103 class:
   estR 755 pt on a 595 pt page). Detection: line bbox height > 1.3 ×
   median line height AND text length > band capacity (band width /
   median char width from high-confidence lines). Split at capacity,
   avoiding a leading punctuation on the second half.

## Line clustering (04_merge)

8. **Center-distance clustering over-merges** when two visual lines have
   overlapping vertical padding (p62 class: 4 lines → 2 lines, x0 order
   scrambled). The y0 rule (block joins the cluster iff
   `block.y0 < cluster.y0 + 0.5 * cluster_height`) is the robust one;
   clusters sort by `(y0, x0)`.
9. **Blocks outside any text band** (band detection missed a line):
   `band_of` assigns by max y-overlap, and a band-miss is tolerated as
   long as the line still typesets — but it will show up as a verify
   line-count mismatch; fix the geometry (band thresholds), not the
   merge.
9a. **Panel number sorts to the END of its line → duplicate number.**
    The panel-number block's bbox top can sit a few px BELOW the body
    text's bbox top (the number's glyphs start lower / the text block's
    bbox top is a tall preceding glyph). Sorting a y-cluster by
    `(y0, x0)` then places the number after the text it prefixes, so the
    line renders as "（23）哪吒笑道：…(23)" (number both inline from OCR
    and at the end from the block). Fix: within a cluster, first sort by
    bbox top, then group blocks into sub-rows by vertical overlap
    (overlap >= 0.4 * min height), and emit sub-rows top-down, left-to-
    right within a sub-row. The number (same visual row, x0 at the line
    start) then precedes the text. Machine-checked by `panel_no_single`
    in `06_verify` (a panel number may appear at most once, and only at
    the start of line 0).
9b. **Dense type: inter-line dips never reach the ink threshold.** With
    large-stroke type set tight (《孙膑和庞涓》 p31/p63/p112), the
    coverage dip between consecutive lines can stay above the
    `line_ink_bbox` min_frac (0.02) — stroke tips of line N+1 reach up
    into the gap. The chained scan (line N+1's top starts at line N's
    ink bottom + 1) then clips every following line's ink top LOW, the
    lines drift down cumulatively, and the last line leaves the page.
    Signature: verify `text` fails with `n_got < n_expected` (last line
    missing from the PDF) while earlier lines match. Fix in `04_merge`:
    detect the true dip as the coverage LOCAL MINIMUM inside the bbox
    overlap of consecutive lines (min < 0.5 x P75 of the window
    coverage — P75, not the window center, which can land inside the
    dip itself) and clip the ink extents at it (`y_start`/`y_end` of
    `line_ink_bbox`). Clean-gap pages are unaffected: the split lands in
    the white gap where the chain already started.

## Typesetting (05_typeset)

10. **Ink-height-based font size runs ~15% wide.** Scanned CJK glyph
    height ≈ `ink_em` em of the *original* type, but the original line
    width is the constraint that matters for fidelity. So: start from
    ink height, then `n_units` (CJK=1.0, ASCII/digit=0.55), `fpt =
    min(fpt_ink, target/n_units)` floored at 0.60×; tracking fills
    under-target width (cap 0.08 em). (Failure mode otherwise: long
    lines overflow the right margin or the last character is clipped —
    p17/p24 class.)
11. **Per-line, not per-band.** A band may contain multiple visual lines;
    every line carries its own `line_bbox_in_render` and its own
    `start_char`/`end_char`; typeset from those, never from `bands[i]`.
11a. **Per-line font size jitters across a page.** Computing `font_pt`
     independently per line makes the first line (usually shorter, or
     width-capped) render smaller than the following lines — a visible
     size jump the user reads as "排版不统一". Fix: compute each line's
     natural size, take the per-page MEDIAN as the page font, cap it by
     the smallest full-line fit (so no line overflows), floor at
     0.60 * median ink size, and set EVERY line to that one size (tracking
     still fills under-target width). All lines on a page then share one
     `font_pt`.
11b. **reportlab charSpace leaks across text objects — setCharSpace(0)
     is a no-op.** reportlab emits the Tc operator only for NON-ZERO
     values, so `setCharSpace(0.0)` writes nothing to the content stream
     and the canvas keeps the PREVIOUS text object's tracking: a
     justified line's tracking then stretches every following line's
     cells to `pt + leaked_track` (and every `c.drawString` line after
     it). Symptom: a colophon's left column rendered at ~2.9 em cells
     and intruding into the right column's x-range (《孙膑和庞涓》 p114:
     the title's 28.75 pt tracking leaked into the 15.2 pt columns),
     while the first line after the justified one looked fine. Diagnose
     with `pdftotext -bbox` per-page: the cell width (distance between
     successive same-line word boxes) vs the line's font size — a ratio
     != 1.0 on a line whose config has no tracking is the leak. Fix:
     always call `setCharSpace(track if track else -0.001)` — the
     imperceptibly-negative sentinel forces a Tc emit (0.0 is skipped).
     Apply to EVERY text-drawing path: `05_typeset` (per panel line) and
     `07_special._draw_justified`; route `drawString`-style lines through
     a `textOut` wrapper that sets the sentinel (a fresh TextObject
     starts at 0 and emits no Tc, so it inherits the leak). Note the
     leak is invisible to the position QA (line x0 anchors are unaffected;
     only the line's right edge drifts), so the cell-width check above is
     the detector.

## Figure / geometry

12. **OpenCV frame detection fails on ~5% of pages** (faded box, heavy
    texture inside the box, box touching the image edge). Fallback:
    `02_geometry.py --page N --vl-frame x0,y0,x1,y1` using the VL image
    box; the skew is still estimated from the top strip. Record
    `frame_source: vl_fallback` in the geo file so QA can show it.
13. **Full-box frame restamp makes frames double-thick** (p115 class) and
    fools the figure QA (the output bbox grows past the mapped frame).
    Restamp per edge: measure each edge's mean brightness after
    enhance; only redraw edges whose mean > 150 (i.e. faded); if all
    four are dark, keep the original frame untouched.
14. **Figure QA with Otsu loses thin frames.** Use the 0.2/99.8
    percentile bbox of pixels < 240 in the rendered output figure region.
    Works for dense art and thin-line art alike.
14a. **Percentiles alone miss the restamped frame border** on light
    line-drawings (the border line carries <0.2% of the region's ink, so
    the 99.8 percentile lands on the art body — 《哪吒》p5: bottom edge
    6.5% short). And a text-line cut at "top of first text line − 15 px"
    clips the frame bottom when the book typesets text flush against the
    frame (《哪吒》p5: text starts 2 px below the frame line). Fix in
    `06_verify.output_figure_bbox`: measure ink **inside the expected
    mapped frame box** (±4 px), then extend the box to any strong
    full-span dark row/column (≥30% of span < 128 gray) — the frame
    border is the outermost strong line in the region, so min/max is
    safe.
14b. **Margin seals contaminate the text-band OCR** (《哪吒》: a red
    vertical owner's seal in the right margin of every panel page, read
    as short low-confidence garble — "KH", "KP", "UKC", "快", "亚大" —
    which cluster into fake lines or fuse into real ones). Two layers:
    (1) `textband_crop_box` clamps the OCR crop's x-range to 65 px
    beyond the frame edge when the geometry union overhangs the frame by
    >80 px (text overhangs the frame by at most ~52 px in both books,
    seals start ≥51 px outside — do NOT clamp to the frame edge itself,
    you would slice the last glyph); (2) `04_merge` drops low-confidence
    (<0.85) blocks sitting outside the frame's x span (real overhanging
    text reads ≥0.97 and survives). `ocr_to_page_scale` must use the
    same `textband_crop_box`, or the scale is computed from a crop that
    was never made and every bbox drifts.
15. **Frame top edge at y0=0** (box touches the scan edge, p115): deskew
    can slice the frame; the percentile QA tolerates it, but check the
    preview — if the top line of the frame is visibly clipped, nudge the
    frame bbox by a few px and re-run 03/05/06 for that page only.
15a. **Frame bottom fuses with the first caption line.** The fitted
    frame bbox bottom can sit INSIDE the blurry frame stroke (the fitted
    line is the stroke centre), so the crop (=bbox) swallows the first
    text line's top — the figure appears to contain body text. Worse, on
    some pages the "frame" bbox bottom is actually the first text line
    fused into the box (《哪吒》p20/p22/p25/p27/p28/p33). Two mechanisms:
    (a) `refine_frame_bottom` walks up from the bbox bottom to the
    sustained white gap, then requires the band below the gap to be
    single-line text (per-column std >0.5) and the band above to be a
    uniform frame border (per-column std <0.75 in its bottom 25px) — so
    it fires only when a real text line is trapped inside the box, not
    when the box bottom is merely blurry; (b) `frame_ink_extent` caps
    its downward scan at the first-caption ink top (`text_top` arg) so
    the crop can never reach the text. Machine-checked by
    `figure_text_sep` in `06_verify` (crop bottom <= text top + 2px;
    +6px tolerance for case-B flush pages where the frame feathering
    legitimately overhangs the text faint top).
15b. **Gap scan: white-gap-first, not advance-while-dark.** The old
    two-phase "advance while rows >=3% dark, stop at the first light
    row" failed when (a) the frame stroke is a blurry gradient extending
    many px below the bbox (p20: f~0.3, not a sharp >0.45 line), or
    (b) dark figure-corner content (water, corner blur) kept isolated
    rows >=3% inside the white gap (p11 bottom-right) — both gave false
    case-B or mislocated the text top. Now: find the first SUSTAINED
    white gap (>3 rows, dark-column coverage <0.14); stroke bottom =
    the row above it; text top = the first row after it where coverage
    is >=0.02 AND rising (mean of next 8 rows >=0.08, i.e. `_text_rising`)
    — the rising test catches the faint top of a CJK line (sparse
    stroke tips, coverage <0.14 so it reads "white") while rejecting
    isolated corner specks. The ORIGINAL-side and OUTPUT-side (gap
    fidelity check) MUST use the identical `gap_scan` with identical
    windows (60px white-gap search, 160px text search; output side
    scaled by `k=page_pt/page_px`).
15c. **Projection-band top can sit ABOVE the real text, truncating a
    blurry frame stroke.** Capping `frame_ink_extent` at
    `bands[0].bbox[1]` (the projection band top) clipped the frame bottom
    short on pages where the band top is above the true first-caption ink
    top (《哪吒》p7: band top 1555 vs real text top 1578 -> crop stopped at
    1553, 7px above the stroke core at 1554-1560 -> output gap 2pt too
    big). Fix: compute the figure/text gap FIRST (it only needs the
    frame bbox), then cap `frame_ink_extent` at the gap's
    `first_text_ink_top_px` (the true ink top), and post-cap the
    `frame_ink_bbox` bottom at `text_top - 1` so the crop never
    reaches the caption ink.
15d. **Faint band BELOW the frame line fuses into the fitted bbox.**
    On some scans (《孙膑和庞涓》 p70) the contour bbox bottom sits
    ~20 px BELOW the strong frame line: a faint band (3-8% dark, scan
    noise / halftone gradient) connects to the line and the morphological
    close absorbs it. Scanning down from the bbox swallows the band and
    the crop bottom lands in the visible figure/text gap (output gap
    ~7pt vs original ~26pt). Fix in `frame_ink_extent`: stop the
    down-scan at the first SUSTAINED faint run (>=3 consecutive rows
    <10% dark); when the row just below the bbox is already faint
    (0.03-0.10), the strong line's bottom is AT/ABOVE the bbox — find
    the lowest >=30% row scanning UP from the bbox (<=40 px), extend it
    over a short (<=6 px, >=10%) anti-alias tail, cap at the bbox; a
    symmetric guard pulls the crop top to the strong line's top when the
    row just above the bbox is faint. Signature when it regresses:
    `figure_text_gap` fails with got << expected while `figure` passes;
    compare `frame_ink_bbox` bottom vs the strong line's bottom on a
    150 dpi crop.
15e. **Never transform a color image.** A color illustration (cover
    plate, color figure) has NO pixel treatment: no grayscale, no
    illumination whitening, no level stretch, no sharpen, no binarize —
    place the original color crop/render 1:1. Detection:
    `lib.common.is_color_image` (p99 of per-pixel max(R,G,B)−min(R,G,B) >
    30; gray scans sit at ~0-3, the 《孙膑和庞涓》 cover at 202). Wiring:
    `03_enhance` main() routes color figures to `enhance_variant:
    "color_as_is"` (crop saved as-is to the usual `pNNN_bw.png` path,
    no restamp); `06_verify` skips `print_fitness` for that variant (its
    channel-spread <= 8 and paper-white >= 240 tests are grayscale
    tests); `07_special.enhance_full` mode `auto` places a color page
    as-is and a gray page through bw_clear; `_crop_render` keeps a
    color crop in color even when `color: false` (the flag selects the
    GRAYSCALE treatment for gray scans, not a color kill). Signature of
    the bug: a colorful cover rendered as a clean gray image — it looks
    "processed", not faithful.

## Vector variant (`03_enhance`, `variant: "vector"`)

The `vector` figure treatment (freshly-printed high-contrast B/W) has its
own set of traps, learned on 《龙子太郎》 p135 + an 8-page stress test,
then on p19 when a user review exposed the v1 detail loss:

1. **Illumination must be page-level, not local.** A local blur-division
   (flat = img / blur(img) * 255) over-flattens large halftone tone regions:
   a dark gray rock (raw ~110) divided by its own local mean (~110) reads
   white, erasing the tone. Instead fit a low-order (quadratic) 2-D
   polynomial to *paper pixels* (bright + locally smooth) and divide the
   whole page by it. `page_illumination()` returns `("poly2", I)` or a
   Gaussian fallback.
2. **One-direction binarization loses light detail.** v1 judged every
   pixel as “darker than a threshold = ink”. Detail comes in two classes:
   dark-on-light (most lines) AND light-on-dark (white wavy lines on dark
   mountains, crack highlights on dark rock). v1’s `fine | coarse` OR
   swallowed the second class — the user’s P19 review: water waves, the
   mountain path, cliff cracks, branch texture all gone, and “almost every
   frame too black”. Fix (v2): judge each pixel against its LOCAL tone
   (`blur(flat,31)`): `ink = flat<T_dark | (flat<tone−dev) & tone≥T_solid`;
   `solid = tone<T_solid`; `bright = flat>tone+bright_dev & tone<bright_tone`
   (force white); `bw=(ink|solid)&~bright`. Tune `T_dark`/`T_solid`/`dev`
   together; validate with the `ink_coverage` check (below).
3. **`coarse_thr` (v1) lived on the 0–255 blurred scale, not 0–1.** The
   v1 bug: `fine` was a boolean mask and `coarse_thr=195` was compared
   against it, so `coarse` was always false and no tone ever solidified.
   (History — v2 replaced the whole scheme; the lesson survives: never
   compare a 0–255 threshold against a boolean mask.)
4. **`restamp_frame` fill must be 0 for vector.** The bw_clear path
   restamps frame edges with `fill=40` (a dark gray) — fine for grayscale,
   but it injects a mid-gray value that fails the `vector_binary` check.
   Pass `fill=0` in the vector variant.
5. **rsvg-convert: `-d` alone distorts the aspect ratio.** `-d 144` alone
   gave 7368×3727 for a 3684×2795 pt SVG (height scaled 4/3 instead of 2).
   Pass BOTH `-d 144 -p 144` (pixel density + dpi). potrace SVG units are
   points (1/72 in), so 144 dpi = exactly 2× oversample.
6. **rsvg emits RGBA with a transparent background.** Converting straight
   to grayscale composites the alpha onto black (cv2 IMREAD_GRAYSCALE
   premultiplies; PIL `.convert("L")` does too) — the figure gets a black
   box. Alpha-composite onto white first, then `convert("1")`.
7. **potrace `-t` eats small halftone dots.** `-t 5` (the v1 default)
   drops components with area <~5 px² — exactly the size of isolated
   halftone dots. Use `-t 2` for the SVG master. (Thin LINES survive any
   `-t` — they are long connected components; only isolated specks/dots
   are at risk. Count `connectedComponents` before/after the potrace+
rsvg round-trip to verify losslessness — expect equal counts.)
8. **No median pre-filter.** `medianBlur(flat, 3)` erases 1-px lines: the
   median of a 1-px line’s 3×3 neighbourhood is paper. Denoise AFTER
   binarization instead (drop ink components < `speckle_px`).
9. **`vector_binary` QA**: figure must be true 1-bit (values ⊆ {0,255}).
   Catches leftover gray from restamp or a bad composite.
10. **`ink_coverage` QA (the “not too black” machine check)**: output ink
    fraction over the figure region vs the original scan’s ink-equivalent
    (`mean(255−gray)/255` over the same crop). Ratio must be in [0.55, 1.5].
    A retypeset carries roughly the SAME ink as the scan; a fresh book is
    slightly LIGHTER than its yellowed scan (ratio ~0.75–0.9 is typical
    and correct). Fails high = over-solidified tone (“too black”); fails
    low = detail erased.
11. **Degrade, don’t crash.** Missing potrace/rsvg → fall back to bw_clear
    with a warning.
12. **Match the reference font before tuning the figure.** The “look” the
    user wants is figure treatment AND font combined. Run `pdffonts` on
    their manual reference first (the 《龙子太郎》 reference used
    `LXGWWenKaiMono`, not the skill default Regular).

## Build system

16. **GPU memory: one model owner at a time.** The resident MCP server
    keeps PaddleOCR + PaddleOCR-VL loaded on the T4. Before
    `08_batch_infer.py` (which loads both models in-process), `pkill -f
    paddleocr_mcp_server`. After the batch, restart it (the agent's MCP
    config auto-respawns on next tool call).
17. **Idempotency.** Every stage skips/writes deterministic outputs;
    `08_batch_infer` skips existing `data/vl|ocr/pNNN.json`. After
    editing merge/typeset code, re-run only the pages you need
    (`batch_runpages.sh 10 45`) before the final full pass.
18. **Parallel run_page** (`xargs -P 6`): all stages are CPU-bound
    (OpenCV/reportlab) except reading files — safe to parallelize; keep
    logs per page (`/tmp/rp_$p.log`) or one bad page hides in a wall of
    output.
19. **pdftoppm file naming** zero-pads to the document's page-count
    width (70 pages → `-01`…`-70`; 139 → `-001`…`-139`). Always `ls`
    before assuming a name, or glob.
20. **GS string options use `-s`, not `-d`**:
    `-dColorConversionStrategy=Gray` errors with "Invalid value for
    option … use -sNAME= to define string constants".
20a. **reportlab caches embedded images by file path.** Drawing two
    *different* PIL images through the *same* temp filename silently
    re-embeds the first one for the second `drawImage` (《哪吒》p2: the
    right rectangular seal rendered as a squashed copy of the left
    circular seal — same pattern, wrong aspect). Give each image a
    unique temp path (`07_special._place_image` uses a content-hash
    name); detect by counting `/Subtype /Image` XObjects in the page
    PDF — one fewer than expected = a reused image.

## Verification & review

21. **verify `text` check is content-exact** (normalized-equal per line
    vs dataset). A verify FAIL on `text` almost always means the merge
    produced a different line split than the dataset says — diff
    `data/layout/pNNN.json text.lines[].text` against
    `pdftotext` output of that single page PDF; the dataset is the
    source of truth, fix the producer, not the reporter.
22. **200 dpi downsampled GS output is visually lossless for this
    workflow** (line art at 200 dpi; 250 dpi source → 200 dpi output).
    Keep the raw merged file (250 dpi, ~340 MB for the reference book)
    alongside the optimized one (~58 MB) — the optimized file is what
    you distribute.
23. **Final human-pass items the machine checks miss**: wrong seal
    position, number inside parentheses rendered with a stray space,
    colophon right-column misalignment, cover watermark you forgot to
    remove. A 5–8 page contact sheet of the final book vs the original,
    plus `pdftotext` of two full pages, catches these.
24. **Colophon numbers are where OCR is weakest** (small type, dense
    rules). Always read 书号/印数/定价 off a ≥150 dpi crop at high zoom
    and double-check digits (the reference run had a 8067·856 vs 8087·855
    mix-up caught only by zooming the original).

24a. **Two machine checks that catch the layout regressions this project
    actually hit:** `figure_text_sep` (the figure crop bottom must not
    pass the first caption ink top: `crop_bottom <= text_top + 2px`, +6px
    for case-B flush pages) catches "figure swallows body text"; and
    `panel_no_single` (a panel number appears at most once, and only at
    the start of line 0) catches "number duplicated at the end of its
    line". Both are cheap (no render) and run on every page in
    `06_verify`, so a regression is caught in the batch, not by the
    human pass. Note the dataset line field is `simplified`/`raw`, NOT
    `text` — reading `ln["text"]` raises `KeyError` in any verify/merge
    code that touches lines.
25. **Figure QA tolerance must be span-relative, not coordinate-relative.**
    Judging each corner by `abs(err)/max(1, |exp|) < 4%` works when the
    figure box lives mid-page, but in source (1:1) mode a box a few pt
    from the page edge makes 4%-of-coordinate a sub-pixel demand that
    quantized 150 dpi measurement trips (false `figure` FAILs on
    otherwise-perfect pages). Judge each corner against the box's own
    span: `abs(got-left - exp-left) / box_width < 4%` (and analogously
    right/top/bottom with the span in that axis).

## Page-layout fidelity (margin design)

25. **The page margin is part of the book.** Some books (《哪吒》) print
    a decorative strip in the right margin on every panel page: a
    full-height page-edge line, a running-title tab (vertical black box
    with the book title in seal script), notch bars, and an owner's red
    seal. Dropping it makes the retypeset page visibly narrower/different
    from the original even though frame+figure+text are correct. The fix
    (`03_enhance.crop_margin_strip`, gated by config `margin_strip`,
    default on): crop the strip from just past the figure's outer ink
    extent (`frame_ink_bbox[2] + 2`, zero overlap with the figure) to the
    page edge of the COLOR deskewed render, whiten per-channel
    (illumination division + level stretch per RGB channel — the red seal
    keeps its color, the yellow cast goes), and redraw it 1:1 via
    `geo.margin_crop_box` in `05_typeset` (draw order: figure → margin →
    text, so text overhanging the frame stays on top). It auto-detects
    blank margins (no ink <140 and no reddish pixels → skip), so it is
    safe to leave on for books without a margin design.
26. **Do not crop the margin strip at the frame bbox edge.** The frame
    line is thick/blurry and the fitted bbox sits inside its stroke;
    starting the strip at `frame[2]` overlaps the figure's restamped frame
    edge. The strip is color (per-channel noise), and any overlap lands
    inside the figure QA region — crop at `frame_ink_bbox[2] + 2` instead.
27. **`print_fitness` region box semantics.** The expected/measured figure
    boxes are `(x0, y0, x1, y1)`, NOT `(x, y, w, h)`. The old code
    unpacked them as x/y/w/h, so the measured region silently extended to
    `x0 + x1` (past the right edge). That was harmless while the right of
    the figure was blank page, but the moment a color margin strip landed
    there, `channel_spread_p999` jumped 9–30 (>8 threshold) on ~half the
    book. Fix: unpack as `x0r, y0r, x1r, y1r` and slice
    `img[y0r+20:y1r-20, x0r+20:x1r-20]`, and measure against the EXPECTED
    box (the measured one can be extended by the strip's edge line).
28. **Slice-shape bug hides as "0 pages affected".** `a.shape` is
    `(height, width)`; writing `W, H = a.shape` (swapped) makes a
    right-margin band slice `a[H//4:3*H//4, x0:W-5]` have width 0 →
    `mean()` of the empty slice is `nan`, `nan > 0.02` is False, and a
    per-page scan silently reports 0/66 pages have margin ink while the
    margin clearly does. Print the slice `.shape` once, and remember
    empty-slice mean is nan, not 0.

## Merge / GS

29. **The `/Matrix` patch runs in-place on the single-page PDFs**
    (`out/pages/p*.pdf`), not on the merged file. Order: typeset →
    `patch_matrix.py` → `pdfunite` → GS. Re-running typeset overwrites the
    single pages, so re-patch before the next merge (a second run prints
    `patched 0 image(s)` — that's the idempotency signal you patched the
    same files again, NOT that the merged file has matrices).
30. **GS `pdls` device may not exist** in stock Ghostscript builds
    (`Unknown device: pdls` / `rangecheck in .putdeviceprops`). The
    downsample settings work identically on `-sDEVICE=pdfwrite` with
    `-dPDFSETTINGS=/prepress` — that's the portable choice.

## Special pages (07_special)

31. **Measure special-page coordinates by pixel projection, not OCR
    bboxes.** For full-page retype (back-title tables, summary pages),
    OCR bbox y-values were systematically off by 15–30 pt on the 刘秀
    pair (scans with rules/tables confuse the detector, and the bbox→pt
    scale factor drifts). Pixel-projection row/column bands on a 125–300
    dpi render of the original page (`ink < 150–170` threshold, band
    split on zero-ink rows) give exact ink top/bottom/left/right for
    every line and character. Always re-derive `pt = ink_h*SCALE/0.914`
    and the baseline from the measured ink top.
32. **Whiteout boxes leak at line ends and logo corners.** The original
    printed line often extends 10–30 pt PAST the table's right rule
    (loose justification), and watermark logos (du1001) sit at a
    different y on every page. After the first render, pixel-scan the
    OUTPUT render in the whiteout zones: any surviving ink means the box
    was too small. Iterate box → render → scan until `n=0` before the
    final merge.
33. **`images` are drawn AFTER `whiteout_boxes`; use `background` for
    the full-page plate.** A retype page with a full-page processed
    background needs the order: background → whiteout → text → overlay
    images (crops/seals). Putting the full-page plate in `images` makes
    the whiteout erase the crops just placed, or (with
    `draw_images_first`) leave the original text visible. The `background`
    key in `07_special.py` encodes the correct order.
34. **`img()` places by top-center, not center-center.** `_place_image`
    anchors at top-center: `top_pt = ay(center_y) - h*SCALE/2`. Using
    the measured center as `top_pt` shifts the crop half a crop-height
    down (30 pt for a 60-pt logo).
35. **Yellowed-paper special pages need `force_gray` on every crop.**
    `is_color_image()` flags a yellowed crop as color purely from paper
    chroma; the crop then keeps its yellow box around the ink. With
    `color: false, force_gray: true` the crop goes through the grayscale
    enhance (bw_clear on yellowed books) and comes out clean black-on-
    white. Color seals/logos keep `color: true`.
36. **Don't trust `minAreaRect` angle metadata** (absorbed from the
    paddleocr-digitize S3 lesson; our 02_geometry already follows the
    safe pattern). OpenCV's rect angle is ambiguous across versions and
    aspect ratios — a real 0.5° tilt can come back as 89.5°. Derive
    skew from the box TOP EDGE instead: `degrees(atan2(p1.y-p0.y,
    p1.x-p0.x))` on the ordered corner pair (02_geometry does exactly
    this on the frame polygon). The box itself is fine for the warp;
    only the metadata angle lies.
37. **Dot-glyph QA false alarm: U+00B7 → U+2219.** LXGW (and other CJK
    fonts) map U+00B7 (·) to the U+2219 (∙) glyph; text extracted from
    the output PDF no longer string-matches the data layer → "missing
    chars on 8 pages" that are all book-number dots. Normalize
    U+2219/U+2027/U+30FB → U+00B7 on both sides before any text compare
    (06_verify check #11 does this).
38. **Character-level QA needs `rawdict`, not `dict`.**
    `get_text("dict")` MERGES adjacent same-baseline lines (a page
    number "33" + 20 px tracking joins the caption into one "line") →
    line-level bbox comparison mass-misfires. Use `rawdict` chars;
    strip whitespace before building probes (empty-string elements
    break 4/6-char probe windows, e.g. "62 龙子…" → probe "62龙子"
    never matches); if a probe hits multiple candidates (repeated
    phrases are real book text too — eyeball before suspecting OCR),
    take the candidate whose union bbox is closest to the original
    line.
39. **The rescue recipe depends on book type.** The strip
    stretch(p10..p99)+2x INTER_CUBIC recipe targets light-ink / yellowed
    scans (A/B'd: beats Otsu — which kills light ink entirely — and
    CLAHE). On a CLEAN digital scan (white paper, crisp ink) pure
    upsampling fragments det (one line → 12 segments, quote lines lost
    whole) — use the raw strip there (`rescue.stretch: false`). And on
    dense-halftone pages, if per-line rescue still garbles, STOP
    auto-rescue and manually proofread against the source; do not
    brainstorm a fourth recipe on the same page.
40. **High-confidence homophone mis-OCRs (score ≥ 0.98).** PaddleOCR has
    STABLE biases for homophone/shape-similar chars (孙膑→孙宾 16×,
    美街→美术) that the score cannot catch. Build a book-specific
    lexicon and apply it AFTER all OCR + rescue and BEFORE typeset;
    then re-verify PDF text counts before finalizing (a cached plan /
    stale dataset will silently undo the fix otherwise).
