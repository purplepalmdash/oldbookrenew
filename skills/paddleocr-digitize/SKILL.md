---
name: paddleocr-digitize
description: "用 PaddleOCR MCP 把扫描版书刊（连环画/图画书/老杂志：带边框插图+字幕，泛黄、无文字层）数字化重排为可打印、文字可选取、坐标与原书严格一致的 PDF：逐页 JSON（layout 坐标+每行 quad 起止点）、插图边框水平对齐+Otsu 黑白化、低置信乱码字幕行救援（条带增强重识别）、混排书分治路由（对话气泡默认不OCR/横排→标准OCR/竖排→vcol专用/低置信→视觉模型目读）、竖排文字（vcol整列识别或90°旋转识别+逐字堆叠）、字符级逐页比对测试。与 guji-restore（LLM 识别/灰度保网点路线）互补：用户要求用 PaddleOCR/PaddleOCR MCP 识别、或要求纯黑白+严格原坐标重建、或书带边框插图+横排字幕时选本技能。"
compatibility: "Python 3.10+ venv（pymupdf, opencv-python, numpy, mcp>=2.3, onnxruntime, onnxruntime-openvino, openvino）+ PaddleOCR MCP server（官方 paddleocr_mcp + 自建 layout_server.py，见 references/mcp-setup.md）+ 一个 CJK TTF 字体（LXGW WenKai）。国内镜像源 pip 安装。"
metadata:
  version: "1.4"
  origin: "Distilled from the longzitailang project (139-page scanned lianhuanhua) + alibaba40 project (63-page, watermark/deskew/CPU-only, 2026-10) + liuxiu project (test3 上/下册 156+164 页双路线批量：白净→Scribus 两端对齐 slot，泛黄→build_pdf Otsu，2026-11) + oldbookrenew main 整合（2026-11：A 组吸收——erase_template 模板反混合 / build_pdf --gs Ghostscript 终优化 / --ink-top + probe_baseline 实测基线 / margin_ink_check 框外墨检测门 / 水印判定子技能） + 混排书分治路由（2026-11-03：对话气泡默认不OCR / 横排→标准ocr(8765)、竖排→vcol专用服务(8785) / 置信度分流→视觉模型目读；哪吒闹海·青蛙与老虎·白蛇传 301页验证）"
---

# 扫描书刊数字化（paddleocr-digitize）

把泛黄扫描 PDF 变成**可打印的黑白 PDF + 严格原坐标的可选取文字层**。
识别全部走 **PaddleOCR MCP**（官方 `paddleocr` server 出文字，自建
`paddleocr-layout` server 出版式坐标）。

## 何时用本技能 / 何时用 guji-restore

| 判据 | paddleocr-digitize（本技能） | guji-restore |
|---|---|---|
| 识别引擎 | 用户要求 PaddleOCR / PaddleOCR MCP | 无引擎要求，用 LLM 视觉逐行读 |
| 输出 | 纯黑白二值（Otsu），最省墨 | 灰度白化，插图保留灰调层次 |
| 文字 | 按原 quad 严格坐标放置（不重排） | 逐行重排（首字/尾字/空行校验） |
| 典型对象 | 带黑边框插图+横排字幕的连环画/图画书 | 线装古籍、版式复杂老书 |
| 坐标数据 | 逐页 JSON 含 layout 坐标 + 每行起止点 | work/NNN.json 行框 |

两者可以混用：先跑本技能流程，个别疑难页（复杂版式）交给 guji-restore 的 LLM 路线。

## 门径 —— 开工前必须按序完成

1. **侦察**：`pdfinfo <src.pdf>`（页数/尺寸/方向）→ 渲染 50dpi 前 8 页 +
   中间 2 页 + 最后 2 页 contact sheet → **逐页目检**并分类：
   彩色封面/保持原样页 / 扉页（竖排等特殊版式）/ 纯文字页 / 插图+字幕正文页 /
   水印尾页。**不要假设所有页尺寸一致**（本项目 p139 只有 240×192）。
2. **基准页**：选一页代表性正文页（用户给参考样张更好），先只跑这一页
   全流程 S1→S6，请用户**视觉验收**后再批量。
3. **book.json**（`<out>/book.json`，全部书特定参数）：
   ```json
   {
     "keep_pages": [1, 139],            // 原样插入（insert_pdf，保留原尺寸/彩色）
     "special_pages": {"2": "page002_special.json"},  // 手工几何页
     "text_only_pages": [3, 138],       // 纯文字，无插图
     "out_name": "书名字_数字化.pdf",
     "title": "书名", "author": "...", "subject": "出版社 年份"
   }
   ```
4. **决策留痕**：特殊页处理方式、删不删水印/广告页 —— 先定后做。

## 目录约定（`<out>` = digitize/ 风格输出目录）

```
<out>/
├── book.json               书特定参数
├── <成品>.pdf
├── data/pageNNN.json       识别结果（S2；S4 救援后更新）
├── data/pageNNN_special.json  特殊页手工几何（如 p2）
├── work/pages/pNNN.png     2x 渲染
├── work/images/pageNNN_k.png  处理后黑白插图
├── work/images/pageNNN.json  插图几何（帧中心/宽高/倾角）
└── work/qa/compare_pNNN.png  原图|重建 并排对比图
```

## 流水线

```
<src.pdf>
  S1 渲染 Nx ──────────────── scripts/mcp_batch.py（内置渲染）
  S1.5 整页摆正 ───────────── scripts/mcp_batch.py（边框倾角+中位回退，旋转回写）
  S2 MCP 识别 layout+ocr ──── scripts/mcp_batch.py  → data/pageNNN.json
  S3 插图截取/对齐/二值化 ──── scripts/process_page.py → work/images/
  S4 乱码字幕行救援 ────────── scripts/rescue_lines.py（自动找 score<0.6 的页）
  S5 重建 PDF ─────────────── scripts/build_pdf.py
  S5' Scribus 重建 ────────── scripts/scribus_build.py（plan JSON 驱动，需常驻 bridge）
  S6 逐页比对测试+对比图 ───── scripts/test_page.py   （ALL PASS 才算完成）
```

旁路：`scripts/remove_watermark.py`（彩色页斜纹水印去除，见下）。

## 混排书籍分治路由（横/竖 + 气泡 + 置信度）—— 2026-11 三书 301 页验证

文字方向混杂的书（横排前/后页 + 竖排正文等），**先定结构再分治**：

1. **定结构**：整书低清 contact sheet 总览（≤2000px，一轮只读 1 张）→
   划分 前页/正文/后页 并确认各部分文字方向（横/竖）。
