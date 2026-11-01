# Hardware tiers (scan-retypeset)

The pipeline's only heavy step is the MCP inference phase
(`08_batch_infer` VL layout + line OCR, plus `09_rescue` strip re-OCR).
Everything else (render, geometry, enhance, typeset, verify, merge, GS)
is CPU work.

## Tier 1 — NVIDIA GPU (validated; best throughput)

T4 (16 GB) single card, measured on the 刘秀 pair:

| step | 158-page book |
|---|---|
| `08_batch_infer all` (VL ~5.5 s/page + OCR ~0.3 s/page) | ~15-16 min |
| `01_render` 250 dpi (poppler, 12-core) | ~10 min (two books share one render stage) |
| `batch_runpages` (enhance→typeset→verify, JOBS=6, CPU-bound) | ~4-10 min |
| Ghostscript /prepress (183 MB output) | ~3-5 min |
| end-to-end incl. survey/measurement | ~1-1.5 h agent time, hands-off pipeline < 40 min |

Single-GPU rule: stop the resident PaddleOCR MCP server before
`08_batch_infer` / `09_rescue` (two resident model sets may OOM), restart
it afterwards.

## Tier 2 — CPU only (works, slower; plan time ×3-5 on inference)

- `08_batch_infer` runs with the MCP venv's CPU paddle: VL is the slow
  part (order of minutes per page for the layout model); a 150-page book
  takes hours. Split the job: run `vl` overnight, `ocr` (fast even on CPU
  for the small text-band crops) in the day.
- `09_rescue` strips are small (frame-width × caption height) — even on
  CPU, strip re-OCR is practical per page (~tens of seconds).
- Everything else is unchanged. `JOBS` scales with cores for
  `batch_runpages`.

Minimum: 4 cores / 8 GB RAM / SSD, ~500 MB per book working directory
(250 dpi renders dominate) + output PDF.

## Tier 3 — remote GPU (no local GPU at all)

The MCP server module is plain Python: run the inference phase **on the
GPU host** and bring the results back:

```sh
# on the book project root (this machine):
rsync -a src.pdf pipeline/ gpuhost:bookwork/
ssh gpuhost  # in the book dir there, MCP venv python:
  python pipeline/08_batch_infer.py all
  python pipeline/09_rescue.py auto      # optional
rsync -a gpuhost:bookwork/data/vl data/
rsync -a gpuhost:bookwork/data/ocr data/
# continue locally: batch_runpages.sh (CPU is fine)
```

(Or point `mcp.server_dir` at a shared mount and run 08 locally if the
GPU host is the MCP host.) This is the same pattern as the sibling
skill's `remote_gpu_server.py` (ssh-stdio MCP wrapper with scp caching) —
use whichever fits your machines.

## Choosing

- Have a T4-class box in reach → Tier 1 (this is the validated path).
- Laptop / CPU server → Tier 2, accept slower inference; quality is
  identical (same models, same code path).
- No GPU anywhere but one on the LAN → Tier 3.
- A machine running the **sibling** paddleocr-digitize skill on iGPU
  (OpenVINO, 8-12 s/page) is also a viable inference host: its OCR dumps
  are PaddleOCR output too, but the dump schema differs (pageNNN.json vs
  pNNN.json blocks) — run *that* skill's pipeline end-to-end on the GPU
  host instead of mixing dumps between skills.
