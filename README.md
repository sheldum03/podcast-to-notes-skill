[中文版](README_zh.md) | English

# Podcast to Notes Skill

A Claude Code skill that converts any podcast or YouTube URL into structured, interactive notes with clickable timestamps and an embedded audio player.

## How It Works

```
URL → audio (yt-dlp) → transcript (Whisper) → LLM analysis → interactive HTML
```

The skill splits work into two tracks:

- **Scripts** handle deterministic mechanics: download, transcription, chunking, rendering
- **The calling agent** handles LLM work: outline extraction, quote selection, translation

This design makes it portable across any AI coding agent (Claude Code, Codex, Qwen Code, etc.).

## Features

- Auto-detects spoken language (Chinese / English) and generates notes in the same language
- Long episodes (>2h) are automatically chunked with overlap for continuity
- Three output formats:
  - **HTML Dashboard** — multi-pane layout with sticky audio player, TOC sidebar, full-text search, dark mode
  - **HTML Simple** — single-column, mobile-friendly
  - **Markdown** — with Mermaid mind map, for Obsidian / note-taking apps

## Quick Start

### 1. Install the skill

Copy the `SKILL.md` and supporting files into your Claude Code skill directory, or reference it directly.

### 2. First-time setup

```bash
python scripts/precheck.py
```

This detects your OS, GPU, and installed tools. Follow its instructions to install any missing dependencies (yt-dlp, Whisper, ffmpeg).

### 3. Use it

Give Claude Code a podcast URL:

> "Summarize this podcast: https://www.youtube.com/watch?v=..."

The skill handles the rest — download, transcription, analysis, and rendering.

## Pipeline Steps

| Step | Who | What |
|------|-----|------|
| 0 | `precheck.py` | Detect environment, install dependencies |
| 1 | Agent | Collect URL + optional context (focus area, guest info) |
| 2 | `prepare.py` | Download audio, transcribe, chunk if needed |
| 3 | Agent | Run two-pass LLM analysis (outline → quotes/insights) |
| 4 | `render.py` | Generate final HTML dashboard or Markdown |
| 5 | Agent | Present the rendered result |

## Project Structure

```
SKILL.md                           # Main skill definition
scripts/
  precheck.py                      # Environment detection & dependency check
  prepare.py                       # Download + transcribe + chunk
  render.py                        # Render final output (HTML/MD)
references/
  install.md                       # Platform-specific install guides
  transcription_backends.md        # Whisper local vs cloud API options
  prompts.md                       # LLM prompt templates
  chunking.md                      # Long-episode chunking strategy
  output_formats.md                # Output format visual examples
  troubleshooting.md               # Common issues & fixes
```

## Supported Transcription Backends

- **Local**: Whisper (MLX on Mac, CUDA on Windows/Linux, CPU fallback)
- **Cloud**: Groq, Deepgram, AssemblyAI

See `references/transcription_backends.md` for setup details and tradeoffs.

## Requirements

- Python 3.10+
- yt-dlp
- ffmpeg
- Whisper (local) or a cloud transcription API key

## License

MIT
