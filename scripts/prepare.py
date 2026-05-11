"""
prepare.py — Download audio, transcribe it, chunk if long.
This script does NOT call any LLM. The agent calling this skill does
the analysis using its own model.

Output: a prep.json with metadata, chunks (transcript text + timestamps),
and full_transcript_path.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

CONFIG_DIR = Path.home() / ".podcast-to-notes"
CONFIG_FILE = CONFIG_DIR / "env.json"
ENV_FILE = CONFIG_DIR / ".env"

# Long-episode threshold. Above this, chunk the transcript.
# 60K tokens ≈ 240K characters ≈ ~2 hours of speech.
LONG_THRESHOLD_TOKENS = 60_000
CHUNK_TARGET_TOKENS = 25_000   # ~30-45 min per chunk
OVERLAP_SECONDS = 90           # 1.5 min overlap to preserve cross-boundary context


def load_env_into_environ():
    """Load .env file into os.environ if not already set."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v and not os.environ.get(k.strip()):
            os.environ[k.strip()] = v


def load_config():
    if not CONFIG_FILE.exists():
        sys.exit("❌ Run scripts/precheck.py first.")
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


# ==================== Token estimation ====================

def estimate_tokens(text):
    """
    Rough token estimate. English ≈ 1 token per 4 chars.
    Be conservative — better to chunk early than blow context.
    """
    return len(text) // 3


# ==================== Stage 1: Metadata + Download ====================

def stage_metadata(url, work_dir):
    meta_file = work_dir / "metadata.json"
    if meta_file.exists():
        return json.loads(meta_file.read_text(encoding="utf-8"))

    try:
        result = subprocess.run(
            ["yt-dlp", "--skip-download", "--dump-json", "--no-playlist", url],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"   ⚠️ Metadata fetch failed: {result.stderr[-200:]}")
            return {"webpage_url": url}
        raw = json.loads(result.stdout)
        meta = {
            "title": raw.get("title", ""),
            "uploader": raw.get("uploader", ""),
            "upload_date": raw.get("upload_date", ""),
            "duration": raw.get("duration", 0),
            "webpage_url": raw.get("webpage_url", url),
            "description": (raw.get("description", "") or "")[:500],
        }
        meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        return meta
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"   ⚠️ {e}")
        return {"webpage_url": url}


def stage_download(url, work_dir):
    """Returns Path to downloaded audio file."""
    # Look for an existing audio file (excluding json/info files)
    audio_exts = {".m4a", ".mp3", ".wav", ".opus", ".webm", ".aac", ".ogg"}
    existing = [p for p in work_dir.iterdir()
                if p.is_file() and p.suffix in audio_exts and p.stem == "audio"]
    if existing and existing[0].stat().st_size > 1024:
        print(f"   ↩️ Reusing existing audio: {existing[0].name}")
        return existing[0]

    print(f"   Downloading audio...")
    result = subprocess.run(
        ["yt-dlp",
         "-x", "--audio-format", "m4a", "--audio-quality", "0",
         "--no-playlist",
         "-o", str(work_dir / "audio.%(ext)s"),
         "--print", "after_move:filepath",
         url],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr[-500:]}")

    # yt-dlp prints the final filepath when --print after_move:filepath used
    printed_path = result.stdout.strip().split("\n")[-1] if result.stdout else ""
    if printed_path and Path(printed_path).exists():
        return Path(printed_path)

    # Fallback: find by extension
    candidates = [p for p in work_dir.iterdir()
                  if p.is_file() and p.suffix in audio_exts and p.stem.startswith("audio")]
    if not candidates:
        raise FileNotFoundError("Downloaded audio file not found")
    return candidates[0]


# ==================== Stage 2: Transcribe ====================

# Supported languages — we only optimize for these two.
# Others will still transcribe (Whisper handles 99 languages), but downstream
# prompts assume {output_language} is zh or en.
SUPPORTED_LANGUAGES = {"zh", "en"}


def _normalize_language(raw):
    """Whisper backends return slightly different codes (zh vs zh-CN, etc).
    Normalize to 'zh' or 'en', or None if neither."""
    if not raw:
        return None
    s = str(raw).lower().split("-")[0].split("_")[0]
    if s in ("zh", "chi", "chinese", "cmn", "yue"):
        return "zh"
    if s in ("en", "eng", "english"):
        return "en"
    return s  # return raw normalized for diagnostics; caller decides what to do


