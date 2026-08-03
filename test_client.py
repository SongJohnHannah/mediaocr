"""MCP stdio 客户端测试：验证 wechat-ocr MCP server 可用。"""
import asyncio
import json
import os
import sys

# 用官方 mcp 客户端库测试
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_DIR = r"C:\Users\songf\wechat-ocr-mcp"
PYTHON = os.path.join(SERVER_DIR, ".venv", "Scripts", "python.exe")
SERVER = os.path.join(SERVER_DIR, "server.py")
TEST_IMG = r"C:\Users\songf\research\demo7\test_biz.png"


async def main():
    params = StdioServerParameters(command=PYTHON, args=[SERVER], cwd=SERVER_DIR)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # 1) 初始化
            await session.initialize()
            print("✓ MCP 初始化成功")

            # 2) 列出工具
            tools = await session.list_tools()
            print(f"✓ 工具列表: {[t.name for t in tools.tools]}")

            # 3) 调用 ocr_image
            result = await session.call_tool("ocr_image", {"image_path": TEST_IMG})
            print("✓ ocr_image 调用成功")
            for content in result.content:
                if content.type == "text":
                    data = json.loads(content.text)
                    print(f"  errcode={data['errcode']} 行数={data['line_count']} "
                          f"尺寸={data['width']}x{data['height']}")
                    for line in data["lines"]:
                        print(f"    [{line['confidence']:.3f}] {line['text']}")
                else:
                    print(f"  [非文本内容] {content.type}")

            # 4) 调用 ocr_batch
            result2 = await session.call_tool("ocr_batch", {"image_paths": [TEST_IMG, TEST_IMG]})
            for content in result2.content:
                if content.type == "text":
                    data = json.loads(content.text)
                    print(f"✓ ocr_batch: total={data['total']} success={data['success']}")


if __name__ == "__main__":
    asyncio.run(main())
