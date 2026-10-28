# mediaocr 架构

项目是本地 Python MCP 服务，不包含前端或数据库。

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 协议与入口 | `server.py` | 平台分流、微信引擎发现、懒初始化、MCP stdio / Streamable HTTP |
| 任务编排 | `core.py` | 格式化结果、图片批量、PDF 文本提取/渲染、自适应 dpi、视频去重、spawn 池 |
| Windows/Linux 后端 | `wcocr.pyd` / `wcocr.so` + `engine/` | 原有 CPython 3.11 原生扩展和微信引擎 |
| macOS 后端 | `macos_ocr.py` | 查找原生引擎、启动常驻隔离进程、串行请求、超时、关闭与重试 |
| macOS 原生桥接 | `macos_ocr_worker.py` | 临时启动器本地签名、MMMojo ctypes ABI、Mach IPC、工作进程协议 |
| macOS 消息 | `macos_ocr_protocol.py` | 微信 4.x protobuf 请求/响应与既有 wcocr 结果结构互转 |
| 提取与分发 | `extract_engine.py` / `scripts/build_macos.py` / `pyproject.toml` | 本机引擎提取、便携 ZIP、Python wheel/源码包 |

图片调用链：MCP `ocr_image` → `run_pipeline` → `WeChatOCR.ocr` → 平台后端 →
`_format_result`。批量超过阈值时，各 spawn worker 仍独立创建 `WeChatOCR`，
因此 macOS 可复用同一套进程池，不把原生指针或线程锁跨进程传递。
PDF 和视频经过文本提取、渲染或抽帧后进入同一调用链。

macOS 每个后端对应一个 Python 工作进程和一个微信 OCR 子进程。
工作进程保留独立 JSON stdout 管道，将微信原生 stdout 重定向到 stderr，
通过 10001 握手、10010 请求、10011 响应调用 OCR。
MMMojo 配置 1000 与子进程 `--base-bundle-id` 使用相同的进程专属值。
ctypes 显式声明固定参数与可变参数的分界，兼容 Apple Silicon ABI。

微信原始启动器带有应用沙箱继承要求；运行时只对临时副本进行 ad-hoc 签名。
动态库和模型通过链接引用选定的引擎目录，保持 `Contents/Frameworks` 与
`Contents/Resources` 相对结构。仓库已包含 `engine/macos/` 的完整资源，
运行时的链接均指向项目内文件，不依赖微信安装目录。文件来源和哈希记录在
`engine/macos/manifest.json`，可用 `scripts/verify_macos_engine.py` 直接验证。
所有识别发生在本机，不启动微信主程序，不读取账户数据。

验证分为无需启动引擎的单元测试，以及 `MEDIAOCR_RUN_NATIVE_TESTS=1` 启用的
macOS 实机测试。Windows/Linux 在 Mac 上验证平台分流和发现逻辑；原生执行需各自系统。