def stage_transcribe(audio_path, work_dir, config):
    """Returns (segments, detected_language). Segments: [{start, end, text, speaker?}]."""
    segments_file = work_dir / "segments.json"
    lang_file = work_dir / "language.txt"
    if segments_file.exists():
        print(f"   ↩️ Reusing existing transcript")
        segments = json.loads(segments_file.read_text(encoding="utf-8"))
        lang = lang_file.read_text(encoding="utf-8").strip() if lang_file.exists() else "en"
        return segments, lang

    backend = config["transcription"]
    btype = backend["type"]
    provider = backend["provider"]
    print(f"   Backend: {provider} ({btype})")
    print(f"   Language: auto-detect (zh / en supported)")

    if btype == "cloud":
        segments, detected = _transcribe_cloud(audio_path, provider)
    elif provider == "mlx":
        segments, detected = _transcribe_mlx(audio_path)
    elif provider == "whisperx":
        segments, detected = _transcribe_whisperx(audio_path)
    elif provider in ("faster_whisper_cuda", "faster_whisper_cpu"):
        segments, detected = _transcribe_faster_whisper(
            audio_path, use_cuda=(provider == "faster_whisper_cuda"))
    else:
        raise RuntimeError(f"Unknown backend: {provider}")

    detected = _normalize_language(detected) or "en"
    if detected not in SUPPORTED_LANGUAGES:
        print(f"   ⚠️ Detected language '{detected}' not in supported set {SUPPORTED_LANGUAGES}; "
              f"transcript may be okay but downstream prompts assume zh or en.")

    print(f"   ✓ Detected language: {detected}")

    segments_file.write_text(json.dumps(segments, indent=2, ensure_ascii=False),
                             encoding="utf-8")
    lang_file.write_text(detected, encoding="utf-8")
    return segments, detected


def _transcribe_cloud(audio_path, provider):
    if provider == "groq":
        return _transcribe_groq(audio_path)
    elif provider == "deepgram":
        return _transcribe_deepgram(audio_path)
    elif provider == "assemblyai":
        return _transcribe_assemblyai(audio_path)
    raise RuntimeError(f"Unknown cloud provider: {provider}")


