# Watermark judgment sub-skill (later-added overlays)

Ancient/serial-book scans frequently carry **watermarks stamped by later
(re)scanners** — site logos, a horse/slogan emblem, “扫描 by …”, URLs,
page-corner stamps. They are NOT part of the book and must be judged
per book, then removed (the default) or kept on explicit user request.

## When to run

During the **survey step**, BEFORE writing the config — and only once per
book. You must have rendered page samples first (step 1 of the workflow).
The judgment requires having read through the WHOLE book (contact sheets
or low-dpi renders of every page, or at least every page type), so the
decision is evidence-based, not guessed from 2–3 samples.

## Step 1 — Full-book scan for repeated overlays

A scan-time overlay is **pixel-identical on every page it appears on**
(same scanner job stamped the same stamp); original book content varies
page to page. Exploit that:

1. **Text repetition (OCR side).** After MCP inference (or from a low-res
   OCR pass), collect every page's OCR lines. A line that is SHORT (≲ 15
   chars), appears on 3+ pages, at nearly the SAME position (same corner /
   same y-band), with variable but similar readings (“天岸马奇通人中龙” /
   “天岸马奇逸人中龙” / “天岸马”) is a watermark slogan. Original captions
   repeat only in their FORMAT (panel number), never in content at a
   fixed corner position.
   ```
   grep -l '天岸' data/layout/p*.json | wc -l   # near-total ⇒ watermark
   ```
2. **Template match (pixel side).** For any suspicious patch (a logo in a
   corner, a slogan under a figure, a bottom strip), cut the patch from
   one page's 250 dpi render and template-match it against ALL pages:
   ```python
   tmpl = base[y0:y1, x0:x1]; mask = tmpl < 150
   recall = (mask & (page_patch < 150)).sum() / mask.sum()
   # recall ≈ 1.0 on most pages, ≈ 0 elsewhere ⇒ scan-time overlay
   ```
   Also check the patch on the sister volume / a different book from the
   same series: a genuine overlay shows recall ≈ 0 there.
3. **Visual contact sheet.** Render 8–12 pages (first, middle, last,
   several types) at 60–72 dpi into contact sheets and look at page
   corners, bottom strips, and the area BETWEEN figure and caption.
   Recurring marks that are the SAME on every page = watermark candidates.

## Step 2 — Classify: later watermark vs original design

| signal | later watermark | original design |
|---|---|---|
| appears on sister volume (same series) | usually NOT (unless same scan job) | YES |
| content: URL, site name, personal name, slogan, “扫描/制做 by X” | YES | NO |
| position: fixed corner / bottom strip, same px on every page | YES | NO (margins vary with print) |
| sits ON TOP of the printed banner/art | YES (stamped after print+scan) | NO |
| printed publisher seal / 书号 / colophon mark | NO | YES |
| running header, page-edge line, binding line | NO | YES (part of the original print) |
| cover art elements (title, illustrator mark) | NO | YES |

When a mark is ambiguous (e.g. a seal that could be the publisher's),
prefer asking the user — but DEFAULT TO REMOVING scanner-added marks
(site logos, slogans, personal stamps), and KEEP original printed
design (publisher marks, running heads, binding lines, cover art).

## Step 3 — Report to the user, respect the client

Show: what the watermark is (rendered crop), on which pages (count +
examples), and the intended action (remove by default). If the user says
keep (or keep only the logo but not the slogan, etc.), record that
decision in the config comments and apply per element.

## Step 4 — Removal mechanics (config-driven, see config.md)

1. **Panel pages (generic, the workhorse):** `watermark_boxes:
   [[x0, y0, x1, y1], ...]` (normalized page fractions, measured once by
   pixel projection — the overlay is identical on every page, so ONE box
   covers the book). `03_enhance` paints the box out of the deskewed page
   before figure / margin-strip crops (median ring fill; dark VERTICAL
   LINE columns crossing the box are restored from the segment above the
   box, so an original page-edge line survives); `04_merge` drops the
   watermark's OCR line (bbox centre inside the box AND left edge in the
   left 60% of the box's x span — short caption lines ending in the same
   corner start further right and survive). After the first full
   re-run, verify: no watermark ink left in the margin strips, the
   watermark text is gone from every `data/layout/pNNN.json`, and
   `pdftotext` of the final PDF does not contain the slogan.
2. **Special pages (cover / title / synopsis / colophon):** add the same
   box to the page's `whiteout_boxes` (erased from the full-page
   background under the re-typeset text).
3. **Watermark ON a colored banner/plate image** (it survives a
   whiteout of the page background because the banner crop is drawn on
   top): use the image entry's `erase_template: {"page": N, "boxes":
   [[...], ...]}` — TEMPLATE UN-BLENDING, the preferred method: the
   same box on page N (where the identical watermark sits on plain
   paper) is used to measure the per-pixel ink absorption
   `a = (P - T)/P`, and `base = observed / (1 - a)` restores the
   underlying ART where the watermark strokes ran (it only removes the
   watermark shape; the banner painting survives). Requires the
   scan-time overlay (pixel-identical across pages — step 1 verified
   that). If no plain-paper page exists (or the box would overlap
   other art on the template page), fall back to `erase_ink: [[...],
   ...]` — pixel-level dark-ink replacement with the local non-ink
   mean. Keep the boxes TIGHT for erase_ink: everything dark inside a
   box is erased, a loose box smears a flat patch over the art.
   erase_template is tolerant of loose boxes (flat banner regions have
   a≈0 and pass through), but keep them reasonable.
4. **Keep:** simply don't add the box(es); the watermark travels through
   the normal crop/margin-strip path.

## Pitfalls (learned on 刘秀)

- The watermark line often shares a geometry TEXT BAND with the last
  caption line (the band detector merges them). Dropping by band is wrong;
  drop by line (the 04_merge rule above).
- A whiteout box must NOT be so wide/tall that it eats the caption lines
  starting from the left edge — measure the watermark ink bbox exactly
  (pixel projection), pad ~1%, done.
- OCR readings of the slogan vary between pages (small, blurred type):
  match by POSITION + repetition, not by exact string.
- The vertical page-edge line near the watermark is usually ORIGINAL
  (present on the clean sister volume at the same x). The 03_enhance
  vertical-line-column restore keeps it in the margin strip.
- If the watermark is only under a banner on ONE special page, the panel
  `watermark_boxes` alone won't cover it — special pages need their own
  `whiteout_boxes` / `erase_ink`.
