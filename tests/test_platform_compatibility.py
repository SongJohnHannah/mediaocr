"""Verify that macOS remains an additional backend alongside Windows/Linux."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


def load_server(platform):
    spec = importlib.util.spec_from_file_location("compatibility_server", server.__file__)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "platform", platform):
        spec.loader.exec_module(module)
    return module


class PlatformCompatibilityTests(unittest.TestCase):
    def check_original_backend(self, platform, engine_name):
        module = load_server(platform)
        native = Mock()
        native.init.return_value = True
        raw = {"errcode": 0, "width": 100, "height": 60, "ocr_response": []}
        native.ocr.return_value = raw
        # A Windows/Linux request must work even when no macOS modules are available.
        modules = {"wcocr": native, "macos_ocr": None,
                   "macos_ocr_worker": None, "macos_ocr_protocol": None}
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, modules):
            root = Path(temp)
            image = root / "screenshot.png"
            image.touch()
            engine, runtime = root / engine_name, root / "runtime"
            ocr = module.WeChatOCR(engine, runtime)
            try:
                self.assertIs(ocr.ocr(image), raw)
                self.assertIs(ocr.ocr(image), raw)
                native.init.assert_called_once_with(str(engine), str(runtime))
                self.assertEqual(native.ocr.call_count, 2)
                native.ocr.assert_called_with(str(image.resolve()))
            finally:
                ocr.close()

    def test_windows_keeps_wcocr_backend(self):
        self.check_original_backend("win32", "wxocr.dll")

    def test_linux_keeps_wcocr_backend(self):
        self.check_original_backend("linux", "wxocr")

    def test_original_backend_initialization_failure_is_reported(self):
        for platform in ("win32", "linux"):
            with self.subTest(platform=platform):
                module = load_server(platform)
                native = Mock()
                native.init.return_value = False
                with patch.dict(sys.modules, {"wcocr": native}):
                    ocr = module.WeChatOCR(Path("engine"), Path("runtime"))
                    with self.assertRaisesRegex(RuntimeError, "初始化失败"):
                        ocr._ensure_init()
                    self.assertFalse(ocr._initialized)
                    native.ocr.assert_not_called()

    def test_missing_image_never_loads_platform_libraries(self):
        for platform in ("win32", "linux", "darwin"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temp:
                module = load_server(platform)
                with patch.dict(sys.modules, {"wcocr": None, "macos_ocr": None}):
                    ocr = module.WeChatOCR(Path("engine"), Path("runtime"))
                    with self.assertRaises(FileNotFoundError):
                        ocr.ocr(Path(temp) / "missing.png")
                    self.assertFalse(ocr._initialized)

    def test_macos_uses_additional_backend_without_wcocr(self):
        module = load_server("darwin")
        native = Mock()
        native.ocr.return_value = {"errcode": 0, "ocr_response": []}
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "screenshot.png"
            image.touch()
            with patch.dict(sys.modules, {"wcocr": None}), \
                    patch("macos_ocr.MacOSOCR", return_value=native) as factory:
                ocr = module.WeChatOCR(Path("wxocr"), Path("Frameworks"))
                try:
                    self.assertEqual(ocr.ocr(image)["errcode"], 0)
                    factory.assert_called_once_with("wxocr", "Frameworks")
                    native._ensure_init.assert_called_once()
                    native.ocr.assert_called_once_with(str(image.resolve()))
                finally:
                    ocr.close()
                native.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
