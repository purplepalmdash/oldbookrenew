# Vertical text (竖排) — recipes

Vertical title pages, 题签, colophon columns. Two routes:

## Route A — retypeset (recommended; selectable text)

`07_special.py` `retype` supports `vertical: true` layout lines
(v0.7.0): characters stay UPRIGHT and are stacked top-to-bottom,
centered on `opts.x` (the `_vtext` primitive; char pitch 1.18×pt, tune
per book).

```json
"layout": [
  ["竖排题名", 28, [0,0,0], {"vertical": true, "x": 700, "baseline": 120}]
]
```

`baseline` is the FIRST CHAR's baseline, pt from the page top (same
convention as horizontal `opts.baseline`).

Recipe (measured, not guessed):

1. Render the original page at 250-300 dpi.
2. Find the columns: column projection on the ink mask (vertical text =
   dark COLUMNS where horizontal text gives dark rows). Each column:
   x-centre, y-top, y-bottom, char count → per-char slot height
   `(y-bottom - y-top) / n`.
3. Read the text: top-to-bottom per column; columns read RIGHT to LEFT
   (classical order) — verify against the printed neighbours before
   assuming.
4. Cross-check every char: high-zoom crops of the first/last 3 chars of
   each column (hard requirement: first/last char exact).
5. Write the measured x/y + text into the `retype` config
   (`07_special` maps source-px → A4-pt like everything else; or
   measure directly in source pt with the page-box factor).

Pitfalls (all hit in practice):

- **Spatial char order ≠ reading order.** If you rotate the page 90° and
  OCR it as horizontal text, the doc-orientation module may internally
  flip — never derive char order from rotated x; place chars in READING
  order top-to-bottom.
- **Big calligraphic titles (大字题) are missed by page-level det.**
  Crop the vertical strip, rotate the STRIP (not the page), OCR the strip;
  font size = column height / char count.
- **Show-through ghost lines (透印):** on thin paper, the back page's text
  bleeds through and OCRs as garbage lines in the margin. They are the
  "garbage lines" of a vertical page — keep a content whitelist (the real
  columns) and drop the rest; if unsure, compare against the facing page
  in the scan.
- **Char pitch**: 1.18×pt is the default; a column that must fit an
  original slot height → set pt so that n×1.18×pt ≈ original column
  height, rather than forcing pt from the title size.

## Route B — recognize via rotation (reading aid, not placement)

When the agent needs to READ a dense vertical page (not retypeset it):

1. Rotate the whole page 90° CW (`cv2.rotate ROTATE_90_CLOCKWISE`) —
   vertical becomes horizontal.
2. OCR the rotated image normally.
3. Rotate det boxes back to the original frame:
   `X1=y1', X2=y2', Y1=H-1-x2', Y2=H-1-x1'` (H = original height).
4. Reading direction stays top-to-bottom per column; chars are
   evenly distributed over the column's y-span:
   `yc_i = Y1 + (i+0.5)·(Y2-Y1)/n`.

## Route C — keep-as-image

For a full-page title page that IS the design (decorative, mixed sizes,
seals): `cover_as_is` / full-page `background` + `whiteout_boxes` under
the few retyped lines (see the 刘秀 p2/p3 pattern). No vertical code
needed — the page is an image with a light text layer.
