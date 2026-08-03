"""mediaocr 2-tool 测试：ocr_image（单张/批量）+ ocr_document（PDF/视频）"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_DIR = r"C:\Users\songf\wechat-ocr-mcp"
PYTHON = os.path.join(SERVER_DIR, ".venv", "Scripts", "python.exe")
SERVER = os.path.join(SERVER_DIR, "server.py")
IMG = r"C:\Users\songf\research\demo7\test_biz.png"
BIG = r"C:\Users\songf\research\test_big.png"
TEXT_PDF = r"C:\Users\songf\research\test_text.pdf"
SCAN_PDF = r"C:\Users\songf\research\test_scan.pdf"
VIDEO = r"C:\Users\songf\research\test_video.mp4"


async def call(session, name, args):
    r = await session.call_tool(name, args)
    for c in r.content:
        if c.type == "text":
            return json.loads(c.text)
    return None


async def main():
    params = StdioServerParameters(command=PYTHON, args=[SERVER], cwd=SERVER_DIR)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"✓ 工具列表: {names}")
            assert names == ["ocr_image", "ocr_document"], f"工具列表不对: {names}"

            for t in tools.tools:
                print(f"  描述长度[{t.name}]: {len(t.description or '')} 字符")

            # 1) ocr_image 单张（传单元素列表）
            r = await call(session, "ocr_image", {"image_paths": [IMG]})
            print(f"\n✓ ocr_image 单张: errcode={r['errcode']} 行数={r['line_count']}")
            print(f"  首行: {r['lines'][0]['text']}")
            assert r["errcode"] == 0 and r["line_count"] >= 4

            # 2) ocr_image 批量（多张）
            r = await call(session, "ocr_image", {"image_paths": [IMG, BIG]})
            print(f"✓ ocr_image 批量: total={r['total']} success={r['success']}")
            assert r["success"] == 2

            # 3) ocr_document 文本型 PDF
            r = await call(session, "ocr_document", {"document_path": TEXT_PDF})
            print(f"✓ ocr_document PDF(文本型): 类型={r['pages'][0]['type']}")
            assert r["pages"][0]["type"] == "text_layer"

            # 4) ocr_document 扫描型 PDF
            r = await call(session, "ocr_document", {"document_path": SCAN_PDF})
            print(f"✓ ocr_document PDF(扫描型): 类型={r['pages'][0]['type']} OCR={r['pages'][0]['text'][:20]!r}")
            assert r["pages"][0]["type"] == "ocr"

            # 5) ocr_document 视频
            r = await call(session, "ocr_document", {"document_path": VIDEO, "interval_sec": 3.0, "max_frames": 3})
            print(f"✓ ocr_document 视频: frames={r['frames_processed']} 含文字={r['frames_with_text']}")
            assert r["frames_with_text"] >= 1

            # 6) 错误处理：不支持的类型
            r = await call(session, "ocr_document", {"document_path": r"C:\Users\songf\research\demo7\test_biz.png"})
            print(f"✓ 错误处理(图片传给document): errcode={r['errcode']}")
            assert r["errcode"] == -1

            print("\n=== 全部测试通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
