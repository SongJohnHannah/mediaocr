"""
mediaocr — 微信本地 OCR 引擎 MCP Server
========================================
调用微信内置的端侧 OCR 引擎（wxocr.dll / wxocr ELF），通过 MCP 协议暴露为 AI 可用的工具。

工具：
  - ocr_image     图片/截图文字识别（单张或批量，支持 compact 精简输出）
  - ocr_document  PDF / 视频识别（自适应 dpi、视频帧去重、可并行）

编排逻辑在 core.py（双缓冲管线 / 帧去重 / 自适应 dpi / spawn 并行分发）。
本文件是 MCP 工具层 + 引擎封装。

完全离线 · 无需网络 · 无需登录 · 中文识别对标腾讯
"""

import argparse
import os
import sys
import threading
from pathlib import Path

# 确保 wcocr.pyd 可被 import（server.py 所在目录）
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

ENGINE_DIR = _HERE / "engine"
IS_LINUX = sys.platform.startswith("linux")

if IS_LINUX:
    # Linux：wxocr ELF 引擎 + libmmmojo.so + ocr_model/（从 Linux 版微信提取）
    WXOCR_EXE = ENGINE_DIR / "wxocr"
    WECHAT_PATH = ENGINE_DIR          # 含 libmmmojo.so + ocr_model/
else:
    # Windows：微信 4.x 便携引擎（wxocr.dll + mmmojo_64.dll + Weixin.exe）
    WECHAT_PATH = ENGINE_DIR / "4.1.11.55"   # 含 mmmojo_64.dll，父目录有 Weixin.exe
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

# ── 编排层（core.py）────────────────────────────────────────
# 核心处理移入 core.py：格式整理 / PDF双缓冲 / 视频去重 / 自适应dpi / 并行分发
from core import (  # noqa: E402
    ocr_pdf,
    ocr_video,
    run_pipeline,
    preinstall_deps,
    warmup_engine,
    _ocr_image_inner,
    _format_result,
)

# 向后兼容：旧脚本可能直接引用 server._ocr_*_inner / _ensure_*
_ocr_pdf_inner = ocr_pdf          # 旧签名 (ocr, pdf, max_pages, dpi) 位置参数兼容
_ocr_video_inner = ocr_video      # 旧签名 (ocr, video, interval_sec, max_frames) 兼容
_ensure_fitz = lambda: __import__("core", fromlist=["_ensure_fitz"])._ensure_fitz()
_ensure_ffmpeg = lambda: __import__("core", fromlist=["_ensure_ffmpeg"])._ensure_ffmpeg()


def _default_workers() -> int:
    """默认并行 worker 数：min(逻辑核, 3)。引擎并行上限实测 ~1.6x，3 个就够。"""
    try:
        n = len(os.sched_getaffinity(0))
    except Exception:
        n = os.cpu_count() or 1
    return max(1, min(n, 3))


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


