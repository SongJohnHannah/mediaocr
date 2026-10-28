"""Verify the project's copied WeChat engine and OCR a synthetic screenshot.

This script checks files and performs OCR directly; it does not build a package.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import _ocr_image_inner
from macos_ocr import MACOS_LIBRARIES, MACOS_MODELS
from server import WeChatOCR, _find_engine


def main():
    if sys.platform != "darwin":
        raise RuntimeError("此脚本验证 macOS 引擎，请在 macOS 上运行")
    local = ROOT / "engine" / "macos"
    manifest = json.loads((local / "manifest.json").read_text(encoding="utf-8"))
    expected = {f"Contents/Frameworks/{name}" for name in ("wxocr", *MACOS_LIBRARIES)}
    expected.update(f"Contents/Resources/{name}" for name in MACOS_MODELS)
    if set(manifest["files"]) != expected:
        raise RuntimeError("项目 OCR 文件清单不完整")
    for name, metadata in manifest["files"].items():
        path = local / name
        if path.is_symlink() or not path.resolve().is_relative_to(local.resolve()):
            raise RuntimeError(f"项目 OCR 资源必须是独立的本地文件: {name}")
        if path.stat().st_size != metadata["size"]:
            raise RuntimeError(f"项目 OCR 资源大小不匹配: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]:
            raise RuntimeError(f"项目 OCR 资源 SHA256 不匹配: {name}")
    frameworks = (local / "Contents" / "Frameworks").resolve()
    engine = _find_engine()
    if engine != (frameworks / "wxocr", frameworks):
        raise RuntimeError("默认加载的不是项目内引擎，请检查 MEDIAOCR_WECHAT_PATH 配置")
    ocr = WeChatOCR(*engine)
    try:
        result = _ocr_image_inner(ocr, str(ROOT / "tests" / "fixtures" / "wechat_sample.png"))
        if result["errcode"] != 0 or "微信截图识别测试" not in result.get("text", ""):
            raise RuntimeError(f"项目内微信 OCR 识别验证失败: {result}")
        print(json.dumps({
            "verified_files": len(expected),
            "wechat_version": manifest["wechat_version"],
            "engine": str(engine[0]),
            "line_count": result["line_count"],
            "confidence": result["confidence"],
            "text": result["text"],
        }, ensure_ascii=False, indent=2))
    finally:
        ocr.close()


if __name__ == "__main__":
    main()
