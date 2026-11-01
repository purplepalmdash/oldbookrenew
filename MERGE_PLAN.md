# oldbookrenew 整合方案（bread × test-mc12le000）

状态：**执行中**（bread 起草于 2026-11-01；test-mc12le000 侧已执行大半，
进度注记见下。本文件由 bread 分支推送，现取回 main 随整合推进更新。）

## 0a. 执行状态注记（bread，2026-11-01）

- **A 项清单 6/6 完成**（直接落在 main 的 paddleocr-digitize 内，v1.3；
  A7 已由 test-mc12le000 自完成于根 HARDWARE.md）：
  - A1 → `scripts/erase_template.py`（模板反混合移植；乘性吸墨合成测试：
    红底 171→179 真值 180、暗画 116→120 真值 120，框外像素级不变）；
  - A2 → `build_pdf.py --gs`（**带文字层回归门**：抽 3 页比对提取文本，
    失败自动弃 gs 版。实测 gs 10.02.1 对 CID TrueType 子集损坏 ToUnicode
    映射 → 本机自动中止，保原 PDF；新入事故手册两条：gs 文字层损坏、
    1-bit 图转 JPEG 膨胀 43.7→91.2MB）；
  - A3（缩窄为检测器）→ `scripts/margin_ink_check.py`（S5 前置框外墨门：
    Otsu − 插图框(外扩10px) − 行 quad 外扩 − book.json 已判定区；WARN
    退出码 1 + 高亮图。刘秀下 20 页实测：抓出天岸马水印区+2 处扫描噪声带
    → 判定记录后 0 WARN；合成右页边签条 14067px 必中）；
  - A4 → SKILL 水印判定子技能（整书重复行→模板 recall→姊妹卷对照→
    留证据处置；天岸马改判案例并入几何清除节）；
  - A5 → `scripts/probe_baseline.py` + `build_pdf.py --ink-top`（整行墨顶
    实测：LXGW 字体墨顶 0.80 vs 验证常数 0.88，差 = det quad 顶 padding
    ~0.08em——换字体用 Δ 相对校正；默认 0.88 不动，回归验证：刘秀下
    重构建 162 页全页文字层+抽样 4 页 0.1pt 位置全等）；
  - A6 → 事故手册 OCR bbox 1920 预处理坐标系条目（引用 scan-retypeset
    pitfalls 同条目）。
- 验证：9 个 CLI 脚本 py_compile+--help 全过；新文件敏感信息 0 命中；
  默认行为零漂移（--ink-top/--gs 均 opt-in，无参重建与 shipped 逐页一致）。
- 待办（案例驱动）：A1 真书案例（彩色图扫描期水印 + 无 T4）；A2 换 gs
  版本重试（找到能保 ToUnicode 的 gs 后可启用真减容）；细边线真书验证
  （与 test-mc12le000 的 I-3 跟踪项同源）。

---

## 0. 执行状态注记（test-mc12le000，2026-11-01）

- **整合分支 = `main`**（c609691）：已基于 bread HEAD 301ff6a 重构为
  `skills/scan-retypeset/` 并排布局并推送，第 5 节步骤 1–2 完成；
  步骤 3（cherry-pick）/4（顶层 README、HARDWARE）已在 main 上完成
  （根 README 即路由入口，HARDWARE.md 已含 iGPU/CPU + T4 分档）。
- **B 项清单 8/8 完成**（直接落在 main 的 scan-retypeset 内）：
  - B1 → `scripts/pipeline/09_rescue.py`（score<0.6 分诊 + 条带增强重 OCR
    + `--check` 全书兜底；刘秀下册 158 页 auto 分诊 + 5 页审计实测通过）
  - B2 → `06_verify.py` 检查 #11 `chars`（rawdict 字符多重集 + U+2219/2027/30FB
    →U+00B7 归一；158/158 页含新检查回归通过）。逐字符 ±14pt 定位版为
    后续增强（多重集版先落）
  - B3 → `references/watermark.md` §D-map（跨页图案平均/相位校验/亮度相关 r）
  - B4 → `07_special.py` retype `vertical` + `references/vertical.md`（冒烟通过）
  - B5 → `lib/common.py::weighted_gaps` + 05_typeset/07_special 逐字加权 tracking
    （slot∝advance；纯 CJK 行保持原均匀路径零回归；刘秀下册全书重排回归中）
  - B6 → SKILL.md "Multi-book sessions"（R−B≳30 → bw_clear；未做 config
    `route` 字段，判据同效）
  - B7 → `references/hardware.md` + 根 HARDWARE.md T4 节
  - B8 → 05_typeset per-page 统一字号（长行中位数）原有，已确认
