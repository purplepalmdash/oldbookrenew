# oldbookrenew —— 扫描旧书 / 古籍 / 连环画 数字化重制（agent 技能库）

把**无文字层的扫描版 PDF**（泛黄老书、连环画、图画书、老杂志）重制为
**可打印、文字可检索可选取、版面与原件严格一致**的 PDF。整个流程沉淀为
三个可被编码 agent（pi / Claude Code 等）加载的技能 + 与书无关的参数化
脚本，由不同机器上的 agent 各自贡献、在 `main` 分支上整合。

## 三个技能，按书和机器选路线

| | `skills/scan-retypeset` | `skills/paddleocr-digitize` | `skills/guji-restore` |
|---|---|---|---|
| 识别引擎 | PaddleOCR MCP（VL 版面 + 行 OCR，GPU 批量） | PaddleOCR MCP（det/rec + 自建版面 server，iGPU/CPU/远程 GPU 均可） | LLM 视觉逐行读（完全离线） |
| 版面重建 | **1:1 重排**（reportlab；图在原框、行在原基线、页边 running design 1:1 重绘） | 严格原坐标文字层（PyMuPDF，不重排）；可选 Scribus 路线出 .sla | 逐行重排（行数/首尾字/空行绝对一致） |
| 图处理 | 去黄 bw_clear / 矢量化高对比黑白 / 彩色保持 | Otsu 纯黑白（省墨）或灰度白化保网点 | 灰度白化，插图保留灰调层次 |
| 低置信救援 | `09_rescue` score 分诊 + 条带增强重 OCR + 全书条带兜底 | S4 条带救援 / 逐行 det 框救援 / 条带兜底 | est 字数交叉校验 + LLM 双读 |
| 特殊页 | 配置驱动 handler（封面/扉页/提要/版权页）+ 竖排 `vertical` 行 | 手工几何 JSON（单元格表格、竖排列） | PLAN 决策表 + 手工几何 |
| 水印 | 水印判断子技能（整书判定后加 vs 原书设计，默认去除）：`watermark_boxes` / `erase_tmpl` 模板反解 / D-map 平铺反解 | Otsu 天然抑制浅灰 + 固定角几何删行 + 封面斜纹反解 | 页缘不重排自然消失；图内人工标记 |
| 硬件 | T4 级 GPU 最快（CPU 可跑，慢 3-5×）；远程 GPU 模式 | 无独显可用：iGPU 8-12s/页、纯 CPU 可用、远程 GPU | 4 核 / 8GB 即可 |
| 典型对象 | 泛黄/洁净印刷连环画、旧书，要 A4 印刷成品 + 页边设计保真 | 黑边框插图+横排字幕，要纯黑白省墨 / .sla 源文件 / 无独显机器 | 线装古籍、版式复杂、完全离线环境 |

**路由速查**：有 T4 且要 A4 印刷品质 + 页边设计保真 → `scan-retypeset`；
笔记本/无独显/要 .sla/数字扫描书 → `paddleocr-digitize`；无 GPU 无 MCP、
LLM 在线 → `guji-restore`。三技能可混用：主流程跑一条，疑难页交另一条。
同一批书质量不同时**逐本实测背景后分路线**（白净 vs 泛黄的量化判据见
`skills/scan-retypeset/SKILL.md` "Multi-book sessions"）。

硬件/系统要求分档（最低配置、核显加速、T4 实测、远程 GPU）见
[`HARDWARE.md`](HARDWARE.md)。

## 已验证战绩（各技能全流程跑通 + 逐页机器 QA）

| 书 | 页数 | 技能 | 要点 |
|---|---|---|---|
| 龙子太郎（河北美术 1984） | 139 | 两条技能都跑过 | vector 变体基准书；右栏 running design 保真 |
| 哪吒 | 70 | scan-retypeset | 左栏 running design、藏书印、~12 min 无人值守 |
| 巴黎圣母院（半色调木刻） | 213 | 两条技能都跑过 | dot 编号、无号页、整版底图；whiten 保网点 |
| 孙膑和庞涓（数字扫描） | 114 | paddleocr-digitize | Scribus MCP 路线（.sla）、条带权威重识别 |
| 刘秀 上/下册 | 156+164 | 两条技能都跑过 | 双路线批量：白净→Scribus slot，泛黄→bw_clear；全册扫描水印去除（模板反解/几何删行互相印证）；版权页单元格重建 |

## 安装

```sh
# pi 用户（默认，安装全部技能到 ~/.pi/agent/skills/）
./install.sh

# 只装某个技能 / 装到别处
./install.sh scan-retypeset
./install.sh paddleocr-digitize guji-restore --dest ~/.claude/skills   # Claude Code

# 也可以直接整目录软链（技能是纯文本+脚本，无构建步骤）：
ln -s $(pwd)/skills/scan-retypeset ~/.pi/agent/skills/scan-retypeset
```

各技能的运行环境依赖（venv、poppler、Ghostscript、PaddleOCR MCP server、
Scribus 可选）见各自 `SKILL.md` 的 Prerequisites/Setup 一节。

## 目录结构

```
README.md                # 本文件（路由入口）
HARDWARE.md              # 硬件/系统要求分档（最低/核显/T4 实测/远程 GPU/验证环境清单）
install.sh               # 安装 skills/* 到 agent 技能目录
LICENSE                  # MIT（技能文本与脚本）
skills/
├── scan-retypeset/      # 1:1 重排管线（00-09 阶段脚本 + 配置驱动特殊页 +
│                        #   水印判断子技能 + 35+5 条 pitfalls）
├── paddleocr-digitize/  # PaddleOCR 主路线（S1-S6 + Scribus 路线 + 救援层）
└── guji-restore/        # LLM 视觉离线路线（S0-S5 + 校准法 + 故障目录）
```

## 设计纪律（三个技能共同）

1. **门径先行**：侦察（渲染 contact sheet 逐页目检分类）→ 基准页全流程 +
   用户视觉验收 → 才准批量。所有书特定参数落配置文件（`config.json` /
   `book.json`），脚本与书无关。
2. **坐标是唯一真理**：渲染像素坐标贯穿全书，重建精确映射、不做任何
   重排漂移；QA 逐页机器校验（图框偏差、行位置、文本内容、面板号锚点、
   字符级多重集），**ALL PASS 才算完成**。
3. **失败要可自动发现**：低置信乱码行用 score 阈值自动找页救援；帧级
   前缀匹配抓"行数不变但末字折行"；字号一致性逐页检查。
4. **决策留痕**：特殊页处理、删页、水印 —— 先定后做，写进 PLAN/决策表
   /config。

## 许可与致谢

- 技能文本与脚本：MIT（见 [LICENSE](LICENSE)），供学习改造；商业使用请
  遵循所依赖组件的许可。
- 识别引擎：PaddleOCR / PaddleX（百度）；版面模型 PP-DocLayout_plus-L /
  PaddleOCR-VL。
- 字体：霞鹜文楷 LXGW WenKai（SIL OFL 1.1）。
- Scribus 路线依赖第三方 [scribus-mcp](https://pypi.org/project/scribus-mcp/)
  （仓库不收录其代码）。

## 协作约定（多机器/多 agent）

- `main` 为整合分支（默认分支），以 `main` 为准。
- 各机器在自己的分支上迭代（分支名 = 主机名），目录并排
  （`skills/<技能名>/`），互不删改对方文件；合并用新整合分支，
  冲突面最小。
- 吸收对方技能的能力时：代码进自己技能目录，文档在对方技能目录
  只增不改（或先在 PR/讨论中确认）；CHANGELOG 记录来源。
