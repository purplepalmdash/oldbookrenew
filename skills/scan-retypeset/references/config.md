# Config reference (`pipeline/config.json`)

The pipeline is 100% config-driven: nothing book-specific is hardcoded
in the scripts. `lib/common.py` resolves ROOT (the book project root)
and reads this file once; every stage consumes the constants it exports.

ROOT resolution (first match wins): `$RETYPESET_ROOT` → grandparent of
`pipeline/lib/common.py` (scripts copied into `<book>/pipeline/`) →
`$PWD`. The root must contain `pipeline/config.json`.

## Top-level fields

| key | type | required | meaning |
|---|---|---|---|
| `source_pdf` | str | yes | scanned book PDF, relative to ROOT |
| `total_pages` | int | yes | page count (survey with `pdfinfo`) |
| `drop_pages` | [int] | no | 1-based pages removed from the output (watermark pages etc.) |
| `panel_pages` | [int, int] | yes | inclusive range of standard panel pages |
| `panel_no_offset` | int | yes | panel number = page − offset (the anchor check in 04/06) |
| `panel_no_format` | `"bare"` \| `"paren"` \| `"dot"` | yes | canonical lead-number form: `9␣␣text`, `（9）text`, or `9. text` (number + period + space, 《巴黎圣母院》); extraction accepts all three styles on input |
| `no_panel_no` | [int] | no | panel-range pages that print no leading number (e.g. an unnumbered 序幕 run); the number anchor check is skipped for them (line 0 must simply not start with a number) |
| `render_dpi` | int | no, default 250 | pdftoppm dpi for `data/pages/pNNN.png` |
| `a4` | {width_pt, height_pt, margin_pt} | yes | output page geometry. **Orientation must match the source scan** — measure the source page box (`pdfinfo`) and any user-made reference PDF before writing this. Portrait A4: 595.28 / 841.89 / 56.7; landscape A4: 841.89 / 595.28 / 56.7 |
| `font` | str | yes | TTF path (embedded as a subset) |
| `font_name` | str | no | PDF font name; QA checks `font_name.split('-')[0][:4]` in `pdffonts` |
| `font_pt` | float | no | nominal body size (start hint only; per-line sizing wins) |
| `ink_em` / `asc_em` | float | no | measured CJK ink height / ascent in em of the original type (LXGW WenKai: 0.914 / 0.818); drive the initial font-size estimate in 05 |
| `enhance` | {variant, ...} | no | `bw_clear` (de-yellow + whiten + sharpen + contrast) is the default for GRAYSCALE scans; `vector` is the "freshly printed" B/W variant (see `enhance.vector` block in `config.example.json`). **Color-figure rule**: with the default variant, a figure/page with meaningful color (p99 of per-pixel max(R,G,B)−min(R,G,B) > 30) is auto-detected and placed 1:1 in its ORIGINAL COLOR — no grayscale, no whitening, no stretch, no sharpen, no binarize (`enhance_variant: "color_as_is"`); the `print_fitness` grayscale test is then skipped for that page |
| `margin_strip` | bool | no, default true | crop + redraw the printed running design in the page margins 1:1 — right side (edge line / running-title tab / red seal) AND left side (small emblems, e.g. the 刘秀 horse mark, stored as `pNNN_margin_left.png` / `margin_crop_box_left`). Both auto-detected per page: blank margins are skipped (area rule, plus a thin full-height dark-column check so a 1-2px page-edge line on a wide margin is not missed), so leave it on for books without margin design; set false to force off |
| `watermark_boxes` | [[x0,y0,x1,y1]] | no, default [] | **watermarks added by later (re)scanners** (a logo/slogan stamped on every scan — e.g. 刘秀 下册 bottom-left horse + “天岸马奇通人中龙” + thin underline). Boxes are normalized page fractions (of render W/H). Two effects: (1) `03_enhance` paints each box out of the deskewed page (median ring fill + vertical-line-column restore) BEFORE figure / margin-strip crops, so neither crop carries the watermark; (2) `04_merge` drops any merged line whose bbox **centre** is inside a box AND whose left edge is in the left 60% of the box’s x span (real caption lines ending in the same corner start further right). A scan-time overlay is identical on every page — measure the ink bbox once by pixel projection on a few pages and one box covers the book. For special pages add the same box to their `whiteout_boxes`; a watermark sitting ON a colored banner/plate image goes in that image’s `erase_ink` (pixel-level dark-ink replacement with local non-ink mean, see below) |
| `mcp` | {server_dir, ocr_language, server_venv_python} | yes | directory with `paddleocr_mcp_server.py`; `server_venv_python` is the interpreter that runs `08_batch_infer.py` |
| `special_pages` | map | no | page → handler config, see below |

