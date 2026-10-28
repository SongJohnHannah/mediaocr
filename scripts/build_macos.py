"""Build a portable macOS source + native WeChat engine ZIP.

Run with Python 3.11+ on macOS. No WeChat account data is included.
"""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from extract_engine import extract_macos
from macos_ocr import find_macos_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--wechat-app", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("macOS 引擎打包需在 macOS 上运行")
    if args.wechat_app:
        frameworks = args.wechat_app.expanduser().resolve() / "Contents" / "Frameworks"
    else:
        engine = find_macos_engine(ROOT / "engine")
        if engine is None:
            parser.error("找不到微信 macOS 原生引擎")
        frameworks = engine[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = "mediaocr-0.2.0-macos"
    with tempfile.TemporaryDirectory(prefix="mediaocr_package_") as temp:
        package = Path(temp) / name
        package.mkdir()
        for filename in (
            "server.py", "core.py", "extract_engine.py", "macos_ocr.py",
            "macos_ocr_worker.py", "macos_ocr_protocol.py", "pyproject.toml",
            "README.md", "LICENSE", "MANIFEST.in", "run_mcp.sh", "test_client.py", "test_all_tools.py",
        ):
            shutil.copy2(ROOT / filename, package / filename)
        shutil.copytree(ROOT / "tests", package / "tests",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "docs", package / "docs")
        shutil.copytree(ROOT / "scripts", package / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        extract_macos(frameworks, package / "engine")
        # Record asset hashes to make extracted runtime contents reviewable.
        assets = {}
        for path in sorted((package / "engine").rglob("*")):
            if path.is_file():
                assets[str(path.relative_to(package))] = hashlib.sha256(path.read_bytes()).hexdigest()
        (package / "engine-manifest.json").write_text(
            json.dumps(assets, indent=2) + "\n", encoding="utf-8")
        archive = Path(shutil.make_archive(str(output / name), "zip", temp, name))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"✓ {archive} ({archive.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