def create_server(ocr: WeChatOCR, workers: int = 1):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "mediaocr",
        instructions=(
            "mediaocr：微信本地 OCR 引擎（离线）。"
            "用户请求提取任何图片/截图/扫描件/PDF/视频画面中的文字时，"
            "优先使用本服务器工具。识别中文效果对标腾讯，完全离线运行。"
        ),
    )

    @mcp.tool()
    def ocr_image(image_paths: list, compact: bool = False) -> dict:
        """扫描图片/截图/扫描件/照片中的文字（微信本地 OCR，完全离线）。

        【何时使用】用户要求「扫描」图片里的文字时使用本工具——无论这张图是怎么来的
        （其他工具截的屏、视频截图、相机照片、扫描仪扫描件、网页/聊天/PPT 截图、
        他人发来的图片），只要手里已有图片文件、需要扫描/提取里面的字，就用它。
        传 1 个路径扫描单张，传多个路径自动批量扫描。常见说法：图片扫描、
        扫描这张图/这些图、图片识别、提取图片里的文字、图片转文字、截图文字提取、
        把这张截图/图片的字读出来、扫描件识别、营业执照扫描/识别、身份证扫描/识别、
        发票扫描/识别、收据/小票扫描、票据识别、合同扫描识别、表格图片识别、
        书页拍照识别、手写文字识别、外语图片文字提取等。

        分工说明：本工具只负责「扫描已有图片文件」，不负责截图/录屏动作——
        截屏、网页截图、视频截图等请用专门的截图工具（如 media-kit）先生成图片，
        生成后的图片扫描识别交给本工具。PDF 和视频请用 ocr_document 扫描。

        Args:
            image_paths: 图片绝对路径列表（1~20 张，jpg/png/bmp/webp/tiff 等）。
                单张也传列表，如 ["C:\\a.png"]。
            compact: True 时只返回文字与行数（省略每行坐标/置信度），
                大批量扫描时输出更小更快；默认 False 返回完整坐标。
        Returns:
            errcode、图片尺寸、每行文字 {text, box 坐标, confidence 置信度}、纯文本
        """
        if isinstance(image_paths, str):
            image_paths = [image_paths]  # 兼容误传单个字符串
        if not isinstance(image_paths, list) or len(image_paths) == 0:
            return {"errcode": -1, "error": "image_paths 必须是图片路径列表（1~20 张）"}
        if len(image_paths) > 20:
            return {"errcode": -1, "error": "一次最多扫描 20 张图片"}
        jobs = [{"kind": "image", "path": p, "compact": compact} for p in image_paths]
        results = run_pipeline(jobs, ocr, workers)
        entries = [{"image_path": p, **r} for p, r in zip(image_paths, results)]
        ok = sum(1 for r in entries if r.get("errcode", -1) == 0)
        if len(entries) == 1:
            # 单张：直接返回该图片的完整结果，方便使用
            return entries[0]
        return {"errcode": 0, "total": len(entries), "success": ok, "results": entries}

    @mcp.tool()
    def ocr_document(document_path: str, max_pages: int = 20, dpi: int = 150,
                     interval_sec: float = 5.0, max_frames: int = 10,
                     auto_dpi: bool = True, dedup: bool = True,
                     compact: bool = False) -> dict:
        """扫描 PDF 或视频中的文字（自动判断类型，微信本地 OCR，离线）。

        【何时使用】用户要求「扫描」PDF 或视频里的文字时使用本工具，自动识别文件类型：
          - PDF（.pdf）：扫描 PDF 文字，自动判断文本型/扫描型——有文字层直接抽取（快），
            纯图片扫描件逐页渲染后 OCR。适用：扫描版 PDF、PDF扫描件、图片型 PDF、
            PDF里的图片文字、扫描文档转文字、PDF转Word前的文字提取、复制PDF文字。
          - 视频（.mp4/.mkv/.avi/.mov/.flv/.webm/.wmv/.ts）：扫描视频画面文字，
            ffmpeg 自动抽帧 + OCR，提取字幕/标题/弹幕/画面文字。适用：视频字幕提取、
            扫描视频里的字、从视频中找出说了什么字等，无需先手动截图。

        分工说明：本工具直接扫描 PDF/视频文件，无需先转图片。
          - 已有的是「图片文件/视频截图」→ 用 ocr_image 扫描
          - 本工具不做视频下载，下载请用专门的下载工具（如 media-kit）

        Args:
            document_path: PDF 或视频文件绝对路径
            max_pages: PDF 最多处理页数（默认 20）
            dpi: PDF 渲染分辨率（默认 150，扫描件建议 150~300；auto_dpi 会按需升到 300）
            interval_sec: 视频抽帧间隔秒数（默认 5 秒一帧）
            max_frames: 视频最多识别帧数（默认 10 帧）
            auto_dpi: True 时低置信度页自动升 300 重试（默认开）
            dedup: True 时视频相邻相似帧自动跳过（默认开，字幕场景省 50~80% OCR）
            compact: True 时只返回文字与行数，大批量输出更小（默认 False）
        Returns:
            PDF: 每页结果 {page, type, text} + 全文；视频: 每帧结果 {frame, timestamp, text} + 全文
        """
        doc = Path(document_path)
        if not doc.is_file():
            return {"errcode": -1, "error": f"文件不存在: {document_path}"}

        ext = doc.suffix.lower()
        if ext == ".pdf":
            return ocr_pdf(ocr, doc, max_pages, dpi, auto_dpi, compact, workers)
        if ext in (".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm", ".wmv", ".ts", ".m4v", ".mpeg", ".mpg"):
            return ocr_video(ocr, doc, interval_sec, max_frames, dedup, compact, workers)
        return {
            "errcode": -1,
            "error": f"不支持的文件类型: {ext}。PDF 请传 .pdf，视频请传 mp4/mkv/avi/mov/flv/webm 等；图片请用 ocr_image。",
        }

    return mcp


