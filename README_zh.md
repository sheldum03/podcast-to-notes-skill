中文 | [English](README.md)

# Podcast to Notes Skill

一个 Claude Code 技能，可将任意播客或 YouTube 链接转化为结构化的交互式笔记，支持时间戳点击跳转和内嵌音频播放器。

## 工作原理

```
URL → 音频 (yt-dlp) → 转录 (Whisper) → LLM 分析 → 交互式 HTML
```

技能将工作分为两条轨道：

- **脚本**负责确定性流程：下载、转录、分块、渲染
- **调用方 Agent** 负责 LLM 工作：大纲提取、金句筛选、翻译

这种设计使其可在任意 AI 编程助手（Claude Code、Codex、Qwen Code 等）上运行。

## 核心特性

- 自动识别音频语言（中文/英文），并以相同语言生成笔记
- 长节目（>2小时）自动分块，保留上下文重叠
- 三种输出格式：
  - **HTML 仪表盘** — 多面板布局，固定音频播放器、目录侧边栏、全文搜索、暗色模式
  - **HTML 简洁版** — 单栏布局，适合移动端
  - **Markdown** — 含 Mermaid 思维导图，适配 Obsidian 等笔记工具

## 快速开始

### 1. 安装技能

将 `SKILL.md` 及配套文件复制到你的 Claude Code 技能目录，或直接引用。

### 2. 首次环境检测

```bash
python scripts/precheck.py
```

脚本会检测操作系统、GPU 和已安装工具，并提示缺少的依赖（yt-dlp、Whisper、ffmpeg）。

### 3. 使用

给 Claude Code 一个播客链接即可：

> "帮我总结这个播客：https://www.youtube.com/watch?v=..."

技能会自动完成下载、转录、分析和渲染。

## 流水线步骤

| 步骤 | 执行者 | 内容 |
|------|--------|------|
| 0 | `precheck.py` | 检测环境，安装依赖 |
| 1 | Agent | 收集 URL + 可选上下文（关注领域、嘉宾信息） |
| 2 | `prepare.py` | 下载音频、转录、必要时分块 |
| 3 | Agent | 两轮 LLM 分析（大纲 → 金句/洞察） |
| 4 | `render.py` | 生成最终 HTML 仪表盘或 Markdown |
| 5 | Agent | 呈现渲染结果 |

## 项目结构

```
SKILL.md                           # 技能主定义文件
scripts/
  precheck.py                      # 环境检测与依赖检查
  prepare.py                       # 下载 + 转录 + 分块
  render.py                        # 渲染最终输出（HTML/MD）
references/
  install.md                       # 各平台安装指南
  transcription_backends.md        # Whisper 本地 vs 云端 API 选项
  prompts.md                       # LLM 提示词模板
  chunking.md                      # 长节目分块策略
  output_formats.md                # 输出格式可视化示例
  troubleshooting.md               # 常见问题与解决方案
```

## 支持的转录后端

- **本地**：Whisper（Mac 上用 MLX，Windows/Linux 上用 CUDA，CPU 兜底）
- **云端**：Groq、Deepgram、AssemblyAI

详见 `references/transcription_backends.md`。

## 环境要求

- Python 3.10+
- yt-dlp
- ffmpeg
- Whisper（本地）或云端转录 API 密钥

## 许可证

MIT
