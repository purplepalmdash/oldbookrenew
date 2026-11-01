#!/usr/bin/env python3
"""PaddleOCR 版面检测 MCP 服务（PP-DocLayout_plus-L，ONNX + OpenVINO 运行在 Intel 核显）。

官方 paddleocr-mcp 的 pp_structurev3 工具只输出 Markdown（不含坐标），
本服务直接暴露版面检测的原始元素坐标（image/text/title/table 等 bbox），
用于古籍页面数字化流程（worktips 2.1 / 2.2）。
"""

import json
import os

from fastmcp import FastMCP

mcp = FastMCP("paddleocr-layout")

_MODEL_NAME = os.environ.get("LAYOUT_MCP_MODEL", "PP-DocLayout_plus-L")
_predictor = None


def _get_predictor():
    global _predictor
    if _predictor is None:
        from paddlex.inference.models import create_predictor

        env_prov = os.environ.get("LAYOUT_MCP_PROVIDERS", "").split(",")
        providers = [p.strip() for p in env_prov if p.strip()]
        if not providers:
            try:
                import onnxruntime as ort
                avail = ort.get_available_providers()
                providers = (["OpenVINOExecutionProvider"] if "OpenVINOExecutionProvider" in avail
                             else []) + ["CPUExecutionProvider"]
            except Exception:
                providers = ["CPUExecutionProvider"]
            if providers == ["CPUExecutionProvider"]:
                print("layout: OpenVINO EP 不可用，用 CPU", flush=True)
        _predictor = create_predictor(
            model_name=_MODEL_NAME,
            device="cpu",
            engine="onnxruntime",
            engine_config={
                "providers": providers,
                "provider_options": [
                    ({"device_type": "GPU"} if p == "OpenVINOExecutionProvider" else {})
                    for p in providers
                ],
            },
        )
    return _predictor


@mcp.tool()
def detect_layout(image_path: str) -> str:
    """检测扫描书页的版面元素（版面格局识别）。

    输入图片绝对路径，返回 JSON 字符串：
    {"image_path": ..., "width": 像素宽, "height": 像素高,
     "elements": [{"label": "image|text|title|table|...", "score": 0-1,
                   "bbox": [x1, y1, x2, y2]}]}
    坐标为该图片的像素坐标，bbox 为左上角/右下角。
    """
    import numpy as np
    from PIL import Image

    pred = _get_predictor()
    with Image.open(image_path) as im:
        w, h = im.size
    results = list(pred.predict(image_path, batch_size=1))
    r = results[0]
    d = r if isinstance(r, dict) else r.__dict__
    boxes = d.get("boxes", [])
    elements = []
    for b in boxes:
        coord = [float(c) for c in np.asarray(b["coordinate"]).ravel()]
        elements.append(
            {
                "label": b.get("label", str(b.get("cls_id"))),
                "score": round(float(b.get("score", 0.0)), 4),
                "bbox": [round(c, 1) for c in coord],
            }
        )
    out = {
        "image_path": str(image_path),
        "width": w,
        "height": h,
        "elements": elements,
    }
    return json.dumps(out, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