2. **按方向分治**：
   - 横排 → 标准管线（本技能 S1–S6 原样；纯提文本时整段 PDF 直接送 8765 `ocr` 单调用）；
   - 竖排 → **vcol 竖排专用服务**（aiBox:8785，工具 `ocr_vertical`，图片输入）：
     整列识别 + 右起阅读序 + 标点恢复 + 逐字 y + `dark_bg`（黑底白字，如页码，自动反色）
     + `□` 占位（有墨迹未识别，多为标点）。一次性 streamable HTTP 客户端、
     **一个会话循环逐页**（勿每页起一次 MCP 工具调用），实测 0.2–1.3s/页、
     286 页 ≈ 6 分钟。渲染 dpi：大页（~768pt 宽）150dpi、小字小页（~280pt 宽）
     300dpi（字高 <14px 时 □ 显著增多；先单页 A/B 试 dpi 再批量）。
     返回的 `quad` + `chars.yc` 可换算 special JSON 的 `vertical_columns` 几何
     （见"竖排文字/扉页特判"节）。
   - 8775（paddleocr_mcp + `ocr_vert.yaml` 竖排调参流水线，接口同 8765）：
     只需整本粗读、不要求列序/标点/页码时的单调用替代；精排用 vcol。
3. **插图里的对话框/对话气泡默认不做 OCR**：speech bubble 内的文字默认不建
   文字层（S3 二值化插图原样保留，信息不丢）；文字层只管插图框外的字幕/
   正文行。例外：用户明确要台词，或气泡位于插图框外。
4. **置信度分流**：
   - 高置信（本流程 score≥0.62；simple 尾行 >~90%；vcol 列 score≥0.7 且无 □）→ 直接用；
   - 低置信/乱码行 → 先走 S4 救援配方；
   - 救援仍失败 → **视觉模型目读**：渲染源图 ≥2x（长边 ≤2000px）交视觉模型
     读出乱码区回填，标 `rescued: "vision-read"`；
   - 目读不稳 → 人工校对（`rescued: "manual-proofread"`）。系统性误字
     （如 哪吒→哪旺猴）确认后可全书批量替换。

脚本都在本技能 `scripts/` 下，`<out>` 目录经 `--out` 传入；运行示例：

```sh
PY=<paddleocr-venv>/bin/python
SK=<本技能目录>
$PY $SK/scripts/mcp_batch.py     --src book.pdf --out digitize --scale 2.0
$PY $SK/scripts/process_page.py  --out digitize
$PY $SK/scripts/rescue_lines.py  --out digitize
$PY $SK/scripts/build_pdf.py     --src book.pdf --out digitize
$PY $SK/scripts/test_page.py     --src book.pdf --out digitize --compare 2,5,50
```

## S1 渲染 —— 2x 原则

源扫描 ~1050×900px 时渲染 **2.0x**（→2048×1536）。坐标精度与 OCR det
对小字/浅字的召回都依赖分辨率；2x 下每行 quad 高度 ~90-110px，PaddleOCR
识别率最高。渲染坐标（px）是全书唯一坐标基准，S5 再统一映射到 pt。

**低分辨率源（原生 <700px）升到 3x 甚至 4x**（如 601×490 的扫描 → 3x
=1803×1470 识别、4x 做水印图）。代价：边框检测在低 x 下更不可靠（见 S1.5）。

## S1.5 整页摆正（mcp_batch.py）

扫描页常带 0.3°–0.9° 斜摆，S3 只能摆正插图框，字幕行仍是斜的 → 文字层
与图框不同轴。**在 S1 渲染后、S2 识别前**对整页旋转摆正：

1. 探测：每页灰度→Otsu→形态学→minAreaRect 找最大框，从**顶边**算倾角
   （同 S3，不用 angle 元数据）。低 x 下检测不全（601px 页只检出 24/62），
   检不到的页用**全页中位倾角**回退。
2. 执行：|angle|>0.03° 才旋转（`rotate 2x`，`expand=False`，白边填补），
   回写渲染图；每页 `deskew_deg` 写入 page JSON。
3. S3/S5 的几何全部在摆正后的坐标系里算，build_pdf 的 `rot_quad` 会把
   文字 quad 反向旋回与摆正图框同轴（文字不再斜摆）。
4. **坑**：中位回退在"真直页"上会把 0.4° 强加给本来直的页 → 阈值内不旋转、
   旋转量小（<1°）时白边几乎不可见，可接受。

## S2 识别（mcp_batch.py）—— 心得

- **JSON schema**（下游全部依赖它）：
  `{page, render_scale, width, height,
     layout:[{label,score,bbox:[x1,y1,x2,y2]}],
     ocr:{text, confidence, lines:[{text,score,quad:[[x,y]×4],
           start_point, end_point}], dropped_in_image_lines}, timings}`
  `start_point`=行左上、`end_point`=行右下（worktips 式"严格起止点"）。
- **两个 server 并发持有**（一个 stdio 会话对两个 server），~8-12s/页，
  139 页 ~20 分钟。已有 JSON 的页自动跳过，断点续跑安全。
- **bbox 两种格式**：det 可能回扁平 `[x1,y1,x2,y2]` 或四点多边形，
  `_norm_quad` 统一成 4 角点。
- 已存在 book.json 时自动跳过 keep/special 页（原样页不需要识别；
  竖排扉页的标准识别结果是垃圾，勿用）。
- **插图内 OCR 行过滤**：layout 的 image bbox 比真实插图框**更松**（常伸出
  框外 20-40px）。判断行是否在图内用**行中心点 + 4px 容差**，不要用
  行角点/起点在框内（会把紧贴图框的字幕行误杀，本项目 p75/p78/p93/p120/
  p123/p137 六页首行就是这样被 layout bbox 误杀的）。**下游（S5）也不要
  再用 layout bbox 二次过滤**。
- `res.is_error`（mcp 2.3.0 属性名，不是 isError）；`input_data` 传**绝对路径**
  （MCP server 工作目录是别的，**相对路径会被拒**：`Relative paths are not
  supported`）。本脚本已对 OCR 输入 `Path.resolve()`。
- **CPU-only 机器**：`layout_server.py` 已做 OpenVINO→CPU 自动回退（启动时
  `get_available_providers()` 探测）；mcp_batch 的 layout/pipeline 默认已
  指向本技能自带文件。CPU 机显式换 CPU pipeline：
  ```sh
  export PADDLEOCR_MCP_PIPELINE=<本技能>/references/ocr_cpu.yaml
  ```
- **远程 GPU（推荐；本机验证环境为局域网 RTX 3050 机器）**：
  ```sh
  export PADDLEOCR_MCP_REMOTE=1
  export PADDLEOCR_MCP_REMOTE_HOST=user@<远程机>
  ```
  mcp_batch 经本地 wrapper（`scripts/remote_gpu_server.py`，随技能打包）
  ssh stdio 拉起远程机上的 paddleocr_mcp（GPU, ocr_gpu.yaml）；本地图片自动
  scp 到远端 `inbox/`（size+mtime 缓存，重复调用不重传），调用方仍传本地绝对
  路径。首次调用触发远端模型加载+CUDA 热身（~1 分钟）。远程路径根目录可配
  `PADDLEOCR_MCP_REMOTE_ROOT`（默认 `~/tools/paddleocr-mcp`）；免 SSH 的
  HTTP 模式用 `PADDLEOCR_MCP_REMOTE_URL`。

