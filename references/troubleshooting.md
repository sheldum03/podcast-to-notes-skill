# Troubleshooting

## yt-dlp errors

**`ERROR: Sign in to confirm you're not a bot`** — YouTube rate-limiting. Add `--cookies-from-browser chrome` (or firefox) to `prepare.py`'s yt-dlp call, OR wait 30 min.

**`HTTP 403`** — IP flagged. Try VPN, or wait an hour.

**Chinese podcast platforms (xiaoyuzhoufm / ximalaya) — download fails or gives wrong file** — yt-dlp uses generic extractor for these, which can be flaky. Workarounds:
1. Find the direct MP3/M4A URL in the page source (look for `<audio src=...>` or in Network tab of devtools) and download it manually
2. Use `yt-dlp --list-formats <URL>` to see what was detected
3. As fallback, point `prepare.py` at a local audio file by copying it to `work_<hash>/audio.m4a` before running

## Whisper / transcription errors

**Wrong language transcribed (e.g. Chinese audio comes out as English translation)** — fixed in current version: `prepare.py` no longer sets `language="en"`; Whisper auto-detects. If you upgraded from an older copy, replace `scripts/prepare.py`.

**MLX downloads a 1.6 GB model on first run** — expected. The skill prefers `mlx-community/whisper-large-v3-turbo`. If you've already downloaded **any** of these:
- `mlx-community/whisper-large-v3-turbo`
- `mlx-community/whisper-large-v3-mlx`
- `mlx-community/whisper-large-v3`

`prepare.py`'s `_find_local_mlx_whisper_model()` auto-detects and reuses it. No need to hardcode paths.

**HuggingFace download hangs / times out (China users)** — set the mirror env var BEFORE running prepare.py:
```bash
export HF_ENDPOINT=https://hf-mirror.com
```
`precheck.py` detects when HF is unreachable and shows this hint. To pre-download via mirror:
```bash
HF_ENDPOINT=https://hf-mirror.com huggingface-cli download mlx-community/whisper-large-v3-turbo
```
Then run prepare.py with `HF_HUB_OFFLINE=1` to avoid further HF calls.

**`Could not load library cudnn_ops_infer64_8.dll` (Windows)** — install cuDNN 9 from NVIDIA Developer site, add DLLs to PATH.

**`CUDA out of memory`** — close other GPU apps, OR change `large-v3-turbo` to `medium` in the relevant `_transcribe_*` function in `prepare.py`.

## Speaker diarization errors

**`401 Unauthorized` from pyannote** — two causes:
1. `HUGGINGFACE_TOKEN` not in `.env`
2. Haven't agreed to model licenses. Visit and click "Agree":
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0

**Two speakers detected as 3+** — pyannote sometimes splits one person's pitch range. In `_apply_pyannote_diarization`, change `pipeline(audio_path)` to `pipeline(audio_path, num_speakers=2)`.

## Cloud API errors

**`401`** from Groq/Deepgram/AssemblyAI — wrong API key, or zero balance. Check provider's console.

**Cloud upload very slow** — large m4a files. Worth converting to lower bitrate first if cellular: `ffmpeg -i audio.m4a -b:a 64k audio_small.m4a`.

## LLM errors (you, the agent, deal with these)

The skill itself doesn't call LLMs — but if you (the agent) hit issues during pass 1 / pass 2:

**Pass 1 returns invalid JSON** — strip ```` ```json ```` fences. If still invalid, the transcript may have unusual artifacts (Whisper hallucination — long repeated text). Check `segments.json` for stuck loops.

**Pass 2 output truncated** — increase your max_tokens budget, or use the chunked-extraction strategy in `chunking.md`.

**Multi-chunk merge produces inconsistent mind map** — the chunks may have used different `root` topics. Add merge guidance: "use [specific topic] as the unified root".

## Rendering errors

**Mermaid mindmap doesn't render** — browser can't reach jsdelivr CDN. Either: open while online once (it caches), or replace the `<script type="module">` with a local mermaid copy.

**Timestamps not clickable** — check the audio path. The HTML uses relative paths by default; if you moved the HTML without the audio, links break. Solution: bundle audio in same folder, or edit `<source src="...">` to a public URL.

**Search box doesn't find a quote** — the search is text-based, only searches visible content. If a quote is in a collapsed/hidden card, it won't match.

## Resume after partial failure

Each stage in `prepare.py` checks for existing output:
- Audio downloaded? skip
- `segments.json` exists? skip transcription
- `prep.json` exists? you can edit it manually if needed

To force a stage to redo, delete its output:
```bash
rm podcast_output/work_<hash>/segments.json   # forces transcribe redo
```

For LLM work (you, the agent): the chunk outline JSONs are saved per-chunk. If chunk 3 failed, just redo chunk 3.

## Still stuck?

Look at intermediate files in `podcast_output/work_<hash>/`:
- `metadata.json` empty → yt-dlp failed
- `segments.json` has weird repetitions → Whisper hallucinating, try a different model size
- `chunks/chunk_*.txt` empty → something went wrong in chunking, check transcript wasn't actually empty
- `outline.json` malformed → pass 1 prompt or LLM issue
- No `insights.md` → pass 2 didn't run