- **A 项清单 7/7 待 bread 侧认领**（在 bread 分支做，目录并排合入 main）。
  两处确认：A4 天岸马判定已改按本技能水印子技能框架留证据（bread 已认
  "出版社装饰文字"为误标）；A6 来源引用准确（`references/pitfalls.md` 已有
  1920 预处理坐标系条目，代码侧 `MAX_OCR_SIDE` 注释同在）。
- **bread 代码审查已响应（`review_issues_scan-retypeset.md`，6 项全修；**
  详见 CHANGELOG 0.7.0 "bread-branch review fixes"）：
  I-1 批量失败页可见性（`== <PHASE> FAILED pages` + `pNNN.failed` 标记
  + 一轮自动重试 + run_page 快速报错；`existing_pages` 改名）；
  I-2 check 拆合行假报（`line_audit_ratio` ds→got 单向 + 邻接拼接容忍，
  单调放宽不新增假报；验证中发现并规避了 got 侧多水印行会新造假报的
  陷阱，刘秀下 p40-44 实测 0/5）；I-3 细边线漏检（列投影窄暗列检测，
  哪吒 134 页 + 024 316 页-侧 A/B 零差异，合成 6 例全对；"找只有细
  边线的真书验证" 登记为后续跟踪项）；I-4 docstring 措辞（"exactly
  equivalent" → 行端半 slot 差异）；I-5 numpy import 位置；I-6
  install.sh 不再静默覆盖有本地修改的目录（--force/交互/非交互 skip+exit 2，
  顺带修了参数循环的 set -u 隐患）。README 龙子太郎行改为"右侧装饰条
  在框内"。跟踪项（B2 ±14pt 逐字定位版）双方均已登记，不阻塞。
- **第 7 节已核事实交叉核对一致**：margin_strip 动机案例即哪吒（本技能
  CHANGELOG 0.3.0 与 examples 一致，仅 `nezha/config.json` 开 `margin_strip`）；
  龙子太郎右侧装饰条在框内、不入页边设计层。天岸马=扫描期叠加，删除动作
  不变。
- **第 6 节验证**：刘秀下回归基线 = 158/158 页 06_verify PASS（含 #11 chars）；
  A1 erase_tmpl 样张 = 刘秀下册 p2（技能内 docs/samples 已含去水印前后图）；
  A3 margin_strip 样张、B3 D-map 样张（alibaba40 封面）、iGPU 交叉验证 = 待办。
- **交叉验证 bread v1.3 / commit a3fddd2（A 组，2026-11-01）**：
  - 零污染确认：6 个改动文件全部在 `skills/paddleocr-digitize/` 内 + 本文件注记；
    4 个脚本 py_compile 通过；默认行为零漂移（--gs/--ink-top 均 opt-in）。
  - 其新坑（gs 10.02.1 损坏 CJK ToUnicode）对照我方成品验证：本机 gs 10.07.1，
    024/025 成品 PDF 均通过 GS 前后 3 页（p5/p80/p163）字符多重集门 → 全等。
    该门已固化为 scan-retypeset workflow.md §7 的强制步骤；pitfalls 新增
    #42（gs 文字层，互引双方事故手册）+ #43（1-bit 图禁 JPEG 重压缩，
    实测 43.7→91.2MB 膨胀案例）。
  - 共享跟踪项登记：A1 真书案例（彩色图扫描期水印 + 无 T4 机器）、细边线真书
    验证（其 A3 margin_ink_check ↔ 我方 I-3 同源）——遇到首本符合条件的书
    即触发，任一方可做。

