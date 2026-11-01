---
name: guji-restore
description: "Restore scanned old books (古籍/旧书/老书扫描 PDF) into a print-ready black-and-white edition: whiten yellowed pages to pure white (ink saving), illustrations whitened while keeping tone shading, LLM per-line text recognition with geometric cross-verification (line count / first char / last char / blank lines exactly matching the original), re-typeset with an embedded CJK kaishu font, color covers kept as-is. Use when the user asks to repair / clean / de-yellow / OCR-re-typeset / make printable a scanned book or old-book PDF (yellowed, scan noise, no text layer)."
compatibility: "Python 3.10+ (pymupdf, Pillow, numpy) + poppler-utils (pdftoppm/pdfinfo) + one CJK TTF font (e.g. LXGW WenKai). Fully offline. New-machine setup: see §Setup."
metadata:
  version: "1.1"
  origin: "Distilled from the longzitailang project (139-page scanned picture book, 2026-07 → 10)"
---

# 古籍扫描修复（guji-restore）

把泛黄、无文字层的旧书扫描 PDF 变成**可直接打印**的干净 PDF：

- 背景**纯白**、黑白分明（省墨；灰度输出，只耗黑墨）
- 插图白化但**保留灰调层次**（网点变平滑，木刻深填不丢）
- 文字由 **LLM 逐行识别**，行数、每行首字/尾字、空行、段号位置**与原书绝对一致**
- 用 CJK 字体（默认霞鹜文楷）**子集嵌入**重排，最终 PDF 可检索
- 彩色封面/特殊页原样保留

本技能的脚本**与书无关**：所有书特定参数在 `<项目根>/book.json`。
本技能只覆盖**横排**版式（已验证）；竖排线装书见 §适配说明。

## 门径 —— 开工前必须按序完成

1. **项目记忆**：项目根有 `PLAN.md` 就通读（进行中的项目先读它）；没有就从
   `references/PLAN-template.md` 建一个。所有决策写进它的决策表。
2. **侦察**：`pdfinfo <src>`（页数/页面尺寸/方向）→ 跑 S0 出 manifest →
   **agent 逐页目检** `work/render50/`（彩色/黑白正文/纯文字/特殊/广告），
   结果落 `book.json`：`keep_pages` / `drop_pages` / `no_frame_pages`。
3. **基准页**：用户给参考样张（期望加工效果）或选一页代表性正文页。
   把这一页完整跑 S2→S5，按 `references/calibration.md` 对样张调参，
   **请用户视觉验收**。验收前**禁止批量**；验收后把 S3 参数冻结进
   `work/params.json`，并把 BASE_OFF 等几何常量记入 PLAN。
4. **决策留痕**：每个特殊页怎么处理、删不删广告页、纸型方向 —— 都先问/先定，
   写 PLAN 决策表（D1、D2…），再动手。

## Setup（新机器 / 新项目）

```sh
# 1. 依赖
apt install poppler-utils          # pdftoppm pdfinfo
pip install pymupdf pillow numpy   # 见 requirements.txt
# 2. 字体（任一 CJK TTF；默认霞鹜文楷）
#    例: apt install fonts-lxgw-wenkai → /usr/share/fonts/truetype/lxgw-wenkai/LXGWWenKai-Regular.ttf
# 3. 项目骨架
mkdir <project> && cd <project>
cp -r <skill>/scripts src
cp <skill>/references/book.example.json book.json   # 按 §book.json 修改
# 4. 从上面门径 1 开始
```

## 流水线（都在 `<project>/src` 下运行）

| 阶段 | 命令 | 产物 |
|---|---|---|
| S0 盘点 | `python s0_classify.py` | `work/manifest.csv`（色彩度分类，起点而已，须目检确认） |
| S2 分割 | `python s2_segment.py 4`（可多页；`--no-frame`、`--line-box Y0,Y1`、`--color-render`） | `NNN_<dpi>.png`、行裁切 `NNN_Lk.png`、`NNN_dbg.png`、`NNN.json`（lines/tokens/est_chars/images/rules） |
| S3 白化 | `python s3_whiten.py 4`（`--pxstats PNG [X0 Y0 X1 Y1]` 看像素统计） | `NNN_img{i}.png`（灰度，背景纯白） |
| S4 识别 | agent 视觉读图 + `s4_fill.fill(...)`（见下节） | JSON 写入逐 token text/verified，全过 → `llm_done` |
| S5 排版 | `python s5_typeset.py 4` | `NNN_out.png` 预览 |
| S6 组装 | `python s5_typeset.py --assemble` | `out/<out_pdf>` + 自动校验（页数/文本层/字体嵌入） |