def _transcribe_groq(audio_path):
    """Groq Whisper Large v3 — fastest, ~$0.04/hr. Auto language detection."""
    try:
        from groq import Groq
    except ImportError:
        sys.exit("❌ pip install groq")
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    print("   Uploading to Groq...")
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(audio_path.name, f.read()),
            model="whisper-large-v3",
            response_format="verbose_json",
            timestamp_granularities=["segment"],
            # No language param → Whisper auto-detects.
        )
    segments = [
        {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
        for s in result.segments
    ]
    detected = getattr(result, "language", None) or "en"
    return segments, detected


def _transcribe_deepgram(audio_path):
    """Deepgram Nova-3 — supports diarization. Auto language detection."""
    try:
        from deepgram import DeepgramClient, PrerecordedOptions, FileSource
    except ImportError:
        sys.exit("❌ pip install deepgram-sdk")
    client = DeepgramClient(os.environ["DEEPGRAM_API_KEY"])
    print("   Uploading to Deepgram...")
    with open(audio_path, "rb") as f:
        payload: FileSource = {"buffer": f.read()}
    # detect_language=True returns the detected language in metadata.
    options = PrerecordedOptions(model="nova-3", detect_language=True,
                                 diarize=True, punctuate=True, smart_format=True)
    response = client.listen.rest.v("1").transcribe_file(payload, options)
    channel = response.results.channels[0]
    detected = getattr(channel, "detected_language", None) or \
               getattr(channel.alternatives[0], "language", None) or "en"
    words = channel.alternatives[0].words
    return _aggregate_words(words), detected


def _aggregate_words(words, target_duration=20):
    """Merge consecutive words into ~target_duration second segments."""
    segments = []
    current = {"start": None, "end": None, "text": [], "speaker": None}
    for w in words:
        if current["start"] is None:
            current["start"] = w.start
            current["speaker"] = getattr(w, "speaker", None)
        if w.end - current["start"] > target_duration or \
           (getattr(w, "speaker", None) != current["speaker"] and current["text"]):
            current["end"] = current["text"] and words[words.index(w) - 1].end or w.start
            segments.append({
                "start": current["start"],
                "end": current["end"],
                "text": " ".join(current["text"]),
                "speaker": f"SPEAKER_{current['speaker']}" if current["speaker"] is not None else None,
            })
            current = {"start": w.start, "end": None, "text": [],
                       "speaker": getattr(w, "speaker", None)}
        current["text"].append(w.punctuated_word if hasattr(w, "punctuated_word") else w.word)
    if current["text"]:
        segments.append({
            "start": current["start"],
            "end": words[-1].end,
            "text": " ".join(current["text"]),
            "speaker": f"SPEAKER_{current['speaker']}" if current["speaker"] is not None else None,
        })
    return segments


def _transcribe_assemblyai(audio_path):
    """AssemblyAI — supports diarization. Auto language detection."""
    try:
        import assemblyai as aai
    except ImportError:
        sys.exit("❌ pip install assemblyai")
    aai.settings.api_key = os.environ["ASSEMBLYAI_API_KEY"]
    print("   Uploading to AssemblyAI...")
    transcriber = aai.Transcriber()
    config = aai.TranscriptionConfig(speaker_labels=True, language_detection=True)
    transcript = transcriber.transcribe(str(audio_path), config)
    if transcript.error:
        raise RuntimeError(f"AssemblyAI error: {transcript.error}")
    segments = [
        {"start": u.start / 1000, "end": u.end / 1000,
         "text": u.text.strip(), "speaker": u.speaker}
        for u in (transcript.utterances or [])
    ]
    detected = getattr(transcript, "language_code", None) or "en"
    return segments, detected


def _find_local_mlx_whisper_model():
    """Look for an already-downloaded MLX Whisper model in HF cache.
    Avoids re-downloading 1.6 GB if user already has one.

    Returns: model id to pass to mlx_whisper.transcribe, preferring smaller/faster
    variants when multiple are present.
    """
    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    if not cache_root.exists():
        return None

    # Preference order: turbo first (smaller, ~4x faster), then full v3.
    preferred = [
        "mlx-community/whisper-large-v3-turbo",
        "mlx-community/whisper-large-v3-mlx",
        "mlx-community/whisper-large-v3",
        "mlx-community/whisper-turbo",
    ]
    for repo in preferred:
        # HF cache dir form: models--mlx-community--whisper-large-v3-turbo
        cache_name = "models--" + repo.replace("/", "--")
        cache_dir = cache_root / cache_name
        if not cache_dir.exists():
            continue
        # Find a snapshot dir with actual files
        snapshots = cache_dir / "snapshots"
        if not snapshots.exists():
            continue
        for snap in snapshots.iterdir():
            if snap.is_dir() and any(snap.iterdir()):
                # Found one. Use the absolute path so HF_HUB_OFFLINE=1 works.
                return str(snap.resolve())
    return None


def _transcribe_mlx(audio_path):
    """MLX Whisper. Auto-detects language. Uses any locally-cached model
    before falling back to downloading turbo."""
    import mlx_whisper
    local_model = _find_local_mlx_whisper_model()
    if local_model:
        print(f"   Using cached MLX model: {Path(local_model).parent.parent.name}")
        model_id = local_model
    else:
        print(f"   No cached MLX model found; will download mlx-community/whisper-large-v3-turbo")
        model_id = "mlx-community/whisper-large-v3-turbo"

    print("   Loading MLX Whisper (auto-detecting language)...")
    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=model_id,
        task="transcribe",
        word_timestamps=False,
        # No language param → MLX auto-detects.
    )
    segments = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                for s in result["segments"]]
    detected = result.get("language", "en")
    if os.environ.get("HUGGINGFACE_TOKEN"):
        segments = _apply_pyannote_diarization(audio_path, segments)
    return segments, detected