## S3 图像处理（process_page.py）—— 心得

每页对 layout 的每个 image bbox：

1. **截取**：bbox 外扩 `PAD=40`px。
2. **边框检测**：灰度→Otsu→反相→9×9 矩形核 morphological close→
   findContours；取满足 `area>0.3·H·W` 且 `w>0.6·W, h>0.6·H` 的最大
   minAreaRect = 插图框。
3. **水平对齐**：`order_points`（sum/diff 法排 tl,tr,br,bl）→
   `getPerspectiveTransform` 到 `wd×hd` 矩形。
   - **坑：不要用 minAreaRect 返回的 angle 元数据**——OpenCV 角度约定
     在不同版本/宽高比下歧义，0.5° 的真实倾斜会被算成 89.5°。
     倾角一律从 box4 **顶边**算：`atan2(tr.y-tl.y, tr.x-tl.x)`。
     （warp 本身用 box4，不受影响，只有元数据会错。）
4. **二值化**：`Otsu` 全局阈值。**不要用 flat-field 背景估计方案**——
   本项目 A/B 对比：flat+Otsu 在木刻黑色填充区引入大量噪点，
   直接 Otsu（thr≈150-210 自动）背景纯白、线条锐利、网点全保留，完胜。
   黄化均匀的老书扫描适用；若某页黄化明显不均再单独处理。
   **半色调网点/木刻插图的数字扫描改用 `--mode whiten`**（`g>215→255`，
   灰度保留）：Otsu 把网点打散成噪点、破坏灰调层次；whiten 背景纯白、
   墨线/网点灰度完整，A/B 实测明显更优（巴黎圣母院 208 插图）。
   判断：源图是 JPEG 数字扫描且插图带明显半色调网点 → whiten；
   真扫描黄化老书 → Otsu。
5. **回退**：检测不到边框（无框插图/圆形插图）→ 直接用 layout bbox
   裁剪 + Otsu，不旋转。圆形插图可用"中部近方形大轮廓"定位
   （`0.5<aspect<2.0, area>2e5`，排除竖条文字列）。
6. 输出元数据：`frame_center_page`（**页坐标**，S5 放图用）、
   `frame_w/h`、`angle_deg`。本项目 136 框最大倾角 0.88°、中位 0.00°。

## S4 乱码字幕行救援（rescue_lines.py）—— 本流程最关键的经验

**现象**：浅灰油墨（老书常见）使 PaddleOCR 把清晰可读的字幕识别成乱码，
如 `具房局路路路堂房市房司`、`3适市刷别房`，且 **score<0.6**
（正常行 score≥0.62，大多 0.9+）。严重时整页只剩 2 行乱码、真字幕整行丢失。

**判别**：
- **score 阈值（<0.6）是唯一可靠信号**。试过字符频率表、bigram 未现率、
  墨迹灰度均值——全部失效：乱码恰好由"房市局路堂品机商民"这类**常用字**
  组成，灰度与真文字重叠。
- 自动找受影响页：扫全部 page JSON，`score<0.6` 的行所在页即救援对象。

**救援配方**（= Guji.md "先清理图像再 OCR" 的自动化）：
1. 条带 = 插图框底边以下到页底（字幕区）。
2. 增强 = **百分位对比度拉伸 `stretch(g, lo_p=10, hi_p=99)` + 2x INTER_CUBIC
   上采样**。试过 Otsu 二值化版本（浅字全灭，0 行检出）和 CLAHE 版本
   （可用但行切分略差）——**纯拉伸最优**，且保持原书行切分。
3. 条带送 MCP `ocr`（CPU 即可，~5s/页），行框映射回页坐标
   （`x=x'/2, y=fb+y'/2`，`fb`=条带顶边）。
4. **替换**该页条带内全部旧行（含被误杀的 0.6-0.85 分行），保留条带外行；
   只保留 `CJK≥2 字` 或纯数字页码行；旧乱码行记入 `ocr.rescue` 元数据。
5. 重跑 S5/S6。本项目 15 页 32 行乱码全部恢复真实文本。
- 救援默认用 **CPU pipeline**（条带小图 ~5s/页，`--pipeline` 可换）；
  自动跳过 book.json 的 keep/special 页与已 `rescued` 的行。
- **失败安全**（v1.1）：救援 OCR 失败/无结果的页**保留原行**，不再把好行
  删掉；输入路径全部 `resolve()` 绝对化。

**条带配方的三个失效模式**（巴黎圣母院，半色调淡点底扫描书，10 页实测）：
1. **行距过窄**（~7px）：灰度行投影被网点底粘连成 2 个"大行"，条带重识别
   的"行切分"与原书不符 → 改用 **line_rescue2.py 逐行救援**（整页 det 行框
   = 原书行切分，逐框 crop±10px+2x 送 rec，文本回填、原 quad 保留）。
2. **宽行碎片化**：条带 2x 后 det 把 >600px 宽行拆成多段，文本整段丢失 →
   同样逐行救援；det 多段时只认最长段（最长片段过滤）。
3. **整行丢失**：条带 det 漏检整行（不只首末行，中段也漏）→ 逐行救援或
   全书条带兜底重识别（见 S4.5）抓出，人工回填。
- **密集网点页判据**：逐行救援后仍反复乱码/score<0.6（p191 实测）→
  **停止自动救援**：先试**视觉模型目读**（渲染源图 ≥2x，长边 ≤2000px，
  视觉模型读出乱码区回填，标 `rescued: "vision-read"`）；目读不稳再转人工
  逐行对照源图校对，行文本手填并标 `rescued: "manual-proofread"`。
  不要无脑再试新配方。

## S4.5 全书条带兜底检查（strip_check.py 配方）

救援完成后（或书整体偏难时），对全部正文页跑**条带并行重识别**（4 worker，
CPU ~30min/208 页@3x），与现有行 difflib 比对（ratio<0.9 标记）：
- 能抓到 det 漏检的**中段整行**（条带 det 比整页 det 召回高）、行尾缺字、
  行序错、引号方向。
- **注意**：条带版行切分可能与原书不同（行距窄时粘连/拆分），不能盲替换；
  标记页逐页对照源图人工定稿（本配方 208 页只标记 6 页，人工修正量小）。

## S5 重建（build_pdf.py）—— 心得

- **页面几何**：目标 A4 横向 842×595pt；`S = PH/render_H`（2x 时 595/1536），
  `OX = (PW - render_W*S)/2` 水平居中，`to_pt(x,y)=(x*S+OX, y*S)`。
  所有原坐标按比例精确映射，**不做任何重排**。