---

原始草案：

## 1. 目标结构

```
oldbookrenew/
├── README.md              # 入口：路由表（机器画像 × 书画像）+ 仓库导航
├── HARDWARE.md            # 合并：iGPU/CPU 分档（bread）+ T4/PaddleOCR-VL 档（对方）
├── LICENSE                # MIT（对方已提供）
├── CHANGELOG.md           # 对方已有，合并后继续维护
├── .gitignore
└── skills/
    ├── scan-retypeset/        # 对方管线（重构：仓库根 → skills/scan-retypeset/）
    │   ├── SKILL.md
    │   ├── scripts/pipeline/  # 00–08 + lib/common.py + run_page.py
    │   ├── references/        # pitfalls / watermark / config / workflow / performance / scribus
    │   ├── examples/          # lianhuanhua / nezha / paris / liuxiu
    │   └── docs/samples/      # 前后对比样张
    ├── paddleocr-digitize/    # bread 主路线（S1-S6 双路线，PyMuPDF + Scribus MCP）
    └── guji-restore/          # bread 离线 LLM 视觉路线
```

冲突面：对方分支删掉了 bread 全部文件后在根目录重建。整合时**以目录并排解决**——对方先把自己重构进
`skills/scan-retypeset/`（其已承诺），再与 bread 合并，两边文件零重叠。

## 2. 路由判据（进入口 README + 各技能 description）

| 条件 | 选 |
|---|---|
| 有 T4 级 GPU + A4 印刷优先 + 版面 1:1 + 页边设计保真（框外有墨） | scan-retypeset |
| 笔记本 / iGPU / 纯 CPU + 纯黑白省墨 + 需 .sla 可编辑源 或 两端对齐复刻 | paddleocr-digitize（Scribus/build_pdf 双路线，按纸质实测分流） |
| 无 GPU、离线、竖排/不规则版面、无法装 PaddleOCR MCP | guji-restore |

注：margin_strip 适用判据是"**插图框外有墨**"（哪吒有、龙子太郎无——后者右侧装饰条在框内，
p5/p20/p50 已核）。scan-retypeset 的 margin_strip 配置本身已是"空白页边自动跳过"，判据一致。

## 3. Cherry-pick 清单：paddleocr-digitize ← scan-retypeset

| # | 项 | 来源（对方） | 落到我们 |
|---|---|---|---|
| A1 | 模板反混合 erase_tmpl（彩色图上像素一致覆盖层，空白页测吸收场 a=(P−T)/P） | `07_special.py::_erase_template` | SKILL 水印节升级 + 新脚本（现 remove_watermark 只管平铺斜纹） |
| A2 | Ghostscript 终优化 | README L150 `gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.5 -dPDFSETTINGS=/prepress` | build_pdf 路线 S5 之后加一步（刘秀上 82.8MB → 预期 ~40-50MB） |
| A3 | 页边 running design 1:1 重绘 | `03_enhance.py::crop_margin_strip`（L234，config `margin_strip`） | build_pdf/Scribus 重建模型补"框外墨"层；触发=检测到框外墨，非默认路径 |
| A4 | 水印判定子技能（证据框架：整书重复行 + 模板 recall + 姊妹卷对照；后加 vs 原书设计） | `references/watermark.md` | 并入我们水印节；天岸马案例改为其判定（扫描期叠加，姊妹卷无此标记为证）——bread 原标签"出版社装饰文字"为误标，已认 |
| A5 | 基线 probe render 实测 ink_em/asc_em | 其 05_typeset 方法 | build_pdf 常量 0.88 的可选精化（现常量 + QA 精调 0.86-0.90 仍为默认） |
| A6 | OCR bbox 坐标系坑（server 预处理空间长边≤1920px ≠ 页面像素，必须映射） | `references/pitfalls.md` | SKILL 事故手册 +1 条 |
| A7 | T4 + PaddleOCR-VL 性能档（VL ~5.5s/页、OCR ~0.3s/页、100 页 1-1.5h） | `references/performance.md` | HARDWARE.md 新增 GPU 分档行 |

