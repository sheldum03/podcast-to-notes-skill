---
name: podcast-to-notes
description: Convert any podcast or YouTube URL into a structured Markdown or info-dense HTML dashboard with outline, mind map, key quotes, and inspiration hooks — all linked to clickable timestamps in an embedded audio player. Use this skill whenever the user provides a podcast URL, YouTube link, or audio file and wants notes/summary/transcript/outline/highlights, even if they don't say "podcast" — phrases like "summarize this video", "extract key points from this audio", "make notes from this interview", or "convert this episode to text" should trigger it. The skill handles the deterministic parts (download, transcription, chunking, rendering) via scripts; the LLM analysis (outline, quotes, translation) is run by Claude/the calling agent itself, so it works with any model the agent has access to. Long episodes (>2h) are auto-chunked. Also use if the user asks about setting up local podcast transcription, configuring Whisper, or building a podcast notes workflow.
---

# Podcast → Structured Notes

A two-track skill: **scripts handle the deterministic mechanics** (download, transcribe, chunk, render); **the calling agent handles the LLM work** (analysis, quotes, translation) using whatever model it has.

## Pipeline overview

```
URL → audio (yt-dlp) → transcript (Whisper, local or cloud)
   → chunked if >2h → agent runs prompts itself → render MD/HTML
```

## Step 0: First-time setup (MANDATORY on first invocation)

Run precheck. It detects OS/GPU/installed tools and tells you what's missing:

```bash
python scripts/precheck.py
```

Read the printed status, then act:

- **`status: ready`** → proceed to Step 1.
- **`status: needs_install`** → load `references/install.md` and follow the section matching the user's `recommended_install_path` (`mac_mlx`, `windows_cuda`, `linux_cuda`, or `cpu_fallback`). Show the user the install commands. After they confirm install completed, re-run precheck.
- **`status: needs_config`** → user needs to pick a transcription backend (local vs cloud) and provide credentials if cloud. See `references/transcription_backends.md`.

The script writes `~/.podcast-to-notes/env.json`; subsequent calls skip detection.

## Step 1: Get URL + minimal context

Ask for the URL if not provided. Optionally collect (these go into the LLM prompts later, improving output quality but not required):

- **Focus area** — what the user actually cares about (e.g. "model architecture", "AI go-to-market"). Shapes what the LLM emphasizes.
- **Guest name + 1-line background** — improves quote attribution.
- **Output language** — by default this **matches the spoken language** (Whisper auto-detects in step 2: Chinese audio → Chinese notes; English audio → English notes). Only ask the user if they explicitly say they want a different output language than what's being spoken (e.g. English notes from a Chinese podcast). The chosen language is stored in `prep.json["output_language"]` and used in step 3 as `{output_language}`.

If user just hands a URL, that's fine. Auto-extract title/uploader/date via yt-dlp later.

## Step 2: Mechanical pipeline (scripts only)

Run the prep script. It downloads audio, transcribes (auto-detecting language: zh / en supported), and chunks the transcript if needed:

```bash
python scripts/prepare.py <URL> --output-dir ./podcast_output
```

When the script finishes, it prints `prep.json` location, the detected language, and **explicit next-step commands**. **Read those instructions** — they tell you exactly what to do in steps 3 and 4.

Output: a `prep.json` file containing:
- `metadata`: title, uploader, date, duration, audio_path
- `transcript_language`: `zh` or `en` (auto-detected from speech)
- `output_language`: `中文` or `English` (default matches `transcript_language`; user can override in Step 1)
- `chunks`: list of transcript chunks. Short episode (<2h) = single chunk. Long episode = multiple chunks with overlap markers.
- `full_transcript_path`: path to the full timestamped transcript text file

## Step 3: LLM analysis (you, the agent, do this — DO NOT SKIP)

> ⚠️ **This step is mandatory.** The pipeline is not done after `prepare.py`. If you stop here, or if you hand-write HTML notes instead of producing the structured `outline.json` and `insights.md`, the renderer cannot work and the user gets a worse result than the skill is designed for. **Do not improvise. Follow the prompts in `references/prompts.md` exactly and save the two output files at the exact paths specified.**

