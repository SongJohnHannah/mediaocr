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

> ⚠️ 引擎二进制为微信专有资产，**不随本仓库分发**，请运行 `extract_engine.py` 从你本机已安装的微信中提取（相当于自带引擎，无需联网下载任何东西）。

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

# 从本机微信提取 OCR 引擎（~55MB）
.venv/Scripts/python.exe extract_engine.py
```

### 3. 获取 wcocr.pyd（调用封装）

`wcocr.pyd` 是引擎调用封装，来自 [swigger/wechat-ocr](https://github.com/swigger/wechat-ocr) 的 demo-7 release（无开源 license，仅限个人自用）：

```bash
# 从 GitHub release 下载 demo7.7z，解压后把 wcocr.pyd 复制到项目根目录
curl -L -o demo7.7z https://github.com/swigger/wechat-ocr/releases/download/demo-7/demo7.7z
# 解压后: cp demo7/wcocr.pyd ./
```

> 或者：自己按 [swigger/wechat-ocr](https://github.com/swigger/wechat-ocr) 的说明编译 `wcocr.pyd`。

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
| `ocr_image` | `image_path: str` | 单张图片识别，返回每行文字 + 坐标框 + 置信度 + 纯文本 |
| `ocr_batch` | `image_paths: list` | 批量识别（≤20 张） |

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