- **保持原样页**：`doc.insert_pdf(src, from_page, to_page)`，保留原尺寸
  （封面 4:3、尾页可能 240×192）——PDF 允许混排页尺寸。
- **book.json 新字段**（v1.1）：
  - `drop_pages: [63]`：整页丢弃（纯水印广告尾页等），成品页数 = 源 - drop。
  - `image_overrides: {"1": "work/images/override_p001.png"}`：整页替换
    （封面/封底经去水印后的处理图），不 OCR、不重建文字层。
  - special JSON `text_invisible: true`：整页图页（扉页等图里已印好文字）
    的文字层用**白色**（可选取、不可见）——黑色文字层叠整页图会双影。
  - `--jpeg-quality N`：灰度图页（whiten 插图）重编码 JPEG 降体积
    （PNG 64MB/页 → JPEG q80 ~100KB/页，211 页 106MB→63MB，网点无损）。
    彩色图页自动跳过不重编码。
  - `--gs`：保存后 Ghostscript 终优化 + **文字层回归门**（抽 3 页比对提取
    文本；部分 gs 版本弄坏 CJK 子集 ToUnicode 映射时自动弃 gs 版、保原
    PDF）。⚠ 实测 gs 10.02.1 对 CID TrueType 子集会损坏文字层（字体表
    uni=yes 但提取出乱码）→ 该环境下 `--gs` 会自动中止；换 gs 版本/
    `--gs-args` 后可再试。Otsu 黑白书**不要**用 `--jpeg-quality` 减容
    （1-bit 图转 JPEG 反而变大：实测 43.7→91.2MB）——`--jpeg-quality`
    只适用灰度网点图（whiten 插图，巴黎圣母院案例）。
  - `--ink-top N`：基线常数（默认 0.88）。注意 0.88 = 字体墨顶 + det quad
    顶 padding（LXGW 实测：0.80+0.08）；**换字体**时跑 `probe_baseline.py`
    量新旧字体墨顶，用 Δ 相对校正（新常数 ≈ 0.88 + 新−旧），QA 基准页
    确认；同字体同 det 不疑不动（双书 500+ 行 0 偏差实证）。
- **文字**：PyMuPDF `fitz.Font(fontfile=LXGW TTF)` + `TextWriter`；
  保存前 `doc.subset_fonts()`（子集嵌入，体积 6-7MB/139页）。
- **字号公式**（保证铺满原行）：
  `fs = min( w_pt*100/FONT.text_length(text,100),  h_pt/0.92 ) * 0.98`
  （宽基 vs 高基取小；0.98 防溢出；h/0.92 因 CJK 墨迹高≈0.92em）。
  **⚠ 行间一致性**：排版类书（字幕/正文）应用**每页统一字号**=各行
  宽基比的中位数，而不是逐行 min——逐行取小会让短行/高 quad 行字号
  漂移，肉眼可见（Scribus 路线同样适用，见 S5'）。
- **基线**：`baseline = y1_pt + 0.88*fs`（CJK 墨迹顶≈基线上方 0.88em，
  使墨迹顶对齐原行顶；QA 后可微调 0.86-0.90）。
- **宽间距行**（字号宽基≫高基，如"内 容 提 要"）：逐字等距铺满原宽，
  每字槽宽 `w_pt/n`。
  **坑：`TextWriter.append(point,...)` 的 point 是字形 advance 左缘**，
  不是中心——锚点必须写 `slot_center - fs*0.5`，否则整行右移半字宽
  （本项目 p3 标题因此 dx=26pt）。
- **彩色文字**（如红色水印声明行）：单独一个 TextWriter，
  `tw.write_text(page, color=(r,g,b))`。
- **竖排文字列**（扉页）：见下节。
- 插图：`page.insert_image`，rect 以 `frame_center_page` 为中心、
  `frame_w*S × frame_h*S`。

## 双路线批量：同一批书质量不同时按实测分路线（2026-11 刘秀上/下册验证）

一批扫描书可能混有“白净数字扫描”和“严重泛黄老书”——**逐本实测背景后分路线**，
不要一刀切：

1. **实测**：各取 2-3 页 2x 灰度，测背景灰度均值与 R−B 差。
   背景≈255 且 |R−B|≤5 = 白净（无泛黄）；背景≤235 或 R−B≥20 = 泛黄。
2. **白净 → S5' Scribus 路线**（出 .sla 源文件；字号/行宽控制更精细，
   适合要复刻两端对齐字距的书）；**泛黄 → S5 build_pdf 路线**（Otsu 黑白化
   去黄能力强，PyMuPDF 的逐行宽度拟合字号天然吸收两端对齐的行宽差）。
   两本均 Otsu 二值化 + 2x 渲染，其余 S1-S4/S6 完全同流程。
3. **QA 注意**：Scribus 路线成品页用**源页面尺寸**（如 767.9×576），
   build_pdf 路线成品是 **A4 842×595**——test_page 必须按路线传
   `--page-size`（默认 842 595），白净书用 Scribus 路线却按 A4 跑 QA
   会全页系统性假报（504 条行偏差，实测）。
4. **交付**：白净书另存 .sla（`save_document_as`，ASCII 路径后改名）。

## 两端对齐行（justified）的复刻 —— 刘秀上下册

老书正文/提要常是**两端对齐**（字距均匀撑满原行宽）。两条路线的处理：

- **build_pdf 路线**：逐行宽度拟合字号（`fs = min(宽基, 高基)*0.98`）天然
  把每行撑到原宽，无需特殊处理（实测 524/524 行 dw 全过）。
- **Scribus 路线**：`set_text_alignment=justify` **对单行段落无效**（末行不
  拉伸，实测）；`set_text_horizontal_scale` 会拉变形，不可用。正解 =
  **逐字 slot frame**：纯 CJK 行每字等宽 slot（`slot = w/n`，字 frame 居中，
  字框宽 1.1em）；**含数字行用加权 slot**（slot 宽 ∝ 各字真实 advance：
  `slot_i = w * adv_i / Σadv`），行宽精确等于原 quad 宽。
- 短尾行（非对齐，宽 <0.95×页内最宽行）：单 frame + 本行宽度拟合字号
  `fs = min(fs_page, pt(w)/em*0.99)`（**w 要先 pt() 换单位**——px/pt 混用是
  本任务踩的头号 bug：min() 恒取页字号，宽度拟合静默失效）。
- **Scribus 数字-CJK 词间距伪影**（实测）：同一 frame 内 CJK↔数字**直连**
  处会多出 ~0.25-0.4em 空隙（行间不一致，数字多时行宽膨胀 10-20pt，甚至
  把行挤折行）。逐字独立 frame 的 slot 排法天然规避。**凡含数字的行，
  Scribus 路线一律逐字 slot，不要整行单 frame**。

