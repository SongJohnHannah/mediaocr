"""MCP stdio 客户端测试：验证 mediaocr MCP server 可用（Windows / Linux 通用）。"""
import asyncio
import json
import sys
from pathlib import Path

# 用官方 mcp 客户端库测试
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_HERE = Path(__file__).resolve().parent
IS_LINUX = sys.platform.startswith("linux")

if IS_LINUX:
    PYTHON = str(_HERE / ".venv" / "bin" / "python")
else:
    PYTHON = str(_HERE / ".venv" / "Scripts" / "python.exe")
SERVER = str(_HERE / "server.py")
TEST_IMG = str(_HERE / "test_img.png")


async def main():
    params = StdioServerParameters(command=PYTHON, args=[SERVER], cwd=str(_HERE))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # 1) 初始化
            await session.initialize()
            print("✓ MCP 初始化成功")

            # 2) 列出工具
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"✓ 工具列表: {names}")
            assert "ocr_image" in names and "ocr_document" in names, "工具列表不完整"

            # 3) 调用 ocr_image（单张）
            result = await session.call_tool("ocr_image", {"image_paths": [TEST_IMG]})
            for content in result.content:
                if content.type == "text":
                    data = json.loads(content.text)
                    print(f"✓ ocr_image: errcode={data['errcode']} 行数={data['line_count']} "
                          f"尺寸={data['width']}x{data['height']}")
                    for line in data["lines"][:3]:
                        print(f"    [{line['confidence']:.3f}] {line['text']}")
                else:
                    print(f"  [非文本内容] {content.type}")

            # 4) 调用 ocr_document（传图片路径应报错提示，验证分流逻辑）
            result2 = await session.call_tool("ocr_document", {"document_path": TEST_IMG})
            for content in result2.content:
                if content.type == "text":
                    data = json.loads(content.text)
                    print(f"✓ ocr_document(传图片): errcode={data.get('errcode')} "
                          f"error={data.get('error', '')[:40]}")


if __name__ == "__main__":
    asyncio.run(main())
