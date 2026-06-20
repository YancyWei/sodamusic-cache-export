# SodaMusic Cache Export

[![Python 3](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg)](https://github.com/yourname/sodamusic-cache-export)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> 将 汽水音乐（SodaMusic）macOS 本地缓存导出为可播放音频文件。

SodaMusic Cache Export 是一个**只读**的本地缓存导出工具。它解析 SodaMusic 在 macOS 上留下的 `LunaCacheV2` 缓存目录，识别已缓存的 `.bin` 媒体文件，对受 CENC 保护的 MP4/M4A 缓存进行离线解密，最终写出带元数据、封面和歌词的可播放音频文件。

**核心原则：**

- 只读访问缓存，绝不修改 `LunaCacheV2`。
- 仅导出已缓存到本地的媒体，不调用 SodaMusic 媒体 API，不下载远程文件。
- CENC 解密依赖本地 SodaMusic 的 `device.node` 与本机 `node` 环境。

---

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [环境要求](#环境要求)
- [安装](#安装)
- [使用方式](#使用方式)
  - [Web UI](#web-ui)
  - [命令行](#命令行)
  - [导出所有缓存](#导出所有缓存)
  - [导出指定音质](#导出指定音质)
  - [等待并导出目标歌曲](#等待并导出目标歌曲)
  - [批量导出](#批量导出)
- [技术原理](#技术原理)
- [项目结构](#项目结构)
- [测试](#测试)
- [免责声明](#免责声明)
- [许可证](#许可证)

---

## 功能特性

- 本地 Web UI：一键启动、查看环境状态、搜索并导出歌曲。
- 离线 CENC 解密：利用 SodaMusic 本地 `device.node` 解析 `spade` 密钥，AES-CTR 解密 MP4/M4A 样本。
- 多格式输出：保留原始容器、转码为 MP3，或提取为原生 FLAC。
- 元数据写入：自动嵌入标题、艺术家、专辑、封面图与 LRC 歌词（KRC 转 LRC）。
- 缓存分析：在不导出的情况下分析本地索引与实际缓存状态。
- 目标等待：监听缓存目录，等待指定歌曲/音质出现并自动导出。
- 批量任务：通过 JSON/JSONL/CSV 批量等待并导出多首歌曲。
- 导出清单：生成 `manifest.json` 与 `manifest.csv`（UTF-8-BOM，兼容 Excel）。
- 音频校验：使用 `ffmpeg` 或 macOS `afconvert` 验证输出可解码性。

---

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/yourname/sodamusic-cache-export.git
cd sodamusic-cache-export

# 2. 安装 Python 依赖
python3 -m pip install -r requirements.txt

# 3. 构建前端（需要 Node.js）
# 注意：src/web/ 是构建产物，由 web/ 经 build:web 生成，不应手动修改。
cd web && npm install && npm run build:web && cd ..

# 4. 启动 Web UI
python3 src/start_sodamusic_export.py
```

> **关于 `src/web/`：** 该目录是前端构建产物，由 `web/scripts/copy-to-src-web.mjs` 从 `web/dist/` 复制而来，不应手动修改，也不应提交到版本控制。修改前端源码后请重新运行 `npm run build:web`。

启动器会自动检查依赖、安装缺失的 Python 包，并在 macOS + Homebrew 环境下自动安装 `node` 与 `ffmpeg`。服务就绪后会自动打开浏览器访问 `http://127.0.0.1:8765`。

---

## 环境要求

### 必需

- Python 3.10+
- `pycryptodome`（AES-CTR 解密）
- `mutagen`（元数据写入）

### CENC 解密必需

- `node`（用于加载 SodaMusic 的 `device.node`）

### MP3 / FLAC 输出或严格校验必需

- `ffmpeg` / `ffprobe`

### macOS 可选

- `afconvert`（系统自带，输出可解码性回退校验）
- `osascript` / `swiftc`（播放录制功能）

---

## 安装

### 自动安装（推荐）

```bash
python3 src/start_sodamusic_export.py
```

### 手动安装

```bash
# Python 依赖
python3 -m pip install -r requirements.txt

# macOS 系统工具
brew install node ffmpeg

# 前端构建
cd web
npm install
npm run build:web
cd ..
```

---

## 使用方式

### Web UI

启动本地服务后访问：

```text
http://127.0.0.1:8765
```

Web UI 提供：

- 环境就绪检查
- 一键导出全部缓存
- 搜索目标歌曲并等待缓存
- 输出格式、码率、严格校验等高级选项

### 命令行

#### 导出所有缓存

```bash
python3 src/export_sodamusic_cache.py
```

#### 导出为 MP3

```bash
python3 src/export_sodamusic_cache.py --format mp3
```

#### 导出为原生 FLAC

```bash
python3 src/export_sodamusic_cache.py --format flac --require-output-match
```

#### 仅复制原始缓存容器

```bash
python3 src/export_sodamusic_cache.py --format original
```

#### 指定输出目录

```bash
python3 src/export_sodamusic_cache.py --output-dir "/Users/$(whoami)/Music/SodaMusic Export"
```

### 导出指定音质

```bash
python3 src/analyze_sodamusic_cache.py \
  --query "零几年听的情歌" \
  --artist "GG啵！" \
  --target lossless/flac \
  --selection-out /tmp/sodamusic-selection.json

python3 src/export_sodamusic_cache.py \
  --selection-file /tmp/sodamusic-selection.json \
  --format flac \
  --require-output-match
```

### 等待并导出目标歌曲

启动 watcher，然后在 SodaMusic 客户端中播放目标歌曲并切换到目标音质：

```bash
python3 src/watch_sodamusic_cache.py \
  --query "零几年听的情歌" \
  --target lossless/flac \
  --require-indexed \
  --require-single-track \
  --stable-seconds 1 \
  --selection-format flac \
  --selection-out /tmp/sodamusic-target.json \
  --export-when-found \
  --default-format flac \
  --require-output-match
```

或使用一键目标包装器：

```bash
python3 src/target_sodamusic_cache.py \
  --query "零几年听的情歌" \
  --artist "GG啵！" \
  --target lossless/flac \
  --wait-index \
  --output-dir "/Users/$(whoami)/Music/SodaMusic Export"
```

### 批量导出

```bash
# 生成目标列表
python3 src/analyze_sodamusic_cache.py \
  --query "零几年听的情歌" \
  --artist "GG啵！" \
  --target lossless/flac \
  --batch-target-out /tmp/sodamusic-targets.json

# 执行批量等待与导出
python3 src/batch_target_sodamusic_cache.py /tmp/sodamusic-targets.json \
  --output-dir "/Users/$(whoami)/Music/SodaMusic Export"
```

### 缓存分析（不导出）

```bash
python3 src/analyze_sodamusic_cache.py \
  --json-out /tmp/sodamusic-cache-analysis.json \
  --csv-out /tmp/sodamusic-cache-analysis.csv
```

---

## 技术原理

1. **解析索引**：扫描 `LunaCacheV2/entries.db` 中的 msgpackr 记录，提取 `chunkId`（缓存 UUID）、`trackId`、音质、`spade` 密钥材料与曲目元数据。
2. **匹配缓存**：将索引记录与本地 `<cacheUuid>.bin` 文件匹配，按文件大小与音质排序候选。
3. **文件嗅探**：通过文件头识别 `flac`、`mp3`、`mp4`、`m4a` 等真实格式。
4. **CENC 解密**：解析 MP4 box（`stsz`、`stsc`、`stco`/`co64`、`senc`、`tenc` 等），调用本地 `device.node` 解码 `spade` 得到 AES 密钥，使用 AES-CTR 解密样本，并恢复原始音频 sample entry。
5. **转码与写入**：根据目标格式，直接保留、转码为 MP3，或解包为 FLAC，并通过 `mutagen` 写入封面与歌词。
6. **校验**：使用 `ffmpeg`/`ffprobe` 或 `afconvert` 验证输出可播放性与格式匹配性。

更详细的协议说明见 [`docs/local-cache-protocol.md`](docs/local-cache-protocol.md)。

---

## 项目结构

```text
sodamusic-cache-export/
├── src/                              # 核心脚本
│   ├── export_sodamusic_cache.py     # 核心导出器
│   ├── analyze_sodamusic_cache.py    # 缓存分析器
│   ├── target_sodamusic_cache.py     # 单目标工作流
│   ├── watch_sodamusic_cache.py      # 缓存监听等待
│   ├── batch_target_sodamusic_cache.py # 批量目标工作流
│   ├── sodamusic_export_web.py       # Web UI 服务器
│   ├── start_sodamusic_export.py     # 跨平台启动器
│   ├── runtime_dependencies.py       # 依赖检查与安装
│   ├── record_sodamusic_playback.py  # 播放录制回退
│   └── web/                          # 构建后的前端资源
├── web/                              # Next.js 15 前端源码
├── scripts/                          # 平台启动脚本
│   ├── start.command                 # macOS 双击启动
│   ├── start.sh                      # POSIX shell
│   ├── start.bat                     # Windows CMD
│   └── start.ps1                     # Windows PowerShell
├── tests/                            # 单元测试
├── docs/                             # 协议文档
├── examples/sample-export/           # 示例导出文件
├── requirements.txt                  # Python 依赖
└── README.md                         # 本文件
```

---

## 测试

```bash
python3 -m pytest tests/
```

当前测试套件包含 312 个测试，覆盖导出器核心逻辑、MessagePack 解析、MP4 box 解析、加密解密、分析器、监听器、目标工作流、批量任务、Web 服务器请求处理、启动器与依赖检查。所有外部工具（`node`、`ffmpeg`、`device.node`、网络封面下载）均被 Mock。

---

## 免责声明

- 本工具**仅读取** SodaMusic 本地缓存，不会修改、上传或重新分发任何受版权保护的内容。
- 本工具**不实现** SodaMusic 服务器鉴权、URL 签名或远程媒体下载。
- `device.node` 属于 SodaMusic 官方客户端文件，不会随本仓库提供或重新分发；工具仅在用户本机已安装 SodaMusic 的前提下动态定位并加载该文件。
- 请遵守当地法律法规，仅将本工具用于个人已合法获取的缓存内容备份。

---

## 许可证

[MIT](LICENSE)
