# mediaocr 📸

> 把微信内置的本地 OCR 引擎提取出来，封装成 **MCP server**，供 AI 客户端离线调用。

微信 Windows / Linux / macOS 客户端都内置端侧 OCR 引擎。
本项目通过 MCP 协议暴露图片、PDF 和视频文字识别工具。
Windows、Linux、macOS 各自的原生引擎都已随仓库分发。
macOS 引擎从微信 4.1.7 复制到 `engine/macos/`，准备好 Python 依赖后即可使用，
无需另外安装、启动或登录微信。三个平台共用识别接口，分别加载本平台的微信引擎。

本项目同时保留 Windows、Linux、macOS 三个平台的支持，由运行系统自动选择后端：

| 平台 | 调用后端 | 引擎与入口 |
| --- | --- | --- |
| Windows | 原有 `wcocr.pyd` | `engine/ocr_engine/` + `engine/4.1.11.55/`；`run_mcp.cmd` |
| Linux | 原有 `wcocr.so` | `engine/wxocr` + `libmmmojo.so` + `ocr_model/`；`python server.py` |
| macOS | 新增 `macos_ocr.py` | 自带 `engine/macos/`；`run_mcp.sh` |

**换到另一台 Mac 也无需安装微信**：完整下载最新 `main` 分支，保留项目内的
`engine/macos/`，准备好 Python 3.11+ 和项目依赖后即可离线识别图片。

**完全离线 · 无需网络 · 无需登录 · 识别质量对标腾讯**

---

## ✨ 特性

- 🚀 **开箱即用**：Windows / Linux / macOS 三平台引擎已随仓库分发，无需额外提取/下载
- 🍎 **macOS 原生支持**：优先使用项目内微信引擎，复用图片、批量、PDF 和视频接口
- 🔒 **完全离线**：图片不出本机，隐私安全
- 🇨🇳 **顶级中文识别**：实测营业执照类文字置信度 0.99+
- ⚡ **速度快**：单张识别 0.2~0.4s（暖）；批量可并行（`--workers`，实测 x1.38）
- 🎞 **视频帧去重**：字幕场景 OCR 调用减少 50~80%
- 🧠 **自适应 dpi**：扫描页默认 150，低置信度自动升 300 重试，质量不减速度更快
- 🗜 **compact 输出**：大批量扫描只回文字，payload 减 ~73%
- 🔌 **MCP 协议**：兼容 Claude Desktop、Hermes、Cursor 等一切支持 MCP 的客户端

## 🧠 原理

微信 OCR = 端侧深度学习模型（.xnet 格式，FP16 量化）：
`文本检测 (text_det)` → `文本识别 (text_rec)` → `段落/后处理`

> **编排层**：`core.py` 在引擎之上做四件事——
> ① 视频抽帧后按「全帧 + 底部字幕带」双指纹去重，重复帧不 OCR；
> ② 扫描页默认 150dpi 渲染，低置信度自动升 300 重试；
> ③ 大批量任务走 spawn 进程池（`--workers N`，每 worker 独立引擎连接）并行；
> ④ 结果支持 `compact` 精简输出（省略坐标，只回文字）。

运行时依赖（实测最小集，共 55MB）：
| 文件 | 大小 | 作用 |
|---|---|---|
| `wxocr.dll` | 24MB | OCR 引擎本体（微信 4.x；3.x 为 WeChatOCR.exe） |
| `.xnet` 模型 + 字符集 | 26MB | 检测/识别/段落模型 |
| `Weixin.exe` | 3MB | Chromium 宿主（`--type=wxocr` 模式加载引擎） |
| `mmmojo_64.dll` | 2.5MB | IPC 通信框架（唯一硬依赖） |

> ✅ 三个平台的引擎二进制**已随本仓库分发**，安装 Python 依赖后即可运行。
> 更新 macOS 引擎可用 `extract_engine.py --wechat-app /Applications/WeChat.app`；其他平台见对应章节。

