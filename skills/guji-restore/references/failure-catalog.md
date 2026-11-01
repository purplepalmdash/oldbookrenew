# 故障索引（症状 → 原因 → 处置）

来自 139 页《龙子太郎》扫描修复的全部实战教训。**遇到症状先查这里，
不要凭直觉第三次重试。** 每条对应 PLAN.md 的决策/进度记录。

## 版面分割（S2）

| # | 症状 | 原因 | 处置 |
|---|---|---|---|
| F1 | 3–4 行文字并成 1 块（脏页） | 纸张纹理/污渍使行间暗度 > 阈值，`find_lines` 合并 | 看行投影谷值手动定框：`s2_segment.py N --line-box Y0,Y1 [--line-box …]`（x 自动取墨迹范围）；记入 PLAN 决策。本项目 p62/p67/p121 |
| F2 | 自动图框没找到（无框页/低对比框） | `find_frame` 要求 >55%W 横线与 >50%H 竖线；无框、断框、框线细都失败 | agent 读 200dpi 图定图框**内沿**四角 → `s4_fill.fill(n, frame_inner=[x0,y0,x1,y1])` → 重跑 s2（`llm_frame_inner` 是最终权威，s2 见之即以 LLM 框为准）。本项目 16 个无框页全走此路 |
| F3 | 报 `frame interior sparse` | 检出的"框"其实是文字区细框，不是插图 | 看 `NNN_dbg.png`；确属文字页 → `--no-frame` 或 `no_frame_pages` |
| F4 | 行首/行尾缺字，或整行漏检 | 短行（末行）低于高度阈值 / 行与图框粘连 | 读 `_dbg.png` 确认；短末行用 `s4_fill.fill(..., add_lines=[...])` 手工补行 |
| F17 | 图框 bbox 明显错位（x/y 互换状） | 历史 bug：auto-frame 分支写成 `[t+8,l+8,r-8,b-8]` | bbox 一律 `[x0,y0,x1,y1]`；改代码后**必须**核对已落盘 JSON 的旧值（本项目用 post 脚本按 `llm_frame_inner` 统一重写 +2 内缩） |
| F25 | 分割出一行“极宽、极扁”的假行（h<~60px、est_chars≥~30、y 紧贴框边） | `llm_frame_inner` 上/下沿略松，框线本身被 `find_lines` 当成行 | 收紧 frame_inner（上沿下移/下沿上移 2–6px）重跑 S2；若只是框线残留，直接从 JSON 删该行并把后续行 id 重新编为 0 起连续 |
| F26 | binm 几乎全空或全黑，otsu 阈值离谱（如 61 vs 163） | 手写了 Otsu 实现（累计公式/索引错） | **只用脚本的 `otsu(gray)`**，不要内联手写。binm 异常先查阈值再查渲染 |

## 识别（S4）

| # | 症状 | 原因 | 处置 |
|---|---|---|---|
| F5 | est 与字数不符（est≠got） | token 切分误（斑点 token、字粘连）或识别错 | 重读该行裁切 `NNN_Lk.png`；斑点 → `drop_tokens`；分组错 → `rebuild`；视觉确认无误 → `overrides`（容忍 est 差，记 flag） |
| F6 | 难辨字形（的/孄、说/悦、己/已、末/未） | 老字体 + 扫描模糊 | 双读（全页 + 行裁切 + **首3/尾3字高倍裁切**）；仍存疑 → `flags` 记录 + 交付清单请用户确认。首尾字绝对一致是硬指标 |
| F7 | 标点拿不准 / 旧式标点 | 原书用旧式标点（。，．」等） | 忠实原书，不"现代化"；拿不准记 flag |
| F28 | 重填时 `drop_tokens` 报 `IndexError: list assignment index out of range` | token 上次已删，再传同一索引越界 | 新版脚本已幂等（越界索引忽略+flag）；旧脚本则重填时**省略** drop_tokens |
| F29 | `add_lines` 后 `TypeError: '<' not supported between 'str' and 'int'` 或 `KeyError: 3` | 行 id 用了字符串 "L3"（与 int id 混排不可比较）；或新行没在 text 字典给条目 | 行 id 一律 int；新行必须在 text 字典给文本。新版脚本已自动 int 化并对缺条目给明确 probs |

