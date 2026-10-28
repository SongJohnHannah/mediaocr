"""Opt-in real macOS OCR + MCP tests; run outside a Mach IPC sandbox.

MEDIAOCR_RUN_NATIVE_TESTS=1 python -m unittest discover -s tests -v
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import _ocr_image_inner, ocr_pdf, ocr_video, run_pipeline
from server import WeChatOCR, _find_engine

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "wechat_sample.png"
NATIVE = sys.platform == "darwin" and os.environ.get("MEDIAOCR_RUN_NATIVE_TESTS") == "1"


@unittest.skipUnless(NATIVE, "set MEDIAOCR_RUN_NATIVE_TESTS=1 on macOS")
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine = _find_engine()
        if engine is None:
            raise RuntimeError("native tests require the WeChat macOS engine")
        cls.ocr = WeChatOCR(*engine)

    @classmethod
    def tearDownClass(cls):
        cls.ocr.close()

    def assert_text(self, result):
        self.assertEqual(result["errcode"], 0, result)
        self.assertIn("微信截图识别测试", result["text"])
        self.assertIn("今天下午三点开会", result["text"])
        self.assertIn("123456", result["text"])

    def test_project_engine_without_installed_wechat_fallback(self):
        import macos_ocr
        frameworks = (ROOT / "engine" / "macos" / "Contents" / "Frameworks").resolve()
        validate = macos_ocr.validate_macos_engine

        def require_project_engine(path):
            self.assertEqual(path.resolve(), frameworks, "不应回退到已安装的微信")
            return validate(path)

        with patch.dict(os.environ, {"MEDIAOCR_WECHAT_PATH": ""}), \
                patch("macos_ocr.validate_macos_engine", side_effect=require_project_engine):
            self.assertEqual(_find_engine(), (frameworks / "wxocr", frameworks))
        self.assertEqual(self.ocr.wechat_path, str(frameworks))
        self.assert_text(_ocr_image_inner(self.ocr, str(FIXTURE)))

    def test_chinese_screenshot_coordinates_and_compact(self):
        result = _ocr_image_inner(self.ocr, str(FIXTURE))
        self.assert_text(result)
        self.assertEqual((result["width"], result["height"]), (1000, 600))
        self.assertGreater(result["confidence"], 0.9)
        self.assertGreater(result["lines"][0]["box"]["right"], result["lines"][0]["box"]["left"])
        compact = _ocr_image_inner(self.ocr, str(FIXTURE), compact=True)
        self.assert_text(compact)
        self.assertNotIn("lines", compact)

    def test_parallel_spawn_batch_and_missing_file(self):
        paths = [str(FIXTURE)] * 7 + [str(FIXTURE.parent / "不存在.png")]
        results = run_pipeline([{"kind": "image", "path": p} for p in paths], self.ocr, workers=2)
        self.assertEqual(len(results), 8)
        for result in results[:7]:
            self.assert_text(result)
        self.assertEqual(results[-1]["errcode"], -1)

    def test_unicode_path_and_empty_image(self):
        import pymupdf as fitz
        with tempfile.TemporaryDirectory(prefix="微信 截图 ") as temp:
            path = Path(temp) / "测试 图片.png"
            path.write_bytes(FIXTURE.read_bytes())
            self.assert_text(_ocr_image_inner(self.ocr, str(path)))
            blank = Path(temp) / "blank.png"
            with fitz.open() as doc:
                doc.new_page().get_pixmap().save(blank)
            result = _ocr_image_inner(self.ocr, str(blank))
            self.assertEqual(result["errcode"], 0, result)
            self.assertEqual(result["line_count"], 0)

    def test_worker_recovers_after_process_exit(self):
        self.assert_text(_ocr_image_inner(self.ocr, str(FIXTURE)))
        worker = self.ocr._backend._process
        worker.kill()
        worker.wait(timeout=5)
        self.assert_text(_ocr_image_inner(self.ocr, str(FIXTURE)))
        self.assertNotEqual(worker.pid, self.ocr._backend._process.pid)

    def test_scanned_and_text_pdf(self):
        import pymupdf as fitz
        with tempfile.TemporaryDirectory() as temp:
            scanned = Path(temp) / "scan.pdf"
            with fitz.open() as doc:
                page = doc.new_page(width=1000, height=600)
                page.insert_image(page.rect, filename=str(FIXTURE))
                doc.save(scanned)
            self.assert_text(ocr_pdf(self.ocr, scanned))
            text = Path(temp) / "text.pdf"
            with fitz.open() as doc:
                doc.new_page().insert_text((30, 50), "MediaOCR PDF text layer 123456")
                doc.save(text)
            result = ocr_pdf(self.ocr, text)
            self.assertEqual(result["errcode"], 0)
            self.assertEqual(result["pages"][0]["type"], "text_layer")
            self.assertIn("123456", result["text"])

    def test_video_frame_ocr_and_dedup(self):
        from core import _ensure_ffmpeg
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "fixture.mp4"
            subprocess.run([_ensure_ffmpeg(), "-y", "-loop", "1", "-i", str(FIXTURE),
                            "-t", "2", "-r", "4", "-pix_fmt", "yuv420p", str(video)],
                           check=True, capture_output=True, timeout=30)
            result = ocr_video(self.ocr, video, interval_sec=0.5, max_frames=4)
            self.assert_text(result)
            self.assertGreater(result["frames_skipped_dedup"], 0)


@unittest.skipUnless(NATIVE, "set MEDIAOCR_RUN_NATIVE_TESTS=1 on macOS")
class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_tools_and_errors(self):
        # The SDK can recover after invalid stdout; do not let that hide corruption.
        with self.assertNoLogs("mcp.client.stdio", level="ERROR"):
            await self._check_stdio_tools()

    async def _check_stdio_tools(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def call(session, name, arguments):
            result = await asyncio.wait_for(session.call_tool(name, arguments), 60)
            self.assertFalse(result.isError, result)
            return json.loads(next(c.text for c in result.content if c.type == "text"))

        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], cwd=str(ROOT))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self.assertEqual([t.name for t in (await session.list_tools()).tools],
                                 ["ocr_image", "ocr_document"])
                result = await call(session, "ocr_image", {"image_paths": [str(FIXTURE)]})
                self.assertEqual(result["errcode"], 0, result)
                self.assertIn("微信截图识别测试", result["text"])
                batch = await call(session, "ocr_image", {"image_paths": [str(FIXTURE)] * 2, "compact": True})
                self.assertEqual(batch["success"], 2, batch)
                self.assertNotIn("lines", batch["results"][0])
                missing = await call(session, "ocr_image", {"image_paths": [str(FIXTURE.parent / "missing.png")]})
                self.assertEqual(missing["errcode"], -1)
                invalid = await call(session, "ocr_document", {"document_path": str(FIXTURE)})
                self.assertEqual(invalid["errcode"], -1)
                empty = await call(session, "ocr_image", {"image_paths": []})
                self.assertEqual(empty["errcode"], -1)

    async def test_streamable_http_image_tool(self):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}/mcp"
        with tempfile.TemporaryFile(mode="w+") as log:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "server.py"), "--http", str(port)],
                cwd=ROOT, stdout=log, stderr=log,
            )
            try:
                async with httpx.AsyncClient(trust_env=False) as client:
                    for _ in range(100):
                        if process.poll() is not None:
                            log.seek(0)
                            self.fail(f"HTTP server exited: {log.read()}")
                        try:
                            await client.get(url, timeout=0.2)
                            break
                        except httpx.TransportError:
                            await asyncio.sleep(0.1)
                    else:
                        self.fail("HTTP server startup timed out")
                def client_factory(headers=None, timeout=None, auth=None):
                    return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, trust_env=False)

                async with streamablehttp_client(url, httpx_client_factory=client_factory) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool("ocr_image", {
                            "image_paths": [str(FIXTURE)], "compact": True,
                        })
                        data = json.loads(next(c.text for c in result.content if c.type == "text"))
                        self.assertEqual(data["errcode"], 0, data)
                        self.assertIn("微信截图识别测试", data["text"])
            except Exception:
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    unittest.main()
