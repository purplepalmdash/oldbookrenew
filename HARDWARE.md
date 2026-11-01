# 硬件与系统要求（HARDWARE）

结论先行：**不需要独立 GPU**，**Scribus 是可选依赖**（只影响最后拼 PDF 的
一环）。整套技能的全部验证都在一台 2021 年普通笔记本上完成：
**i7-1165G7（8 线程）/ Iris Xe 核显 / 38GB RAM / NVMe SSD**。

## 按路线分档

### ① guji-restore（LLM 识别重排路线）——最轻

识别交给 LLM 视觉模型（走 agent 平台），本机只做图像处理与 PDF 拼装：

| 项 | 要求 |
|---|---|
| CPU | 任意 4 核 x86-64 |
| RAM | 8GB 足够 |
| 磁盘 | 每本书工作目录 ~100-500MB（2x/3x 渲染页）+ 成品 PDF |
| GPU | 不需要 |
| 系统依赖 | Python 3.10+、poppler-utils（pdftoppm/pdfinfo）、一个 CJK TTF 字体 |

### ② paddleocr-digitize（PaddleOCR MCP 路线）——主路线

| 项 | 要求 / 效果 |
|---|---|
| CPU | x86-64 带 **AVX2**（onnxruntime 硬性要求）；4 核可用，8 核舒适 |
| Intel 核显（推荐） | 11 代+ Iris Xe，装 `apt install intel-opencl-icd` → **8-12s/页**（含 layout；150 页 ≈ 25 分钟）。本技能 pipeline yaml 的主推配置（OpenVINO GPU） |
| 纯 CPU（无核显） | 整页 ~30-60s、条带救援 ~5s → 150 页约 2-3 小时。慢但完全可用；pipeline 换 `ocr_cpu.yaml` |
| NVIDIA 显卡 | **不必需**。官方 server 支持 CUDA，但本技能验证的是 OpenVINO/CPU/远程三套配置，N 卡需自行换 yaml 的 providers；或直接走下面的"远程 GPU 模式" |
| 远程 GPU 模式（可选） | 局域网内任意一台带 GPU 的机器即可（验证环境为入门级 RTX 3050 8G——OCR 推理不吃卡），主机器无显卡也能跑 8-12s/页；配置见 `skills/paddleocr-digitize/SKILL.md` S2 节 |
| RAM | 8GB 底线，16GB 舒适（det + rec + layout 三模型常驻约 2-3GB） |
| 磁盘 | PaddleX 模型缓存 **~700MB**（首次运行自动下载到 `~/.paddlex/`）+ 每本书工作目录 150-500MB + 成品 PDF |

**已知坑（详见技能 SKILL.md 事故手册）**

- iGPU 大批量跑完后可能进入坏状态：`onnxruntime` 不再列出
  OpenVINO EP、`clinfo` 无平台，**重启系统才能恢复**。对策：批量识别尽早、
  一次做完；零散救援/特判走 `ocr_cpu.yaml`（不影响质量）。
- EP 注册失败是**抛错**而非静默回退，MCP server 启动即暴露，好排查。

### ③ Scribus 路线（S5'，可选）——无额外硬件

**只在两种情况需要**：想要 `.sla` 可编辑源文件交付物；或书是两端对齐排版、
想精确复刻字距。它只替换主流程"最后拼 PDF"一环（识别仍是 PaddleOCR）：

| 项 | 要求 |
|---|---|
| 硬件 | 与主路线同一台机器即可（Scribus 常驻 ~300MB；构建 154 页文档 ~3.5 分钟、导出 300dpi PDF 另 ~3 分钟） |
| 软件 | Scribus 1.6.1+（Linux/Win/macOS）+ 三方 [scribus-mcp](https://pypi.org/project/scribus-mcp/)（`uvx` 即装）+ Linux 无桌面时 `xvfb-run`；bridge 常驻启动命令见 SKILL.md S5' 节 |

## 一句话配置建议

| 场景 | 配置 |
|---|---|
| 最低（能跑通全流程） | 4 核 / 8GB / SSD（PaddleOCR 走 CPU，批量时间 ×3 左右） |
| 推荐（= 全部验证环境的配置） | 11 代+ Intel 核显笔记本或台式机 / 8-16 线程 / 16GB+ / SSD 20GB+ 空闲 |
| 加 Scribus 路线 | 推荐配置 + 装 Scribus，无额外硬件 |
| 无核显但有 N 卡 | 可换官方 CUDA pipeline（自行验证）或用远程 GPU 模式 |

## 验证环境清单

| 书 | 机器 | 加速方式 | 批量耗时 |
|---|---|---|---|
| 龙子太郎 139p / alibaba40 63p | i7-1165G7 + Iris Xe（笔记本） | iGPU（OpenVINO） | ~20 min / 139 页 |
| 巴黎圣母院（208 插图） | 同上 | iGPU | 同左量级 |
| 孙膑和庞涓 114p | 同上 | iGPU | ~15 min |
| 刘秀上/下册 156+164p | 同上 | iGPU（批量）+ CPU（救援/特判） | 两本合计 ~50 min 识别 |
| （远程 GPU 模式） | 局域网 RTX 3050 8G 机器 | 远端 CUDA | 与 iGPU 同量级 |

## ④ NVIDIA T4（scan-retypeset 路线，2026-11 刘秀上下册实测）

scan-retypeset（VL 版面 + 行 OCR，GPU 批量）在单张 T4 上的实测档位：

| 步骤 | 158 页书（250dpi） |
|---|---|
| `08_batch_infer all`（VL ~5.5s/页 + OCR ~0.3s/页） | ~15-16 min |
| `01_render` 250dpi（poppler，12 核） | ~10 min（两书共享） |
| `batch_runpages`（JOBS=6，CPU 受限） | ~4-10 min |
| Ghostscript /prepress | ~3-5 min |
| 端到端（含 survey/测量，agent 时间） | 无人值守管线 <40 min |

- **单卡规则**：跑 `08_batch_infer` / `09_rescue` 前停掉常驻 PaddleOCR
  MCP 服务进程（两套常驻模型可能 OOM），跑完再启。
- **纯 CPU 机**：识别阶段 ×3-5 慢（VL 是瓶颈；条带救援 `09_rescue` 因
  裁剪图小仍实用）。质量不变（同模型同代码路径）。最低 4 核 / 8GB /
  SSD + 每书工作目录 ~500MB（250dpi 渲染为主）。
- **远程 GPU**：rsync 书目录到 GPU 机跑 08/09，rsync 回 `data/vl` +
  `data/ocr`，本机继续排版（CPU 即可）。详见
  `skills/scan-retypeset/references/hardware.md`。

一句话：**scan-retypeset 有 T4 最快，无 T4 也能跑（慢但可用）；
paddleocr-digitize 在无独显机器上更快**（iGPU/CPU 是它的主场）。
