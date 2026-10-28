"""macOS WeChat discovery and an isolated, persistent native OCR worker."""

import atexit
import json
import os
import select
import signal
import subprocess
import sys
import threading
from pathlib import Path


MACOS_LIBRARIES = ("libwxocr.dylib", "libmmmojo.dylib")
MACOS_MODELS = (
    "text_det_fp16_v1.xnet", "text_rec_fp16_v2.xnet", "OCRParaDetV1.1.0.26.xnet",
    "charset_zh10798.txt", "sohu_simp.txt",
    "QBarDetV1.7.0.26.xnet", "QBarSegV1.1.0.26.xnet",
    "FPMMFG1V1.2.0.26.xnet", "FPMMFG2V1.3.1.26.xnet",
    "PrivacyDetectV8.0.26.xnet", "DeepSR_REPSR_R_M4C16_Flip_ep24.xnet",
)


def validate_macos_engine(frameworks: Path) -> bool:
    resources = frameworks.parent / "Resources"
    return (frameworks / "wxocr").is_file() and all(
        (frameworks / name).is_file() for name in MACOS_LIBRARIES
    ) and all((resources / name).is_file() for name in MACOS_MODELS)


def find_macos_engine(engine_dir: Path) -> tuple[Path, Path] | None:
    """Prefer an explicit app/portable directory, then packaged and installed WeChat."""
    override = os.environ.get("MEDIAOCR_WECHAT_PATH")
    candidates = []
    if override:
        path = Path(override).expanduser()
        candidates = [path, path / "Contents" / "Frameworks"]
    else:
        candidates = [
            engine_dir / "macos" / "Contents" / "Frameworks",
            Path("/Applications/WeChat.app/Contents/Frameworks"),
            Path("/Applications/Weixin.app/Contents/Frameworks"),
            Path.home() / "Applications/WeChat.app/Contents/Frameworks",
            Path.home() / "Applications/Weixin.app/Contents/Frameworks",
        ]
    for frameworks in candidates:
        if validate_macos_engine(frameworks):
            frameworks = frameworks.resolve()
            return frameworks / "wxocr", frameworks
    return None


class MacOSOCR:
    """Keep native crashes/logs outside the MCP process and bound every wait."""

    def __init__(self, executable: str, frameworks: str, timeout: float = 45):
        self.executable = str(executable)
        self.frameworks = str(frameworks)
        self.timeout = timeout
        self._process = None
        self._lock = threading.RLock()
        atexit.register(self.close)

    def _receive(self) -> dict:
        process = self._process
        if not select.select([process.stdout], [], [], self.timeout)[0]:
            raise TimeoutError(f"微信 macOS OCR 超时（{self.timeout:g} 秒）")
        line = process.stdout.readline()
        if not line:
            raise RuntimeError(
                "微信 macOS OCR 进程已退出，请检查 stderr 日志。"
                "受限沙箱可能禁止 Mach IPC，请在普通终端运行。"
            )
        message = json.loads(line)
        if "error" in message and "errcode" not in message:
            raise RuntimeError(message["error"])
        return message

    def _ensure_init(self):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            self.close()
            worker = Path(__file__).with_name("macos_ocr_worker.py")
            self._process = subprocess.Popen(
                [sys.executable, "-u", str(worker), self.executable, self.frameworks],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1, start_new_session=True,
            )
            try:
                if self._receive() != {"ready": True}:
                    raise RuntimeError("微信 macOS OCR 返回了无效的初始化消息")
            except Exception:
                self.close()
                raise

    def ocr(self, image_path: str) -> dict:
        path = Path(image_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"图片不存在: {path}")
        with self._lock:
            self._ensure_init()
            try:
                self._process.stdin.write(json.dumps({"path": str(path)}) + "\n")
                self._process.stdin.flush()
                return self._receive()
            except Exception:
                self.close()
                raise

    def close(self):
        with self._lock:
            process, self._process = self._process, None
            if process is None:
                return
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Only signal the private process group when graceful shutdown fails.
                # A dead group can already be gone by the time wait() returns.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    if process.poll() is None:
                        process.kill()
                process.wait()
            finally:
                process.stdout.close()
