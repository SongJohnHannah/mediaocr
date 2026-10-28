import importlib.util
import io
import os
import sys
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server
from core import _format_result
from extract_engine import extract_macos
from macos_ocr import MACOS_LIBRARIES, MACOS_MODELS, MacOSOCR, find_macos_engine
from macos_ocr_protocol import Request, Response, decode_response, encode_request


def make_engine(root):
    frameworks = root / "Contents" / "Frameworks"
    resources = root / "Contents" / "Resources"
    frameworks.mkdir(parents=True)
    resources.mkdir()
    for name in ("wxocr", *MACOS_LIBRARIES):
        (frameworks / name).write_bytes(b"fixture")
    for name in MACOS_MODELS:
        (resources / name).write_bytes(b"fixture")
    return frameworks


def server_for(platform):
    spec = importlib.util.spec_from_file_location("platform_server", server.__file__)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "platform", platform):
        spec.loader.exec_module(module)
    return module


class DiscoveryTests(unittest.TestCase):
    def test_macos_does_not_select_bundled_windows_engine(self):
        module = server_for("darwin")
        with patch("macos_ocr.find_macos_engine", return_value=None) as find:
            self.assertIsNone(module._find_engine())
            find.assert_called_once_with(module.ENGINE_DIR)

    def test_windows_engine_still_discovered(self):
        module = server_for("win32")
        with tempfile.TemporaryDirectory() as temp:
            module.WECHAT_PATH = Path(temp)
            module.WXOCR_DLL = Path(temp) / "wxocr.dll"
            module.WXOCR_DLL.touch()
            (module.WECHAT_PATH / "mmmojo_64.dll").touch()
            self.assertEqual(module._find_engine(), (module.WXOCR_DLL, module.WECHAT_PATH))

    def test_linux_engine_still_discovered(self):
        module = server_for("linux")
        with tempfile.TemporaryDirectory() as temp:
            module.ENGINE_DIR = module.WECHAT_PATH = Path(temp)
            module.WXOCR_EXE = Path(temp) / "wxocr"
            module.WXOCR_EXE.touch()
            (Path(temp) / "libmmmojo.so").touch()
            (Path(temp) / "ocr_model").mkdir()
            self.assertEqual(module._find_engine(), (module.WXOCR_EXE, module.WECHAT_PATH))

    def test_windows_installed_fallback_uses_paths(self):
        module = server_for("win32")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "plugins"
            extracted = plugin / "8011" / "extracted"
            extracted.mkdir(parents=True)
            (extracted / "wxocr.dll").touch()
            version = root / "Weixin" / "4.1"
            version.mkdir(parents=True)
            (version / "mmmojo_64.dll").touch()
            module.WXOCR_DLL = root / "missing.dll"
            module._FALLBACK_CANDIDATES = [(plugin, version.parent)]
            self.assertEqual(module._find_engine(), (extracted / "wxocr.dll", version))

    def test_portable_native_engine_is_preferred(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frameworks = make_engine(root / "macos")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(find_macos_engine(root),
                                 (frameworks.resolve() / "wxocr", frameworks.resolve()))

    def test_explicit_app_and_framework_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / "微信 App.app"
            frameworks = make_engine(app)
            for path in (app, frameworks):
                with self.subTest(path=path), patch.dict(os.environ, {"MEDIAOCR_WECHAT_PATH": str(path)}):
                    self.assertEqual(find_macos_engine(root)[1], frameworks.resolve())

    def test_incomplete_override_does_not_silently_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frameworks = make_engine(root / "WeChat.app")
            (frameworks.parent / "Resources" / MACOS_MODELS[0]).unlink()
            with patch.dict(os.environ, {"MEDIAOCR_WECHAT_PATH": str(frameworks)}):
                self.assertIsNone(find_macos_engine(root))

    def test_extraction_preserves_resources_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frameworks = make_engine(root / "WeChat.app")
            with patch("extract_engine.subprocess.run") as sign:
                destination = extract_macos(frameworks, root / "engine")
            self.assertEqual((destination / "Resources" / MACOS_MODELS[0]).read_bytes(), b"fixture")
            self.assertTrue((destination / "Frameworks" / "wxocr").stat().st_mode & 0o111)
            sign.assert_called_once()
            self.assertIn(str(destination / "Frameworks" / "wxocr"), sign.call_args.args[0])

    def test_macos_extraction_never_downloads_windows_fallback(self):
        import extract_engine
        with patch.object(sys, "platform", "darwin"), \
                patch.object(sys, "argv", ["extract_engine.py", "--force-download"]), \
                patch("extract_engine.download_fallback") as download, \
                patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                extract_engine.main()
            download.assert_not_called()


class ProtocolTests(unittest.TestCase):
    def test_unicode_request_and_native_feature_flags(self):
        request = Request.FromString(encode_request(42, "/tmp/微信 截图.png"))
        self.assertEqual(request.task_id, 42)
        self.assertEqual(request.pic_path, "/tmp/微信 截图.png")
        self.assertTrue(request.options.t1)
        self.assertTrue(request.options.t2)
        self.assertFalse(request.options.t3)

    def test_response_coordinates_and_compact_output(self):
        response = Response(task_id=42, errcode=0)
        response.result.width = 800
        response.result.height = 600
        response.result.lines.add(text="微信截图", rate=0.98, left=10, top=20, right=100, bottom=50)
        task_id, raw = decode_response(response.SerializeToString())
        formatted = _format_result(raw, "fixture")
        self.assertEqual(task_id, 42)
        self.assertEqual(formatted["text"], "微信截图")
        self.assertEqual(formatted["lines"][0]["box"],
                         {"left": 10, "top": 20, "right": 100, "bottom": 50})
        self.assertNotIn("lines", _format_result(raw, "fixture", compact=True))

    def test_native_error_is_preserved(self):
        response = Response(task_id=2, errcode=-7)
        _, raw = decode_response(response.SerializeToString())
        formatted = _format_result(raw, "fixture")
        self.assertEqual(formatted["errcode"], -7)
        self.assertIn("errcode=-7", formatted["error"])

    def test_absent_result_is_failure(self):
        _, raw = decode_response(Response(task_id=2).SerializeToString())
        self.assertEqual(raw["errcode"], -1)


class WorkerTests(unittest.TestCase):
    def test_shutdown_falls_back_to_own_child_if_group_signal_is_denied(self):
        process = Mock(pid=12345)
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 3), 0]
        process.poll.return_value = None
        ocr = MacOSOCR("unused", "unused")
        ocr._process = process
        with patch("macos_ocr.os.killpg", side_effect=PermissionError):
            ocr.close()
        process.kill.assert_called_once()
        process.stdout.close.assert_called_once()
        self.assertIsNone(ocr._process)

    def test_native_process_exit_is_a_recoverable_error(self):
        ocr = MacOSOCR("unused", "unused")
        with patch("macos_ocr.select.select", return_value=([1], [], [])):
            ocr._process = SimpleNamespace(stdout=io.StringIO(""))
            try:
                with self.assertRaisesRegex(RuntimeError, "进程已退出"):
                    ocr._receive()
            finally:
                ocr._process = None

    def test_timeout_is_bounded(self):
        ocr = MacOSOCR("unused", "unused", timeout=0.01)
        with patch("macos_ocr.select.select", return_value=([], [], [])) as select:
            ocr._process = SimpleNamespace(stdout=io.StringIO(""))
            try:
                with self.assertRaises(TimeoutError):
                    ocr._receive()
                self.assertEqual(select.call_args.args[3], 0.01)
            finally:
                ocr._process = None

    def test_missing_image_does_not_start_native_process(self):
        ocr = MacOSOCR("unused", "unused")
        with patch.object(ocr, "_ensure_init") as start:
            with self.assertRaises(FileNotFoundError):
                ocr.ocr("/missing/mediaocr-image.png")
            start.assert_not_called()

    def test_warmup_and_ocr_share_one_initialization(self):
        module = server_for("darwin")
        entered, release = threading.Event(), threading.Event()
        backend = Mock()
        backend._ensure_init.side_effect = lambda: (entered.set(), release.wait(5))
        backend.ocr.return_value = {"errcode": 0}
        with tempfile.NamedTemporaryFile(suffix=".png") as image, \
                patch("macos_ocr.MacOSOCR", return_value=backend) as factory:
            ocr = module.WeChatOCR(Path("wxocr"), Path("frameworks"))
            warmup = threading.Thread(target=ocr._ensure_init)
            request = threading.Thread(target=ocr.ocr, args=(image.name,))
            warmup.start()
            self.assertTrue(entered.wait(5))
            request.start()
            release.set()
            warmup.join(5)
            request.join(5)
            self.assertFalse(warmup.is_alive() or request.is_alive())
            factory.assert_called_once()
            backend._ensure_init.assert_called_once()
            backend.ocr.assert_called_once()
            ocr.close()


if __name__ == "__main__":
    unittest.main()
