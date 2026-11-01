# guji-digitize —— 扫描旧书 / 古籍 / 连环画 数字化重制（agent 技能库）

把**无文字层的扫描版 PDF**（泛黄老书、连环画、图画书、老杂志）重制为
**可打印的黑白 PDF + 坐标严格一致、可检索可选取的文字层**，整套流程沉淀为两个
可直接被编码 agent（pi / Claude Code 等）加载的技能 + 一批与书无关的参数化脚本。

## 两个技能，按书选路线

| | `skills/paddleocr-digitize` | `skills/guji-restore` |
|---|---|---|
| 识别引擎 | **PaddleOCR MCP**（官方 server + 自建版面检测 server） | **LLM 视觉逐行读**（完全离线） |
| 输出 | 纯黑白二值（Otsu），最省墨 | 灰度白化，插图保留灰调层次 |
| 文字 | 按原 quad **严格原坐标**放置（不重排） | 逐行重排（行数/首字/尾字/空行与原书绝对一致） |
| 典型对象 | 带黑边框插图 + 横排字幕的连环画/图画书/老杂志 | 线装古籍、版式复杂老书、要求重排的字库本 |
| 依赖 | Python venv + PaddleOCR MCP + （可选）Scribus MCP | 仅 Python + poppler + 一个 CJK 字体 |

两技能可混用：主流程走 paddleocr-digitize，个别疑难页（复杂版式）交给
guji-restore 的 LLM 路线；同一批书质量不同时**逐本实测背景后分路线**
（白净 → Scribus 路线复刻两端对齐字距；泛黄 → PyMuPDF Otsu 路线）。

## 已验证战绩（全部全流程跑通 + 逐行字符级 QA）

| 书 | 页数 | 要点 |
|---|---|---|
| longzitailang（龙子太郎连环画） | 139 | 首本全流程：摆正/救援/竖排扉页/水印尾页 |
| alibaba40 | 63 | 水印去除（浅灰斜纹反解）、摆正、CPU-only |
| 巴黎圣母院（半色调木刻） | 208 插图 | whiten 保网点、逐行救援、引号归一化 |
| 孙膑和庞涓（数字扫描 PDF） | 114 | **Scribus MCP 路线**（.sla 源文件）、条带权威重识别 |
| 刘秀 上/下册（CC 下载站） | 156+164 | **双路线批量**：白净→Scribus 两端对齐 slot；泛黄→build_pdf；版权页单元格表格重建 |

## 快速开始

```sh
# 1. 安装技能（pi 用户放到 ~/.agents/skills/ 或 ~/.pi/agent/skills/；
#    Claude Code 用户放到项目 .claude/skills/ 或 ~/.claude/skills/）
cp -r skills/paddleocr-digitize skills/guji-restore <skills-dir>/

# 2. 环境（paddleocr-digitize 需要；guji-restore 只需 Python+poppler+字体）
python3.12 -m venv <PADDLEOCR_HOME>/venv
<PADDLEOCR_HOME>/venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    mcp==2.3.0 fastmcp paddleocr-mcp pymupdf opencv-python numpy \
    onnxruntime onnxruntime-openvino openvino
export PADDLEOCR_MCP_VENV_BIN=<PADDLEOCR_HOME>/venv/bin
# CJK 字体：apt install fonts-lxgw-wenkai（或任意 CJK TTF，用 --font 指定）

# 3. 让 agent 干活（对 agent 说人话即可，技能自带门径/基准页/验收流程）：
#    “把 xxx.pdf 数字化重制，用 PaddleOCR，要求纯黑白、文字可检索、坐标与原书一致”
```

环境细节（MCP 注册、iGPU/OpenVINO 加速、CPU 回退、远程 GPU）见
`skills/paddleocr-digitize/references/mcp-setup.md`。

## 目录结构

```
skills/
├── paddleocr-digitize/
│   ├── SKILL.md                 # 技能主体：门径、S1-S6 流水线、各阶段心得、事故手册
│   ├── scripts/
│   │   ├── mcp_batch.py         # S1 渲染+S1.5 摆正+S2 识别（断点续跑）
│   │   ├── process_page.py      # S3 插图截取/边框对齐/Otsu 或 whiten 二值化
│   │   ├── rescue_lines.py      # S4 低置信乱码行条带救援（score<0.6 自动找页）
│   │   ├── line_rescue2.py      # S4 变体：逐行 det 框救援（行距窄/网点底）
│   │   ├── fix_quotes.py        # 引号保守归一化 + 待人工清单
│   │   ├── build_pdf.py         # S5 PyMuPDF 重建（严格原坐标、字体子集、drop/keep）
│   │   ├── scribus_build.py     # S5' Scribus MCP 重建（plan JSON 驱动，出 .sla）
│   │   ├── test_page.py         # S6 逐页字符级比对测试 + 并排对比图
│   │   ├── remove_watermark.py  # 彩色页浅灰斜纹水印反解去除
│   │   ├── layout_server.py     # 自建版面检测 MCP server（PP-DocLayout_plus-L）
│   │   └── remote_gpu_server.py # 远程 GPU 包装器（ssh stdio / HTTP，scp 缓存）
│   └── references/
│       ├── mcp-setup.md         # MCP 环境搭建（venv/注册/accel/CPU 回退）
│       ├── ocr_openvino.yaml    # iGPU 加速 pipeline（~8-12s/页）
│       ├── ocr_cpu.yaml         # 纯 CPU pipeline
│       ├── json-schema.md       # 逐页 JSON schema 与下游依赖
│       └── scribus-route/       # Scribus 路线参考规划器（孙膑/刘秀两套）
│                                 #   + overflow_check.py 帧级折行检测
│                                 #   + strip_all.py 条带并行重识别
│                                 #   + strip_fix.py 词表订正
└── guji-restore/
    ├── SKILL.md                 # 技能主体：S0-S5、校准法、失败目录
    ├── requirements.txt
    ├── scripts/                 # s0_classify / s2_segment / s3_whiten /
    │                            # s4_fill / s5_typeset / bookcfg（与书无关）
    └── references/              # PLAN-template / calibration / failure-catalog /
                                 # schema / book.example.json
```

## 设计要点（两个技能共同的纪律）

1. **门径先行**：侦察（渲染 contact sheet 逐页目检分类）→ 基准页全流程 +
   用户视觉验收 → 才准批量。所有书特定参数落 `book.json`，脚本与书无关。
2. **坐标是唯一真理**：2x 渲染像素坐标贯穿全书，重建时精确映射、不做任何重排；
   QA 用逐字符（`rawdict`）行级比对（位置 ±14pt / 宽度容差），**ALL PASS 才算完成**。
3. **失败要可自动发现**：低置信乱码行用 score<0.6 自动找页救援；帧级前缀匹配
   抓“行数不变但末字折行”；字号一致性逐页 span size 检查。
4. **决策留痕**：特殊页处理、删页、水印 —— 先定后做，写进 PLAN/决策表。

## 许可与致谢

- 识别引擎：PaddleOCR / PaddleX（百度）；版面模型 PP-DocLayout_plus-L。
- 字体：霞鹜文楷 LXGW WenKai（SIL OFL 1.1）。
- Scribus 路线依赖第三方 [scribus-mcp](https://pypi.org/project/scribus-mcp/)
  （本仓库不收录其代码）。
- 技能文本与脚本：随源码提供，供学习改造（商业使用请遵循所依赖组件的许可）。
