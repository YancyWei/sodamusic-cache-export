# SodaMusic Cache Export

[English](README.en.md) | 简体中文

[![Python 3](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg)](https://github.com/YancyWei/sodamusic-cache-export)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> 将汽水音乐（SodaMusic）本地缓存导出为可播放音频文件的只读工具。

SodaMusic Cache Export 解析 SodaMusic 在 macOS 上留下的 `LunaCacheV2` 本地缓存目录，识别已缓存的 `.bin` 媒体文件，对受 CENC 保护的 MP4/M4A 缓存进行离线解密，并输出带元数据、封面和歌词的可播放音频文件。

## 特性

- 只读访问缓存目录，不修改 `LunaCacheV2`
- 仅导出已经缓存到本地的媒体，不调用 SodaMusic 媒体 API
- 使用本机 SodaMusic 自带的 `device.node` 做离线密钥派生
- 支持原始容器导出、MP3 转码、FLAC 导出
- 自动写入标题、艺术家、专辑、封面和歌词
- 提供本地 Web UI、分析脚本、监听等待和批量导出流程
- 生成 `manifest.json` 与 `manifest.csv` 便于审计与追踪

## 仓库内容

- `src/`: Python 核心脚本、Web 服务、启动器、录音回退方案
- `web/`: Next.js 15 前端源码
- `tests/`: Python 单元测试
- `docs/`: 协议和设计文档
- `examples/sample-export/`: 示例 manifest 文件

## 快速开始

```bash
git clone https://github.com/YancyWei/sodamusic-cache-export.git
cd sodamusic-cache-export
python3 -m pip install -r requirements.txt
cd web
npm install
npm run build:web
cd ..
python3 src/start_sodamusic_export.py
```

默认会启动本地服务并打开：

```text
http://127.0.0.1:8765
```

## 环境要求

必需：

- Python 3
- `pycryptodome`
- `mutagen`

离线解密必需：

- `node`

MP3 / FLAC 输出或严格校验必需：

- `ffmpeg`
- `ffprobe`

macOS 可选：

- `afconvert`
- `osascript`
- `swiftc`

## 常用命令

分析缓存但不导出：

```bash
python3 src/analyze_sodamusic_cache.py \
  --json-out /tmp/sodamusic-cache-analysis.json \
  --csv-out /tmp/sodamusic-cache-analysis.csv
```

导出所有可播放缓存：

```bash
python3 src/export_sodamusic_cache.py
```

导出 MP3：

```bash
python3 src/export_sodamusic_cache.py --format mp3
```

导出 FLAC：

```bash
python3 src/export_sodamusic_cache.py --format flac --require-output-match
```

等待目标歌曲缓存后自动导出：

```bash
python3 src/target_sodamusic_cache.py \
  --query "零几年听的情歌" \
  --artist "GG啵！" \
  --target lossless/flac \
  --wait-index
```

## 测试

Python 测试：

```bash
python3 -m pytest tests/
```

前端测试：

```bash
cd web
npm test
```

## 公开内容检查

当前仓库已做这些约束：

- `.gitignore` 忽略 `web/node_modules/`、`web/.next/`、`web/dist/`、`src/web/`、`.env*`、日志和 IDE 文件
- 示例目录仅保留 `manifest.json` / `manifest.csv`，不包含实际导出的音频文件
- 示例路径中的用户名已匿名化为 `/Users/<user>/...`

仍需注意：

- `examples/sample-export/` 含真实曲名、专辑名、`track_id`、`resource_id`、`cache_uuid`
- 这些字段不属于凭据，但如果你不想公开样本数据，建议后续再做一轮脱敏

## 文档

- 本地缓存协议说明：[docs/local-cache-protocol.md](docs/local-cache-protocol.md)
- 前端设计记录：[docs/design-taste-frontend/DESIGN.md](docs/design-taste-frontend/DESIGN.md)

## 免责声明

- 本工具仅读取 SodaMusic 本地缓存，不修改缓存目录
- 不实现 SodaMusic 服务端鉴权、签名或远程媒体下载
- `device.node` 不随仓库分发，仅在用户本机已安装 SodaMusic 时动态使用
- 请仅在合法且个人可用的范围内使用

## 许可证

[MIT](LICENSE)