## 版权页/表格页（单元格版式）配方 —— 刘秀 p155/p163

OCR 把表格拆成单元格框（每格一个 quad）。Scribus 路线重建：

1. 手工把每行整理成 `(文本, x1, x2)` 单元格列表（对 150-200dpi 源图逐字
   校对：乱码数字、异体字、水印残留行删除）；y 用行 quad 的 y1/y2。
2. **字号 = 实测墨高/0.88**（从 2x 灰度图逐行测墨区高度），**不要用 quad
   高度**（det 框含 padding，按框高算会偏大 5-15%）。
3. 1 字格：单 frame 居中；≥2 字格：**加权 slot** 逐字（同两端对齐节）——
   原书单元格内也是宽字距，居中/自然流都会 dw 超标。
4. 横线：在**原始灰度图**上找细暗行（`seg<215 计数>350`，Otsu 图可能丢
   浅线），成组取均值 y，画 0.8pt line。
5. build_pdf 路线的 text_only 版权页：直接改 data/pageNNN.json 的 lines
   （合并拆行、去水印行、订正文字）再重 build 即可——**改 JSON 要写回
   `l["text"]` 并同步 `ocr.text`**（局部变量赋值不写回是静默 no-op，实测坑）。

## 出版社装饰文字/水印残留行的几何清除（刘秀下册 115 行）

老书角落常有固定文本行（如左下“开张天岸马奇逸人中龙”），整本被 OCR
当字幕行。特征：**固定角落 + 每页相同文本** → 几何规则批量删：
`x1<150 & x2≤300 & y1>1050`（2x 坐标，按该书实测），删前先按文本计数确认
命中页与行数，删后重建 QA。不要逐页手删。

**判定先于删除**（2026-11 天岸马案例改判）：刘秀下册的这行联语我们初判为
“出版社装饰文字”，后按下方水印判定子技能复核——**姊妹卷（上册）同一扫描源
无此标记 + 模板匹配 recall=1.0 → 应判为扫描期叠加水印，不是印书内容**。
删除动作不变，但判定路径要留证据（整书重复行/模板 recall/姊妹卷对照）：
“同书姊妹卷没有”是区分“印在书上”与“扫描时加上”的最硬证据。

## S5' Scribus MCP 重建路线（替代 build_pdf.py，2026-10 孙膑和庞涓 114 页验证）

用户要求“用 Scribus 排版”或需要 .sla 源文件/可编辑文档时用
`scripts/scribus_build.py`（plan JSON → 经 scribus-mcp 执行 → 导出 PDF）。
672 ops（113 图+427 文本框）全程 **~20s**，多一个 .sla 交付物。

- **常驻 Scribus+bridge 必须先拉起**（MCP server 默认 headless 每次调用
  起一个 `scribus -g`，无状态、~1.2-2s/调用，不可用于多步构建）：
  ```sh
  xvfb-run -a -s "-screen 0 1280x960x24" scribus -g -ns -py <scribus_mcp>/bridge/scribus_mcp_bridge.spy
  ```
  之后 MCP `mode=auto` 自动切 interactive（5-12ms/调用，状态持久）。
  **坑**：Scribus 1.6.1 的 `-py` 在 GUI 模式（无 -g）静默不执行脚本 →
  必须 `-g`；bridge 的 discovery 文件路径随 `XDG_RUNTIME_DIR`，拉起时
  与 MCP server 的环境对齐（`/run/user/<uid>` 或 `/tmp`）。
- **run_script 门控**：`SCRIBUS_MCP_RUN_SCRIPT=1`（或 `--enable-run-script`
  启动参数）未设时该工具不存在；bridge 协议本身支持 `kind:exec`，
  但工具面未暴露——用“规划器出 plan JSON + scribus_build.py 执行”绕开。
- **plan 生成铁律**（规划器读 data/pageNNN.json + work/images 几何）：
  - 单位：2x 渲染 px /2 = pt；mm = pt*25.4/72。**px/pt 混用是头号 bug**
    （frame 坐标函数统一吃 px，字号单独吃 pt）。
  - 字号：**每页一个统一字号** = 各行（行宽/有效em数）比的中位数
    （长行 em>=12 定标；无长行时全部行参与）——原书是单一字号排版的，
    逐行取 min(宽基,高基) 会让短行/高 quad 行字号漂移（实测 110 页中
    60 页出现 1.6-4pt 行间差，用户肉眼可见）。帧宽 = max(quad宽,
    文本在统一 fs 下的实际宽)+4mm，防短行因统一字号变宽而换行
    （右伸部分在页边白处，不可见）。**例外**：扉页/版权页这种“原书
    确实有不同字号”的页按视觉块分组——标题行自定、同块正文行统一
    （如 p114 标题自定，其余行取 normal 行中位数）。
  - 文本宽度必须用**真实字体实测**：`true_em(t) = ImageFont.getlength(t)/100`
    （用注册给 Scribus 的同一 TTF）。实测校准（LXGW WenKai + Scribus
    1.6.1）：CJK/全角标点 1.0em、**数字 0.6em（含紧邻汉字，此前
    “全角数字”的判断是错的）**、空格 0.35em（CJK 模式压缩时可低至
    0.25em）。PIL 实测宽与 PDF 渲染一致 <0.1%。
  - **帧宽 = max(quad宽, fs*true_em*1.04)**：4% 余量吸收 CJK 换行算法
    与空格压缩差异。任何手工 em 模型（如数字 0.55/空格 0.30）都会
    系统性低估 ~1% → 长行末尾单字折行、与下一行重叠（实测 110 页中
    15 页中招，用户肉眼可见）。**行数 QA 抓不到这种折行**（总行数
    不变）——必须做帧级检测：锚定 PDF 行，若它是数据行真前缀且
    剩余字符出现在下一 PDF 行行首 = 折行溢出（见
    references/scribus-route/overflow_check.py）。
  - frame 顶 = 原行墨迹顶 + `(0.928-0.86)*fs` 补偿（LXGW ascent 0.928em）。
  - 宽间距行（slot>1.5*fs_page）：逐字 frame + center 对齐，字框宽 1.1em。
  - 页面方向：**宽>高的页面用 `orientation=portrait` + 物理 w/h**——
    传 landscape 会被 swap 成竖版（实测 767.9×576 → 576×767.9）。
  - **image 三件套**：create_image_frame → load_image →
    **scale_image_to_frame**（无 DPI 元数据的 PNG 被 Scribus 按错误 DPI
    放置，实测放大 1.45x 且左上锚定；第三步强制贴合 frame）。
  - 文本 op 顺序：**set_text → set_font → set_font_size**（1.6.1 的
    setFont 只作用于已有文本；先 setFont 后 setText 会静默回退 DejaVu，
    CJK 全丢且 ok=true 无报错——最阴的坑，用单测脚本验证过）。
  - 颜色：每行 ink 中位数从彩色渲染采样（**BGR！**），B>R+25 判蓝等。
