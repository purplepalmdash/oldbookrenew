"""S4 fill helper: apply LLM transcription to per-page JSONs.

Protocol (see SKILL.md "S4 LLM recognition protocol"):
  1. The agent views NNN_<dpi>.png (or line crops NNN_Lk.png) and transcribes
     line by line, token by token, including punctuation.
  2. Call fill() with line_id -> list of token texts (x order). Each token's
     char count is cross-checked against est_chars (tolerance max(2, 10%));
     failures keep verified=False + a flag, and block llm_done.
  3. Final check: first-3 + last-3 chars of every line re-confirmed on
     high-zoom crops (first/last chars must be absolutely right).

Usage (from src/):
  import s4_fill
  s4_fill.fill(6, [880, 470, 2045, 1575], {0: ["3"], 1: ["..."]})
  s4_fill.report(1, 139)

Arguments:
  n           page number
  frame_inner LLM-read inner frame edge [x0,y0,x1,y1] (render px); None for
              frameless pages. Also rewrites images[0].bbox (+2 inset) and
              content_bbox. Additional images' bboxes are edited in the JSON
              directly.
  text        line_id -> list of token texts in x order (must match the
              token count, after drop_tokens). Every line must be covered.
  flags_extra extra flag strings
  drop_tokens line_id -> [token idx] to delete (specks)
  overrides   line_id -> True marks line visually re-verified (est mismatch
              tolerated, recorded in flags)
  rebuild     line_id -> list of (x0, x1, text) replacing the token list
  add_lines   list of (line_id, [x0,y0,x1,y1], [(x0,x1,text),...]) to append
              a line entry (e.g. a short last line missed by segmentation)
"""
import json
from bookcfg import PAGES


def fill(n, frame_inner, text, flags_extra=None, drop_tokens=None,
         overrides=None, rebuild=None, add_lines=None):
    p = PAGES / f"{n:03d}.json"
    obj = json.loads(p.read_text())
    if frame_inner is not None:
        obj["llm_frame_inner"] = frame_inner
        x0, y0, x1, y1 = frame_inner
        if obj.get("images"):
            obj["images"][0]["bbox"] = [x0 + 2, y0 + 2, x1 - 2, y1 - 2]
            obj["images"][0]["source"] = "llm-visual-inner"
    drop_tokens = drop_tokens or {}
    overrides = overrides or {}
    rebuild = rebuild or {}
    probs = []
    if add_lines:
        for (lid, bbox, toks) in add_lines:
            # ids must be ints; accept "3" or "L3" (str ids break the sort)
            lid = int(str(lid).lstrip("Ll").strip())
            obj["lines"].append({
                "id": lid, "bbox": bbox, "crop": f"{n:03d}_L{lid}.png",
                "tokens": [{"x0": a, "x1": b, "est_chars": max(1, round((b - a) / max(1, (bbox[3] - bbox[1] + 1)))), "text": tx, "verified": False} for (a, b, tx) in toks],
                "text": "", "verified": False})
            obj["lines"].sort(key=lambda l: l["id"])
            obj.setdefault("flags", []).append(
                f"L{lid} line added manually (missed by segmentation)")
            if lid not in text:
                probs.append(
                    f"L{lid}: added line must also have a text entry")
    for l in obj["lines"]:
        if l["id"] in rebuild:
            l["tokens"] = [{"x0": a, "x1": b, "est_chars": max(1, len(tx)),
                            "text": "", "verified": False}
                           for (a, b, tx) in rebuild[l["id"]]]
            obj.setdefault("flags", []).append(
                f"L{l['id']} tokens rebuilt from visual groups")
    for l in obj["lines"]:
        if l["id"] in drop_tokens:
            # idempotent: clamp out-of-range indices (refill after a
            # previous drop must not crash with IndexError)
            idxs = sorted({i for i in drop_tokens[l["id"]]
                           if 0 <= i < len(l["tokens"])}, reverse=True)
            for i in idxs:
                del l["tokens"][i]
            if idxs:
                obj.setdefault("flags", []).append(
                    f"L{l['id']} {len(idxs)} speck token(s) removed")
            if len(drop_tokens[l["id"]]) > len(idxs):
                obj.setdefault("flags", []).append(
                    f"L{l['id']} stale drop index(es) ignored (idempotent)")
        texts = text.get(l["id"])
        if texts is None:
            probs.append(f"L{l['id']}: no text given for this line")
            continue
        toks = l["tokens"]
        if len(texts) != len(toks):
            probs.append(f"L{l['id']}: token count {len(toks)} != given {len(texts)}")
            continue
        ok = True
        for t, tx in zip(toks, texts):
            t["text"] = tx
            est, ln = t["est_chars"], len(tx)
            if abs(ln - est) > max(2, int(0.10 * ln)):
                ok = False
                probs.append(f"L{l['id']} est{est} got{ln} {tx[:4]}")
            t["verified"] = ok
        l["text"] = "".join(texts)
        if l["id"] in overrides:
            if not ok:
                obj.setdefault("flags", []).append(
                    f"L{l['id']} est mismatch crop-reconfirmed, kept")
            ok = True
        l["verified"] = ok
    boxes = [i["bbox"] for i in obj.get("images", [])] + \
            [l["bbox"] for l in obj["lines"]]
    if boxes:
        obj["content_bbox"] = [min(b[0] for b in boxes),
                               min(b[1] for b in boxes),
                               max(b[2] for b in boxes),
                               max(b[3] for b in boxes)]
    if flags_extra:
        obj["flags"] = list(obj.get("flags", [])) + flags_extra
    if probs:
        obj.setdefault("flags", []).extend(probs)
    if obj.get("lines") and not probs and \
            all(l.get("verified") for l in obj["lines"]):
        obj["llm_done"] = True
    else:
        obj["llm_done"] = False  # never leave a stale True on a broken refill
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    return probs


def report(lo, hi):
    for n in range(lo, hi + 1):
        try:
            d = json.loads((PAGES / f"{n:03d}.json").read_text())
        except FileNotFoundError:
            print(f"p{n}: MISSING")
            continue
        done = d.get("llm_done")
        nl = len(d.get("lines", []))
        fr = d.get("llm_frame_inner")
        print(f"p{n}: lines={nl} llm_done={done} frame={fr} "
              f"flags={d.get('flags')}")
