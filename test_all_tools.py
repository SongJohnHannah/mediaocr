"""mediaocr 全工具测试：图片/批量/文本PDF/扫描PDF/视频/文本抽取"""
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

            # 验证触发词在描述里
            for t in tools.tools:
                desc = t.description or ""
                print(f"  描述长度[{t.name}]: {len(desc)} 字符")

            # 1) ocr_image
            r = await call(session, "ocr_image", {"image_path": IMG})
            print(f"\n✓ ocr_image: errcode={r['errcode']} 行数={r['line_count']}")
            print(f"  首行: {r['lines'][0]['text']}")

            # 2) ocr_batch
            r = await call(session, "ocr_batch", {"image_paths": [IMG, BIG]})
            print(f"✓ ocr_batch: total={r['total']} success={r['success']}")

            # 3) ocr_pdf 文本型
            r = await call(session, "ocr_pdf", {"pdf_path": TEXT_PDF})
            print(f"✓ ocr_pdf(文本型): pages={r['processed_pages']} 类型={r['pages'][0]['type']}")
            print(f"  内容: {r['pages'][0]['text'][:60]!r}")

            # 4) ocr_pdf 扫描型
            r = await call(session, "ocr_pdf", {"pdf_path": SCAN_PDF})
            print(f"✓ ocr_pdf(扫描型): pages={r['processed_pages']} 类型={r['pages'][0]['type']}")
            print(f"  OCR: {r['pages'][0]['text'][:40]!r}")

            # 5) ocr_video
            r = await call(session, "ocr_video", {"video_path": VIDEO, "interval_sec": 3.0, "max_frames": 3})
            print(f"✓ ocr_video: frames={r['frames_processed']} 含文字帧={r['frames_with_text']}")
            for f in r["frames"][:2]:
                print(f"  {f['timestamp_sec']}s: {f['text'][:30]!r}")

            # 6) extract_pdf_text
            r = await call(session, "extract_pdf_text", {"pdf_path": TEXT_PDF})
            print(f"✓ extract_pdf_text: pages={r['total_pages']} 含文字={r['pages_with_text']}")
            print(f"  内容: {r['text'][:50]!r}")

            # 7) 错误处理：不存在的文件
            r = await call(session, "ocr_image", {"image_path": r"C:\nope.png"})
            print(f"✓ 错误处理: errcode={r['errcode']} error={r['error']!r}")


if __name__ == "__main__":
    asyncio.run(main())