- **构建幂等**：每次 `create_document` 重置文档，重跑=全量重建（~20s），
  无需增量。
- **QA**：`get_text` 逐页行数 vs data 行数比对（宽排逐字页会多“行”属
  正常，但**行数相等不代表没折行**——长行末字折行后总行数不变，
  必须跑 overflow_check.py 帧级前缀匹配检测）+ **逐页字号一致性**
  （get_text('dict') span size：每页正文应只有一个 size，>1pt 散布=
  字号公式有 bug）+ 并排渲染目检 + 关键名词计数（人名 OCR 变体应清零）。

## 数字扫描（非真扫描）书专节 —— 2026-10 孙膑和庞涓（Aspose 数字 PDF）

书页无泛黄、墨色干净的**数字 PDF**（源内嵌 1024×768 整页位图）与真扫描
配方不同：

- **字幕条带识别：直接用原始条带，不做拉伸/2x 放大**。rescue_lines.py 的
  拉伸配方针对浅灰墨；清晰数字扫描上纯放大反而让 det 碎片化（一行拆成
  12 段、引号行整段丢失）。原条带（帧底+8 到页底）det 直接出完整行。
  **兜底**：全书正文页条带并行重识别（4 worker，iGPU ~1.5min/110 页），
  结果权威替换——可顺带抓到整页 det 漏检的**页底行**（距页底 <40px 的
  最后一行最易漏，110 页抓到 1 页整行漏 + 1 页半漏）和碎片化页。
- **插图：灰度白化替代 Otsu 二值化**。Otsu 对半色调网点插图破坏灰调层次
  （p57 实测明显发糙）；`np.where(g>215, 255, g)` 白化去纸面浅灰、
  保留墨线/网点灰度，视觉与原版一致且仍是黑白印刷友好。
- **人名/术语订正**：PaddleOCR 对同音近形字有稳定偏差（孙膑→孙宾
  16+处、美街→美术、人氏→人民），score 常 ≥0.98 无法靠阈值过滤 →
  建书特定词表全局替换（strip_fix.py `FIXES`）。**坑**：任何后续整批
  重识别都会覆盖订正 → 订正必须放在**所有识别之后、规划器之前**，
  且重建前重新核对 PDF 文本计数（plan 缓存会再坑一次）。
- **逐行救援**（带引号/宽间距行 det 碎片化）：行投影找 band → 逐行裁
  10px 边距 → 3x INTER_CUBIC → OCR（2x 会碎，3x 稳定；Otsu 版更碎）。
  行投影失效时（行距窄、网点底粘连）改用 det 行框逐框 crop（line_rescue2.py）。
- **半色调木刻连环画变体**（巴黎圣母院：847×650 JPEG，木刻插图+淡点底
  字幕，无摆正）：插图 `--mode whiten`（见 S3）；字幕识别 3x 渲染即可；
  ~5% 页淡点底干扰出乱码行 → S4 条带配方（行距宽时）/ 逐行救援（行距窄时）
  / 密集网点页人工校对；**引号**：OCR 系统性丢闭引号（开 228 闭 143），
  用 fix_quotes.py 保守修复（行尾补”＋”紧跟：转“，全部改动打印人工过目）；
  跨行对话的闭引号位置不可自动判定，留作人工校对项。
- **对话引号不可全自动**：闭引号位置（行内哪处收尾）与"："后引号归属
  都需读原图；自动只做行尾补闭引号这类无歧义情形，其余标待人工。
- **build_pdf/test_page 几何读取坑**：两脚本"读第一个 data JSON 取页面
  几何"会被无 width 字段的 special JSON 坑到（目录排序 page002_special
  先于 page002）→ 已改为读第一个**含 width** 的 data JSON；total 改按
  最大页号而非文件数（special 文件会让数数虚高）。

## 竖排文字 / 扉页特判（本项目 p2 配方）

0. **有 aiBox 时首选 vcol 竖排专用服务**（8785 `ocr_vertical`，见"混排书籍
   分治路由"节）：整列识别/右起阅读序/标点/逐字 y/dark_bg 页码一步到位，
   无需旋转坐标换算；下列 90° 旋转配方作为 vcol 不可用（服务未起/非 aiBox
   环境）时的回退路线。

1. 页面旋转 90° CW（`cv2.rotate ROTATE_90_CLOCKWISE`）→ 竖排变横排 →
   MCP OCR。原图 (x,y) → 旋转 (x',y')：`x'=H-1-y, y'=x`；
   逆变换 `x=y', y=H-1-x'`。
2. 旋转 det 行框 → 原图竖列：`X1=y1', X2=y2', Y1=H-1-x2', Y2=H-1-x1'`。
   **阅读方向=自上而下**；字符均匀分布在 [Y1,Y2]：
   `yc_i = Y1+(i+0.5)*(Y2-Y1)/n`。
   **坑**：旋转图里字符的空间顺序（左→右）与阅读序可能相反
   （doc-orientation 模块会内部翻转），**不要从旋转 x 推字符顺序**，
   一律按文本阅读序+自上而下放置。
3. **重建时字符正立、逐字堆叠**（`baseline = yc*S + 0.40*fs`，
   `x = xc - fs*0.5`）——不是把整行文字旋转 90°。
4. 大字书法标题（如 4 字书名）常被整页 det 漏掉 → 单独裁竖条、旋转条带
   再 OCR；字号用列高/字数。
5. 透印幽灵行（背面文字透出，OCR 成乱码）：竖排页的"垃圾行"多为
   透印，按阅读内容白名单保留真列即可。
6. 几何写入 `pageNNN_special.json`（vertical_columns + horizontal_lines
   + image），book.json 里 `special_pages: {"2": "page002_special.json"}` 登记。
7. 扉页插图（无框圆形插图等）：`mcp_batch` 跳过 special 页，插图单独处理——
   轮廓/目检定 bbox 后调 `process_page.process_image_box(page_g, box, pad=40)`
   （无框自动回退不旋转），存 `work/images/pageNNN_0.png`，几何写入
   special JSON 的 `image` 字段（build_pdf 按它放图）。

## 水印判定子技能（后加 vs 原书设计）—— 先判定再动手

任何“疑似水印”的重复元素（固定角文字/图案、平铺纹、页边印记），
删除前先按证据链判定归属，**判定结果写进 book.json 或报告**：

1. **整书扫描**：OCR 全页，找“固定角落 + 每页相同”的短行/图案（OCR 重复行
   是最便宜的第一信号；图案类用人眼过一遍接触印样）。
2. **模板匹配**：取一页上该元素的 ROI，与其余页同位置互相关——
   **像素一致（recall≈1.0）→ 扫描期叠加**（scanner 空间固定覆盖层）；
   每页略有差异（纸质倾斜/打印批次）→ 更可能是印书内容。
