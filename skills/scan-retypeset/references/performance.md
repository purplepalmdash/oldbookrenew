# Performance reference (measured)

Per-stage wall-clock timings, measured on the reference machine during
the 《龙子太郎》 full-book run. Machine: NVIDIA T4 15 GB (CUDA), 16-core
CPU, Arch Linux. Book: 139 scanned pages (134 standard panels), 250 dpi
renders ~2669×2000 px.

| stage | scope | time | notes |
|---|---|---|---|
| survey (page-type classification) | 139 pages | ~30 min | agent reads contact sheets; one-time |
| 01_render | 139 pages @250dpi | ~1 min | ~0.3 s/page, CPU |
| 02_geometry | 134 panel pages | ~4 min | CPU, OpenCV; 8 pages needed `--vl-frame` fallback |
| 08_batch_infer (VL) | 134 pages | ~12.5 min | ~5.5 s/page on T4, GPU |
| 08_batch_infer (OCR) | 134 pages | ~40 s | ~0.3 s/page on T4, GPU |
| 03+04+05+06 (run_page) | 134 pages @ 6 workers | ~10 min per round | CPU-bound; 5 rounds during bring-up, each round fixes a class of issue |
| 07_special | 4 pages | ~2 min | includes OCR of colophon + image crops |
| pdfunite (138 pages) | merged book | ~1 min | |
| gs optimize | 138 pages, 200 dpi out | ~3 min | 340 MB → 58 MB |
| final QA (test_pages all + pdftotext + review) | — | ~10 min | agent visual pass over contact sheet |

**Planning numbers for a new book of N panel pages (T4-class GPU):**

- one-time survey: 20–40 min (scales with page-type variety, not N)
- GPU inference: `N × ~6 s` (VL dominates; CPU-only box is ~20–40× slower)
- per-page build rounds: `~N/10 min` per round at 6 workers; budget 3–6
  rounds for a new layout, ~1 for a same-family book (same publisher /
  printer / era)
- end to end, hands-off after survey: **~1–1.5 h for 100 panels** on the
  reference machine.

CPU-only note (no GPU): the pipeline still works — run VL/OCR via the
MCP tools page-by-page (or `08_batch_infer.py` on CPU); expect
~30–60 s/page for VL, making a 100-panel book a half-day job.

---

## Second-book validation: 《哪吒》 (112哪吒, CC download)

70-page scan (Aspose-generated, 767.9×576 pt landscape pages), 66
standard panels + cover/frontispiece/colophon + dropped watermark page.
Same machine as above. Measured per stage, this run:

| stage | scope | time | notes |
|---|---|---|---|
| survey + config | 70 pages | ~35 min | page-type map, seal/watermark discovery, special-page config | 
| 01_render | 70 pages @250dpi | 150 s | ~2.1 s/page CPU (incl. per-page deskew) |
| 02_geometry | 66 panels | 9 s | 6 parallel; 65/66 OpenCV, 1 VL-fallback frame |
| 08_batch_infer (VL) | 66 pages | ~290 s | ~4.4 s/page on T4 |
| 08_batch_infer (OCR) | 66 pages | ~33 s | ~0.5 s/page on T4 |
| run_page round 1 | 66 pages, 6 workers | 56 s | 52/66 pass first round |
| seal fix: re-crop + re-OCR | 66 pages | 36 s | crop clamp only re-runs OCR, not VL |
| run_page round 2 | 66 pages, 6 workers | 60 s | **66/66 pass** |
| 07_special | 3 pages (cover/frontispiece/colophon) | 3 s | config-driven, no OCR needed |
| pdfunite | 69 pages | 3 s | 62 MB |
| gs optimize | 69 pages, 200 dpi | 6 s | → 20.9 MB |
| final QA | test_pages all + visual | ~5 min | 66/66 |

**Total hands-off pipeline time (survey done → distributed PDF): ~12 min**
on the reference machine (GPU inference 5.3 min of it). The whole
session including survey, special-page authoring, two fix rounds and
visual review was ~3 h wall-clock — the fix rounds cost minutes because
verify localizes failures to one check per page.

Cross-book observations:

- Same-family book (same era/publisher style): geometry + batch infer +
  one run_page round is enough *unless* the book has margin content the
  reference lacked (seals, watermarks, spine marks) — that class of
  issue shows up as `low_confidence`/`text` verify failures, not
  geometry failures.
- The seal fix (crop clamp + margin-block filter) is now in the
  pipeline itself, so the next same-family book should need one clean
  round from the start.

### Margin-design strip round (layout fidelity pass)

After the first 66/66 pass, a side-by-side with the original showed the
right-margin design (running-title tab, edge line, red seal) was missing.
Measured, this fidelity pass:

