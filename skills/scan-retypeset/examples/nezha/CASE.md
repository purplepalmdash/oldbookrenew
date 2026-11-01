# Case: 《哪吒》 (112哪吒, CC下载站 scan)

Second-book validation of the skill — a same-family book (1980s–90s
Chinese lianhuanhua, scanned landscape, Aspose re-encoded) with page
layout different enough from the reference to exercise every config
knob.

## Source

- 70 pages, page box 767.9 × 576 pt (landscape scan), PDF 1.3,
  Aspose.Pdf 10.1.0 producer (not a direct flatbed scan — text is
  already partly digital, but panels are raster).
- p70 is a small 240 × 192 pt page that is a near-blank watermark page
  (green logo + red domain + slogan, top-right) → `drop_pages: [70]`.

## Page map (survey result)

| pages | type | handler |
|---|---|---|
| 1 | color cover (art panel + 哪吒 title) | `cover_as_is` |
| 2 | frontispiece: title, credits, red warnings, 2 seals | `retype` (explicit layout) |
| 3–68 | 66 standard panels | panel pipeline |
| 69 | colophon (blue boxed 2-column) | `colophon` |
| 70 | watermark page | dropped |

Panel numbering: fullwidth parens with inner spaces — `（ 64 ）  哪吒…`.
`panel_no_format: "paren"`, and the number = page − 2
(`panel_no_offset: 2`), i.e. p3 = （1） … p68 = （66）.

## Notable book-specific issues (all handled by the pipeline now)

1. **Right-margin owner's seal on every panel page** — a red vertical
   seal outside the frame box, read by line-OCR as short low-confidence
   garble (`KH`, `KP`, `UKC`, `快`, `1`, `亚大` …). Fixed by
   `textband_crop_box` (crop clamp to frame+65 px) + `04_merge`
   margin-block filter. 24 contaminated blocks on 24 pages, all gone
   after re-OCR; text verify went 52/66 → 66/66.
2. **Text overhangs the frame right edge** by up to ~52 px on ~10 pages
   (the original typesetting runs the last glyph past the frame line).
   Never clamp the OCR crop to the frame edge itself.
3. **Light line-drawings**: the restamped frame border carries <0.2% of
   the figure's ink, so the old percentile-only figure QA failed on
   p5/p28 (bottom edge 4–6.5% short). `output_figure_bbox` now measures
   inside the expected box and extends to strong full-span dark
   rows/columns.
4. **Frontispiece (p2) retype**: 10 text lines (black + red warnings),
   2 seal images. `retype` handler with explicit `layout` + `images`.
   Hit the reportlab per-path image-cache bug (see pitfalls #20a) —
   both seals went through one temp path and the right seal rendered as
   a squashed left seal.
5. **Colophon (p69)**: standard blue box/tab two-column, `right_at`
   alignment map. 书号 8113·849, 印数 1—133000, 定价 0.10 元,
   发行 广西新华书店 — all read off high-zoom crops (pitfall #24).
6. **Right-margin design strip on every panel page** — the book prints a
   full-height page-edge line, a vertical black running-title tab (哪吒 in
   seal script), notch bars, and an owner's red seal in the right margin
   (see the source scan). The first pass dropped it (the frame+figure+text
   were right, but the page was visibly narrower than the original). Fix:
   `03_enhance.crop_margin_strip` crops the strip from just past the
   figure's outer ink extent to the page edge of the COLOR deskewed
   render, whitens per-channel (yellow out, red seal stays red),
   auto-skips blank margins; `05_typeset` redraws it 1:1 from
   `geo.margin_crop_box` (draw order figure → margin → text). Regression
   caught on the way: the `print_fitness` QA region had a box-semantics
   bug (pitfalls #27/#28) that surfaced only once color landed right of
   the figure. Layout fidelity pass: ~15 min wall-clock, 66/66 held,
   gap error unchanged (median 0.19 pt / max 1.15 pt).

## Layout-consistency round (figure/text separation, panel number, font size)

A side-by-side with the original exposed three regressions the margin
pass didn't cover:

7. **Figure crop swallowed body text.** On 6 pages (p20/p22/p25/p27/p28/p33)
   the fitted frame bbox bottom was the first text line fused into the
   box (blurry frame border), so the figure crop cut into the caption.
   Fixed by `refine_frame_bottom` (recover the true frame bottom from
   the white gap + text-band shape) + `frame_ink_extent` capped at
   the gap's `first_text_ink_top_px` + post-capping the crop at
   `text_top - 1`. New machine check `figure_text_sep`. Also fixed the
   gap measurement itself: rewrote `gap_scan` to be white-gap-first
   (pitfall 15b) so blurry frame strokes and dark figure corners no
   longer produce false case-B.
8. **Panel number duplicated at the end of its line ("（23）…(23)").**
   The number block sorted after the text it prefixes (y0 ordering).
   Fixed by sub-row grouping in `04_merge` (pitfall 9a). New machine
   check `panel_no_single`.
9. **Per-line font size jitter.** The first line rendered smaller than
   the rest. Fixed by a unified per-page font (median, full-line cap,
   0.6 floor; pitfall 11a).

Result after this round: 66/66 pass (including the two new checks),
figure↔text gap median error 0.19 pt (max 1.06 pt), no figure contains
body text, no duplicated panel numbers, one font size per page.

## Result

- 69-page A4 book: `out/哪吒_retypeset.pdf` (~68 MB, 250 dpi raw, with
  `/Matrix` so GS can downsample) and `out/哪吒_retypeset_opt.pdf`
  (~34 MB, GS 200 dpi — distribute this one; the margin strips keep the
  red seal in color).
- 66/66 panel pages pass `test_pages.py all` (now including the
  `figure_text_sep` and `panel_no_single` machine checks); ~3,920
  selectable text chars; LXGWWenKai embedded (subset); figure↔text gap
  median error 0.19 pt (max 1.06 pt) vs the original scan; one unified
  font size per page; no figure crop contains body text; no duplicated
  panel numbers.
- Pipeline wall time (survey done → distributed PDF): **~12 min** on the
  T4 machine for the core build, **+~15 min** for the margin-fidelity
  pass, **+~10 min** for the layout-consistency pass; see
  `../../references/performance.md` for the stage tables.