## `special_pages` handlers

Keys are 1-based page numbers (string). Each value is a dict; `handler`
selects the function in `07_special.py`.

### `cover_as_is`

```json
"1": {
  "handler": "cover_as_is",
  "whiteout_box": [0.02, 0.02, 0.98, 0.09]
}
```

Places the full original page render (grayscale or color as scanned)
centered on A4. Optional `whiteout_box` (normalized fractions of the
page) fills a watermark strip with the median color of the border ring
around it and re-places the cleaned image.

### `enhance_full`

```json
"1": { "handler": "enhance_full" }
```

Full-page placement for covers/illustrated plates. Color-figure rule
(auto, default `mode: "auto"`): a COLOR page (p99 per-pixel chroma > 30)
is placed 1:1 AS-IS in its original color — no pixel treatment. A
grayscale page gets the bw_clear treatment (illumination-division
whitening + level stretch + unsharp mask). Force with `mode: "as_is"`
or `"enhance"`.

### `retype`

Full free-form re-typesetting of a page (frontispiece, synopsis, …).

```json
"2": {
  "handler": "retype",
  "y_start": 150,
  "layout": [
    ["标题文字", 30, null,  {"before": 0}],
    ["副标题", 14, null,    {"before": 16}],
    ["左对齐行", 14, null,  {"align": "left", "x_off": 90, "before": 34}],
    ["红色行", 12, [0.75, 0.05, 0.05], {"before": 20, "align": "left", "x_off": 90}]
  ],
  "images": [
    {"box": [0.03, 0.66, 0.25, 0.94], "x_pt": 85, "top_pt": 570, "size_pt": 130, "color": true},
    {"box": [0.70, 0.63, 0.805, 0.92], "x_pt": 418, "top_pt": 547, "size_pt": 150, "color": true}
  ]
}
```

- `background` (optional): the full-page (or any-region) BASE plate — a
  processed scan of the original page (yellowed paper texture, ornate
  rules, corner emblems) drawn FIRST, under everything. Then
  `whiteout_boxes` erase the original printed text, then the re-typed
  `layout` text, then `images` crops/seals go on top. This is the
  standard back-title / synopsis / title-page recipe. Do NOT put the
  full-page plate in `images` — with the default order the whiteout
  would erase the crops just placed. `background` accepts the same keys
  as `images` entries, plus `force_gray: true` (with `color: false`):
  force the grayscale enhance even when `is_color_image()` flags the
  crop as color (yellowed-paper scans keep a yellow box around the ink
  otherwise).
- `layout` rows: `[text, pt, color|null, opts]`; `opts.before` = pt gap
  from the previous row, `align` defaults to center, `x_off` shifts from
  center (positive = right). Lines are drawn top-down from `y_start`.
  Strict-fidelity mode: `opts.x` (absolute left x, pt), `opts.baseline`
  (absolute baseline, pt from the TOP of the page) and `opts.width`
  (justify to exactly this width) place the line at its measured
  original coordinates — use for title/synopsis/colophon pages where
  each line's span was measured from the scan. For CJK justified lines
  the right size is `pt = measured_width / char_count` (keeps the exact
  original line span; tracking is only added when width exceeds
  natural; lines mixing CJK with digits/Latin use advance-weighted gaps
  (slot ∝ advance) instead of uniform tracking — see the typeset note
  in SKILL.md).
  **Sizing rule for uniform text blocks** (synopsis paragraphs): one `pt`
  for the whole block — the MEDIAN of the measured line ink heights —
  not one size per line. Per-line sizing turns per-line ink jitter
  (rows with tall glyphs measure 8-10% taller) into a visible size step
  (《巴黎圣母院》 p3: a 副/洛/·-heavy row measured 55 px vs 51 px median
  → 16.6 pt vs 15.4 pt). Per-line `x`/`width`/`baseline` stay
  line-accurate; only the size is uniform.
- `draw_images_first: true` draws `images` BEFORE the text (background
  plates — e.g. an ornate title-page frame). Default false keeps images
  on top of text (seals over type). With a full-page background image,
  set `whiteout_boxes: [[x0,y0,x1,y1], ...]` (normalized SOURCE-page
  fractions, mapped with the same contain-fit): the boxes are filled
  white after the image and before the text, erasing the original
  printed text under the re-typeset lines (《巴黎圣母院》 p2 title page).
