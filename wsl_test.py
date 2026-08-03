"""WSL 下 mediaocr MCP server 全链路测试：协议层 + 实际 OCR 调用"""
import asyncio, json, sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = "/tmp/mediaocr/server.py"
PY = "/tmp/mediaocr/.venv/bin/python"
TEST_IMG = "/tmp/demo7/test呀哈哟.png"

async def main():
    params = StdioServerParameters(command=PY, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # 1) initialize
            init = await session.initialize()
            print(f"[1] initialize OK  server={init.serverInfo.name} v{init.serverInfo.version}")
            print(f"    capabilities: {init.capabilities.model_dump() if hasattr(init.capabilities,'model_dump') else init.capabilities}")

            # 2) tools/list
            tools = await session.list_tools()
            print(f"\n[2] tools/list 共 {len(tools.tools)} 个工具:")
            for t in tools.tools:
                schema = t.inputSchema
                params_ = schema.get("properties", {}) if isinstance(schema, dict) else {}
                print(f"    - {t.name}({', '.join(params_.keys())})")

            # 3) 实际调用 ocr_image（单张）
            print(f"\n[3] 调用 ocr_image(image_paths=['{TEST_IMG}']) ...")
            try:
                res = await session.call_tool("ocr_image", {"image_paths": [TEST_IMG]})
                for c in res.content:
                    txt = c.text if hasattr(c, "text") else str(c)
                    try:
                        data = json.loads(txt)
                        print("    errcode:", data.get("errcode"))
                        print("    error:", data.get("error"))
                        print("    尺寸:", data.get("width"), "x", data.get("height"))
                        print("    行数:", data.get("line_count"))
                        for line in data.get("lines", [])[:5]:
                            print(f"      [{line['confidence']}] {line['text']}")
                    except Exception:
                        print("    raw:", txt[:500])
                if res.isError:
                    print("    !!! call_tool 返回 isError=True")
            except Exception as e:
                print(f"    !!! 调用抛异常: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
