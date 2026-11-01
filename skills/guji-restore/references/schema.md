# 逐页 JSON 结构（work/pages/NNN.json）

坐标均为 **render_dpi 渲染图上的像素**（默认 200dpi）。
bbox 约定 `[x0, y0, x1, y1]`，x1/y1 取像素格右/下边界（即"宽 = x1-x0"）。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `page` | int | 页号（1 起） |
| `class` | str | 页面类别（body / cover / special…），人工或 agent 标注 |
| `images` | `[{id, bbox, source}]` | 插图裁剪。`source` ∈ `auto-frame-hint` / `llm-visual-inner` |
| `frame_hint` | `[t,l,b,r]` \| null | 自动长边线图框（**仅提示**，可错） |
| `llm_frame_inner` | `[x0,y0,x1,y1]` | LLM 读图得到的图框**内沿**四角，最终权威；s2 重跑时以此为准 |
| `lines` | `Line[]` | 文本行（含段号行），上→下排列 |
| `rules` | `[[x0,y0,x1,y1]]` | 细线（厚度 <12px）：版权页双线框等，重排时重画 |
| `noise_boxes` | `[[...]]` | 被忽略的中薄噪声带（脏扫描纸纹） |
| `content_bbox` | `[x0,y0,x1,y1]` | images + lines 的并集；决定排版缩放 |
| `flags` | `str[]` | 存疑/告警记录；**交付时必须逐条列入清单请用户确认** |
| `llm_done` | bool | S4 完成且全部交叉校验通过；s2 见此跳过（布局已定稿） |
| `bin_thr` | int | 该页 Otsu 二值化阈值 |
| `vlines` | `[{x, y, fontsize, text}]` | （特殊页）竖排文字列：x=列中心，y=首字顶，逐字竖排 |

## Line

```json
{
  "id": 1,
  "bbox": [333, 1689, 2622, 1779],
  "crop": "004_L1.png",
  "tokens": [
    {"x0": 333,  "x1": 435,  "est_chars": 1,  "text": "1", "verified": true},
    {"x0": 516,  "x1": 2629, "est_chars": 23, "text": "在日本国的高山峻岭中，…", "verified": true}
  ],
  "text": "1在日本国的高山峻岭中，…",
  "verified": true
}
```

- **tokens** 按"空隙 ≥ 0.9×行高"切分，x 升序。段号、正文、双栏各列都是
  独立 token，**保留原始 x 位置** → 重排时悬挂缩进、栏距自然还原。
- `est_chars = round(token宽 / 行高)`（汉字近似方形），是 S4 交叉校验基准。
- 行裁切 `NNN_Lk.png`：行 bbox 四周外扩（y ±4~5px，x ±6px）自 200dpi 灰度图。
- `text` = 各 token text 拼接；`verified` 要求全部 token 校验通过。

## 空行 / 空段

不显式表示。两行 y 间距异常大即空行；S5 按原始相对 (x, y) 落位，空行自动保留。

## 多图页

`images` 可含多个图（如书名页：书法题字框 + 署名框）。`s4_fill.fill` 的
`frame_inner` 只重写 `images[0]`；其余图的 bbox 由 agent 直接编辑 JSON 并
记 `source`。`rules` 与 `vlines` 同理直接编辑 JSON。

## 状态流

```
s2_segment → 生成 lines/tokens(est_chars)、images、rules、content_bbox
s4_fill    → 写入 tokens[].text、verified；全过 → llm_done=true
s2 重跑    → llm_done 的页跳过；非 llm_done 页重切分但保留同 bbox 行的已验文字
s3_whiten  → 读 images[].bbox 产出 NNN_img{i}.png
s5_typeset → 读全部字段产出 NNN_out.png 预览 / 最终 PDF
```