S2 会自动渲染（幂等，已有就跳过）；`llm_done` 的页重跑 S2 会被跳过。
页间结构见 `references/schema.md`。**先基准页走通全链，再批量。**

### 批量节奏

1. 批量 S2（每页看 stdout + 抽查 `_dbg.png`；行合并页 `--line-box`，
   无框页走 LLM 定框，见故障索引 F1/F2）
2. 批量 S4：**默认单 session 每批 5 页**（长会话上下文有限，持久状态靠
   JSON，抗压缩）。每批：读全页图 → 目检版式/异常 → fill 写回 →
   `s4_fill.report(lo, hi)` 看 flags。多路并行 subsession **仅在用户明确
   同意时用**（本项目用户明确拒绝并行、要求本 session 每批 5 页）。
   并行时任务提示词必含：项目目录、PLAN.md 路径、本组页列表、
   S4 协议全文、"est 不符先重读裁切再写回，拿不准就 flag"
3. 主会话 `s4_fill.report(1, N)` 扫 flags → 逐页重读修 flag
4. **post 批处理**（全部 OCR 完成后、S3 之前）：确认每页 `images[].bbox`
   都来自 `llm_frame_inner`（±2 内缩）——若部分页 fill 时没传 frame_inner
   或 JSON 里残留旧 auto-frame-hint，跑 post 脚本统一按 `llm_frame_inner`
   重写 `images[].bbox` 与 `content_bbox`；然后**删 `work/scale.json`**
   （bbox 变了，全局 scale 必须重算）
5. 批量 S3（冻结参数）→ 批量 S5 预览 → S6 组装

## S4 LLM 识别协议（全链核心，agent 视觉，不用 tesseract）

0. **坐标空间**：一切 bbox/坐标都在**原渲染图像素**（200dpi）里。read 工具
   显示图会缩小（如 2845×2134 显示为 2000×1500，工具会注明倍数）——目测
   的坐标要乘回倍数才是 JSON 里的值。拿不准就读行裁切 `NNN_Lk.png`（裁切
   图也标了倍数）或直接用分割器给出的 token x 范围。
1. **读图**：200dpi 全页图 `NNN_<dpi>.png`；行多或字小改读行裁切
   `NNN_Lk.png`。逐行逐字转录，**含标点，忠实原书**（旧式标点不现代化）。
   先数行：行数、每行首字/尾字、空行（y 大间隙）、段号位置与原书一致是硬指标。
2. **写回**（每个 token 独立给文本，x 序；line id 必须与 JSON 现有 id 一致，
   注意有的页 id 从 1 开始或跳过）：
   ```python
   import s4_fill
   probs = s4_fill.fill(
       6,                                  # 页号
       [880, 470, 2045, 1575],             # frame_inner：LLM 读出的图框内沿；无框页传 None
       {0: ["3"], 1: ["在日本国的高山峻岭中，有一条很深的山沟，山沟里"]},
       flags_extra=["L1 '的'疑'孄'，重读确认"])
   s4_fill.report(1, 139)                  # 扫全书记录
   ```
   每 token 字数与 `est_chars` 交叉校验（容差 max(2, 10%)）；不符 →
   `verified=False` + flag + 阻塞 `llm_done`，必须解决才算完。
   **无框页**：agent 读图定框 → 先把 `llm_frame_inner` 写进 JSON →
   重跑 S2 再填文（s2 见 `llm_frame_inner` 即以 LLM 框为准）。
3. **终验**：每行取**首 3 字 + 尾 3 字**的高倍裁切再确认一遍
   （首尾字绝对一致是硬指标）。
4. **存疑**：双读后仍拿不准的字 → `flags` 记录，**交付时列清单请用户确认**，
   不许静默猜。
5. **修复工具**（脚本已做防护，见 F28/F29）：斑点 token → `drop_tokens`
   （可重复传入，越界索引自动忽略）；token 分组错/段号被切碎 → `rebuild`
   （给整行新的 [(x0,x1,text),…]，rebuild 后不要再同时传 drop_tokens）；
   视觉确认 est 无误 → `overrides`；整行漏检（如超短末行「……」）→
   `add_lines`（line id 用 int；新行也要在 text 字典里给条目）。
6. **重填**：fill 是覆盖式。修错时只传有变化的参数即可；`llm_done`
   会按当前状态重算（不全过自动清 False，不会残留假 True）。

## 关键设计（为什么这样）

