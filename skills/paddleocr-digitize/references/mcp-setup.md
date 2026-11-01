# PaddleOCR MCP 环境搭建（本项目验证过的配置）

路径约定：`<PADDLEOCR_HOME>` = paddleocr-mcp 工具根目录（本机验证环境为
`/path/to/paddleocr-mcp`）；`<SKILL>` = 本技能目录（layout_server.py
与 yaml 均已随技能打包，无需单独拷贝）。

## 1. venv 与依赖（国内镜像）

```sh
python3.12 -m venv <PADDLEOCR_HOME>/venv
V=<PADDLEOCR_HOME>/venv/bin
$V/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    mcp==2.3.0 fastmcp paddleocr-mcp \
    pymupdf opencv-python numpy \
    onnxruntime onnxruntime-openvino openvino
```

批量脚本 `mcp_batch.py` 需要能找到该 venv：`export
PADDLEOCR_MCP_VENV_BIN=<PADDLEOCR_HOME>/venv/bin`（或把 venv/bin 加入 PATH）。

- `paddleocr-mcp`：官方 MCP server（`paddleocr_mcp` 可执行文件，工具 `ocr` /
  `pp_structurev3` / …；`ocr` 的 `output_mode: detailed` 返回 `OCRResult.to_dict()`）。
- `fastmcp`：自建 layout server 用。
- Intel iGPU 加速另需系统包：`apt install intel-opencl-icd`。
- 模型自动下载到 `~/.paddlex/official_models/`（约 600MB+，首次每模型 ~1 分钟）。

## 2. 注册到 `~/.pi/agent/mcp.json`

```json
{
  "mcpServers": {
    "paddleocr": {
      "type": "stdio",
      "command": "<PADDLEOCR_HOME>/venv/bin/paddleocr_mcp",
      "args": [],
      "env": {
        "PADDLEOCR_MCP_MODEL": "PP-OCRv5",
        "PADDLEOCR_MCP_PPOCR_SOURCE": "local",
        "PADDLEOCR_MCP_PIPELINE_CONFIG": "<SKILL>/references/ocr_openvino.yaml",
        "PADDLEOCR_MCP_DEVICE": "cpu"
      },
      "timeout": 600,
      "description": "PaddleOCR 本地 OCR：图片/PDF 文字检测与识别"
    },
    "paddleocr-layout": {
      "type": "stdio",
      "command": "<PADDLEOCR_HOME>/venv/bin/python",
      "args": ["<SKILL>/scripts/layout_server.py"],
      "timeout": 300,
      "description": "版面检测：返回 image/text 等元素的像素坐标 bbox"
    }
  }
}
```

要点：
- `PADDLEOCR_MCP_DEVICE=cpu` 是 **PaddleX 层**的设备（pipeline 框架），
  真正的 GPU 加速在 yaml 的 `provider_options` 里选，两者不冲突。
- 程序内直连（批量脚本）不依赖 pi 的 mcp.json，用
  `mcp.client.stdio.StdioServerParameters` 起同样的命令+env 即可。
- 批量脚本里 **一个进程同时持有两个 stdio 会话**（ocr + layout）效率最高。

## 3. pipeline 配置 yaml

官方 server 支持 `PADDLEOCR_MCP_PIPELINE_CONFIG` 指定 PaddleX 配置。
两个现成模板在本技能 `references/` 下：

- `ocr_openvino.yaml` —— iGPU 加速版（批量识别用）：
  providers `[OpenVINOExecutionProvider, CPUExecutionProvider]` +
  `provider_options [{'device_type': 'GPU'}, {}]`，
  模型 PP-OCRv6_medium_det/rec + PP-LCNet_x1_0（orientation/doc_ori）+ UVDoc
  （`use_doc_unwarping: false`）。**实测 ~8-12s/页**（含 layout）。
- `ocr_cpu.yaml` —— 纯 CPU 版（iGPU 失效/救援小图用）：
  同上但 providers 只有 `CPUExecutionProvider`。单条字幕条带 ~5s。

ORT + OpenVINO EP 配置坑（本项目踩过的）：
- `providers` 和 `provider_options` 是**平行列表**（一一对应），不要嵌套。
- GPU 只需单个 `{'device_type': 'GPU'}`；**不要**加 `num_threads`
  （OV EP 无此选项，加了直接创建失败）。
- EP 注册失败会**抛错**（`Requested ONNX Runtime providers are unavailable`）
  而不是静默回退。

## 4. 自建 layout server（`scripts/layout_server.py`）

**为什么自建**：官方 `pp_structurev3` 只回 Markdown（无坐标），
不满足"每页 JSON 含 image 布局坐标"。自建 FastMCP server 直接调
PaddleX `PP-DocLayout_plus-L`（ONNX + OpenVINO），工具
`detect_layout(image_path) -> JSON`：

```json
{"image_path": "...", "width": 2048, "height": 1536,
 "elements": [{"label": "image|text|title|...", "score": 0.99,
               "bbox": [x1, y1, x2, y2]}]}
```

- 懒加载 predictor（首次调用才建模型，~20s）。
- `device="cpu"` + `engine="onnxruntime"` + 上述 OV GPU provider_options
  = 核显加速（与官方 server 同款配方）。
- 坐标为该图片像素坐标（2x 渲染下即 2x 坐标）。

## 5. iGPU 加速的寿命管理（重要教训）

- OpenVINO GPU 需要 `intel-opencl-icd`；`clinfo` 能看到
  `Intel(R) OpenCL Technology` + iGPU 才算就绪。
- **大批量跑完后 iGPU 可能进入坏状态**：`onnxruntime.get_available_providers()`
  不再列出 `OpenVINOExecutionProvider`、`clinfo` 无平台，
  MCP server 启动即报错。**只有重启系统才能恢复**（不要为它重启
  会话所在主机）。
- 对策：**批量识别尽早、一次做完**（iGPU 健康时 ~20 分钟/139页）；
  后续零散 OCR（竖排特判、救援条带）用 `ocr_cpu.yaml`，不影响结果质量。
- 判断 EP 状态：
  `$V/python -c "import onnxruntime; print(onnxruntime.get_available_providers())"`

## 6. mcp 2.3.0 stdio 客户端坑

- 工具结果属性是 `res.is_error`（不是 `isError`）。
- `StdioServerParameters(env={...})` 的 env 与 SDK 白名单默认环境
  （PATH/HOME 等 ~20 个）**合并**，不含 `LD_LIBRARY_PATH` 等其它变量；
  server 依赖系统库时要么在 venv 内解决，要么把变量名加进 env。
- 保持 stdin 打开直到响应返回；`call_tool` 的 `input_data` 传**绝对路径**。
- 小图/超长图可能返回 `{"error": ...}`（如超 `max_side_limit 4000`），
  调用方要判 key，不要直接取 `text_lines`。