3. **姊妹卷/同系列对照**（最硬证据）：同书姊妹卷、同扫描源无此元素
   → 不可能是印书内容（否则两卷都该有）→ 扫描期叠加。刘秀天岸马即
   以此改判。
4. **处置**：判为后加 → 默认去除 + 向用户报告证据；判为原书设计 →
   保留（页边设计则见 margin_ink_check 门与 S5 special_pages）；
   拿不准 → 报告两边证据让用户定。

## 水印处理（remove_watermark.py + Otsu + erase_template.py）

侦察时先判断水印形态（先跑上面的判定子技能定归属）：**白底上 D=1–9/255
的浅灰斜纹平铺文字**（网站水印）是最常见、也最好除的类型。分四类：

1. **B&W 正文页（插图+字幕）**：不需要专门去水印步骤——S3 的**全局 Otsu
   天然把 D≤9 的浅灰归入背景**（直方图双峰：墨 0–7 / 背景 252–255，水印
   落在背景峰内）。验证：3x 渲染下二值图内无水印纹理。
2. **纯水印页**（整页只有水印+广告文字）：book.json `drop_pages` 丢弃。
3. **彩色封面/封底**（必须保留彩色，平铺斜纹水印）：`remove_watermark.py`
   两步法：
   - `build-dmap`：跨所有 B&W 页对"白底"像素（≥235）取分通道均值
     （窄带分布上均值≈中位数）→ 纯水印图案图 D(p)（255-均值），缺口
     inpaint；`2D 相位搜索`确认各页图案同相（同 (0,0) 对齐）。
   - `correct`：逐像素反解 `C = 255 + (C_obs − (255 − r·D))/(1 − 0.25·r)`。
     **r 必须随局部亮度变化**：`r = lo + (1−lo)·clip((L−100)/110, 0, 1)`，
     L=局部 101px 模糊亮度。米白/浅底 L 高→r≈1.0（白底实测）；深饱和色带
     （如红带）实际响应只有 ~0.45·D，**固定 r=1.0 会在色带上过度反修，
     水印变深色残影**（本项目 p62 红带实测教训）。
   - 可选 `--inpaint y1:y2`：对 r 仍压不掉的深色平铺色带，用 D 图笔画 mask
     （D>4，5×5 膨胀）直接 TELEA inpaint（半径 7）——红带上效果显著，
     但**人脸/精细纹样区域慎用**（笔画 mask 过脸会涂抹）。
   - 输出整页 PNG → book.json `image_overrides`。成品里水印"尽量去除"：
     米色带基本干净，深色纹理带可能留极淡痕迹（半调网点与水印振幅耦合，
     算术法极限），可接受。
   - 验证：与 D 图叠加对齐检查 + 原图|成品并排目检（红带/米色带/人脸
     三个检查区）。
4. **彩色图上的扫描期叠加水印**（水印坐在彩色画作上，Otsu 不适用、
   D-map 是平铺场景不适用）：`erase_template.py` 模板反混合——
   需要书内一页水印落在白纸区作模板页（吸收场 a=(P−T)/P，
   `base=observed/(1−a)` 把底图算回来，只去水印形状、画保留）。
   **必须在摆正前的 2x 原渲染图上做**（水印在 scanner 空间固定，
   两页纸倾斜不同不影响同相；摆正后反而错位）。处理完的图走
   `image_overrides` 整页替换。适用三条件见脚本 docstring（刘秀下册
   p2 彩色横幅马形水印实测）；与 D-map 场景不重叠，可共存。

## S6 QA（test_page.py）—— 心得

- **S5 前置门：`margin_ink_check.py`**（框外墨检测，v1.3）：本路线重建
  模型是 页面=白底+插图+文字层，**插图框外**的墨（版心签条/边线/藏书印/
  页边徽记）会被静默丢失且 test_page 查不出来。重建前必跑：
  对每页渲染图 Otsu 后扣除插图框与所有行 quad 外扩，剩余墨 >0.1% 页面积
  → WARN + 高亮图。处置：确认是页边设计且需保留 → 该页转
  scan-retypeset 路线（margin_strip）或手工几何进 special_pages；
  确认是噪声 → book.json 记 `margin_ink_ignored`。无 WARN 才能进 S5。
- **点号变体归一化**：LXGW 等字体把 U+00B7（·）字形映射为 U+2219（∙），
  `get_text` 提取的文本与数据层不符 → 文本层"缺失 8 页"假警报。
  test_page 已把 U+2219/U+2027/U+30FB 归一到 U+00B7 再比对。
- **必须用字符级匹配**（`get_text("rawdict")` 逐 char）：
  `get_text("dict")` 会把同基线相邻行**合并**（页码"33"+正文字距 20px
  就合并成一行）→ 行级 bbox 比对大面积误报。
- **字符序列先剔除空白**：`""` 元素留在序列里会打乱 4/6 字 probe 窗口
  （"62 龙子…"的 probe "62龙子" 永远匹配不上）。
- **probe 歧义**：6 字 probe 在页内可能多处命中（"龙子太郎"、
  "唤妈妈，"重复出现）→ 收全部候选，取**并集 bbox 最接近原 quad** 的。
  注意：重复短语可能真是书本文字（本项目 p56 "摆开了摔跤的架势"
  连续出现两次），先目检确认再怀疑 OCR。
- **容差**：x/y ±14pt；宽度 `max(16pt, 0.45*fs_est)`；**≤3 字短行
  （页码）跳过宽度检查**（det 框含大量 padding，墨迹比框窄是正常度量伪影）。
- 其余检查：页数、保持原样页尺寸、每页可选取文字量、插图存在性、
  并排对比图（原图|重建，供目检）。
- **验收 = 测试 ALL PASS + 用户对基准页/扉页/封面尾页的目检通过**。

## 运行事故手册

