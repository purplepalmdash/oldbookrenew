# Case study: 《巴黎圣母院》 (连环画, 213 页横版扫描)

The third reference run; added the dot-style numbering and full-page
background re-type features. Source scan: 213 pages, landscape, 202
standard panels with **dot-style numbers** (`31. …`) plus 6 unnumbered
序幕 pages and an ornate full-frame title page.

## Survey findings

- Number format: `dot` (`panel_no_format: "dot"`); panel number rule
  `panel_no = page - offset`.
- 6 unnumbered prologue pages → `no_panel_no` list (extraction is
  style-agnostic; the setting fixes the canonical re-typeset token).
- Original-book number misprints kept faithfully via
  `panel_no_exceptions` (see `config.md`).
- Title page: full-frame background plate under re-typeset text →
  `retype.draw_images_first` + `whiteout_boxes` (v0.6: replaced by the
  `background` key — see `liuxiu` case for the current form).
- Right-margin running design on every page → `margin_strip`.

## Results

- **208/208 panels pass** automated QA; ~84 min wall-clock incl. survey
  (T4 GPU, see `../../references/performance.md`).
- Features contributed to the skill by this book: `panel_no_format: "dot"`,
  `no_panel_no`, `panel_no_exceptions`, `whiteout_boxes` +
  `draw_images_first` (later superseded by `background`).
