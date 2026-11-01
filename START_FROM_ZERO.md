# Start from Zero：从零把手里的扫描版 PDF 数字化

> 读者画像：你刚装了 [pi](https://github.com/badlogic/pi-mono)（或同类编码 agent），
> 手里有一本**没有文字层的扫描版 PDF**（泛黄老书、连环画、图画书、老杂志），
> 想要一份**可打印、文字可选中搜索、版面和原书一致**的干净 PDF。
> 除了"会复制粘贴命令"，你不需要会任何别的东西——**读文件、跑流程、调参数
> 全部由 agent 做**，这份指南告诉你需要准备什么、说什么话、在哪里点头。

---

## 0. 先花 10 秒看你要得到什么

输入：`xxx.pdf`（扫描版，无文字层，可能泛黄、可能有后来加的水印）

输出：`xxx_final.pdf`
- A4 可打印、Ghostscript 优化后的成品
- 图在原位置、每行文字在原基线、版面 1:1 对应原件
- 文字可选可搜（字体子集嵌入）
- 每页都过了机器 QA（字数/字符/行位/图位/页码逐页校验）
- 后来扫描方加的水印被去除（**默认去除，你说了算**）

本仓库提供三条技能路线，**agent 会根据你的机器和书自动选**（见 §5）：

| 路线 | 适合 | 识别引擎 |
|---|---|---|
| `scan-retypeset` | 要 A4 印刷品质 + 版面/页边设计 1:1 保真；有 T4 级 GPU | PaddleOCR MCP（GPU 批量） |
| `paddleocr-digitize` | 普通笔记本/无独显；要纯黑白省墨或 .sla 可编辑源 | PaddleOCR（核显/CPU/远程 GPU） |
| `guji-restore` | 最低配置；线装古籍、版式复杂 | LLM 视觉逐行读（无需本地 OCR 引擎） |

---

## 1. 你需要什么硬件（买/不买的决策）

### 1.1 先自检（30 秒）

```sh
nvidia-smi 2>/dev/null && echo "有 N 卡" || echo "没有 N 卡"
clinfo 2>/dev/null | grep -m1 "Intel" && echo "有 Intel 核显" || echo "无核显信息"
nproc; free -g | head -2; df -h / | tail -1
```

### 1.2 四档决策表

| 你的机器 | 走哪条 | 100 页书的大致耗时 | 要不要花钱 |
|---|---|---|---|
| **T4 / RTX 3060(12G)+ 等 N 卡**（系统内存 16GB 以上） | `scan-retypeset`（最快）或 `paddleocr-digitize` | 管线 <40 分钟（T4 实测）；含 agent 测量验收，首本约 1-2 小时 | 不花 |
| **Intel 11 代+ 核显笔记本**（Iris Xe，16GB） | `paddleocr-digitize`（核显 8-12s/页） | 识别 ~25 分钟；全程约 1-3 小时 | 不花 |
| **纯 CPU 笔记本/台式机**（4 核 8GB 起） | `paddleocr-digitize`（CPU 30-60s/页）或 `guji-restore` | 识别 2-3 小时；全程 3-6 小时 | 不花 |
| **普通电脑、完全无加速硬件 / 想最省心** | 租一块云 GPU 按量计费，几小时搞定后关机 | 同 T4 档 | 每本书几美元量级（以云商行情为准） |

**结论：什么都不买也能跑通**（纯 CPU 或 `guji-restore` 档）。钱只买"更快"。

### 1.3 如果决定花钱（两条路）

- **租云 GPU（新手最推荐）**：任选一家云厂商的按量 GPU 实例
  （T4/A10G 级别，显存 ≥8GB；Ubuntu 即可）。用法不是"把书传到云上重做"，
  而是**远程 GPU 模式**：书还在你本机，agent 把需要 GPU 的推理步骤
  放到云机上跑、把结果 rsync 回来。两条 PaddleOCR 路线都有现成的
  远程 GPU 用法：`paddleocr-digitize` 自带 ssh-stdio wrapper（开箱即用），
  `scan-retypeset` 是文档化的 rsync 步骤（其 references/hardware.md Tier 3）。
  跑完释放实例，只付那几个小时的钱。
- **买二手 N 卡**：T4 16GB（服务器卡，注意功耗/散热）或 RTX 3060 12GB
  级别，几百到一千多元（以行情为准）。注意 T4 是无显示的服务器卡，
  接显示器要用亮机卡；纯命令行/SSH 用则无所谓。

### 1.4 扫描源的建议（影响上限，不影响能不能跑）

- 扫描分辨率 **300dpi 最佳**；150dpi 可接受；72-96dpi（如 1024×768 嵌入图）
  也能跑（本仓库的验证书就有这种），但放大后细节有天花板。
- 尽量**平扫、压平、去阴影**；严重歪斜/透印的书 agent 能处理一部分，
  但越干净越省事。
- 彩色书（封面/彩图）保持彩色扫描即可，黑白正文页不需要彩色。

---

## 2. 软件前提（装一遍，一劳永逸）

以下按 **Ubuntu 22.04/24.04** 写（全部验证环境的系统）。
Windows 用 WSL2 里装 Ubuntu；macOS 用 Homebrew 等价物。
不想逐条敲的话，把 §3 末尾的"一键安装话术"整段发给 pi 即可。

```sh
# 系统包（一次）
sudo apt install -y python3 python3-venv python3-pip git \
     poppler-utils ghostscript fonts-lxgw-wenkai

# 有 Intel 核显加速需求时（paddleocr-digitize 路线）
sudo apt install -y intel-opencl-icd
```

- `poppler-utils`：渲染/合并/查字体（pdftoppm、pdfunite、pdfinfo…）
- `ghostscript`：最终 PDF 优化
- `fonts-lxgw-wenkai`：重排用的 CJK 字体（霞鹜文楷）；
  装完记下实际路径（`fc-list | grep -i lxgw` 可查），配置里要用
- Python 3.10+ 即可（PaddleOCR MCP server 要求 3.10–3.13）

各路线的 Python 依赖（venv 里的 pip 包）agent 会按技能 SKILL.md 自己装，
你不用背清单；大致是 numpy / opencv / pillow / reportlab / pymupdf /
onnxruntime 一类纯 Python 生态。

---

## 3. 安装本仓库 + 技能

```sh
# 1) 克隆（HTTPS 或 SSH 均可，换成你拿得到的地址）
git clone https://github.com/purplepalmdash/oldbookrenew.git
cd oldbookrenew

# 2) 安装技能（默认装全部 3 个到 ~/.pi/agent/skills/）
./install.sh

# 3) 装 PaddleOCR MCP server（scan-retypeset 与 paddleocr-digitize 路线需要；
#    guji-restore 不需要）
git clone https://github.com/Nicvank/paddleocr-mcp.git ~/paddleocr-mcp
cd ~/paddleocr-mcp
python3 -m venv .venv
.venv/bin/python install.py --device auto     # 自动检测 N 卡装 GPU 版，否则 CPU 版
```

- 装完后重启 pi（让它重新扫描技能目录和 MCP 配置）。
- 模型（~600-700MB）首次调用时自动下载到 `~/.paddlex/`，无需手动。
- `paddleocr-digitize` 路线还需要把自己技能自带的
  `layout_server.py` 注册进 pi 的 MCP 配置——**不用手改 JSON**，
  把 §4 的话术发给 pi，它会按 `skills/paddleocr-digitize/references/mcp-setup.md`
  写好 `~/.pi/agent/mcp.json` 并验证。

### 自检

```sh
# pi 里应该能看到三个技能（重启后）：
ls ~/.pi/agent/skills/ | grep -E 'scan-retypeset|paddleocr-digitize|guji-restore'

# MCP 自检（pi 对话里让它跑）：
#   “发一张测试图做 OCR，确认 paddleocr MCP 正常”
#   返回带文字的 JSON 即正常
```

### 一键安装话术（懒人版）

如果不想自己敲上面的命令，把下面这段直接发给 pi，它会装完并自检：

```text
帮我装好 oldbookrenew 技能库的完整环境：
1. apt 装 poppler-utils、ghostscript、fonts-lxgw-wenkai、git、python3-venv；
   我有 Intel 核显的话再装 intel-opencl-icd。
2. 克隆 https://github.com/purplepalmdash/oldbookrenew.git 到 ~/oldbookrenew，
   跑 ./install.sh 安装全部技能到 ~/.pi/agent/skills/。
3. 克隆 https://github.com/Nicvank/paddleocr-mcp.git 到 ~/paddleocr-mcp，
   建 .venv 并跑 install.py --device auto。
4. 按 skills/paddleocr-digitize/references/mcp-setup.md 把 paddleocr 与
   paddleocr-layout 两个 server 写进 ~/.pi/agent/mcp.json。
5. 装完后告诉我需要重启 pi，并给出重启后如何验证 MCP 正常的方法。
```

---

## 4. 使用方法：对 pi 说一句话

装好后，pi 的任意会话里：

```text
用 oldbookrenew 把这本书数字化：<绝对路径>/book.pdf
我的机器：<N 卡台式机 / 核显笔记本 / 纯 CPU / 远程 GPU 在 x.x.x.x>。
要求：A4 可打印、版面尽量 1:1、文字可选中。
```

**然后 agent 会按技能的工作流自己干完**，典型过程：

1. **侦察**（几分钟）：渲染样页、量化纸张泛黄、判彩色/黑白、
   识别特殊页（封面/扉页/版权页/广告页）、**整书判定水印**。
   → 会来问你 1-2 个问题（见下节"你要做的只有这几件事"）。
2. **基准页校准**：挑一页代表性正文页完整跑一遍，给你看
   **加工前后对比图**，请你视觉验收。**你点头之前不会开批量。**
3. **批量推理 + 排版**（无人值守，时间见 §1.2）。
   GPU 纪律：批量前 agent 会停掉常驻 MCP server 避免双份模型占显存。
4. **逐页机器 QA**：每页校验文字字符/行数/位置/图位/页码，
   低置信行自动走救援（条带增强重 OCR），全书条带兜底审计。
5. **特殊页处理 + 合并 + Ghostscript 优化** → `book_final.pdf`。
6. **交付摘要**：页数、体积、QA 通过率、做了哪些取舍（删了哪些页、
   去掉了哪些水印）。

### 你要做的只有这几件事（全程约 10-20 分钟人工时间）

| 时机 | 你做 | 说明 |
|---|---|---|
| 侦察阶段 | 回答水印判定 | agent 会列出它认为的"后来加的水印"和证据（整书重复、姊妹卷对照、模板匹配）。**默认去除，你可以留** |
| 侦察阶段 | 确认特殊页处理 | 封面/扉页/版权页是"原样保留"还是"重排"，agent 给建议 |
| 基准页 | 看一眼对比图，说"过"或指出问题 | 这是质量闸门，值得认真看 30 秒 |
| 交付前 | 抽 3-5 页目检 | 告诉 agent 页码，它渲染对比图给你 |

---

## 5. 路线选择（你不用想，但值得知道 agent 在想什么）

- 有 T4 级 N 卡 + 要印刷品质 → `scan-retypeset`
- 核显/纯 CPU/远程 GPU，要黑白省墨或 .sla → `paddleocr-digitize`
- 最低配置/离线/版式古怪 → `guji-restore`
- 同一批书质量参差 → **逐本实测纸张后分流**（agent 会量化：
  白净书 vs 泛黄书走不同增强档位）
- 某条路线的疑难页 → 可交给另一条路线处理（三技能可混用）

判不准就直接把书丢给 pi 说"你来判断"——路由判据已写进各技能
description 和根 README，agent 会自己路由。

---

## 6. 常见问题速查（新手版）

| 症状 | 处理 |
|---|---|
| 显存爆（OOM） | agent 会在批量前停常驻 MCP；若仍爆，降 `JOBS` 或换 CPU 救援档 |
| Intel 核显大批量后"失联"（EP 不再列出/`clinfo` 无平台） | 已知坑：**重启系统**恢复；零散小图救援走 CPU 配置（不影响质量） |
| 跑得很慢 | 看走的是哪套 yaml/哪条路线；有闲置 GPU 机器就开远程 GPU 模式 |
| 字体报"找不到" | 配置里的 `font` 路径指向了不存在的 TTF；用 `fc-list` 找真实路径让 agent 改 |
| 某页 QA 不过 | 不用你管：agent 重跑/救援/特判；只有反复不过的页才会来找你定夺 |
| 图被切到 / 偏了 | 给 agent 页码 + 一张你截的问题图，它会按像素投影重测坐标 |
| 结果"还黄" / "发灰" | 增强档位问题（bw_clear/vector/whiten），告诉 agent 期望，它调档重跑该页 |
| 首行文字压到插图 | 基准页验收时就会暴露；没暴露的话同样"给页码，让 agent 重测" |
| 不确定删掉的页对不对 | 所有删除/保留决策 agent 都留了清单（drop_pages/keep_pages），翻配置即可 |

更细的坑在 `skills/*/references/pitfalls.md`（40 条+，含复现与对策）。

---

## 7. 时间预算（100 页书，参考值）

| 档位 | 无人值守管线 | 含 agent 测量/你验收的会话 |
|---|---|---|
| T4 | <40 分钟 | 首本 1-2 小时；熟手复本 <1 小时 |
| 核显 | 识别 ~25 分钟 + 排版 | 1-3 小时 |
| 纯 CPU | 识别 2-3 小时 | 3-6 小时 |
| guji-restore | 取决于 LLM 吞吐 | 与 CPU 档同量级，另有 LLM 调用成本 |

首本总是最慢的（agent 要摸清你的书、你也要熟悉验收节奏），
**第二本起显著提速**——同一批书（如一套多册）agent 会直接复用
已验证的配置骨架。

---

## 8. 深入（可选）

- 路由总表 / 已验证战绩 / 目录结构：根 [README.md](README.md)
- 硬件分档与实测数字：[HARDWARE.md](HARDWARE.md)
- 各技能完整工作流：`skills/<技能名>/SKILL.md`
- 踩坑全集：`skills/scan-retypeset/references/pitfalls.md`、
  `skills/paddleocr-digitize/SKILL.md` 事故手册、
  `skills/guji-restore/references/failure-catalog.md`
- 四个已验证书的完整配置 + 案例复盘：
  `skills/scan-retypeset/examples/`（哪吒/龙子太郎/巴黎圣母院/刘秀）
