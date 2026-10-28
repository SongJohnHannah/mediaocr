"""Private worker for the macOS WeChat native engine. Never import from server.py.

The stable C ABI is described at swigger/wechat-ocr/src/mmmojo_funcs.h.
macOS additionally requires configuration 1000 and --base-bundle-id on both ends.
"""

import ctypes as c
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from macos_ocr_protocol import Support, decode_response, encode_request


class NativeOCR:
    def __init__(self, executable: Path, frameworks: Path):
        self._ready = threading.Event()
        self._done = threading.Event()
        self._error = None
        self._result = None
        self._task_id = 1
        self._env = None
        self._temporary = tempfile.TemporaryDirectory(prefix="mediaocr_macos_")
        self._callbacks = []
        self._lib = c.CDLL(str(frameworks / "libmmmojo.dylib"))
        self._bind_functions()
        # Only the copied launcher is ad-hoc signed; WeChat.app is never changed.
        runtime = Path(self._temporary.name) / "Contents" / "Frameworks"
        runtime.mkdir(parents=True)
        launcher = runtime / "wxocr"
        shutil.copy2(executable, launcher)
        launcher.chmod(0o755)
        for name in ("libwxocr.dylib", "libmmmojo.dylib"):
            (runtime / name).symlink_to(frameworks / name)
        (runtime.parent / "Resources").symlink_to(frameworks.parent / "Resources")
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", str(launcher)],
            check=True, capture_output=True, timeout=15,
        )
        # The vendor retains some string pointers, so retain their Python owners.
        self._launcher = os.fsencode(launcher)
        self._bundle_id = f"org.mediaocr.wxocr.p{os.getpid()}".encode()
        self._lib.SetMMMojoConfiguration(1000, c.c_char_p(self._bundle_id))
        self._lib.InitializeMMMojo(0, None)
        self._env = self._lib.CreateMMMojoEnvironment()
        if not self._env:
            raise RuntimeError("无法创建微信 macOS OCR 通信环境")
        self._set_callback(1, c.CFUNCTYPE(None, c.c_uint32, c.c_void_p, c.c_void_p), self._on_read)
        self._set_callback(2, c.CFUNCTYPE(None, c.c_uint32, c.c_void_p, c.c_void_p), self._on_read)
        self._set_callback(3, c.CFUNCTYPE(None, c.c_uint32, c.c_void_p, c.c_void_p), self._on_read)
        self._set_callback(4, c.CFUNCTYPE(None, c.c_bool, c.c_void_p), self._on_connect)
        self._set_callback(5, c.CFUNCTYPE(None, c.c_void_p), self._on_disconnect)
        self._set_callback(6, c.CFUNCTYPE(None, c.c_void_p), lambda _user: None)
        self._set_callback(7, c.CFUNCTYPE(None, c.c_int, c.c_void_p), self._on_launch_failed)
        self._set_callback(8, c.CFUNCTYPE(None, c.c_void_p, c.c_int, c.c_void_p), self._on_error)
        self._lib.SetMMMojoEnvironmentInitParams(self._env, 0, c.c_int(1))
        self._lib.SetMMMojoEnvironmentInitParams(self._env, 2, c.c_char_p(self._launcher))
        self._lib.SetMMMojoEnvironmentInitParams(self._env, 4, c.c_int(1))
        self._lib.AppendMMSubProcessSwitchNative(self._env, b"no-sandbox", b"")
        self._lib.AppendMMSubProcessSwitchNative(self._env, b"base-bundle-id", self._bundle_id)
        self._lib.StartMMMojoEnvironment(self._env)
        self._wait(self._ready)

    def _bind_functions(self):
        pointer = c.c_void_p
        signatures = {
            "SetMMMojoConfiguration": (None, [c.c_int]),
            "InitializeMMMojo": (None, [c.c_int, pointer]),
            "CreateMMMojoEnvironment": (pointer, []),
            # The first two parameters are fixed, the remaining ones are variadic.
            # Declaring this boundary is essential for the Apple Silicon ABI.
            "SetMMMojoEnvironmentCallbacks": (None, [pointer, c.c_int]),
            "SetMMMojoEnvironmentInitParams": (None, [pointer, c.c_int]),
            "AppendMMSubProcessSwitchNative": (None, [pointer, c.c_char_p, c.c_char_p]),
            "StartMMMojoEnvironment": (None, [pointer]),
            "StopMMMojoEnvironment": (None, [pointer]),
            "RemoveMMMojoEnvironment": (None, [pointer]),
            "GetMMMojoReadInfoRequest": (pointer, [pointer, c.POINTER(c.c_uint32)]),
            "RemoveMMMojoReadInfo": (None, [pointer]),
            "CreateMMMojoWriteInfo": (pointer, [c.c_int, c.c_bool, c.c_uint32]),
            "GetMMMojoWriteInfoRequest": (pointer, [pointer, c.c_size_t]),
            "SendMMMojoWriteInfo": (c.c_bool, [pointer, pointer]),
            "RemoveMMMojoWriteInfo": (None, [pointer]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self._lib, name)
            function.restype = result
            function.argtypes = arguments
        cf = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self._run_loop = cf.CFRunLoopRunInMode
        self._run_loop.argtypes = [pointer, c.c_double, c.c_bool]
        self._run_loop.restype = c.c_int
        self._loop_mode = pointer.in_dll(cf, "kCFRunLoopDefaultMode")

    def _set_callback(self, kind, prototype, function):
        callback = prototype(function)
        self._callbacks.append(callback)
        self._lib.SetMMMojoEnvironmentCallbacks(self._env, kind, callback)

    def _fail(self, message):
        self._error = message
        self._ready.set()
        self._done.set()

    def _on_disconnect(self, _user):
        self._fail("微信 macOS OCR 连接已断开")

    def _on_connect(self, connected, _user):
        if not connected:
            self._fail("微信 macOS OCR 无法建立连接")

    def _on_launch_failed(self, code, _user):
        self._fail(f"微信 macOS OCR 启动失败（code={code}）")

    def _on_error(self, buffer, size, _user):
        self._fail(c.string_at(buffer, size).decode("utf-8", errors="replace"))

    def _on_read(self, request_id, info, _user):
        try:
            size = c.c_uint32()
            pointer = self._lib.GetMMMojoReadInfoRequest(info, c.byref(size))
            data = c.string_at(pointer, size.value)
            if request_id == 10001:
                if not Support.FromString(data).supported:
                    self._fail("此微信版本不支持 OCR，请重新提取兼容的 macOS 引擎")
                self._ready.set()
            elif request_id == 10011:
                task_id, result = decode_response(data)
                if task_id == self._task_id:
                    self._result = result
                    self._done.set()
        except Exception as error:
            self._fail(f"微信 macOS OCR 消息解析失败: {error}")
        finally:
            self._lib.RemoveMMMojoReadInfo(info)

    def _wait(self, event):
        deadline = time.monotonic() + 20
        while not event.is_set():
            if time.monotonic() >= deadline:
                raise TimeoutError("微信 macOS OCR 响应超时（20 秒）")
            if self._run_loop(self._loop_mode, 0.02, False) == 1:
                event.wait(0.02)  # A runloop without sources must not busy-spin.
        if self._error:
            raise RuntimeError(self._error)

    def ocr(self, path: str) -> dict:
        self._task_id += 1
        self._done.clear()
        self._result = None
        payload = encode_request(self._task_id, path)
        info = self._lib.CreateMMMojoWriteInfo(1, False, 10010)
        if not info:
            raise RuntimeError("无法创建微信 OCR 请求")
        buffer = self._lib.GetMMMojoWriteInfoRequest(info, len(payload))
        if not buffer:
            self._lib.RemoveMMMojoWriteInfo(info)
            raise RuntimeError("无法分配微信 OCR 请求缓冲区")
        c.memmove(buffer, payload, len(payload))
        # Send takes ownership of info, including on failure.
        if not self._lib.SendMMMojoWriteInfo(self._env, info):
            raise RuntimeError("微信 macOS OCR 请求发送失败")
        self._wait(self._done)
        return self._result

    def close(self):
        if self._env:
            self._lib.StopMMMojoEnvironment(self._env)
            self._lib.RemoveMMMojoEnvironment(self._env)
            self._env = None
        self._temporary.cleanup()


def main():
    # Save the protocol pipe, then route *all* vendor/native/child stdout to stderr.
    # Otherwise native printf output would corrupt the MCP stdio transport.
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())

    def reply(message):
        output.write(json.dumps(message, ensure_ascii=False) + "\n")

    native = None
    try:
        native = NativeOCR(Path(sys.argv[1]), Path(sys.argv[2]))
        reply({"ready": True})
        for line in sys.stdin:
            request = json.loads(line)
            reply(native.ocr(request["path"]))
    except Exception as error:
        reply({"error": str(error)})
    finally:
        if native:
            native.close()
        output.close()


if __name__ == "__main__":
    main()
