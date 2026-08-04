"""mediaocr 2-tool 测试：ocr_image（单张/批量）+ ocr_document（PDF/视频）

Windows / Linux 通用。测试资源缺失时自动跳过对应用例：
  test_img.png        —— ocr_image 必需（仓库内无，需自行放置）
  test_text.pdf       —— 文本型 PDF（可选）
  test_scan.pdf       —— 扫描型 PDF（可选）
  test_video.mp4      —— 视频（可选）
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_HERE = Path(__file__).resolve().parent
IS_LINUX = sys.platform.startswith("linux")
PYTHON = str(_HERE / (".venv/bin/python" if IS_LINUX else ".venv/Scripts/python.exe"))
SERVER = str(_HERE / "server.py")


async def call(session, name, args):
    r = await session.call_tool(name, args)
    for c in r.content:
        if c.type == "text":
            return json.loads(c.text)
    raise RuntimeError(f"工具 {name} 未返回文本内容")


async def main():
    params = StdioServerParameters(command=PYTHON, args=[SERVER], cwd=str(_HERE))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"✓ 工具列表: {names}")
            assert names == ["ocr_image", "ocr_document"], f"工具列表不对: {names}"

            img = _HERE / "test_img.png"
            if img.is_file():
                # 1) ocr_image 单张
                r = await call(session, "ocr_image", {"image_paths": [str(img)]})
                print(f"✓ ocr_image 单张: errcode={r['errcode']} 行数={r['line_count']}")
                if r.get("lines"):
                    print(f"  首行: {r['lines'][0]['text']}")
                assert r["errcode"] == 0 and r["line_count"] >= 1

                # 2) ocr_image 批量（同一张两遍）
                r = await call(session, "ocr_image", {"image_paths": [str(img), str(img)]})
                print(f"✓ ocr_image 批量: total={r['total']} success={r['success']}")
                assert r["success"] == 2

                # 3) 错误处理：图片传给 ocr_document
                r = await call(session, "ocr_document", {"document_path": str(img)})
                print(f"✓ 错误处理(图片传给document): errcode={r['errcode']}")
                assert r["errcode"] == -1
            else:
                print("⚠ 跳过 ocr_image 用例（缺 test_img.png，自行放置后重跑）")

            for name, kw in (("文本型 PDF", "test_text.pdf"),
                             ("扫描型 PDF", "test_scan.pdf"),
                             ("视频", "test_video.mp4")):
                f = _HERE / kw
                if not f.is_file():
                    print(f"⚠ 跳过 {name} 用例（缺 {kw}）")
                    continue
                r = await call(session, "ocr_document", {"document_path": str(f)})
                print(f"✓ ocr_document {name}: errcode={r['errcode']}")

            print("\n=== 测试完成（无跳过则全部通过）===")


if __name__ == "__main__":
    asyncio.run(main())