- **一一对应 = 保留原始相对 (x, y)**：不重排行序；每个 token 落在原始 x
  → 段号悬挂缩进、双栏、空行（y 大间隙）全部自动还原。
- **省墨 = 背景纯白**：LUT 必须把纸面映到 1.0，`paper_seed` 连通域强制 255；
  灰调层次靠"区域均值 + 局部纹理增益"保留，不硬二值化。
- **参数冻结**：`work/params.json` 优先级最高（除单页覆盖）；批量阶段改参数 =
  事故。
- **自动框只是提示**：LLM 读出的 `llm_frame_inner` 是最终权威。
- **images bbox 只盖插图，不盖文字行**：白化图里若含原书文字行，重排后
  新字会叠在旧字上（重影）。图文分区的书（插图框+框下文字）直接用框内沿；
  **文字在框内**的书要把 `images[0].bbox` 手工缩到插图区域（避开所有行
  的 y 带），记 flag。批后可脚本校验：每行 bbox 与 images bbox 垂直重叠
  >40% 行高 = 报警。
- **计数口径**：进度一律以磁盘 JSON 的 `llm_done` 计数（`report` 或 glob
  统计），不用会话记忆里的数（压缩/中断后会漂移）。
- **自动校验闭环**：S4 字数交叉校验、S6 文本层==JSON、字体嵌入检查，
  三层都过 + 视觉抽检才算完成。

## 故障索引（症状 → 第一动作，详见 references/failure-catalog.md）

| 症状 | 第一动作 |
|---|---|
| 行合并（脏扫描） | `s2_segment.py N --line-box Y0,Y1`（行投影谷值定框） |
| 图框没找到/找错 | agent 读图 → `frame_inner` → 重跑 S2 |
| est≠got | 重读该行裁切；drop/rebuild/override |
| 难辨字形（的/孄、说/悦） | 双读 + 首尾3字终验；存疑 → flags + 交付清单 |
| 红字/蓝字残留 | `page_whiten: {"N": {"remove_red": true}}` |
| 页缘淡水印 | 不处理（页缘不重排，自然消失） |
| 排版突然偏大/溢出 | 删 `work/scale.json` 重算 |
| 背景不白 / 过曝 | `--pxstats` 对样张调 LUT / paper_seed |
| 基线偏移 | `BASE_OFF`（基准页上 ±0.02） |

## 特殊页

- **彩色封面**：`keep_pages`，原样 contain-fit，不做任何修改
- **广告页**：`drop_pages`（与用户确认后删）
- **书名页**：大字书法题**留图**（白化）、小字署名用 `vlines` 竖排重排、
  红字声明/水印用 `remove_red` 删
- **版权页**：`--no-frame`；框线记入 `rules` 重画、双栏靠 token 原始 x、
  蓝字 `remove_blue` 后黑字重排；个别行大小用行 `fontsize_src`
- **纯文字页**：`no_frame_pages`

## 适配说明

- **竖排线装古籍**：S2 的水平投影要改成**垂直投影**（找竖列），S4 每列
  从上到下识别，S5 用 `vlines` 或按列排版。数据模型（逐列 JSON + 相对
  位置落位）不变。此路未在本技能中实现/验证，接手竖排书先做基准页验证。
- **非 A4 / 其他开本**：`out_page_pt` + `margin_mm` 即可；全局 scale 自动。
- **多图页 / 复杂版面**：JSON 的 `images` 支持多图；直接编辑 JSON + 记 flags。

## 交付定义（done）

1. 总页数 = 源页数 − drop（脚本自动校验）
2. 每个流水页：PDF 文本层 == JSON（== 原书，脚本自动校验）
3. 字体已子集嵌入（脚本自动校验 `get_fonts()`）
4. 视觉抽检（agent 逐页过目）：图内残留污渍/水印、文字错位、插图过曝/死黑
5. 产出 `out/<out_pdf>`（灰度、纯白底、100% 打印）+ **存疑字符清单**
6. PLAN.md 进度全勾，决策表完整

## 移植到另一台机器

```sh
zip -r guji-restore.zip guji-restore/     # 整个技能目录
# 目标机器：
unzip guji-restore.zip -d ~/.agents/skills/    # pi 技能目录
# 然后按 §Setup 建项目（scripts 拷进新项目 src/，写 book.json）
```
技能自包含：脚本（scripts/）+ 配置样例/结构/调参/故障索引（references/）+
依赖清单（requirements.txt）。书的项目产物（work/、PLAN.md、book.json）
不属于技能；迁移进行中的项目时把它们一并拷走。
