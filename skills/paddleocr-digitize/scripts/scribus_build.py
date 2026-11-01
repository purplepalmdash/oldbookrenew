#!/usr/bin/env python3
"""S5' Scribus MCP 重建（build_pdf.py 的替代路线）。

读取 build_plan.json（由规划器生成），经 scribus-mcp（stdio 客户端 →
bridge → 常驻 Scribus）执行，导出 PDF。

plan op 类型：
  document  {op,w,h,pages}                 创建文档（mm；宽>高也用 orientation=portrait）
  color     {op,name,rgb:[r,g,b] 0-1}      define_color_rgb
  page      {op,n}                          goto_page
  image     {op,name,page,x,y,w,h,path}    create_image_frame + load_image + scale_image_to_frame
  text      {op,name,page,x,y,w,h,text,font,size,color,align}
            顺序铁律：create → set_text → set_font → set_font_size → color/align
            （1.6.1 setFont 只作用于已有文本；先设字体再 setText 会静默丢 CJK）
  line      {op,name,page,x1,y1,x2,y2,color,width_pt}
  export    {op,path}

依赖：常驻 Scribus+bridge 已启动：
  xvfb-run -a -s "-screen 0 1280x960x24" scribus -g -ns -py <scribus_mcp>/bridge/scribus_mcp_bridge.spy
（1.6.1 上 `-py` 仅在 -g 模式执行脚本；GUI 模式静默不跑 → 必须 -g。
bridge discovery 路径随 XDG_RUNTIME_DIR，与 MCP server 环境保持一致。）

环境变量:
  SCRIBUS_MCP_BIN   scribus-mcp 可执行文件（不设则从 PATH 找）
  SCRIBUS_BIN       Scribus 可执行文件（默认 scribus@PATH）
  XDG_RUNTIME_DIR   与常驻 bridge 保持一致（默认 /run/user/1000）

用法: scribus_build.py --plan <out>/build_plan.json [--export <path.pdf>]
"""
import argparse, asyncio, json, os, time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

def _find_mcp_bin():
    v = os.environ.get("SCRIBUS_MCP_BIN")
    if v:
        return v
    import shutil
    p = shutil.which("scribus-mcp")
    if p:
        return p
    raise SystemExit("未设 SCRIBUS_MCP_BIN 且 PATH 找不到 scribus-mcp："
                     "请 export SCRIBUS_MCP_BIN=<scribus-mcp venv>/bin/scribus-mcp")


MCP_BIN = None  # 惰性解析（main 里 _find_mcp_bin()），--help 不需要环境就绪


async def call(s, tool, args, timeout=300):
    t0 = time.time()
    res = await asyncio.wait_for(s.call_tool(tool, args), timeout)
    if getattr(res, "is_error", False):
        raise RuntimeError(f"{tool} error: {res.content}")
    text = "".join(getattr(c, "text", "") for c in res.content)
    d = json.loads(text)
    if d.get("ok") is False:
        raise RuntimeError(f"{tool} not ok: {d.get('error')}")
    return d, time.time() - t0


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--export", default=None, help="覆盖 plan 中/追加的导出路径")
    a = ap.parse_args()
    PLAN = json.load(open(a.plan))
    S = StdioServerParameters(command=_find_mcp_bin(), env={
        "SCRIBUS_BIN": os.environ.get("SCRIBUS_BIN", "scribus"),
        "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"),
    })
    async with stdio_client(S) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            ok, fail = 0, []
            t0 = time.time()
            for i, op in enumerate(PLAN):
                kind = op["op"]
                try:
                    if kind == "document":
                        await call(s, "create_document", {
                            "width_mm": op["w"], "height_mm": op["h"],
                            "margin_top_mm": 0, "margin_left_mm": 0,
                            "margin_right_mm": 0, "margin_bottom_mm": 0,
                            "orientation": "portrait", "pages": op["pages"]})
                    elif kind == "color":
                        rr, gg, bb = [int(round(v*255)) for v in op["rgb"]]
                        await call(s, "define_color_rgb", {"name": op["name"],
                                                           "red": rr, "green": gg, "blue": bb})
                    elif kind == "page":
                        await call(s, "goto_page", {"page_number": op["n"]})
                    elif kind == "image":
                        await call(s, "create_image_frame", {
                            "x_mm": op["x"], "y_mm": op["y"],
                            "width_mm": op["w"], "height_mm": op["h"], "name": op["name"]})
                        await call(s, "load_image", {"name": op["name"], "path": op["path"]})
                        # 无 DPI 元数据的 PNG 被 Scribus 按错误 DPI 放置 → 强制贴合 frame
                        await call(s, "scale_image_to_frame", {"name": op["name"], "proportional": True})
                    elif kind == "text":
                        await call(s, "create_text_frame", {
                            "x_mm": op["x"], "y_mm": op["y"],
                            "width_mm": op["w"], "height_mm": op["h"], "name": op["name"]})
                        await call(s, "set_text", {"name": op["name"], "text": op["text"]})
                        if op.get("font"):
                            await call(s, "set_font", {"name": op["name"], "font": op["font"]})
                        await call(s, "set_font_size", {"name": op["name"], "size_pt": op["size"]})
                        if op.get("color"):
                            await call(s, "set_text_color", {"name": op["name"], "color": op["color"]})
                        if op.get("align") and op["align"] != "left":
                            await call(s, "set_text_alignment", {"name": op["name"], "alignment": op["align"]})
                    elif kind == "line":
                        await call(s, "create_line", {
                            "x1_mm": op["x1"], "y1_mm": op["y1"],
                            "x2_mm": op["x2"], "y2_mm": op["y2"], "name": op["name"],
                            "line_color": op.get("color"), "line_width_pt": op.get("width_pt")})
                    elif kind == "export":
                        await call(s, "export_pdf", {"path": op["path"], "pages": "all",
                                                     "resolution": 300, "compress": True})
                    else:
                        raise ValueError(kind)
                    ok += 1
                except Exception as e:
                    fail.append((i, kind, str(e)[:150]))
                    print(f"FAIL op#{i} {kind}: {str(e)[:150]}", flush=True)
                if i % 100 == 0:
                    print(f"  {i}/{len(PLAN)} ok={ok} fail={len(fail)} t={time.time()-t0:.0f}s", flush=True)
            if a.export:
                try:
                    await call(s, "export_pdf", {"path": a.export, "pages": "all",
                                                 "resolution": 300, "compress": True}, timeout=900)
                    print("export:", a.export)
                except Exception as e:
                    fail.append((-1, "export", str(e)[:150]))
            print(f"DONE ok={ok} fail={len(fail)} total={time.time()-t0:.0f}s")
            if fail:
                json.dump(fail, open(a.plan + ".fail", "w"), ensure_ascii=False, indent=1)


asyncio.run(main())
