"""
mediaocr 引擎提取脚本
=====================
从本机已安装的微信中提取 OCR 引擎（wxocr.dll + 模型 + 运行依赖），
生成项目所需的 engine/ 目录。

原理：
  微信 Windows 客户端内置端侧 OCR 引擎。本脚本定位并复制运行引擎
  所需的最少文件（实测约 55MB）：
    - wxocr.dll        OCR 引擎主体（微信 4.x；3.x 是 WeChatOCR.exe）
    - *.xnet           文本检测/识别/段落模型 + 字符集
    - Weixin.exe       Chromium 宿主（以 --type=wxocr 模式加载引擎）
    - mmmojo_64.dll    IPC 通信框架（唯一硬依赖）

用法：
  python extract_engine.py [--output DIR]
  默认输出到脚本同级的 engine/ 目录。

依赖：
  - 已安装微信（3.x 或 4.x），或
  - 已有完整的微信便携版目录
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

APP_DATA = Path(os.environ.get("APPDATA", ""))


def find_wechat_roots() -> list[tuple[Path, Path]]:
    """返回 [(微信安装根目录, 版本目录), ...] 按版本目录时间倒序。"""
    candidates = [
        # 微信 4.x（新）
        Path(r"C:\Program Files\Tencent\Weixin"),
        # 微信 3.x（旧）
        Path(r"C:\Program Files (x86)\Tencent\WeChat"),
        Path(r"C:\Program Files\Tencent\WeChat"),
    ]
    roots: list[tuple[Path, Path]] = []
    for root in candidates:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and re.match(r"^\d+\.\d+", child.name):
                # 版本目录需含 mmmojo_64.dll（OCR 调用的通信框架）
                if (child / "mmmojo_64.dll").is_file():
                    roots.append((root, child))
    # 最新的版本目录排前面
    roots.sort(key=lambda t: t[1].stat().st_mtime, reverse=True)
    return roots


def find_ocr_plugin() -> Path | None:
    """定位微信 OCR 插件 extracted 目录（含 wxocr.dll / WeChatOCR.exe）。"""
    candidates = [
        APP_DATA / "Tencent/xwechat/XPlugin/plugins/WeChatOcr",      # 4.x
        APP_DATA / "Tencent/WeChat/XPlugin/Plugins/WeChatOCR",       # 3.x
    ]
    for plugin_root in candidates:
        if not plugin_root.is_dir():
            continue
        versions = sorted(
            (d for d in plugin_root.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        for vdir in versions:
            extracted = vdir / "extracted"
            if extracted.is_dir() and any(
                (extracted / f).is_file() for f in ("wxocr.dll", "WeChatOCR.exe")
            ):
                return extracted
    return None


def extract(extracted: Path, version_dir: Path, output: Path) -> None:
    """复制引擎文件到输出目录。"""
    output.mkdir(parents=True, exist_ok=True)
    engine_dir = output / "ocr_engine"
    engine_dir.mkdir(exist_ok=True)

    # 1) wxocr.dll（4.x）或 WeChatOCR.exe（3.x）——引擎本体
    engine_file = extracted / "wxocr.dll"
    is_wx4 = engine_file.is_file()
    if not is_wx4:
        engine_file = extracted / "WeChatOCR.exe"
    if not engine_file.is_file():
        raise FileNotFoundError(f"OCR 引擎文件不存在: {engine_file}")

    # 2) 模型与字符集（extracted 目录下除引擎外的文件，排除 debug.log）
    for f in extracted.iterdir():
        if f.is_file() and f.name.lower() not in ("debug.log",):
            shutil.copy2(f, engine_dir / f.name)

    # 3) Weixin.exe 宿主（4.x）——必须放在版本目录的父级（引擎按相对路径查找）
    wechat_root = version_dir.parent
    host_exe = wechat_root / ("Weixin.exe" if is_wx4 else "WeChat.exe")
    if host_exe.is_file():
        shutil.copy2(host_exe, output / host_exe.name)

    # 4) mmmojo_64.dll（通信框架，唯一硬依赖）——放在版本子目录
    mojo = version_dir / "mmmojo_64.dll"
    if mojo.is_file():
        ver_sub = output / version_dir.name
        ver_sub.mkdir(exist_ok=True)
        shutil.copy2(mojo, ver_sub / "mmmojo_64.dll")
    else:
        print("警告: 未找到 mmmojo_64.dll（微信版本目录中）", file=sys.stderr)

    return is_wx4


def main():
    parser = argparse.ArgumentParser(description="从微信提取 OCR 引擎")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "engine",
                        help="输出目录（默认: 脚本同级 engine/）")
    args = parser.parse_args()

    # 1) 找微信
    roots = find_wechat_roots()
    extracted = find_ocr_plugin()

    if not roots and not extracted:
        print("错误: 未找到已安装的微信。请先安装微信（3.x 或 4.x）后重试。", file=sys.stderr)
        sys.exit(1)

    if extracted is None:
        print("错误: 找到微信但未找到 OCR 插件。", file=sys.stderr)
        print("  OCR 插件通常位于:", file=sys.stderr)
        print("    %APPDATA%\\Tencent\\xwechat\\XPlugin\\plugins\\WeChatOcr\\", file=sys.stderr)
        print("  请确认微信已完整安装（OCR 插件在首次使用图片文字识别后下载）。", file=sys.stderr)
        sys.exit(1)

    version_dir = roots[0][1] if roots else extracted.parents[1]

    print(f"✓ 找到微信: {version_dir}")
    print(f"✓ 找到 OCR 插件: {extracted}")

    is_wx4 = extract(extracted, version_dir, args.output)
    total = sum(f.stat().st_size for f in args.output.rglob("*") if f.is_file())
    print(f"\n✓ 引擎已提取到: {args.output}")
    print(f"  版本: {'微信 4.x (wxocr.dll)' if is_wx4 else '微信 3.x (WeChatOCR.exe)'}")
    print(f"  大小: {total / 1024 / 1024:.1f} MB")
    print("\n下一步:")
    print("  1) 安装依赖:  uv venv --python 3.11 .venv && uv pip install --python .venv/Scripts/python.exe 'mcp[cli]==1.9.*'")
    print("  2) 获取调用封装 wcocr.pyd（见 README「获取 wcocr.pyd」）")
    print("  3) 启动:     .venv/Scripts/python.exe server.py")


if __name__ == "__main__":
    main()