This is the part where **you** (the agent calling this skill) do the LLM work. You don't call an external API — you read the prompts and the transcript, then produce the analysis using your own model context.

### 3a. Load the prompts

Read `references/prompts.md` to get the exact prompt templates and rationale.

### 3b. Pass 1 — outline + mind map

For each chunk in `prep.json["chunks"]`:
1. Read the chunk's transcript text (path is `chunk["transcript_path"]`)
2. Apply the **PASS1 prompt** with the metadata and `output_language` from `prep.json`
3. Output JSON matching the schema in the prompt

**Single-chunk episodes**: one pass1 call → save merged result directly to `./podcast_output/outline.json`.
**Multi-chunk episodes**: one pass1 call per chunk → save each to `./podcast_output/work_<hash>/chunks/chunk_<NN>_outline.json` → merge into `./podcast_output/outline.json` (merge logic in `references/chunking.md`).

### 3c. Pass 2 — quotes + inspiration + translation

After pass 1 is done:
1. Read the full transcript (`full_transcript_path` from prep.json)
2. If full transcript fits your context window, use it directly with the **PASS2 prompt**
3. If not, see `references/chunking.md` for the multi-chunk pass2 strategy (chunked extraction + global merge)
4. Save output to `./podcast_output/insights.md`

### 3d. Verify both files exist

Before moving on, confirm:
- `./podcast_output/outline.json` exists and parses as valid JSON
- `./podcast_output/insights.md` exists and contains the three required section headers

If either is missing, redo that pass. **Do not skip to Step 4 with incomplete inputs** — the renderer assumes the schema is intact.

## Step 4: Render the output (MANDATORY)

> ⚠️ **Do this even if you think the markdown looks fine.** The renderer turns the analysis into the actual deliverable the user wants (interactive HTML with embedded audio + clickable timestamps). Skipping render means the user has to read raw markdown with no audio links — defeating the whole point.

Once `outline.json` and `insights.md` are written, run:

```bash
python scripts/render.py \
    --prep ./podcast_output/prep.json \
    --outline ./podcast_output/outline.json \
    --insights ./podcast_output/insights.md \
    --format html_dashboard
```

Output formats (see `references/output_formats.md` for visual examples):
- **`html_dashboard`** ⭐ — Information-dense multi-pane layout: sticky audio player, table-of-contents sidebar, two-column main area (outline + quotes side by side), full-text search, dark mode. **Default; pick this unless user says otherwise.**
- **`html_simple`** — Single-column HTML, embedded audio, clickable timestamps. Mobile-only.
- **`md`** — Markdown with Mermaid mind map. For Obsidian / note-taking apps.

## Step 5: Present the result

Use `present_files` (if available) on the rendered HTML. Lead with the rendered file as the final deliverable. Intermediate files (transcript, prep.json, outline.json, insights.md) can be mentioned but should not be the headline.

## Critical implementation notes

**Why scripts don't call LLMs**: this skill is meant to be portable across Claude Code, Codex, Qwen Code, etc. Each of those uses a different model, often without an OpenAI-compatible API. By moving LLM work into the agent's own context, the skill works with any model the agent has — and you (the agent) decide whether to use thinking mode, structured output, etc., based on your own model's capabilities.

**Why the chunking logic lives in scripts, not prompts**: deterministic. The same audio always chunks the same way regardless of which LLM is calling.

**Why `references/` is split**: progressive disclosure. Don't preload all references — read only the ones relevant to the current step. `prompts.md` for steps 3b/3c, `chunking.md` for long-episode handling, `output_formats.md` for picking output style, etc.

## References (read on demand, not upfront)

- `references/install.md` — platform install commands
- `references/transcription_backends.md` — local Whisper vs cloud APIs (Groq/Deepgram/AssemblyAI), tradeoffs, setup
- `references/prompts.md` — the two LLM prompts with rationale
- `references/chunking.md` — long-episode chunking strategy and merge logic
- `references/output_formats.md` — visual examples of the three output formats
- `references/troubleshooting.md` — common failure modes
