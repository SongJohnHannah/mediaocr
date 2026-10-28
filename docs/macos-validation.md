# macOS 验证

验证环境：Apple Silicon arm64、macOS 26.6、微信 4.1.7、CPython 3.11.15。
依赖：MCP 1.30.0、protobuf 7.36.2、PyMuPDF 1.28.2、imageio-ffmpeg 0.6.0。

回归套件共 31 项：23 项单元测试和 8 项原生集成测试。

- 平台分流：macOS 不再选择 Windows DLL；Windows/Linux 发现逻辑与 Windows 本机微信回退。
- 三平台并存：Windows/Linux 通过原有 `wcocr.init` / `wcocr.ocr` 调用；macOS 独立使用新增后端。
- 存量文件：Windows/Linux 的 33 个原有引擎、调用库及相关入口与 `main` 的 Git blob 哈希一致。
- 原生识别：合成中英文截图、置信度、图片尺寸、像素坐标、compact 输出。
- 路径与批量：中文/空格路径、8 项任务触发 spawn 池、缺失文件逐项报错。
- 文档与视频：扫描 PDF、PDF 文本层、视频抽帧及相邻重复帧去重。
- 异常处理：空白图、超时、原生进程退出后重启、进程关闭时的信号权限回退。
- 协议：MCP stdio 握手与工具调用、错误参数、日志不污染 JSON-RPC、Streamable HTTP 图片调用。
- 分发：便携 ZIP 的 14 项原生资源 SHA256 检查、解压目录调用便携引擎、独立环境安装 wheel。

运行：

```sh
MEDIAOCR_RUN_NATIVE_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

仅修改临时或提取目录内的启动器签名，不修改微信安装包。测试图不含真实聊天内容。
Intel Mac 尚未实机验证；Windows/Linux 本次仅验证发现与分流，未执行其原生引擎。