def _transcribe_whisperx(audio_path):
    """WhisperX with auto language detection."""
    import whisperx
    device = "cuda"
    print("   Loading WhisperX (auto-detecting language)...")
    # No language param at model load → auto-detect.
    model = whisperx.load_model("large-v3-turbo", device,
                                 compute_type="float16")
    audio = whisperx.load_audio(str(audio_path))
    result = model.transcribe(audio, batch_size=16)
    detected = result.get("language", "en")
    detected_normalized = _normalize_language(detected) or "en"
    print(f"   Detected language: {detected} → normalized to {detected_normalized}")

    # Alignment model is language-specific; only run if language is supported.
    try:
        print("   Aligning timestamps...")
        align_model, metadata = whisperx.load_align_model(
            language_code=detected_normalized, device=device)
        result = whisperx.align(result["segments"], align_model, metadata, audio, device)
    except Exception as e:
        print(f"   ⚠️ Alignment skipped ({e}); using model-level timestamps")

    if os.environ.get("HUGGINGFACE_TOKEN"):
        print("   Speaker diarization...")
        diarize = whisperx.DiarizationPipeline(
            use_auth_token=os.environ["HUGGINGFACE_TOKEN"], device=device)
        diarize_segments = diarize(audio)
        result = whisperx.assign_word_speakers(diarize_segments, result)

    segments = [
        {"start": s["start"], "end": s["end"], "text": s["text"].strip(),
         "speaker": s.get("speaker")}
        for s in result["segments"]
    ]
    return segments, detected


def _transcribe_faster_whisper(audio_path, use_cuda):
    """faster-whisper with auto language detection."""
    from faster_whisper import WhisperModel
    device = "cuda" if use_cuda else "cpu"
    compute = "float16" if use_cuda else "int8"
    print(f"   Loading faster-whisper ({device}, auto-detecting language)...")
    model = WhisperModel("large-v3-turbo", device=device, compute_type=compute)
    # No language param → auto-detect.
    segs, info = model.transcribe(str(audio_path), beam_size=5)
    detected = getattr(info, "language", "en") or "en"
    segments = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segs]
    return segments, detected


def _apply_pyannote_diarization(audio_path, segments):
    try:
        from pyannote.audio import Pipeline
        import torch
    except ImportError:
        return segments
    print("   pyannote diarization...")
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=os.environ["HUGGINGFACE_TOKEN"],
    )
    if torch.backends.mps.is_available():
        pipeline.to(torch.device("mps"))
    diarization = pipeline(str(audio_path))
    turns = [(t.start, t.end, spk)
             for t, _, spk in diarization.itertracks(yield_label=True)]
    for seg in segments:
        mid = (seg["start"] + seg["end"]) / 2
        seg["speaker"] = "UNKNOWN"
        for s_start, s_end, spk in turns:
            if s_start <= mid <= s_end:
                seg["speaker"] = spk
                break
    return segments


# ==================== Stage 3: Format & Chunk ====================

def fmt_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_segment(seg):
    ts = f"[{fmt_timestamp(seg['start'])}]"
    spk = f" {seg['speaker']}:" if seg.get("speaker") else ""
    return f"{ts}{spk} {seg['text']}"


def write_full_transcript(segments, work_dir):
    """Write full transcript as plain text. Returns Path."""
    path = work_dir / "full_transcript.txt"
    text = "\n".join(format_segment(s) for s in segments)
    path.write_text(text, encoding="utf-8")
    return path


