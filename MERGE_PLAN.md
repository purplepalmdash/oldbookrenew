# oldbookrenew 整合方案（bread × test-mc12le000）

状态：草案（未提交）。bread 起草，待双方认领 cherry-pick 项后在整合分支落地。

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