- `images`: `box` is a normalized crop of the page render
  (`data/pages/pNNN.png`); placed with top-center at `(x_pt, top_pt)`
  scaled so the longer side ≤ `size_pt` pt, aspect preserved.
  `color: false` runs the crop through `03_enhance.enhance` (add
  `force_gray: true` for yellowed-paper crops). NOTE the anchor is
  TOP-center: when reproducing an element at its original position,
  `top_pt` must be the TOP edge of the element in output coordinates
  (`ay(measured_top)`), not its center — using the center shifts the
  crop half its height down (《刘秀》 p2 logo: 30 pt off).
  `erase_ink: [[x0,y0,x1,y1], ...]` (optional, normalized PAGE fractions)
  removes a watermark painted ON TOP of the crop (e.g. the 刘秀 下册
  horse + slogan over the p2 banner): each dark pixel (min channel < 100)
  inside a box is replaced pixel-by-pixel with the mean of the surrounding
  non-ink pixels (31 px window, integral-image fast path) — unlike a flat
  median box fill, the surrounding art is preserved. Use tight boxes:
  everything dark inside a box is erased, so a loose box smears a flat
  patch over banner art.
  `erase_tmpl: {"page": N, "boxes": [[...], ...]}` (optional) — TEMPLATE
  UN-BLENDING, the PREFERRED method for a scan-time watermark on colored
  art (pixel-identical across pages): the same PAGE-fraction box on page
  N where the watermark sits on PLAIN PAPER gives the per-pixel ink
  absorption `a = (P_c - T_c)/P_c` (P = local paper color), and
  `base = observed/(1 - a)` restores the underlying art where the
  watermark strokes ran — only the watermark shape is removed; the banner
  painting survives (erase_ink by contrast erases ANY dark ink in the box,
  e.g. part of the painting itself). Requires a plain-paper template page
  with the watermark and no other ink under the boxes. See
  references/watermark.md.
- Without `layout`, an OCR-driven fallback is used (`ocr`, `title_idx`,
  `title_pt`, `body_pt`, `lead_factor`) — prefer explicit layout: it is
  verifiable against the original.

### `title_page`

Vertical-title frontispiece (used by 龙子太郎 p2):

```json
"2": {
  "handler": "title_page",
  "title": "龙子太郎", "title_pt": 64, "title_x_offset": 80,
  "subtitle": "（全一册）",
  "emblem": {"region": [0.25, 0.10, 0.75, 0.45], "color": true, "size_pt": 260,
             "top_margin_pt": 140, "center_x_offset": 40},
  "credits": [["原著", "松谷美代子"], ["改编", "张宇平"], ["绘画", "陆新森 吕振敏"]],
  "credit_pt": 15, "credit_col_gap": 40,
  "publisher": "河北美术出版社", "publisher_x": 50, "publisher_pt": 14,
  "note": "……"
}
```

`emblem.region` is a normalized page region; the largest connected ink
component inside it is cropped (31 px morphological close, 30 px pad)
and placed. Credits render as right-to-left vertical columns.

### `colophon`

Boxed two-column publication-info page:

```json
"69": {
  "handler": "colophon",
  "header": [["责任编辑：李建琛", 11], ["哪　　吒", 15], ["李廷柱 蒙南生 改编 宋剑锋 绘画", 11]],
  "box_w": 470, "box_h": 105, "box_top_margin": 400,
  "left": ["广西人民出版社出版", "南宁市红旗印刷厂印刷", "787×1092 1/64　印张1.0625"],
  "right_at": {"0": "广西新华书店发行", "1": "1983年6月第1版第1次印刷", "2": "印数：1—133000册"},
  "x_right": 290, "lead": 17.5, "pt": 10.5,
  "footer": "书号：8113·849        定价：0.10元"
}
```

- `header` rows are centered above the box (`[text, pt, color?, before?]`).
- `left` lines and `right_at` (keyed by left-line index) form the two
  columns inside the box; `footer` is centered below.
- `color` (optional, default black) tints the box border + text
  (龙子太郎 used blue).

## Per-book tuning knobs that recur

- **panel_no_offset**: derive from two or three pages (number = page −
  offset), not from one.
- **drop_pages**: watermark pages are usually the *last* page and often
  a different page size (check each render's dimensions, not pdfinfo).
- **whiteout_box / emblem.region / images.box**: always verify against
  the actual 250 dpi render of that page (mixed page sizes make
  normalized fractions easy to get wrong).
- **ink_em/asc_em**: measure once per font (render a glyph row, find the
  ink bbox); the same values work for any book using that font.