| 事故 | 现象 | 处理 |
|---|---|---|
| OpenVINO EP 消失 | MCP server 启动报 `Requested ONNX Runtime providers are unavailable: ['OpenVINOExecutionProvider']`；`onnxruntime.get_available_providers()` 只剩 CPU；`clinfo` 无平台 | iGPU 驱动（i915/OpenCL ICD）进入坏状态，**重启才能恢复**（不要为此重启会话主机）→ 切 `ocr_cpu.yaml`（同 MCP server，仅 pipeline 配置换 CPU providers），后续 OCR 全 CPU。批量识别尽量早做（iGPU 健康时） |
| mcp 2.3.0 | 工具结果 `is_error`（非 isError）；`StdioServerParameters.env` 与白名单默认环境合并，**不含 LD_LIBRARY_PATH**（server 需要系统库时注意） | 按属性名用 `res.is_error`；保持 stdin 直到响应返回 |
| ORT+OpenVINO 配置 | EP 注册失败/静默回退 CPU | `providers` 与 `provider_options` 是**平行列表**；GPU 只需单个 `{'device_type':'GPU'}`；**不要**设 `num_threads`（OV EP 无此选项）；PaddleX 层 `device: cpu`，GPU 只在 provider_options 里选 |
| 尾页/封面尺寸异常 | 断言 `pN 应保持原始尺寸` 失败 | 先查源 PDF 各页实际尺寸（`fitz` 逐页 rect），保持原样=按源尺寸断言 |
| Scribus CJK 静默丢失 | 成品 PDF 只剩 ASCII/引号，字体变 DejaVuSans，所有调用 ok=true | setFont 只作用于已有文本：必须 set_text → set_font → set_font_size；另查 `orientation=landscape` 宽高 swap、load_image 错 DPI 放置（补 scale_image_to_frame） |
| 长行末字折行溢出 | 某行最后一个字掉到下一行、与下一行重叠；行数/总字数 QA 全绿 | 帧宽模型低估（手工 em 表数字 0.55/空格 0.30 < 真实 0.6/0.35）→ 帧宽改用真实字体 PIL 实测宽×1.04，并跑帧级前缀匹配检测（overflow_check.py） |
| Scribus 数字行膨胀/折行 | 含数字行比纯 CJK 行宽 10-20pt，或行尾折字；逐字 advance 实测 CJK↔数字直连处多出 0.25-0.4em 空隙 | 同一 frame 内数字与汉字直连触发 Scribus 词间距伪影 → 含数字行改逐字独立 frame（加权 slot）；QA 用逐字符 advance 检查（>1.3em 的 advance 即异常空隙） |
| Scribus bridge 不启动 | 拉起 `scribus -ns -py bridge.spy` 无 discovery/日志 | 1.6.1 的 `-py` 仅 `-g` 模式执行；加 `-g` 并用 xvfb-run；XDG_RUNTIME_DIR 与 MCP server 对齐 |
| 识别 JSON 解析 | `KeyError 'text_lines'` | 单行裁剪图太小/超 max_side_limit 时返回 `{'error':...}`；裁剪图保持 ≥140px 高、≤4000px 长边 |
| 几何被 special JSON 污染 | build_pdf/test_page 报 `KeyError: 'width'` 或 total 虚高 | special JSON 无 width 字段且目录排序在前（page002_special < page002）→ 两脚本已改为读第一个含 width 的 data JSON、total 按最大页号 |
| OCR bbox 坐标系 | 临时裁剪条带重 OCR 后行位置整体平移/缩放，QA 大面积越界 | MCP server 会把输入图**下采样到长边 ≤1920px**（预处理坐标系）再推理，bbox 在该坐标系而非页面像素——任何 ad-hoc 重 OCR 必须按 crop 缩放比映射回页面空间（正式流程 mcp_batch/rescue_lines 已内置映射，手写脚本时最容易踩；scan-retypeset references/pitfalls.md 同条目） |
| gs 损坏 CJK 文字层 | `--gs` 后 `get_text` 出乱码（率→豤），字体表 uni=yes 但映射错；gs 10.02.1 实测 | gs pdfwrite 重嵌 CID TrueType 子集时弄坏 ToUnicode 映射 → build_pdf --gs 已带文字层回归门（抽 3 页比对，失败自动弃 gs 版）；Otsu 1-bit 书减容**不能**走 --jpeg-quality（1-bit 转 JPEG 反而变大：实测 43.7→91.2MB）——需换 gs 版本/参数后重试，或接受 ~44MB/160 页 |
| 1-bit 图 JPEG 膨胀 | `--jpeg-quality` 后体积不降反升（43.7→91.2MB） | `--jpeg-quality` 只适用**灰度网点**图（whiten 插图，如巴黎圣母院半色调木刻）；Otsu 1-bit 图 PNG 已近最优，转 JPEG 编码的是抖动伪影 |
| 文本层“缺失”假警报 | 多页报 8+ 字符缺失，目检实际都在 | 字体 U+00B7→U+2219 字形映射，test_page 已做点号变体归一化 |

## 依赖与性能

- venv（Python 3.12，用 `PADDLEOCR_MCP_VENV_BIN` 或 PATH 指向）：
  `mcp==2.3.0 pymupdf opencv-python numpy onnxruntime onnxruntime-openvino
  openvino paddleocr-mcp`（server 本体）。pip 用国内镜像（TUNA）。
- iGPU 加速：`ocr_openvino.yaml`（PP-OCRv6 medium det/rec + OV GPU）
  ~8-12s/页；CPU `ocr_cpu.yaml` 单页救援 ~5s、整页 ~30-60s。
- vcol 竖排专用（aiBox:8785 streamable HTTP，venv 的 mcp 客户端直调）：
  0.2–1.3s/页（150–300dpi），见"混排书籍分治路由"节与 mcp-setup.md §7。
- 模型缓存 `~/.paddlex/official_models/`（PP-OCRv6_medium_det/rec、
  PP-LCNet_x1_0_*、UVDoc、PP-DocLayout_plus-L_onnx）。

## 相关文件

- `references/mcp-setup.md` —— MCP server 安装/注册/配置（mcp.json 条目、
  两个 yaml、layout_server.py 要点、iGPU 加速；CPU 机器需设
  PADDLEOCR_MCP_PIPELINE + PADDLEOCR_MCP_LAYOUT_SCRIPT）
- `references/scribus-route/` —— S5' Scribus 路线与数字扫描书完整示例
  （plan_build.py 规划器（含 true_em 实测宽/统一字号）+ overflow_check.py
  帧级折行检测 + strip_all.py 条带并行重识别 + strip_fix.py 词表订正；
  plan_build_liuxiu.py = 两端对齐行逐字 slot + 版权页单元格加权 slot +
  含数字行规避词间距伪影的第二套参考规划器）
- `references/json-schema.md` —— pageNNN.json（含 deskew_deg/rescue）/ 几何
  JSON / special JSON 字段说明与下游依赖；book.json 的 drop_pages/
  image_overrides
- `scripts/` —— mcp_batch.py（含 S1.5 摆正）/ process_page.py（含 --mode
  whiten）/ rescue_lines.py（条带配方）/ line_rescue2.py（逐行 det 框救援）/
  fix_quotes.py（引号保守归一化+待复核清单）/ build_pdf.py（drop_pages+
  image_overrides+rot_quad+text_invisible+--jpeg-quality+--ink-top+--gs）/
  probe_baseline.py（字体墨顶偏移实测，给 --ink-top 供数）/
  erase_template.py（彩色图扫描期水印模板反混合，摆正前渲染图）/
  margin_ink_check.py（S5 前置框外墨检测门，WARN 时退出码 1）/ test_page.py
  （drop 映射+点号归一化）/ remove_watermark.py（D 图构建+彩色页反解+
  inpaint）（均可 `--help`，路径参数化）
