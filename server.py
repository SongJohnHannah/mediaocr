"""
WeChat OCR MCP Server
=====================
调用微信自带的本地 OCR 引擎（wxocr.dll），通过 MCP 协议暴露为 AI 可用的工具。

原理：微信 Windows 客户端内置端侧 OCR 引擎（文本检测 + 识别模型），
本 server 直接调用引擎 DLL，完全离线、无需网络、无需登录。

依赖（已随项目自带）：
  - engine/  —— 便携 OCR 引擎（Weixin.exe 宿主 + mmmojo_64.dll + wxocr.dll + .xnet 模型）
  - wcocr.pyd —— 引擎调用封装（CPython 3.11 x64）

用法：
  uv run python server.py              # stdio 模式（MCP 默认）
  uv run python server.py --http 8000  # HTTP/SSE 模式（调试用）
"""

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

# 确保 wcocr.pyd 可被 import（server.py 所在目录）
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

ENGINE_DIR = _HERE / "engine"
WECHAT_PATH = ENGINE_DIR / "4.1.11.55"          # 含 mmmojo_64.dll，父目录有 Weixin.exe
WXOCR_DLL = ENGINE_DIR / "ocr_engine" / "wxocr.dll"

# 引擎不存在时的自动回退：使用已安装微信的 OCR 插件（需微信已安装）
_FALLBACK_CANDIDATES = [
    # 微信 4.x
    (Path(os.environ.get("APPDATA", "")) / "Tencent/xwechat/XPlugin/plugins/WeChatOcr",
     r"C:\Program Files\Tencent\Weixin"),
    # 微信 3.x
    (Path(os.environ.get("APPDATA", "")) / "Tencent/WeChat/XPlugin/Plugins/WeChatOCR",
     r"C:\Program Files (x86)\Tencent\WeChat"),
]


def _find_engine() -> tuple[Path, Path] | None:
    """定位 OCR 引擎，返回 (wxocr.dll 路径, 微信运行时目录)。"""
    # 1) 项目自带的便携引擎
    if WXOCR_DLL.is_file() and (WECHAT_PATH / "mmmojo_64.dll").is_file():
        return WXOCR_DLL, WECHAT_PATH
    # 2) 已安装的微信
    for plugin_root, wechat_root in _FALLBACK_CANDIDATES:
        if not plugin_root.is_dir() or not wechat_root.is_dir():
            continue
        # 找最新版本的 OCR 插件
        versions = sorted(
            (d for d in plugin_root.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        for vdir in versions:
            extracted = vdir / "extracted"
            dll_candidates = [extracted / "wxocr.dll", extracted / "WeChatOCR.exe"]
            for dll in dll_candidates:
                if not dll.is_file():
                    continue
                # 微信版本目录（含 mmmojo_64.dll）
                ver_dirs = sorted(
                    (d for d in wechat_root.iterdir() if d.is_dir() and d.name[0].isdigit()),
                    key=lambda d: d.stat().st_mtime, reverse=True,
                )
                for vd in ver_dirs:
                    if (vd / "mmmojo_64.dll").is_file():
                        return dll, vd
    return None


class WeChatOCR:
    """微信 OCR 引擎封装：懒加载 + 线程锁，常驻进程内复用。"""

    def __init__(self, wxocr_dll: Path, wechat_path: Path):
        self.wxocr_dll = str(wxocr_dll)
        self.wechat_path = str(wechat_path)
        self._lock = threading.Lock()
        self._initialized = False

    def _ensure_init(self):
        if self._initialized:
            return
        import wcocr  # 延迟导入，加快 server 启动
        wcocr.init(self.wxocr_dll, self.wechat_path)
        self._initialized = True

    def ocr(self, image_path: str | Path) -> dict:
        import wcocr
        with self._lock:
            self._ensure_init()
            img = str(Path(image_path).resolve())
            if not Path(img).is_file():
                raise FileNotFoundError(f"图片不存在: {img}")
            result = wcocr.ocr(img)
        return result


# ---------- MCP 工具 ----------

def create_server(ocr: WeChatOCR):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "wechat-ocr",
        instructions=(
            "微信本地 OCR 工具：离线识别图片中的文字（中文/英文），"
            "返回每行文字、坐标框和置信度。支持单张识别与批量识别。"
        ),
    )

    @mcp.tool()
    def ocr_image(image_path: str) -> dict:
        """识别单张图片中的文字（微信本地 OCR 引擎，离线运行）。

        Args:
            image_path: 图片的绝对路径（支持 jpg/png/bmp/webp 等）
        Returns:
            识别结果：图片尺寸 + 每行文字 {text, 坐标, rate 置信度}
        """
        try:
            result = ocr.ocr(image_path)
        except Exception as e:
            return {"errcode": -1, "error": str(e)}

        lines = []
        for item in result.get("ocr_response", []):
            lines.append({
                "text": item.get("text", ""),
                "confidence": round(item.get("rate", 0.0), 4),
                "box": {
                    "left": round(item.get("left", 0), 1),
                    "top": round(item.get("top", 0), 1),
                    "right": round(item.get("right", 0), 1),
                    "bottom": round(item.get("bottom", 0), 1),
                },
            })
        return {
            "errcode": result.get("errcode", 0),
            "image_path": str(Path(image_path).resolve()),
            "width": result.get("width"),
            "height": result.get("height"),
            "line_count": len(lines),
            "lines": lines,
            "text": "\n".join(l["text"] for l in lines),  # 纯文本方便直接使用
        }

    @mcp.tool()
    def ocr_batch(image_paths: list) -> dict:
        """批量识别多张图片中的文字。

        Args:
            image_paths: 图片绝对路径列表（最多 20 张）
        Returns:
            每张图片的识别结果
        """
        if len(image_paths) > 20:
            return {"errcode": -1, "error": "一次最多识别 20 张图片"}
        results = []
        for p in image_paths:
            r = ocr_image(p)
            results.append({"image_path": p, **r})
        ok = sum(1 for r in results if r.get("errcode", -1) == 0)
        return {"errcode": 0, "total": len(results), "success": ok, "results": results}

    return mcp


def main():
    parser = argparse.ArgumentParser(description="WeChat OCR MCP Server")
    parser.add_argument("--http", type=int, default=0, help="HTTP 端口（默认 stdio）")
    args = parser.parse_args()

    engine = _find_engine()
    if engine is None:
        print(
            "ERROR: 找不到微信 OCR 引擎。\n"
            "  请先运行:  python extract_engine.py\n"
            "  （脚本会从本机已安装的微信中提取 OCR 引擎到 engine/ 目录）\n"
            "  或确认: 1) 项目 engine/ 目录完整；2) 本机已安装微信。",
            file=sys.stderr,
        )
        sys.exit(1)
    wxocr_dll, wechat_path = engine
    print(f"[wechat-ocr] 引擎: {wxocr_dll}", file=sys.stderr)
    print(f"[wechat-ocr] 运行时: {wechat_path}", file=sys.stderr)

    ocr = WeChatOCR(wxocr_dll, wechat_path)
    mcp = create_server(ocr)

    if args.http:
        print(f"[wechat-ocr] HTTP 模式 http://127.0.0.1:{args.http}/mcp", file=sys.stderr)
        mcp.run(transport="http", host="127.0.0.1", port=args.http)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
