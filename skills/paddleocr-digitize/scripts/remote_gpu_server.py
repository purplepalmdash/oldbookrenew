#!/usr/bin/env python3
"""PaddleOCR MCP 远程 GPU 包装器（远程 GPU 机，经 PADDLEOCR_MCP_REMOTE_HOST 配置）。

用法:
    remote_gpu_server.py ocr      # 暴露 ocr 工具（远程 paddleocr_mcp, GPU）
    remote_gpu_server.py layout   # 暴露 detect_layout 工具（远程 layout_server_gpu.py）

原理:
    - 启动时不连远程；首次调用时拉起远程 mcp 会话，之后复用同一会话。
    - 两种传输模式（二选一，同一模式内固定）：
      * SSH 模式（默认）：经 ssh stdio 拉起远程
        <REMOTE_ROOT>/run_ocr_mcp.sh | run_layout_mcp.sh。
        入参是**本地绝对路径**（图片/PDF）时，先 scp 到远程机的
        <REMOTE_ROOT>/inbox/（按 size 哈希缓存，
        重复调用不重复传），再以远程路径调用；base64/URL 原样透传。
      * HTTP 模式：设 PADDLEOCR_MCP_REMOTE_URL 指向远程机常驻 HTTP 服务
        （无需 SSH/密钥）。文件以 data URL 随请求内联发送（≤32MB 直接发），
        不经过 inbox；>32MB 的 PDF 按页拆块顺序调用，更大的图片降采样
        JPEG 内联（layout 的 bbox 回缩到原图坐标）。base64/URL 原样透传。
    - SSH 模式下远程 inbox 会缓慢累积，定期手动清理:
        rm -rf <REMOTE_HOST>:<REMOTE_ROOT>/inbox/*
"""

import asyncio
import base64
import hashlib
import http.server
import io
import json
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

HOST = os.environ.get("PADDLEOCR_MCP_REMOTE_HOST", "")
REMOTE_ROOT = os.environ.get(
    "PADDLEOCR_MCP_REMOTE_ROOT", "~/tools/paddleocr-mcp")
INBOX = f"{REMOTE_ROOT}/inbox"
CALL_TIMEOUT = float(os.environ.get("PADDLEOCR_MCP_CALL_TIMEOUT", "600"))

# HTTP 模式：指向远程机常驻 HTTP MCP 服务（无需 SSH）。
#   ocr 进程:    PADDLEOCR_MCP_REMOTE_URL=http://<REMOTE_IP>:8765/mcp
#   layout 进程: PADDLEOCR_MCP_REMOTE_URL=http://<REMOTE_IP>:8766/mcp
HTTP_URL = os.environ.get("PADDLEOCR_MCP_REMOTE_URL", "").strip()
_HTTP_TARGET = (urlparse(HTTP_URL).hostname or "127.0.0.1") if HTTP_URL else ""
_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".bmp": "image/bmp", ".tif": "image/tiff",
    ".tiff": "image/tiff", ".pdf": "application/pdf",
}

# 服务端 mcp 2.3.0 streamable-http 默认 4MiB 请求体硬上限，远程机 venv 已打补丁
# 提到 512MiB（2026-11-01，transport_security.py，备份 .bak-4mib；venv 重装会丢，需重打）。
# 客户端默认 ≤ _INLINE_MAX 的文件直接 data URL 内联；更大文件：
#   ocr:    按页拆子 PDF 顺序调用（OCR 按页独立，无损；detailed 响应不撞 SSE 1MB 上限）
#   layout: 客户端降采样 JPEG（长边 1280）内联，bbox 回缩到原图坐标
_INLINE_MAX = 32 * 1024 * 1024

from fastmcp import FastMCP
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

MODE = sys.argv[1] if len(sys.argv) > 1 else "ocr"
assert MODE in ("ocr", "layout"), "mode must be ocr|layout"
REMOTE_SCRIPT = f"{REMOTE_ROOT}/run_{'ocr' if MODE == 'ocr' else 'layout'}_mcp.sh"

mcp = FastMCP("paddleocr" if MODE == "ocr" else "paddleocr-layout")

_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".pdf"}
# 专用 worker 任务持有远程 mcp 会话；tool handler 只经 queue+future 传递，
# handler task 内不发生任何 mcp await。（mcp 2.3.0 server 调度器的 anyio
# cancel scope 是 task 亲和的：handler task 里长调用期间处理远端日志通知
# 会触发 "Attempted to exit a cancel scope..." 崩溃。）
_req_q: "asyncio.Queue" = None  # type: ignore[assignment]
_state = {"worker": None, "lock": asyncio.Lock()}


def _remote_name(local: Path) -> str:
    st = local.stat()
    tag = hashlib.sha1(f"{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:10]
    return f"{tag}_{local.name}"


async def _run(cmd: list[str]) -> str:
    p = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:2])}... failed: {err.decode()[:500]}")
    return out.decode().strip()