def chunk_segments(segments):
    """
    If transcript fits under threshold, return single chunk.
    Otherwise split at natural boundaries (~30-45 min) with overlap.
    """
    full_text = "\n".join(format_segment(s) for s in segments)
    total_tokens = estimate_tokens(full_text)

    if total_tokens <= LONG_THRESHOLD_TOKENS:
        return [{
            "index": 0,
            "start_seconds": segments[0]["start"] if segments else 0,
            "end_seconds": segments[-1]["end"] if segments else 0,
            "transcript": full_text,
            "estimated_tokens": total_tokens,
            "is_overlap_extension": False,
        }]

    # Need to split. Aim for CHUNK_TARGET_TOKENS per chunk, with OVERLAP_SECONDS overlap.
    chunks = []
    chunk_idx = 0
    chunk_segs = []
    chunk_tokens = 0

    i = 0
    while i < len(segments):
        seg = segments[i]
        seg_text = format_segment(seg)
        seg_tokens = estimate_tokens(seg_text)

        # Will adding this segment overshoot? If so, finalize current chunk.
        if chunk_tokens + seg_tokens > CHUNK_TARGET_TOKENS and chunk_segs:
            # Determine overlap region: include segments from last (OVERLAP_SECONDS)
            chunk_end_time = chunk_segs[-1]["end"]
            chunk_start_time = chunk_segs[0]["start"]
            chunk_text = "\n".join(format_segment(s) for s in chunk_segs)

            # Overlap note for the LLM
            overlap_note = ""
            if chunk_idx > 0:
                overlap_note = f"\n[Note: this chunk overlaps {OVERLAP_SECONDS}s with the previous chunk for context continuity.]\n"
            chunks.append({
                "index": chunk_idx,
                "start_seconds": chunk_start_time,
                "end_seconds": chunk_end_time,
                "transcript": overlap_note + chunk_text,
                "estimated_tokens": chunk_tokens,
                "is_overlap_extension": chunk_idx > 0,
            })
            chunk_idx += 1

            # Start next chunk: roll back to OVERLAP_SECONDS before chunk_end_time
            overlap_threshold = chunk_end_time - OVERLAP_SECONDS
            # Find where to start next chunk (keep overlap segments)
            chunk_segs = [s for s in chunk_segs if s["end"] > overlap_threshold]
            chunk_tokens = estimate_tokens("\n".join(format_segment(s) for s in chunk_segs))

        chunk_segs.append(seg)
        chunk_tokens += seg_tokens
        i += 1

    # Last chunk
    if chunk_segs:
        chunk_text = "\n".join(format_segment(s) for s in chunk_segs)
        overlap_note = ""
        if chunk_idx > 0:
            overlap_note = f"\n[Note: this chunk overlaps {OVERLAP_SECONDS}s with the previous chunk for context continuity.]\n"
        chunks.append({
            "index": chunk_idx,
            "start_seconds": chunk_segs[0]["start"],
            "end_seconds": chunk_segs[-1]["end"],
            "transcript": overlap_note + chunk_text,
            "estimated_tokens": chunk_tokens,
            "is_overlap_extension": chunk_idx > 0,
        })

    return chunks


