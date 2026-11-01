# Case study: 《刘秀》 下册 (连环画, 164 页横版扫描, 重泛黄 + 全册扫描水印)

The v0.6.x reference run — heavily yellowed paper plus a scan-time
watermark on every panel page. Companion volume (《刘秀》上册, clean
paper, no watermark) was processed in the same session with a lighter
`vector` pipeline; the two volumes share one survey and differ only in
config, demonstrating the multi-book workflow.

## Survey findings

- Paper RGB ≈ (236, 220, 167), R−B ≈ +68, chroma_p50 ≈ 70 → **heavy
  yellowing** → `enhance.variant: "bw_clear"` + `color_auto: "off"`
  (yellow cast alone would trip the p99-chroma color detection).
- **Scan-time watermark**: horse emblem + slogan line (OCR reads
  “天岸马奇通人中龙”) + thin underline, lower-left corner of all 158
  panel pages (p5–p162). Judged via `references/watermark.md`:
  - repeated short OCR line in the same corner on ≥3 pages;
  - template match against a plain-paper page: recall = 1.0,
    precision = 1.0 on all 158 pages — the overlay is pixel-identical
      page to page, so **one box covers the book**;
  - the sister volume (上册) has no such mark → scanner-side, not
      printed.
- Left page-edge vertical line (x ≈ 136–148 px at 250 dpi) is original
  design → the whiteout preserves vertical-line columns.
- Special pages: p1 cover as-is; p2 banner (watermark sits **on colored
  painting**); p3 credits; p4 synopsis; p163 colophon table (封二).

## Results

- **158/158 panels pass** after the watermark re-run; final 163-page
  A4-landscape PDF, selectable text, no slogan string in `pdftotext`.
- Panel pages: `watermark_boxes: [[0.0, 0.842, 0.200, 0.993]]` — painted
  out of the deskewed page in `03_enhance` (median ring fill + line
  restore) before all crops; the watermark OCR line is dropped in
  `04_merge` (centre-in-box AND left-edge-in-left-60% rule, so real
  caption lines ending in the same corner survive).
- p2 colored banner: `erase_tmpl` **template un-blending** — the
  watermark absorption field `a = (P − T)/P` is measured on plain paper
  (page 40, same scan-time overlay) and divided out, `base = observed /
  (1 − a)`: watermark removed, original painting preserved. `erase_ink`
  is the fallback when no plain-paper page exists.
- Special pages use the `background` key for full-page plates,
  `force_gray` for de-yellowed crops; coordinates measured by pixel
  projection on rendered originals (OCR y positions were biased).
