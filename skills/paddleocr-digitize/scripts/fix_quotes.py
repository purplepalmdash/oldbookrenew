#!/usr/bin/env python3
"""对话引号保守归一化（OCR 系统性丢闭引号的最低风险修复）。

背景：PaddleOCR 对印刷引号系统性丢失（巴黎圣母院 208 页实测：开 228 闭 143）。
丢引号有两种形态：闭引号被丢（多）、开引号被丢（少）。两个方向全自动都
不可安全：跨行对话的闭引号位置、"：" 之后引号的归属都需读原图判断。
本脚本只做两类高置信修复 + 输出待人工复核清单：

1. 行尾补闭引号：行内引号未配对且行以 。！？ 结尾 → 行尾补 ”。
   （跨行引号的挂起状态在页内跟踪；页间不跨——每页字幕自足。）
2. 开引号补认：” 紧跟全角 ：且后接汉字 → 转 “（开引号被丢、闭引号保留的
   形态）。⚠ 有反例：OCR 把 ！ 误成 ：时该 ” 是真闭引号 → 转换结果全部
   打印，人工过目后再定稿。

用法:
    python fix_quotes.py --out <out> [--dry-run]
    # 修改 data/pageNNN.json 的 ocr.lines[i].text 并重算 ocr.text。
    # --dry-run 只打印不改文件。改完重跑 build_pdf + test_page。
"""
import argparse, json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    OUT = Path(a.out).resolve()

    changes = []
    for f in sorted(OUT.glob("data/page*.json")):
        if "_special" in f.name:
            continue
        rec = json.load(open(f))
        p = rec["page"]
        lines = rec["ocr"]["lines"]
        # 1) ” 紧跟 ：且后接汉字 → “
        for l in lines:
            t = list(l["text"])
            for i, ch in enumerate(t):
                if ch == "”" and i > 0 and t[i-1] == "：" \
                        and i + 1 < len(t) and '\u4e00' <= t[i+1] <= '\u9fff':
                    t[i] = "“"
                    changes.append((p, "”→“", l["text"][:30]))
            l["text"] = "".join(t)
        # 2) 行尾补 ”
        pending = []
        for li, l in enumerate(lines):
            t = list(l["text"])
            for i, ch in enumerate(t):
                if ch == "“":
                    pending.append((li, i))
                elif ch == "”" and pending:
                    pending.pop()
            if pending and pending[-1][0] == li and t and t[-1] in "。！？":
                t.append("”")
                l["text"] = "".join(t)
                changes.append((p, "补”", l["text"][-25:]))
                pending.pop()
        rec["ocr"]["text"] = "\n".join(l["text"] for l in lines)
        if not a.dry_run:
            json.dump(rec, open(f, "w"), ensure_ascii=False, indent=1)

    print(f"changes: {len(changes)}")
    for c in changes:
        print("  ", c)
    print("⚠ 逐条目检（尤其 ”→“ 类，有 ！→： 误读反例）后重跑 build_pdf/test_page")


if __name__ == "__main__":
    main()