| step | scope | time | notes |
|---|---|---|---|
| margin ink survey (per-page strip analysis) | 66 pages | ~10 s | numpy strip scan; first attempt had a swapped W/H slice bug (0/66 false negative — see pitfalls 28) |
| 03_enhance margin crop, all pages | 66 pages, 6 workers | ~40 s | per-channel whiten of the color strip |
| run_page round (05+06) | 66 pages, 6 workers | ~75 s | first round: `print_fitness` regressed 34 pages (box-semantics bug) |
| 06_verify fix + re-run | 66 pages | ~75 s | → **66/66**, gap median 0.19 pt / max 1.15 pt unchanged |
| /Matrix patch + pdfunite + gs | 69 pages | ~15 s | opt book 35.7 MB (color margin strips add ~3 MB vs grayscale-only) |

**Planning note:** budget ~10–15 min of agent wall-clock for a
margin-fidelity pass (most of it is diagnosing the QA region bug, not
the compute). If you already know the book has margin design, it's a
single 03+batch+merge cycle (~3 min) because the code is generic now.

### Figure/text separation & layout-consistency round

After the margin pass, a side-by-side with the original exposed three
layout regressions (figure crop swallowing body text, duplicated panel
numbers, per-line font-size jumps). Fixing them touched geometry
(refine_frame_bottom + white-gap gap_scan + frame_ink cap ordering),
merge (sub-row ordering), typeset (unified per-page font), and verify
(two new machine checks). Measured, this round on 《哪吒》 66 panels:

| step | scope | time | notes |
|---|---|---|---|
| 00_batch_prepare re-crop textbands | 66 pages | ~56 s | crop box switched to `textband_crop_box` (was union±30 → x drift up to 70 px) |
| 08_batch_infer (OCR) | 66 pages | ~20–33 s | re-OCR after crop change; VL untouched |
| 02_geometry re-run (refine + white-gap gap_scan + frame_ink cap) | 66 pages | ~11 s | `refine_frame_bottom` recovers 6 pages whose frame bbox fused the first text line |
| run_page round (03+04+05+06) | 66 pages, 6 workers | ~74 s | first round 64/66 (2 borderline gap-fidelity, fixed by computing gap before the frame_ink cap) → **66/66** |
| 07_special + /Matrix patch + pdfunite + gs | 69 pages | ~20 s | opt book 34 MB |

**Planning note:** a figure/text-separation pass is ~10 min of
wall-clock (mostly geometry re-runs + one run_page round). It is needed
when the original typesets text close to the frame (case-B pages) or the
frame stroke is blurry/gradient. The two new machine checks
(`figure_text_sep`, `panel_no_single`) make the pass verifiable in the
batch, so you don't discover the regression on the human pass.

---

## Third-book run: 《巴黎圣母院》 (213pp landscape, 202 panels)

90s 连环画 of Hugo's Notre-Dame: 202 numbered panels (dot-style number
"31. …"), 6 unnumbered 序幕 pages, ornate title page, dense dark
engraving-style figures. Same machine (T4, 2026-10-31). Measured:

| stage | scope | time | notes |
|---|---|---|---|
| 01_render | 213 pp @250dpi | 350 s | ~1.6 s/page (slower than 哪吒: denser source raster) |
| 02_geometry | 208 pp, -P8 | 23 s | 207/208 OpenCV; 1 VL-fallback frame (near-black figure, frame fused) |
| 08_batch_infer (VL) | 207 pp | 781 s | ~3.8 s/page T4 |
| 08_batch_infer (OCR) | 208 pp | 76 s | |
| batch_runpages | 208 pp, JOBS=8 | 286 s | first pass; 132 pages tripped a 06_verify bug (UnboundLocalError in panel_no_single), build itself was fine |
| re-verify | 132 pp, -P8 | 28 s | |
| 07_special | 4 pp (cover/title/synopsis/back) | ~2 min | title page via `retype` + `draw_images_first` + `whiteout_boxes` (new); synopsis = 18 measured lines |
| fix rounds | 3 pp | ~3 min | p87: OCR speckle line "2 7" (new tiny-line filter); p112/p186: original book MISPRINTED numbers (103→"48.", 177→"120.") kept faithfully via `panel_no_exceptions` |
| test_pages QA | 208 pp | ~1 min | **208/208 pass** |
| pdfunite + gs /prepress | 212 pp | 2 s + 459 s | 66 MB → 51 MB |

Wall-clock start→final: **~84 min** (survey ~5 min, GPU inference
~14 min, everything else fast). Findings that generalized into the
skill: `panel_no_format: "dot"`, `no_panel_no`, `panel_no_exceptions`,
`retype.draw_images_first`/`whiteout_boxes`, and the tiny low-confidence
line filter in 04_merge. Also confirmed: MCP VL layout bboxes are in
ORIGINAL image pixel space (server passes the original file to
PaddleOCR-VL; no rescale needed vs geometry page-px boxes).