## 白化（S3）

| # | 症状 | 原因 | 处置 |
|---|---|---|---|
| F8 | 背景残留灰/黄（不白） | LUT 顶端未到 1.0 或 `paper_seed` 过大 | LUT 最后锚点 (1.0,1.0) 且 0.93 附近压到 0.99；`paper_seed` 调小；用 `--pxstats` 验证背景窗口 `frac255→1` |
| F9 | 插图过曝（灰调丢失）或死黑 | LUT 过陡 / `tone_radius` 过大 | 回退锚点；`p5/p50/p95` 与样张对齐（calibration.md） |
| F10 | 红字声明 / 蓝字水印残留 | 彩色墨水，灰度化后变深灰 | `page_whiten: {"N": {"remove_red": true}}` 或 `remove_blue`；需要时先 `s2_segment.py N --color-render` 出彩色渲染 |
| F11 | 页边空白区淡水印（如"×××扫描制作"） | 扫描附加，在原书页缘 | **不处理**：页缘空白不重排，重排后自然消失。图框内的才需人工标记 |

## 排版（S5/S6）

| # | 症状 | 原因 | 处置 |
|---|---|---|---|
| F12 | 全书排版突然偏大/溢出 | `work/scale.json` 全局 scale 缓存过期（页集变了） | 删 `work/scale.json` 重跑（s5 会重算并回写） |
| F13 | 文字基线整体偏高/偏低 | `BASE_OFF` 未校准 | 基准页上 ±0.02 调，只动一次并记录 |
| F14 | 校验报 text mismatch | JSON 与 PDF 文本层不一致 | 定位页：`page.get_text()` 对 JSON `lines[].text`（按 y,x 排序拼接）；新版脚本已去空白比较并给出首个差异字符上下文；真差异多为某行 token 漏填/错填，回 S4 重读该行 |
| F30 | 校验 mismatch 但人眼看不出差别 | ① PDF 文本抽取在 token 空隙处插入空格（段号后必现）；② 同一 glyph 的 Unicode 映射差异（如 LXGW 里 U+00B7 抽取成 U+2219，渲染其实正确） | 比较前一律 `re.sub(r"\s+","", …)`；剩下仍不符的逐字 diff，确认是字体 glyph-Unicode 差异则渲染无误可放行，内容差异才回 S4 |
| F15 | 校验报字体未嵌入 | 字体路径错 / 未走 TextWriter+fontfile | 确认 `book.json font` 存在；嵌入必须经 `pymupdf.Font(fontfile=)` + `TextWriter`（自动子集） |
| F16 | 竖排/双栏错位 | 竖排用 `vlines` 而非 `lines`；双栏靠 token 各自 x | 竖排页：大字书法留图、小字署名用 `vlines` 逐字竖排（VL_ADV 调字距）；双栏（版权页）：保留各 token 原始 x0，rules 保留框线 |
| F27 | 预览/PDF 里行尾最后一个字消失 | 嵌入字体（如霞鹜文楷）比原书字体略宽，整行超出页面右缘，TextWriter 静默裁切 | 新版 s5 已内置**行级 fit-to-width**（超宽自动缩字号，仅缩到不溢出）；若仍裁切检查 `margin_mm` 或该行 `fontsize_src` |

## 页面级

| # | 情形 | 处置 |
|---|---|---|
| F21 | 彩色封面 | `keep_pages`：原样 contain-fit 插入，**不做任何修改** |
| F22 | 资源站/广告页 | `drop_pages`（交付前与用户确认删除） |
| F23 | 段号 1..99 一位/两位宽度不同 | 独立 token/独立行，按原图 x 对齐，不做等宽对齐 |
| F24 | 正文页中段空行（如 p137：2 行+空行+1 行） | 无需处理：相对 y 落位天然保留 |