async def _transfer(local_path: Path) -> str:
    """本地文件 → 远程机 inbox，返回远程绝对路径（size 匹配则跳过传输）。"""
    remote = f"{INBOX}/{_remote_name(local_path)}"
    try:
        rsize = (await _run(
            ["ssh", HOST, f"stat -c %s {remote} 2>/dev/null || echo -1"])).strip()
    except RuntimeError:
        rsize = "-1"
    if rsize != str(local_path.stat().st_size):
        await _run(["scp", "-q", str(local_path), f"{HOST}:{remote}"])
    return remote


def _lan_ip(target: str) -> str:
    """本机对 target 出口 IP（UDP connect，不发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class _TempFileServer:
    """临时局域网单文件 HTTP 服务（HTTP 模式传大文件给远端 URL 下载用）。"""

    def __init__(self, local: Path, target: str):
        name = _remote_name(local)

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.split("?")[0] != "/" + name:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = local.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        port = self.httpd.server_address[1]
        self.url = f"http://{_lan_ip(target)}:{port}/{name}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


def _shrink_for_inline(local: Path):
    """大图 → 长边 1280 的 JPEG data URL（仍 ≤ _INLINE_MAX 时）。
    返回 (data_url, orig_w, orig_h)；压不够则 None。"""
    from PIL import Image

    im = Image.open(local)
    ow, oh = im.size
    scale = 1280 / max(ow, oh)
    if scale < 1:
        im = im.resize((max(1, int(ow * scale)), max(1, int(oh * scale))),
                       Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=88)
    data = buf.getvalue()
    if len(data) > _INLINE_MAX:
        return None
    du = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    return du, ow, oh


def _pdf_chunks(local: Path, max_bytes: int = _INLINE_MAX, max_pages: int = 40):
    """大 PDF → 子 PDF 字节列表（每块 ≤ max_bytes 且 ≤ max_pages 页）。
    OCR 按页独立处理，拆块不影响结果（simple 文本直接拼接；
    max_pages 上限同时保证 detailed JSON 不撞 SSE 1MB 单事件上限）。"""
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter

    raw = local.read_bytes()
    reader = PdfReader(BytesIO(raw))
    n = len(reader.pages)
    est = len(raw) / max(1, n)
    target = max(1, min(max_pages, int(max_bytes * 0.85 / max(1.0, est))))
    chunks = []
    for start in range(0, n, target):
        w = PdfWriter()
        for i in range(start, min(start + target, n)):
            w.add_page(reader.pages[i])
        buf = BytesIO()
        w.write(buf)
        data = buf.getvalue()
        if len(data) > max_bytes and start + target > start + 1:
            raise RuntimeError(
                f"PDF 块（{min(target, n - start)} 页, {len(data)//1024}KB）超 4MiB 请求上限，"
                f"请用 pdftoppm 降采样后重试: {local}")
        chunks.append(data)
    return chunks


def _http_sendable(value: str, is_ocr: bool):
    """HTTP 模式下把本地文件映射成可发送的输入。
    返回 (sendable, temp_srv, shrink_orig)：
      ≤ _INLINE_MAX        → data URL
      ocr 大文件          → 临时 LAN HTTP 服务 URL（远端自行下载）
      layout 大图         → 降采样 JPEG data URL（shrink_orig=原图宽高）
    非本地文件（URL/base64/远端路径）原样通过。
    """
    p = Path(value)
    if not (p.is_file() and p.suffix.lower() in _IMG_EXTS):
        return value, None, None
    if p.stat().st_size <= _INLINE_MAX:
        mime = _MIME_BY_EXT.get(p.suffix.lower(), "application/octet-stream")
        return "data:{};base64,{}".format(
            mime, base64.b64encode(p.read_bytes()).decode("ascii")), None, None
    shrunk = _shrink_for_inline(p)
    if shrunk is not None:
        du, ow, oh = shrunk
        return du, None, ((ow, oh) if not is_ocr else None)
    # 极端情况（降采样后仍超限）：原文件走 LAN URL——仅当客户端与远程机
    # 同网段可回达时有效，否则远端拉取会失败。
    srv = _TempFileServer(p, _HTTP_TARGET)
    return srv.url, srv, None


def _rescale_bboxes(text: str, ow: int, oh: int) -> str:
    """降采样图的 layout 结果 → bbox 回缩到原图像素坐标。"""
    try:
        d = json.loads(text)
        rw = ow / d.get("width", ow)
        rh = oh / d.get("height", oh)
        for e in d.get("elements", []):
            b = e.get("bbox")
            if isinstance(b, list) and len(b) == 4:
                e["bbox"] = [b[0] * rw, b[1] * rh, b[2] * rw, b[3] * rh]
        d["width"], d["height"] = ow, oh
        return json.dumps(d, ensure_ascii=False)
    except Exception:
        return text


async def _map_input(value: str) -> str:
    """SSH 模式：本地绝对路径 → 远端 inbox 路径（必要时 scp）；URL / base64 透传。
    （HTTP 模式下 tool handler 直接调 _http_sendable，不走这里。）"""
    p = Path(value)
    if p.is_file() and p.suffix.lower() in _IMG_EXTS:
        return await _transfer(p)
    return value  # base64 / URL / 远程已有路径


async def _worker() -> None:
    """专用任务：持有到远端 mcp 的会话（ssh stdio 或 HTTP），串行处理调用。"""
    global _req_q
    try:
        if HTTP_URL:
            ctx = streamable_http_client(HTTP_URL)
        else:
            params = StdioServerParameters(command="ssh", args=[HOST, REMOTE_SCRIPT])
            ctx = stdio_client(params)
        parts = await ctx.__aenter__()
        ro, wo = parts[0], parts[1]
        sess = ClientSession(ro, wo)
        await sess.__aenter__()
        # mcp server 要求先完成 initialize 握手，否则 tools/call 报
        # "Invalid request parameters" (INVALID_PARAMS)。
        await sess.initialize()
        while True:
            tool, args, fut = await _req_q.get()
            try:
                res = await sess.call_tool(
                    tool, args, read_timeout_seconds=CALL_TIMEOUT)
            except BaseException as e:
                if not fut.done():
                    fut.set_exception(e)
                continue
            text = "".join(getattr(c, "text", "") for c in res.content)
            if not fut.done():
                if getattr(res, "is_error", False):
                    fut.set_exception(
                        RuntimeError(f"remote tool {tool} error: {text[:2000]}"))
                else:
                    fut.set_result(text)
    except BaseException as e:
        if not isinstance(e, asyncio.CancelledError):
            print(f"[remote_gpu_server] worker died: {e}", file=sys.stderr, flush=True)


async def _ensure_worker() -> None:
    global _req_q
    async with _state["lock"]:
        if _state["worker"] is None or _state["worker"].done():
            if _req_q is None:
                _req_q = asyncio.Queue()
            _state["worker"] = asyncio.create_task(_worker())


async def _call(tool: str, args: dict) -> str:
    await _ensure_worker()
    args = {k: v for k, v in args.items() if v is not None}
    fut = asyncio.get_running_loop().create_future()
    await _req_q.put((tool, args, fut))
    # shield：handler 被取消时不打断 worker 里的远程调用。
    return await asyncio.shield(fut)


if MODE == "ocr":

    @mcp.tool()
    async def ocr(
        input_data: str,
        output_mode: str = "simple",
        file_type: Optional[str] = None,
        return_images: bool = True,
        runtime_params: Optional[dict] = None,
    ) -> str:
        """OCR（远程 GPU，PADDLEOCR_MCP_REMOTE_HOST 指向的机器）。

        input_data: 本地图片/PDF 绝对路径（自动内联/拆块）或 base64 或 URL。
        output_mode: simple=纯文本, detailed=JSON（含行框/置信度）。
        >32MB 的 PDF 自动按页拆块调用（结果无损，见 _pdf_chunks）。
        """
        base_args: dict[str, Any] = {"output_mode": output_mode}
        if not return_images:
            base_args["return_images"] = return_images
        if file_type:
            base_args["file_type"] = file_type
        if runtime_params:
            base_args["runtime_params"] = runtime_params

        # HTTP 模式大 PDF：按页拆块顺序调用（不依赖远端回连，跨网段也可用）
        if HTTP_URL:
            p = Path(input_data)
            if (p.is_file() and p.suffix.lower() == ".pdf"
                    and p.stat().st_size > _INLINE_MAX):
                parts = []
                for c in _pdf_chunks(p):
                    du = ("data:application/pdf;base64,"
                          + base64.b64encode(c).decode("ascii"))
                    parts.append(
                        await _call("ocr", dict(base_args, input_data=du)))
                return "\n".join(parts)

        srv = None
        if HTTP_URL:
            mapped, srv, _ = _http_sendable(input_data, is_ocr=True)
        else:
            mapped = await _map_input(input_data)
        args = dict(base_args, input_data=mapped)
        try:
            return await _call("ocr", args)
        finally:
            if srv is not None:
                srv.stop()

else:

    @mcp.tool()
    async def detect_layout(image_path: str) -> str:
        """版面检测（远程 GPU，PP-DocLayout_plus-L）。

        image_path: 本地图片绝对路径（自动上传；大图自动降采样后 bbox 回缩到原图坐标）。
        返回 JSON: {"image_path", "width", "height",
        "elements": [{"label", "score", "bbox": [x1,y1,x2,y2]}]}
        """
        srv = None
        shrink_orig = None
        if HTTP_URL:
            mapped, srv, shrink_orig = _http_sendable(image_path, is_ocr=False)
        else:
            mapped = await _map_input(image_path)
        try:
            text = await _call("detect_layout", {"image_path": mapped})
        finally:
            if srv is not None:
                srv.stop()
        if shrink_orig is not None:
            text = _rescale_bboxes(text, *shrink_orig)
        return text


if __name__ == "__main__":
    if not HTTP_URL and not HOST:
        raise SystemExit("SSH 模式需设 PADDLEOCR_MCP_REMOTE_HOST=user@<远程GPU机>"
                         "（或 PADDLEOCR_MCP_REMOTE_URL 走 HTTP 模式）")
    mcp.run()