## 4. Cherry-pick 清单：scan-retypeset ← paddleocr-digitize

| # | 项 | 来源（bread） | 落到对方 |
|---|---|---|---|
| B1 | score<0.6 自动救援循环（阈值分诊→条带=帧底→页底、百分位拉伸+2x→逐行 det 框救援→全书条带兜底→密集网点页转人工的停止边界） | SKILL S4 + `strip_all.py`/`rescue_lines.py` | 其 04_merge/06_verify 加 score 分诊层（现为人工挑页重跑 08_batch_infer） |
| B2 | 字符级 QA（rawdict 逐字符 ±14pt、U+00B7→U+2219 归一、剔除空白再 probe、重复短语取并集 bbox 最近） | SKILL S6 + QA 脚本 | 其 06_verify 行级检查之外补字符级（堵行内漂移漏报/点号变体假警报） |
| B3 | D-map 跨页反解（所有黑白页白底像素通道平均得纯图案，无需白纸页，2D 相位搜索，r 随局部亮度变） | SKILL 水印节 | 其 `references/watermark.md` 增补（管平铺斜纹，与其 erase_tmpl 场景不重叠，共存） |
| B4 | 竖排列配方（90° 旋转 det + 逐字堆叠） | SKILL 竖排节 | 其 07_special 增竖排 handler |
| B5 | 两端对齐逐字 slot（含数字行按 advance 加权；reportlab 版可直接用同法控行宽） | SKILL 两端对齐节 | 其 05_typeset（堵 justify 行宽膨胀） |
| B6 | 纸质实测双路线决策（R−B 通道差分流：白净→Scribus、泛黄→build_pdf） | SKILL 决策节 | 其 config 增 route 字段判据 |
| B7 | HARDWARE.md iGPU/CPU 分档（i7-1165G7/Iris Xe 8-12s/页、纯 CPU 30-60s/页、iGPU 批量后需重启的坑） | bread HARDWARE.md | 合并入根 HARDWARE.md |
| B8 | 每页统一字号=长行中位数（逐行 min 会短行漂移 1.6-4pt）——对方已自认吸收 | SKILL S5 | 其 05_typeset（对方第 3 条已认） |

## 5. 合并机制

1. 对方把 `test-mc12le000` 重构成 `skills/scan-retypeset/`（frontmatter 对齐 pi 格式：
   `name`/`description`/`compatibility`；其 `category`/`tags`/`triggers` 字段并入 description
   或留 SKILL 正文），推 `test-mc12le000-restructured`。
2. bread 拉整合分支 `integrate`（从 bread HEAD 301ff6a），merge 重构分支——目录并排，预期零冲突。
3. 双方按清单第 3/4 节认领 cherry-pick（各自在自己技能内改），各推各的分支。
4. 顶层 README 改写为入口路由（第 2 节表），HARDWARE.md 合并（A7+B7）。
5. 技能 frontmatter 统一：三个技能同格式，加载器只认其一。

## 6. 验证

- 两书回归：刘秀上（Scribus 路线）QA 504/506 偏差 0；刘秀下（build_pdf 路线）524/524 超标 1（已接受）——cherry-pick 后重跑不得劣化。
- 交叉验证：scan-retypeset 在 bread 机器（iGPU）上跑刘秀下一页样张，确认无 T4 时流程可降级。
- 新能力各出 1 页样张：A3 margin_strip（哪吒类书）、A1 erase_tmpl（彩色图水印页）、B3 D-map（alibaba40 封面）。

## 7. 已核事实（防再犯）

- 龙子太郎**无**框外页边设计条：p5/p20/p50 右侧装饰条均在插图框线内（属画面内容，frame 裁剪已含）。
  margin_strip 动机案例只有哪吒；适用判据=框外有墨。
- 刘秀下册"开张天岸马"：姊妹卷（上册）同扫描源无此标记 + 模板 recall=1.0 → **扫描期叠加水印**，
  非印书装饰。删除动作不变，判定与证据留痕按 A4 框架。
