"""
mediaocr — 微信本地 OCR 引擎 MCP Server
========================================
调用微信内置的端侧 OCR 引擎（wxocr.dll），通过 MCP 协议暴露为 AI 可用的工具。

能力：
  - ocr_image   图片/截图文字识别（单张）
  - ocr_batch   批量图片识别
  - ocr_pdf     扫描版 PDF 识别（逐页渲染 + OCR；文本型 PDF 直接抽取文本）
  - ocr_video   视频画面文字识别（ffmpeg 抽帧 + OCR）
  - extract_pdf_text  文本型 PDF 直接抽取文字（无需 OCR）

完全离线 · 无需网络 · 无需登录 · 中文识别对标腾讯
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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

# ── 触发词描述（供 LLM 判断何时调用）──────────────────────────────
# 说明：各工具的描述(docstring)中已内置「何时使用」触发词，
# 让任何 MCP 客户端（Hermes/Claude/Cursor 等）见到相关自然语言描述即触发。


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


def _format_result(result: dict, source: str) -> dict:
    """把 wcocr 原始结果整理成友好的 JSON。"""
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
        "source": source,
        "width": result.get("width"),
        "height": result.get("height"),
        "line_count": len(lines),
        "lines": lines,
        "text": "\n".join(l["text"] for l in lines),
    }


# ── 依赖自动安装（首次用到时懒加载，缺啥装啥，全程不碰系统环境）──────────
_PIP_LOCK = threading.Lock()


def _pip_install(pkg: str) -> bool:
    """用当前解释器静默安装 Python 包到当前 venv；成功返回 True。"""
    with _PIP_LOCK:
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet",
                 "--disable-pip-version-check", "--no-input", pkg],
                capture_output=True, text=True, timeout=600,
            )
            return r.returncode == 0
        except Exception:
            return False


def _ensure_fitz():
    """确保 pymupdf 可用：缺失时自动安装，返回 fitz 模块或 None。"""
    try:
        import fitz
        return fitz
    except ImportError:
        pass
    print("[mediaocr] 检测到缺少 pymupdf，正在自动安装（首次需联网，约 30s）...",
          file=sys.stderr)
    if _pip_install("pymupdf"):
        try:
            import fitz
            print("[mediaocr] pymupdf 安装成功", file=sys.stderr)
            return fitz
        except ImportError:
            pass
    return None


def _ensure_ffmpeg() -> str | None:
    """返回可用 ffmpeg 可执行文件路径。

    优先系统 ffmpeg（PATH）；没有则自动安装 imageio-ffmpeg
    （pip 包，自带静态 ffmpeg 二进制，Windows/Linux 通用，免 root）。
    """
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    except Exception:
        return None
    print("[mediaocr] 检测到缺少 ffmpeg，正在自动安装 imageio-ffmpeg "
          "（首次需联网，约 30s）...", file=sys.stderr)
    if _pip_install("imageio-ffmpeg"):
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            print(f"[mediaocr] ffmpeg 就绪: {exe}", file=sys.stderr)
            return exe
        except Exception:
            return None
    return None


def _ocr_image_inner(ocr: WeChatOCR, image_path: str) -> dict:
    try:
        result = ocr.ocr(image_path)
    except Exception as e:
        return {"errcode": -1, "error": str(e), "source": image_path}
    return _format_result(result, str(Path(image_path).resolve()))


def _ocr_pdf_inner(ocr: WeChatOCR, pdf: Path, max_pages: int = 20, dpi: int = 200) -> dict:
    """PDF 识别：文本型直接抽取，扫描型渲染 + OCR。"""
    fitz = _ensure_fitz()
    if fitz is None:
        return {"errcode": -1, "error": "pymupdf 安装失败，请手动运行: "
                f"{sys.executable} -m pip install pymupdf"}

    pages_out = []
    doc = fitz.open(str(pdf))
    total = min(doc.page_count, max_pages)
    for i in range(total):
        page = doc[i]
        text_layer = page.get_text().strip()
        if len(text_layer) >= 10:
            # 文本型 PDF：直接抽取
            pages_out.append({
                "page": i + 1,
                "type": "text_layer",
                "text": text_layer,
                "line_count": len(text_layer.splitlines()),
            })
        else:
            # 扫描型 PDF：渲染成图片再 OCR
            pix = page.get_pixmap(dpi=dpi)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                tmp_img = f.name
            pix.save(tmp_img)
            try:
                r = _ocr_image_inner(ocr, tmp_img)
                pages_out.append({
                    "page": i + 1,
                    "type": "ocr",
                    "text": r.get("text", ""),
                    "line_count": r.get("line_count", 0),
                })
            finally:
                # wxocr 引擎可能短暂持有文件句柄，重试删除
                for _ in range(5):
                    try:
                        os.unlink(tmp_img)
                        break
                    except PermissionError:
                        time.sleep(0.2)
    doc.close()

    ok = sum(1 for p in pages_out if p.get("text"))
    full_text = "\n\n".join(
        f"--- 第{p['page']}页 ---\n{p['text']}" for p in pages_out if p.get("text")
    )
    return {
        "errcode": 0,
        "source": str(pdf.resolve()),
        "total_pages": total,
        "processed_pages": len(pages_out),
        "pages_with_text": ok,
        "pages": pages_out,
        "text": full_text,
    }


def _ocr_video_inner(ocr: WeChatOCR, video: Path, interval_sec: float = 5.0,
                     max_frames: int = 10) -> dict:
    """视频识别：ffmpeg 抽帧 + OCR。"""
    ffmpeg = _ensure_ffmpeg()
    if ffmpeg is None:
        return {"errcode": -1, "error": "ffmpeg 自动安装失败，请手动安装 "
                "（https://ffmpeg.org）或运行: "
                f"{sys.executable} -m pip install imageio-ffmpeg"}

    frames_out = []
    with tempfile.TemporaryDirectory() as td:
        pattern = os.path.join(td, "frame_%04d.png")
        # 抽帧：每 interval_sec 秒一帧
        cmd = [
            ffmpeg, "-y", "-i", str(video),
            "-vf", f"fps=1/{interval_sec}",
            "-frames:v", str(max_frames),
            pattern,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return {"errcode": -1, "error": f"ffmpeg 抽帧失败: {r.stderr[-300:]}"}

        frame_files = sorted(Path(td).glob("frame_*.png"))
        for idx, f in enumerate(frame_files, 1):
            ts = round((idx - 1) * interval_sec, 1)
            res = _ocr_image_inner(ocr, str(f))
            frames_out.append({
                "frame": idx,
                "timestamp_sec": ts,
                "text": res.get("text", ""),
                "line_count": res.get("line_count", 0),
                "lines": res.get("lines", []),
            })

    ok = sum(1 for f in frames_out if f.get("text"))
    full_text = "\n\n".join(
        f"--- {f['timestamp_sec']}s ---\n{f['text']}" for f in frames_out if f.get("text")
    )
    return {
        "errcode": 0,
        "source": str(video.resolve()),
        "frames_processed": len(frames_out),
        "frames_with_text": ok,
        "frames": frames_out,
        "text": full_text,
    }


def create_server(ocr: WeChatOCR):
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
    def ocr_image(image_paths: list) -> dict:
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
        Returns:
            errcode、图片尺寸、每行文字 {text, box 坐标, confidence 置信度}、纯文本
        """
        if isinstance(image_paths, str):
            image_paths = [image_paths]  # 兼容误传单个字符串
        if not isinstance(image_paths, list) or len(image_paths) == 0:
            return {"errcode": -1, "error": "image_paths 必须是图片路径列表（1~20 张）"}
        if len(image_paths) > 20:
            return {"errcode": -1, "error": "一次最多扫描 20 张图片"}
        results = []
        for p in image_paths:
            results.append({"image_path": p, **_ocr_image_inner(ocr, p)})
        ok = sum(1 for r in results if r.get("errcode", -1) == 0)
        if len(results) == 1:
            # 单张：直接返回该图片的完整结果，方便使用
            return {"errcode": 0, **results[0], "image_path": results[0]["image_path"]}
        return {"errcode": 0, "total": len(results), "success": ok, "results": results}

    @mcp.tool()
    def ocr_document(document_path: str, max_pages: int = 20, dpi: int = 200,
                     interval_sec: float = 5.0, max_frames: int = 10) -> dict:
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
            dpi: PDF 渲染分辨率（默认 200，扫描件建议 200~300）
            interval_sec: 视频抽帧间隔秒数（默认 5 秒一帧）
            max_frames: 视频最多识别帧数（默认 10 帧）
        Returns:
            PDF: 每页结果 {page, type, text} + 全文；视频: 每帧结果 {frame, timestamp, text} + 全文
        """
        doc = Path(document_path)
        if not doc.is_file():
            return {"errcode": -1, "error": f"文件不存在: {document_path}"}

        ext = doc.suffix.lower()
        if ext == ".pdf":
            return _ocr_pdf_inner(ocr, doc, max_pages, dpi)
        if ext in (".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm", ".wmv", ".ts", ".m4v", ".mpeg", ".mpg"):
            return _ocr_video_inner(ocr, doc, interval_sec, max_frames)
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
    print(f"[mediaocr] 引擎: {wxocr_dll}", file=sys.stderr)
    print(f"[mediaocr] 运行时: {wechat_path}", file=sys.stderr)
    if IS_LINUX:
        print(
            "[mediaocr] Linux 模式（wxocr ELF + libmmmojo.so），"
            "需同目录放置 wcocr.so（编译自 swigger/wechat-ocr）",
            file=sys.stderr,
        )

    ocr = WeChatOCR(wxocr_dll, wechat_path)
    mcp = create_server(ocr)

    if args.http:
        print(f"[mediaocr] HTTP 模式 http://127.0.0.1:{args.http}/mcp", file=sys.stderr)
        mcp.run(transport="http", host="127.0.0.1", port=args.http)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