# ==================== Main ====================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Podcast/YouTube URL")
    parser.add_argument("--output-dir", default="./podcast_output")
    args = parser.parse_args()

    load_env_into_environ()
    config = load_config()
    if config["status"] != "ready":
        sys.exit(f"❌ Status not ready: {config['status']}. Run precheck.py.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Each URL gets a deterministic work dir (resumable)
    url_hash = hashlib.sha256(args.url.encode()).hexdigest()[:10]
    work_dir = output_dir / f"work_{url_hash}"
    work_dir.mkdir(exist_ok=True)
    (work_dir / "chunks").mkdir(exist_ok=True)

    print(f"\n{'=' * 64}")
    print(f"  Preparing: {args.url[:60]}")
    print(f"{'=' * 64}\n")
    t0 = time.time()

    print("📥 Fetching metadata...")
    metadata = stage_metadata(args.url, work_dir)
    print(f"   Title: {(metadata.get('title') or '?')[:60]}")
    print(f"   Duration: {metadata.get('duration', 0)}s")

    print("\n🎵 Downloading audio...")
    audio_path = stage_download(args.url, work_dir)
    size_mb = audio_path.stat().st_size / 1024 / 1024
    print(f"   ✓ {audio_path.name} ({size_mb:.1f} MB)")

    print("\n🎙️ Transcribing...")
    segments, detected_lang = stage_transcribe(audio_path, work_dir, config)
    print(f"   ✓ {len(segments)} segments, language={detected_lang}")

    print("\n📝 Writing full transcript...")
    full_path = write_full_transcript(segments, work_dir)
    full_tokens = estimate_tokens(full_path.read_text(encoding="utf-8"))
    print(f"   ✓ {full_tokens:,} estimated tokens")

    print("\n✂️ Chunking...")
    chunks = chunk_segments(segments)
    if len(chunks) == 1:
        print(f"   Single chunk (under {LONG_THRESHOLD_TOKENS:,} token threshold)")
    else:
        print(f"   Split into {len(chunks)} chunks (with {OVERLAP_SECONDS}s overlap)")
        for c in chunks:
            print(f"     · chunk {c['index']}: {fmt_timestamp(c['start_seconds'])} → "
                  f"{fmt_timestamp(c['end_seconds'])} ({c['estimated_tokens']:,} tok)")

    # Save chunks individually (so agent can read them one at a time)
    chunks_dir = work_dir / "chunks"
    for c in chunks:
        chunk_path = chunks_dir / f"chunk_{c['index']:02d}.txt"
        chunk_path.write_text(c["transcript"], encoding="utf-8")
        c["transcript_path"] = str(chunk_path.absolute())
        # Don't keep transcript text inline (it's huge); agent reads by path
        del c["transcript"]

    metadata["audio_path"] = str(audio_path.absolute())
    metadata["audio_filename"] = audio_path.name
    metadata["webpage_url"] = metadata.get("webpage_url", args.url)

    # Default output_language tracks the spoken language unless agent overrides.
    # If transcript is in Chinese, output Chinese notes; if English, English.
    output_language = "中文" if detected_lang == "zh" else "English"

    prep = {
        "url": args.url,
        "metadata": metadata,
        "transcript_language": detected_lang,
        "output_language": output_language,
        "full_transcript_path": str(full_path.absolute()),
        "full_transcript_tokens": full_tokens,
        "chunks": chunks,
        "is_long_episode": len(chunks) > 1,
        "thresholds": {
            "long_episode_tokens": LONG_THRESHOLD_TOKENS,
            "chunk_target_tokens": CHUNK_TARGET_TOKENS,
            "overlap_seconds": OVERLAP_SECONDS,
        },
        "work_dir": str(work_dir.absolute()),
    }
    prep_file = output_dir / "prep.json"
    prep_file.write_text(json.dumps(prep, indent=2, ensure_ascii=False), encoding="utf-8")

    elapsed = time.time() - t0
    print(f"\n{'=' * 64}")
    print(f"✅ Prep done in {elapsed/60:.1f} min")
    print(f"   Language: {detected_lang}  |  Tokens: {full_tokens:,}  |  Chunks: {len(chunks)}")
    print(f"📄 prep.json: {prep_file}")
    print(f"📂 Work dir: {work_dir}")

    # ============= EXPLICIT NEXT STEPS FOR THE CALLING AGENT =============
    # Spelled out because agents have skipped Step 3 in real usage.
    print(f"\n{'=' * 64}")
    print(f"⚠️  PIPELINE IS NOT FINISHED — TWO MORE STEPS REQUIRED")
    print(f"{'=' * 64}")
    print(f"")
    print(f"You (the calling agent) MUST now do the following.")
    print(f"Do NOT stop here. Do NOT hand-write HTML notes — the renderer does that.")
    print(f"")
    print(f"STEP A: LLM analysis (you do this in your own context):")
    print(f"  1. Read references/prompts.md for PASS1 and PASS2 prompt templates.")
    print(f"  2. Read the transcript: {full_path}")
    if len(chunks) > 1:
        print(f"  3. Run PASS1 on each of {len(chunks)} chunks (chunks/chunk_NN.txt).")
        print(f"  4. Merge chunk outlines into one outline.json (see chunking.md).")
        print(f"  5. Run PASS2 on the full transcript (or chunked, if too long).")
    else:
        print(f"  3. Run PASS1 on the full transcript → save as outline.json")
        print(f"  4. Run PASS2 on the full transcript → save as insights.md")
    print(f"  • Use output_language='{output_language}' (matches detected speech).")
    print(f"  • Save outputs to: {output_dir}/outline.json  and  {output_dir}/insights.md")
    print(f"")
    print(f"STEP B: Render (run this command after Step A completes):")
    print(f"")
    print(f"  python scripts/render.py \\")
    print(f"      --prep {prep_file} \\")
    print(f"      --outline {output_dir}/outline.json \\")
    print(f"      --insights {output_dir}/insights.md \\")
    print(f"      --format html_dashboard")
    print(f"")
    print(f"Only the rendered HTML/MD is the final deliverable. prep.json and the")
    print(f"transcript are intermediate; do NOT present those to the user as the result.")
    print(f"{'=' * 64}")


if __name__ == "__main__":
    main()