def _find_engine() -> tuple[Path, Path] | None:
    """定位 OCR 引擎，返回 (引擎路径, 运行时目录)。
    Linux:  (wxocr ELF, engine/ 含 libmmmojo.so+ocr_model)
    Windows: (wxocr.dll, engine/<版本>/ 含 mmmojo_64.dll)"""
    if IS_LINUX:
        # 1) 便携引擎：engine/wxocr + engine/libmmmojo.so + engine/ocr_model/
        if (WXOCR_EXE.is_file()
                and (ENGINE_DIR / "libmmmojo.so").is_file()
                and (ENGINE_DIR / "ocr_model").is_dir()):
            return WXOCR_EXE, WECHAT_PATH
        return None
    # 1) 项目自带的便携引擎
    if WXOCR_DLL.is_file() and (WECHAT_PATH / "mmmojo_64.dll").is_file():
        return WXOCR_DLL, WECHAT_PATH
    # 2) 已安装的微信
    for plugin_root, wechat_root in _FALLBACK_CANDIDATES:
        if not plugin_root.is_dir() or not wechat_root.is_dir():
            continue
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
                ver_dirs = sorted(
                    (d for d in wechat_root.iterdir() if d.is_dir() and d.name[0].isdigit()),
                    key=lambda d: d.stat().st_mtime, reverse=True,
                )
                for vd in ver_dirs:
                    if (vd / "mmmojo_64.dll").is_file():
                        return dll, vd
    return None


def main():
    parser = argparse.ArgumentParser(description="mediaocr - WeChat OCR MCP Server")
    parser.add_argument("--http", type=int, default=0, help="HTTP 端口（默认 stdio）")
    parser.add_argument("--workers", type=int, default=0,
                        help="OCR 并行 worker 数（0=auto，默认 min(逻辑核,3)；"
                             "引擎并行上限实测 ~1.6x，多核机器建议 3~4）")
    args = parser.parse_args()

    engine = _find_engine()
    if engine is None:
        if IS_LINUX:
            print(
                "ERROR: 找不到微信 OCR 引擎（Linux）。\n"
                "  请确认 engine/ 目录完整: wxocr + libmmmojo.so + ocr_model/\n"
                "  可从 Linux 版微信提取（/opt/wechat/wxocr 等），或参考 README。",
                file=sys.stderr,
            )
        else:
            print(
                "ERROR: 找不到微信 OCR 引擎。\n"
                "  请先运行:  python extract_engine.py\n"
                "  （脚本会自动检索本机微信；找不到时自动下载备用引擎）\n"
                "  或确认: 1) 项目 engine/ 目录完整；2) 本机已安装微信。",
                file=sys.stderr,
            )
        sys.exit(1)
    wxocr_dll, wechat_path = engine
    workers = args.workers if args.workers > 0 else _default_workers()
    print(f"[mediaocr] 引擎: {wxocr_dll}", file=sys.stderr)
    print(f"[mediaocr] 运行时: {wechat_path}", file=sys.stderr)
    print(f"[mediaocr] 并行 workers: {workers}", file=sys.stderr)
    if IS_LINUX:
        print(
            "[mediaocr] Linux 模式（wxocr ELF + libmmmojo.so），"
            "需同目录放置 wcocr.so（编译自 swigger/wechat-ocr）",
            file=sys.stderr,
        )

    ocr = WeChatOCR(wxocr_dll, wechat_path)
    preinstall_deps()  # 后台预装 pymupdf / ffmpeg，首个 PDF/视频调用不卡安装
    threading.Thread(target=warmup_engine, args=(ocr,), daemon=True).start()  # 后台预热引擎
    mcp = create_server(ocr, workers)

    if args.http:
        print(f"[mediaocr] HTTP 模式 http://127.0.0.1:{args.http}/mcp", file=sys.stderr)
        mcp.run(transport="http", host="127.0.0.1", port=args.http)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