## 📦 Windows 安装

### 1. 前置条件

- Windows（微信 3.x 或 4.x 已安装）
- Python 3.11（`wcocr.pyd` 是 3.11 ABI）
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip

### 2. 克隆与准备

```bash
git clone https://github.com/SongJohnHannah/mediaocr.git
cd mediaocr

# 创建环境
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe ".[documents]"

# 引擎已随仓库分发（engine/ 目录），clone 即用，无需提取
# 如需从本机微信重新生成：.venv/Scripts/python.exe extract_engine.py
```

### 3. wcocr.pyd（Windows 调用封装，已随仓库分发）

仓库根目录已附带 `wcocr.pyd`（Python 3.11 ABI），clone 即用，无需下载。

> 如需自行获取/重新编译：`wcocr.pyd` 来自 [swigger/wechat-ocr](https://github.com/swigger/wechat-ocr) 的 demo-7 release（无开源 license，仅限个人自用），
> 可 `curl -L -o demo7.7z https://github.com/swigger/wechat-ocr/releases/download/demo-7/demo7.7z` 解压后替换，或按其说明自行编译。

### 4. 测试

```bash
.venv/Scripts/python.exe test_client.py
```

应看到：

```
✓ MCP 初始化成功
✓ 工具列表: ['ocr_image', 'ocr_document']
✓ ocr_image 调用成功
  errcode=0 行数=14
    [0.969] 企业名称: 许昌市建安区嘉言懿行电子..
    ...
```

---

## 🐧 Linux 支持（同一套微信引擎）

Linux 版微信同样内置端侧 OCR 引擎，本项目在 Linux 上使用**同一套微信引擎**
（wxocr ELF + libmmmojo.so + ocr_model/），识别效果与 Windows 版一致。
server.py 自动检测平台，Windows / Linux / macOS 共用同一份代码与工具。

### Linux 版结构差异

| Windows | Linux | 说明 |
|---|---|---|
| `engine/ocr_engine/wxocr.dll` | `engine/wxocr` | 引擎本体（Windows 是 DLL，Linux 是 ELF 可执行文件） |
| `engine/<版本>/mmmojo_64.dll` | `engine/libmmmojo.so` | IPC 通信框架 |
| `engine/Weixin.exe` | （无，wxocr 自包含） | Linux 的 wxocr 是独立守护进程，不需要 Chromium 宿主 |
| `engine/ocr_engine/*.xnet` | `engine/ocr_model/*.xnet` | 模型 + 字符集（Linux 版还多 FPOCRRecog.xnet） |
| `wcocr.pyd` | `wcocr.so` | Python 调用封装（同一套 swigger/wechat-ocr 源码编译） |

### Linux 安装步骤（clone 即用）

Linux 引擎（`engine/wxocr` + `libmmmojo.so` + `ocr_model/`）和 `wcocr.so` 都已随仓库分发，clone 后只需建环境：

```bash
# 1. 前置：uv（Python 3.11）+ mcp
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python ".[documents]"

# 2. 测试
.venv/bin/python test_client.py
```

> 如需**从零重新生成** Linux 引擎与 wcocr.so（引擎是微信专有资产，可按需自建）：
>
> ```bash
> # 提取引擎（Linux 版微信 deb 包解压后拷贝，或已有 /opt/wechat 直接拷）
> #    deb: https://linux.weixin.qq.com/ 的 WeChatLinux_x86_64.deb
> dpkg-deb -x WeChatLinux_x86_64.deb wxdeb/
> mkdir -p engine
> cp wxdeb/opt/wechat/wxocr engine/
> cp wxdeb/opt/wechat/libmmmojo.so engine/
> cp -r wxdeb/opt/wechat/ocr_model engine/
>
> # 编译 wcocr.so（需 g++ C++20 + cmake，自动拉 protobuf）
> git clone --depth 1 https://github.com/swigger/wechat-ocr.git
> cd wechat-ocr
> cmake -B build -DCMAKE_BUILD_TYPE=Release
> cmake --build build -j$(nproc)
> cp build/wcocr.cpython-311-x86_64-linux-gnu.so ../wcocr.so
> ```

### 接入 Hermes（Linux）

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  mediaocr:
    command: /path/to/mediaocr/.venv/bin/python
    args: [/path/to/mediaocr/server.py]
    enabled: true
```

### Linux 已知差异

- `wcocr.so` 已随仓库预编译分发（Python 3.11 ABI），一般无需自己编译；编译方法见上方"从零重新生成"
- 提取引擎时无需微信安装目录结构，`wxocr` + `libmmmojo.so` + `ocr_model/` 三个文件即可独立运行
- 引擎版本随微信更新，接口可能变动

## 🍎 macOS 支持（微信原生引擎）

已在 Apple Silicon、macOS 26.6、微信 4.1.7 上实测中英文图片、中文路径、
批量 spawn 并行、扫描/文本 PDF、视频帧去重、进程异常恢复及 MCP stdio / HTTP 调用。
该版本的微信引擎包含 arm64 / x86_64 两种架构；Intel Mac 尚未实机验证。
需要 macOS 12+、Python 3.11+；不需要 `wcocr.so`、Rosetta 或编译器。
项目已包含微信 4.1.7 的 14 项 OCR 原生资源（约 83.3 MB），可直接从项目目录加载。
`engine/macos/manifest.json` 记录来源版本、架构、文件大小和 SHA256。

### 在另一台 Mac 上安装与验证（无需微信）

完整下载最新 `main` 分支并解压，或使用下面的克隆命令。
项目内已带有对应的原生引擎、通信库和模型，无需在新 Mac 上安装、启动或登录微信，
也无需重新提取引擎。

先准备 Python 3.11+ 和 uv，再按下面的步骤获取项目、安装依赖并验证：

```bash
# 已下载并解压项目时，直接进入其目录，跳过下面两行
git clone --branch main --single-branch https://github.com/SongJohnHannah/mediaocr.git
cd mediaocr

# 创建 Python 环境，安装依赖（含 PDF / 视频支持）
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python ".[documents]"

# 验证项目内的 14 项引擎资源，并直接识别测试截图
.venv/bin/python scripts/verify_macos_engine.py

# 验证 MCP 握手、工具列表和图片识别
.venv/bin/python test_client.py
```

没有 uv 时，也可用 Python 自带的 venv 和 pip 准备环境：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install ".[documents]"
```

首次安装依赖需要网络；环境准备好后，图片识别完全离线。
验证脚本会检查资源哈希，并输出识别文字及项目内引擎路径。

### 启动与引擎选择

```bash
.venv/bin/python server.py
# 或使用项目启动脚本
bash run_mcp.sh
```

默认启动的是 MCP stdio 服务，会等待 MCP 客户端输入；接入方式见下方「macOS MCP 配置」。

默认查找顺序：`engine/macos/` 便携引擎 → `/Applications/WeChat.app` 或
`Weixin.app` → `~/Applications/`。项目内资源完整时直接使用自带引擎，
不依赖微信安装目录。只有需要指定其他引擎来源时，才设置下面的环境变量：

```bash
MEDIAOCR_WECHAT_PATH="/path/to/WeChat.app" .venv/bin/python server.py
```

`MEDIAOCR_WECHAT_PATH` 也接受 `Contents/Frameworks` 目录；显式指定后若资源不完整，
启动会报错，避免无意间使用另一个微信版本。
无需启动或登录微信，不读取聊天记录。已有微信截图、保存的图片均交给 `ocr_image`。

macOS 启动器带有微信应用的沙箱继承要求。后端只复制一个临时启动器并做本地签名，
保持动态库和模型来自原安装包，**不会修改 WeChat.app**。
每个 worker 使用独立 bundle ID 建立 Mach IPC，并隔离原生进程和日志，
防止引擎崩溃或日志破坏 MCP stdio；请求超时后可重新启动引擎。
受限执行沙箱可能禁止 Mach IPC，此时请在普通终端或允许本地进程通信的 MCP 客户端运行。

### 提取与打包

```bash
# 可选：从本机微信更新 engine/macos/，通常无需运行
.venv/bin/python extract_engine.py --wechat-app "/path/to/WeChat.app"

# 构建包含 Python 源码、原生引擎、模型与测试样图的便携 ZIP
.venv/bin/python scripts/build_macos.py
# 产物：dist/mediaocr-0.2.0-macos.zip 及 .zip.sha256

# 构建不含微信专有引擎的 Python wheel / 源码包
uv pip install --python .venv/bin/python build
.venv/bin/python -m build
```

便携包解压后，在其目录中按上面的环境安装命令准备 Python 依赖，再运行
`bash run_mcp.sh` 或 `.venv/bin/python server.py`。
`engine-manifest.json` 记录打包引擎文件的 SHA256。
`engine/macos/` 已提交 Git；`dist/` 为本地构建产物。微信资源仍归腾讯所有。

### macOS MCP 配置

```json
{
  "mcpServers": {
    "mediaocr": {
      "command": "/absolute/path/mediaocr/.venv/bin/python",
      "args": ["/absolute/path/mediaocr/server.py"]
    }
  }
}
```

HTTP 模式：`.venv/bin/python server.py --http 8765`，端点为
`http://127.0.0.1:8765/mcp`（Streamable HTTP）。

### 回归测试

```bash
# 无原生引擎的回归测试（平台发现、协议、错误处理、初始化并发）
.venv/bin/python -m unittest discover -s tests -v
# macOS 完整实机测试（需预装 documents 依赖，允许 Mach IPC）
MEDIAOCR_RUN_NATIVE_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

测试图 `tests/fixtures/wechat_sample.png` 是合成样本，不包含真实聊天或个人信息。

## 🔌 接入 MCP 客户端

### Hermes Agent

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  mediaocr:
    command: C:\path\to\mediaocr\.venv\Scripts\python.exe
    args: [C:\path\to\mediaocr\server.py]
    enabled: true
```

### Claude Desktop

```json
{
  "mcpServers": {
    "mediaocr": {
      "command": "C:\\path\\to\\mediaocr\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\mediaocr\\server.py"]
    }
  }
}
```

新开会话后即可使用工具（MCP 工具无热加载）。

> 性能：启动命令可加 `--workers N`（默认 `min(逻辑核,3)`）控制并行度，
> 批量/PDF/视频任务在多核机器上收益明显。

## 🛠 工具

| 工具 | 参数 | 说明 |
|---|---|---|
| `ocr_image` | `image_paths: list, compact: bool=False` | 图片/截图/扫描件识别（1~20 张自动批量），返回每行文字+坐标框+置信度+纯文本；`compact=True` 只回文字+行数（大批量输出更小） |
| `ocr_document` | `document_path, max_pages=20, dpi=150, interval_sec=5, max_frames=10, auto_dpi=True, dedup=True, compact=False` | PDF / 视频识别：PDF 文本层直抽或渲染 OCR（低置信度页自动升 300 重试）；视频 ffmpeg 抽帧 + 相邻帧去重（字幕场景省 50~80% OCR 调用） |

> 每个工具的 description 都内置了「何时使用」触发词（图片扫描、截图文字提取、
> 营业执照/发票识别、PDF 扫描件、视频字幕提取等），任何 MCP 客户端
> （Hermes / Claude Desktop / Cursor）看到相关自然语言描述即会自动触发。

### 工具分工（避免与其他工具混淆）

| 需求 | 用哪个工具 |
|---|---|
| 已有图片文件/截图/视频截图 → 读字 | `ocr_image` |
| 已有 PDF（扫描件或电子版）→ 读字 | `ocr_document`（自动判断文本型/扫描型） |
| 已有视频文件 → 提取字幕/画面文字 | `ocr_document`（ffmpeg 自动抽帧） |
| 截屏、网页截图、视频截图动作本身 | 专门的截图工具（如 media-kit） |
| 下载视频/网页内容 | 专门的下载工具（如 media-kit） |

## ⚡ 性能（实测，6 核 i7-9750H）

| 优化 | 机制 | 实测收益 |
|---|---|---|
| 视频帧去重 | 全帧+底部字幕带双指纹，重复帧跳过 | 字幕场景 OCR 调用 **-50~80%**（抽帧 12 → OCR 3） |
| 批量并行 | spawn 进程池，每 worker 独立引擎 | 16 张图 **x1.38**（6.1→8.5 img/s） |
| 自适应 dpi | 150 起步，低置信度升 300 重试 | 扫描 PDF **~10%**（2.47→2.21s） |
| 启动预热 | 后台线程预载引擎 | 首调免 ~0.55s 冷启动 |
| 依赖异步安装 | pymupdf/ffmpeg 后台装 | 首调不再卡 30s+ |
| compact 输出 | 只回文字+行数 | payload **-73%** |

`--workers N`：OCR 并行 worker 数，默认 `min(逻辑核,3)`。
实测引擎多守护进程并行上限 ~1.6x（3 worker 后无增益），且每 worker 约 200MB 内存，
故默认封顶 3；多核机器可自行调到 3~4。

## 🗺 项目结构

```
mediaocr/
├── server.py            # MCP server（FastMCP，stdio / --http / --workers N）
├── core.py              # 编排层：批量管线 / 视频帧去重 / 自适应 dpi / spawn 并行池
├── macos_ocr.py         # macOS 引擎发现、独立工作进程、超时与清理
├── macos_ocr_worker.py  # ctypes + MMMojo 原生通信，隔离原生日志
├── macos_ocr_protocol.py # 微信 4.x protobuf 消息适配
├── extract_engine.py    # Windows / macOS 引擎提取
├── pyproject.toml       # 安装依赖、mediaocr 命令与 wheel 打包配置
├── run_mcp.sh           # macOS / Linux 启动入口
├── scripts/build_macos.py # macOS 原生引擎便携包构建
├── tests/              # 回归测试、真实引擎/MCP 集成测试、合成测试截图
├── docs/architecture.md # 架构与 macOS 调用链说明
├── test_client.py       # MCP 协议测试客户端
├── wsl_test.py          # WSL/Linux 全链路测试（协议层 + 实际 OCR）
├── engine/              # Windows / Linux / macOS 原生引擎均已入库
├── wcocr.pyd            # Windows 调用封装（Py 3.11 ABI，已入库）
└── wcocr.so             # Linux 调用封装（Py 3.11 ABI，已入库）
```

> 性能说明：`--workers N` 控制 OCR 并行数（默认 `min(逻辑核,3)`）。引擎多守护进程并行上限实测 ~1.6x（3 worker 后无增益），且每 worker ~200MB 内存；主优化是「减少调用次数」——视频帧去重（字幕场景 -75% 调用）、自适应 dpi（PDF -10%）。

## ⚠️ 已知限制

- **仅限个人自用/学习**：swigger/wechat-ocr 无开源 license，商用有法律风险（商用请用 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) / [RapidOCR](https://github.com/RapidAI/RapidOCR)，Apache-2.0）
- `wcocr.pyd` 是 CPython 3.11 x64 ABI，需用 Python 3.11 运行
- 只输出文字 + 坐标，无身份证/发票结构化字段解析
- 引擎随微信版本更新而变（3.x→4.x 换了 DLL），接口可能变动

## 🙏 致谢

- [swigger/wechat-ocr](https://github.com/swigger/wechat-ocr) — 引擎调用逆向
- [EEEEhex/QQImpl](https://github.com/EEEEhex/qqimpl) — MMMojo 通信原理

## 📄 License

代码部分 MIT。引擎二进制归腾讯所有，`wcocr.pyd` 归其原作者所有。
