# Case study: 《龙子太郎》 (1984, 河北美术出版社)

The reference run this skill was built from. Source: `234.pdf`, 139
scanned pages, 1024×768 pt landscape, yellowed pages, grayscale line art
in a frame box + 2–4 lines of text below.

## Survey findings

- p1 color cover (kept as-is), p2 vertical title page, p3 simplified-Chinese
  abstract, p4–p137 standard panels (134), p138 colophon, p139 watermark
  page (dropped → 138 output pages).
- Panel number rule: `panel_no = page - 3` (verified on ≥5 pages).
- Number format: bare `9` + wide gap.
- Reference style: `example_page1.pdf` (A4, white, ~20 mm margins, figure
  top, text bottom, both-edges aligned).

## Results

- 134/134 panel pages pass `06_verify` (figure <4 % drift, line
  content/position, panel anchor, font embedded, grayscale, paper
  whiteness ≥ 252 mean; `vector_binary` on the vector variant).
- 5 batch rounds to converge (93→104→106→131→134); each round fixed a
  general failure class (see `../../references/pitfalls.md`):
  line clustering (y0 rule), over-tall-line splitting, per-line width
  fit, per-edge frame restamp, percentile figure QA, paren/bare number
  normalization.
- Special pages: p1 cover as-is; p2 title page (emblem = largest ink
  component, de-speckled); p3 abstract retype (hanging punctuation);
  p138 colophon (blue double-column box, title tab breaking the top
  edge, right column aligned to left rows 2/3/4).
- v0.5 (2026-10-30): figure treatment switched from `bw_clear` (grayscale
  halftone) to the **`vector` variant** to match the user's manual
  GIMP+Scribus reference (freshly-printed high-contrast B/W: solid tone,
  crisp lines, sparse halftone dots kept, true 1-bit). Font switched to
  `LXGWWenKaiMono` to match the reference (`pdffonts test.pdf` →
  `VCZKYK+LXGWWenKaiMono-Regular`). All 134 pages re-passed with the
  new variant + `vector_binary` check.
- v0.5.1 (2026-10-30): user review of P19 — v1 binarization lost light
  detail carved into dark tone (white water waves on the dark mountain,
  cliff-crack highlights, faint wave lines) and over-solidified mid-tones
  ("too black"). Replaced with the **v2 local tone-relative binarization**
  (`T_dark 190 / T_solid 150 / dev 25 / bright_dev 45 / bright_tone 185 /
  tone_kernel 31`, no median pre-filter, `potrace_t 2`) + new `ink_coverage`
  QA check (0.55–1.5×, typical ~0.76 here). All 134 pages re-passed.
- **Landscape A4**: the source scan and the user's manual reference
  (`test.pdf`) are landscape (841.89 × 595.28 pt). The first landscape
  attempt had portrait proportions; fixed by setting `config.a4` to
  landscape and rebuilding all pages (figures re-typeset only — the v2
  images were orientation-independent). Special pages (p2/p3/p138) rebuilt
  from **measured 300 dpi coordinates** (OpenCV row/column projections on
  the original, ×0.7751 to A4 pt, hard-coded), including the colophon's
  slightly slanted box drawn as a path and the title tab straddling the box
  border.
- Final outputs: `out/234_retypeset.pdf` (138 p, landscape A4) and
  `out/234_retypeset_opt.pdf` (~71 MB, GS). v1 kept as reference:
  `out/234_retypeset_v1.pdf` / `_opt_v1.pdf` / `out/pages_v1/`.
- Outputs: `out/234_retypeset.pdf` (138 p, A4, ~98 MB, 250 dpi) and
  `out/234_retypeset_opt.pdf` (~55 MB, GS 200 dpi, ~8,900 selectable
  chars, LXGWWenKaiMono embedded). Vector master per figure:
  `data/images/pNNN.svg`.

## Facts that had to be read off the scan (high-zoom)

- 统一书号 **8087·855** (OCR suggested 8067·856 — wrong), 印数 232,000,
  责任编辑 张秋菊, 封面绘者 陆鑫, 印刷 河北新华印刷三厂.
- Lesson: colophon digits are the weakest OCR target; always zoom the
  original before writing them into the config.
