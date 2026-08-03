# mediaocr 📸

> 把微信内置的本地 OCR 引擎提取出来，封装成 **MCP server**，供 AI 客户端离线调用。

微信 Windows 客户端内置端侧 OCR 引擎（文本检测 + 识别，支持 13,562 个中文字符）。
本项目把它从微信中剥离出来（仅需 ~55MB），通过 MCP 协议暴露为 AI 可用的工具。

**完全离线 · 无需网络 · 无需登录 · 识别质量对标腾讯**

---

## ✨ 特性

- 🚀 **开箱即用**：一条命令从本机微信提取引擎，无需下载模型
- 🔒 **完全离线**：图片不出本机，隐私安全
- 🇨🇳 **顶级中文识别**：实测营业执照类文字置信度 0.99+
- ⚡ **速度快**：单张识别 0.4~0.6s
- 🔌 **MCP 协议**：兼容 Claude Desktop、Hermes、Cursor 等一切支持 MCP 的客户端

## 🧠 原理

微信 OCR = 端侧深度学习模型（.xnet 格式，FP16 量化）：
`文本检测 (text_det)` → `文本识别 (text_rec)` → `段落/后处理`

运行时依赖（实测最小集，共 55MB）：
| 文件 | 大小 | 作用 |
|---|---|---|
| `wxocr.dll` | 24MB | OCR 引擎本体（微信 4.x；3.x 为 WeChatOCR.exe） |
| `.xnet` 模型 + 字符集 | 26MB | 检测/识别/段落模型 |
| `Weixin.exe` | 3MB | Chromium 宿主（`--type=wxocr` 模式加载引擎） |
| `mmmojo_64.dll` | 2.5MB | IPC 通信框架（唯一硬依赖） |

> ✅ 引擎二进制**已随本仓库分发**（Windows + Linux 双平台兜底，clone 即用，无需联网下载）。
> 引擎为微信专有资产，如需自行重新生成，可删除 `engine/` 后运行 `extract_engine.py`（Windows）或按下方 Linux 章节操作。

## 📦 安装

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
uv pip install --python .venv/Scripts/python.exe "mcp[cli]==1.9.*"

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
✓ 工具列表: ['ocr_image', 'ocr_batch']
✓ ocr_image 调用成功
  errcode=0 行数=4 尺寸=900x300
    [0.999] 许昌市东城区嘉言懿行文化工作室
    ...
```

---

## 🐧 Linux 支持（同一套微信引擎）

Linux 版微信同样内置端侧 OCR 引擎，本项目在 Linux 上使用**同一套微信引擎**
（wxocr ELF + libmmmojo.so + ocr_model/），识别效果与 Windows 版一致。
server.py 自动检测平台，双分支运行，**Windows / Linux 共用同一份代码与工具**。

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
uv pip install --python .venv/bin/python "mcp[cli]==1.9.*"

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

## 🛠 工具

| 工具 | 参数 | 说明 |
|---|---|---|
| `ocr_image` | `image_path: str` | 单张图片/截图/扫描件识别，返回每行文字+坐标框+置信度+纯文本 |
| `ocr_batch` | `image_paths: list` | 批量识别（≤20 张） |
| `ocr_pdf` | `pdf_path, max_pages, dpi` | PDF 识别：文本型自动抽取，扫描型自动渲染+OCR |
| `ocr_video` | `video_path, interval_sec, max_frames` | 视频画面文字识别（ffmpeg 抽帧+OCR，字幕/标题/弹幕） |
| `extract_pdf_text` | `pdf_path, max_pages` | 文本型 PDF 快速提取文字层（不 OCR） |

> 每个工具的 description 都内置了「何时使用」触发词（图片扫描、截图文字提取、
> 营业执照/发票识别、PDF 扫描件、视频字幕提取等），任何 MCP 客户端
> （Hermes / Claude Desktop / Cursor）看到相关自然语言描述即会自动触发。

### 工具分工（避免与其他工具混淆）

| 需求 | 用哪个工具 |
|---|---|
| 已有图片文件/截图/视频截图 → 读字 | `ocr_image` / `ocr_batch` |
| 截屏、网页截图、视频截图动作本身 | 专门的截图工具（如 media-kit） |
| 已有视频文件 → 提取字幕/画面文字 | `ocr_video`（自动抽帧，无需先截图） |
| 已有 PDF（扫描件或电子版）→ 读字 | `ocr_pdf`（自动判断类型） |
| 已有文本型 PDF → 快速复制文字 | `extract_pdf_text` |
| 下载视频/网页内容 | 专门的下载工具（如 media-kit） |

## 🗺 项目结构

```
mediaocr/
├── server.py            # MCP server（FastMCP，stdio）
├── extract_engine.py    # 从微信提取 OCR 引擎（核心脚本）
├── test_client.py       # MCP 协议测试客户端
├── engine/              # ⚠️ 运行时生成，不入库（~55MB）
└── wcocr.pyd            # ⚠️ 调用封装，不入库（见「获取 wcocr.pyd」）
```

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
