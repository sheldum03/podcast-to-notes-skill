"""
prepare.py — Download audio, transcribe it, chunk if long.
This script does NOT call any LLM. The agent calling this skill does
the analysis using its own model.

Output: a prep.json with metadata, chunks (transcript text + timestamps),
and full_transcript_path.
"""

import argparse
import difflib
import functools
import glob as glob_mod
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

def _retry(max_attempts=3, base_delay=2, backoff=2):
    """Retry with exponential backoff for transient API failures."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except (ConnectionError, TimeoutError, OSError) as e:
                    if attempt == max_attempts:
                        raise
                    delay = base_delay * (backoff ** (attempt - 1))
                    print(f"   ⚠️ Attempt {attempt}/{max_attempts} failed: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
        return wrapper
    return decorator


@contextmanager
def _file_lock(lock_path, timeout=300):
    """Acquire an exclusive file lock. Prevents concurrent writes.
    Uses fcntl on Unix, msvcrt on Windows."""
    lock_file = open(lock_path, 'w')
    try:
        try:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except ImportError:
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        lock_file.close()


CONFIG_DIR = Path.home() / ".podcast-to-notes"
CONFIG_FILE = CONFIG_DIR / "env.json"
ENV_FILE = CONFIG_DIR / ".env"

# Long-episode threshold. Above this, chunk the transcript.
# 60K tokens ≈ 240K characters ≈ ~2 hours of speech.
LONG_THRESHOLD_TOKENS = 60_000
CHUNK_TARGET_TOKENS = 25_000   # ~30-45 min per chunk
OVERLAP_SECONDS = 90           # 1.5 min overlap to preserve cross-boundary context

# PASS2 strategy thresholds (by full transcript token count)
PASS2_FULL_THRESHOLD = 200_000      # Below this: Strategy A (full transcript)
PASS2_CHUNKED_THRESHOLD = 500_000   # Below this: Strategy B (chunked + selection)
# Above 500K: Strategy C (reduce chunk size and re-chunk)


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

def _cjk_ratio(text):
    """Return the fraction of characters that are CJK ideographs."""
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff'
              or '\u3400' <= ch <= '\u4dbf'
              or '\uf900' <= ch <= '\ufaff'
              or '\U00020000' <= ch <= '\U0002a6df'
              or '\u3040' <= ch <= '\u30ff'    # Hiragana + Katakana
              or '\uac00' <= ch <= '\ud7af')   # Korean syllables
    return cjk / len(text)


def estimate_tokens(text):
    """
    Rough token estimate, language-aware.
    - CJK text: ~1 char per token (use len/1.2 to account for punctuation/mixed).
    - Latin/English: ~1 token per 4 chars.
    - Mixed: blend proportionally.
    Be conservative — better to chunk early than blow context.
    """
    if not text:
        return 0
    ratio = _cjk_ratio(text)
    cjk_chars = int(len(text) * ratio)
    latin_chars = len(text) - cjk_chars
    return int(cjk_chars / 1.2 + latin_chars / 4)


# ==================== Audio validation ====================

def validate_audio_file(audio_path):
    """Verify audio file is playable via ffprobe. Returns True if valid.
    Exits with error if file is missing or corrupt. Gracefully degrades if ffprobe is unavailable."""
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        print(f"ERROR: Audio file not found: {audio_path}", file=sys.stderr)
        sys.exit(1)
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(audio_path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"ERROR: Audio file is not playable: {audio_path}", file=sys.stderr)
            print(f"  ffprobe: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
        return True
    except FileNotFoundError:
        print("   ⚠️ ffprobe not found — skipping audio format validation", file=sys.stderr)
        return True
    except subprocess.TimeoutExpired:
        print("   ⚠️ ffprobe timed out — skipping audio format validation", file=sys.stderr)
        return True


# ==================== Stage 1: Metadata + Download ====================

def stage_metadata(url, work_dir):
    meta_file = work_dir / "metadata.json"
    with _file_lock(work_dir / ".metadata.lock"):
        if meta_file.exists():
            cached = json.loads(meta_file.read_text(encoding="utf-8"))
            if cached.get("webpage_url") == url:
                return cached
            print("   ⚠️ URL changed, refreshing metadata...")

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
    # Let stdout pass through so yt-dlp's native progress bar is visible.
    # Only capture stderr for error detection.
    result = subprocess.run(
        ["yt-dlp",
         "-x", "--audio-format", "m4a", "--audio-quality", "0",
         "--no-playlist",
         "-o", str(work_dir / "audio.%(ext)s"),
         url],
        stderr=subprocess.PIPE, text=True, timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr[-500:]}")

    # Find downloaded file by extension
    candidates = [p for p in work_dir.iterdir()
                  if p.is_file() and p.suffix in audio_exts and p.stem.startswith("audio")]
    if not candidates:
        raise FileNotFoundError("Downloaded audio file not found")
    return candidates[0]


def stage_local_audio(audio_file, work_dir):
    """Copy a local audio file into work_dir and generate basic metadata."""
    src = Path(audio_file)
    if not src.is_file():
        sys.exit(f"❌ Audio file not found: {src}")

    dest = work_dir / f"audio{src.suffix}"
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dest)

    # Build metadata with sensible defaults
    duration = 0
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(src)],
            capture_output=True, text=True, timeout=30,
        )
        if probe.returncode == 0:
            fmt = json.loads(probe.stdout).get("format", {})
            duration = int(float(fmt.get("duration", 0)))
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    meta = {
        "title": src.stem,
        "uploader": "local",
        "upload_date": date.today().strftime("%Y%m%d"),
        "duration": duration,
        "webpage_url": str(src.absolute()),
        "description": "",
    }
    meta_file = work_dir / "metadata.json"
    with _file_lock(work_dir / ".metadata.lock"):
        if meta_file.exists():
            cached = json.loads(meta_file.read_text(encoding="utf-8"))
            if cached.get("title") == src.stem and cached.get("duration", 0) > 0:
                return dest, cached
            print("   ⚠️ Audio source changed, refreshing metadata...")
        meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                             encoding="utf-8")

    return dest, meta


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


def stage_transcribe(audio_path, work_dir, config, model_size="large-v3-turbo"):
    """Returns (segments, detected_language). Segments: [{start, end, text, speaker?}]."""
    segments_file = work_dir / "segments.json"
    lang_file = work_dir / "language.txt"
    with _file_lock(work_dir / ".segments.lock"):
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
            segments, detected = _transcribe_mlx(audio_path, model_size=model_size)
        elif provider == "whisperx":
            segments, detected = _transcribe_whisperx(audio_path, model_size=model_size)
        elif provider in ("faster_whisper_cuda", "faster_whisper_cpu"):
            segments, detected = _transcribe_faster_whisper(
                audio_path, use_cuda=(provider == "faster_whisper_cuda"),
                model_size=model_size)
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


@_retry()
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


@_retry()
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


def _aggregate_words(words, target_duration=10, min_duration=3):
    """Merge consecutive words into segments using sentence boundaries and time limits."""
    segments = []
    current = {"start": None, "end": None, "text": [], "speaker": None}
    for i, w in enumerate(words):
        if current["start"] is None:
            current["start"] = w.start
            current["speaker"] = getattr(w, "speaker", None)

        word_text = w.punctuated_word if hasattr(w, "punctuated_word") else w.word
        current["text"].append(word_text)
        elapsed = w.end - current["start"]

        # Speaker change boundary
        speaker_changed = (getattr(w, "speaker", None) != current["speaker"]
                           and len(current["text"]) > 1)
        # Sentence boundary (only if minimum duration met)
        sentence_end = (elapsed >= min_duration and word_text
                        and word_text[-1] in '.!?。！？')
        # Time limit fallback
        time_limit = elapsed > target_duration

        if speaker_changed or sentence_end or time_limit:
            if speaker_changed:
                # Put current word in next segment
                current["text"].pop()
                current["end"] = words[i - 1].end if i > 0 else w.start
            else:
                current["end"] = w.end

            if current["text"]:
                segments.append({
                    "start": current["start"],
                    "end": current["end"],
                    "text": " ".join(current["text"]),
                    "speaker": f"SPEAKER_{current['speaker']}" if current["speaker"] is not None else None,
                })
            current = {"start": w.start if speaker_changed else None,
                       "end": None, "text": [], "speaker": getattr(w, "speaker", None)}
            if speaker_changed:
                current["text"].append(word_text)

    # Final segment
    if current["text"]:
        segments.append({
            "start": current["start"],
            "end": words[-1].end,
            "text": " ".join(current["text"]),
            "speaker": f"SPEAKER_{current['speaker']}" if current["speaker"] is not None else None,
        })
    return segments


@_retry()
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


def _transcribe_mlx(audio_path, model_size="large-v3-turbo"):
    """MLX Whisper. Auto-detects language. Uses any locally-cached model
    before falling back to downloading the specified model size."""
    import mlx_whisper
    local_model = _find_local_mlx_whisper_model()
    if local_model:
        print(f"   Using cached MLX model: {Path(local_model).parent.parent.name}")
        model_id = local_model
    else:
        model_id = f"mlx-community/whisper-{model_size}"
        print(f"   No cached MLX model found; will download {model_id}")

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


def _transcribe_whisperx(audio_path, model_size="large-v3-turbo"):
    """WhisperX with auto language detection."""
    import whisperx
    device = "cuda"
    print(f"   Loading WhisperX model '{model_size}' (auto-detecting language)...")
    # No language param at model load → auto-detect.
    model = whisperx.load_model(model_size, device,
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


def _transcribe_faster_whisper(audio_path, use_cuda, model_size="large-v3-turbo"):
    """faster-whisper with auto language detection."""
    from faster_whisper import WhisperModel
    device = "cuda" if use_cuda else "cpu"
    compute = "float16" if use_cuda else "int8"
    print(f"   Loading faster-whisper '{model_size}' ({device}, auto-detecting language)...")
    model = WhisperModel(model_size, device=device, compute_type=compute)
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


def chunk_segments(segments, chunk_target_tokens=None):
    """
    If transcript fits under threshold, return single chunk.
    Otherwise split at natural boundaries (~30-45 min) with overlap.
    """
    if chunk_target_tokens is None:
        chunk_target_tokens = CHUNK_TARGET_TOKENS
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

    # Need to split. Aim for chunk_target_tokens per chunk, with OVERLAP_SECONDS overlap.
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
        if chunk_tokens + seg_tokens > chunk_target_tokens and chunk_segs:
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


def compute_section_range(duration_seconds, token_count):
    """Return a section range string (e.g. '5-12') based on episode duration.
    Falls back to token_count heuristic when duration is 0 or unavailable."""
    if duration_seconds and duration_seconds > 0:
        if duration_seconds < 300:        # < 5 min
            return "2-4"
        elif duration_seconds < 1800:     # 5-30 min
            return "3-7"
        elif duration_seconds < 5400:     # 30-90 min
            return "5-12"
        else:                             # > 90 min
            return "8-18"
    # Fallback: estimate from token count (~500 tokens/min of speech)
    est_minutes = token_count / 500 if token_count else 30
    if est_minutes < 5:
        return "2-4"
    elif est_minutes < 30:
        return "3-7"
    elif est_minutes < 90:
        return "5-12"
    else:
        return "8-18"


# ==================== Chunk outline merging ====================

def _title_word_overlap(a, b):
    """Return fraction of overlapping words between two section titles."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    return len(intersection) / min(len(words_a), len(words_b))


def merge_chunk_outlines(chunk_outlines):
    """Merge a list of per-chunk PASS1 outline dicts into one combined outline.

    Pure function -- no I/O. Each element must conform to pass1_schema.json.

    Args:
        chunk_outlines: list[dict] -- per-chunk outlines in order.

    Returns:
        dict conforming to pass1_schema.json.
    """
    if not chunk_outlines:
        raise ValueError("chunk_outlines must be a non-empty list")
    if len(chunk_outlines) == 1:
        return chunk_outlines[0]

    # --- tldr: concatenate with separator ---
    tldr = " | ".join(c["tldr"] for c in chunk_outlines if c.get("tldr"))

    # --- outline: concatenate sections, deduplicate consecutive similar titles ---
    all_sections = []
    for c in chunk_outlines:
        for sec in c.get("outline", []):
            all_sections.append(sec)

    merged_sections = []
    for sec in all_sections:
        if merged_sections and _title_word_overlap(
                merged_sections[-1]["section"], sec["section"]) > 0.7:
            # Keep the one with more key_points
            prev = merged_sections[-1]
            if len(sec.get("key_points", [])) > len(prev.get("key_points", [])):
                merged_sections[-1] = sec
        else:
            merged_sections.append(sec)

    # --- mindmap: first chunk's root, merge branches by label, cap at 8 ---
    first_mindmap = chunk_outlines[0].get("mindmap", {})
    if isinstance(first_mindmap, str):
        # Raw Mermaid string -- use first chunk's as-is
        merged_mindmap = first_mindmap
    else:
        root = first_mindmap.get("root", "Podcast")
        branch_map = {}  # label -> list of children
        for c in chunk_outlines:
            mm = c.get("mindmap", {})
            if isinstance(mm, str):
                continue
            for branch in mm.get("branches", []):
                label = branch["label"]
                if label not in branch_map:
                    branch_map[label] = []
                existing_labels = {ch["label"] for ch in branch_map[label]}
                for child in branch.get("children", []):
                    if child["label"] not in existing_labels:
                        branch_map[label].append(child)
                        existing_labels.add(child["label"])

        branches = [{"label": lbl, "children": children}
                     for lbl, children in branch_map.items()]
        branches = branches[:8]  # cap at 8 branches
        merged_mindmap = {"root": root, "branches": branches}

    # --- key_terms: union by term, keep longest explanation ---
    terms_map = {}  # term -> dict
    for c in chunk_outlines:
        for kt in c.get("key_terms", []):
            term = kt["term"]
            if term not in terms_map:
                terms_map[term] = dict(kt)
            else:
                existing = terms_map[term]
                if len(kt.get("explanation", "")) > len(existing.get("explanation", "")):
                    terms_map[term]["explanation"] = kt["explanation"]
                if len(kt.get("translation", "")) > len(existing.get("translation", "")):
                    terms_map[term]["translation"] = kt["translation"]

    return {
        "tldr": tldr,
        "outline": merged_sections,
        "mindmap": merged_mindmap,
        "key_terms": list(terms_map.values()),
    }


# ==================== Main ====================

def _run_merge_chunks(args):
    """CLI handler for --merge-chunks: load chunk outline files, merge, validate, save."""
    # Import validate_outline from render.py (sibling script)
    scripts_dir = Path(__file__).parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from render import validate_outline

    # Expand globs and collect file paths
    paths = []
    for pattern in args.merge_chunks:
        expanded = sorted(glob_mod.glob(pattern))
        if not expanded:
            sys.exit(f"ERROR: No files matched pattern: {pattern}")
        paths.extend(expanded)

    if len(paths) < 2:
        sys.exit(f"ERROR: Need at least 2 chunk outline files to merge, got {len(paths)}")

    print(f"Merging {len(paths)} chunk outlines...")
    chunk_outlines = []
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        chunk_outlines.append(data)
        print(f"  Loaded: {p}")

    merged = merge_chunk_outlines(chunk_outlines)

    # Validate against pass1_schema.json
    schema_path = Path(__file__).parent.parent / "references" / "pass1_schema.json"
    validate_outline(merged, str(schema_path))
    print("  Schema validation passed.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "outline.json"
    out_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved merged outline: {out_path}")


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("url", nargs="?", default=None, help="Podcast/YouTube URL")
    source.add_argument("--audio-file", help="Path to a local audio file (skips download)")
    source.add_argument("--merge-chunks", nargs="+", metavar="FILE",
                        help="Merge per-chunk outline JSON files into one outline.json")
    parser.add_argument("--output-dir", default="./podcast_output")
    parser.add_argument("--model-size", default="large-v3-turbo",
                        help="Whisper model size for local backends (default: large-v3-turbo)")
    parser.add_argument("--chunk-target", type=int, default=CHUNK_TARGET_TOKENS,
                        help=f"Target tokens per chunk (default: {CHUNK_TARGET_TOKENS})")
    args = parser.parse_args()

    # Handle --merge-chunks mode separately (no transcription needed)
    if args.merge_chunks:
        _run_merge_chunks(args)
        return

    if not args.url and not args.audio_file:
        parser.error("Either a URL or --audio-file is required.")

    load_env_into_environ()
    config = load_config()
    if config["status"] != "ready":
        sys.exit(f"❌ Status not ready: {config['status']}. Run precheck.py.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Each input gets a deterministic work dir (resumable)
    source_key = args.url or str(Path(args.audio_file).absolute())
    url_hash = hashlib.sha256(source_key.encode()).hexdigest()[:10]
    work_dir = output_dir / f"work_{url_hash}"
    work_dir.mkdir(exist_ok=True)
    (work_dir / "chunks").mkdir(exist_ok=True)

    display_name = args.url[:60] if args.url else Path(args.audio_file).name
    print(f"\n{'=' * 64}")
    print(f"  Preparing: {display_name}")
    print(f"{'=' * 64}\n")
    t0 = time.time()

    if args.audio_file:
        print("📂 Using local audio file...")
        audio_path, metadata = stage_local_audio(args.audio_file, work_dir)
        size_mb = audio_path.stat().st_size / 1024 / 1024
        print(f"   Title: {(metadata.get('title') or '?')[:60]}")
        print(f"   Duration: {metadata.get('duration', 0)}s")
        print(f"   ✓ {audio_path.name} ({size_mb:.1f} MB)")
    else:
        print("📥 Fetching metadata...")
        metadata = stage_metadata(args.url, work_dir)
        print(f"   Title: {(metadata.get('title') or '?')[:60]}")
        print(f"   Duration: {metadata.get('duration', 0)}s")

        print("\n🎵 Downloading audio...")
        audio_path = stage_download(args.url, work_dir)
        size_mb = audio_path.stat().st_size / 1024 / 1024
        print(f"   ✓ {audio_path.name} ({size_mb:.1f} MB)")

    print("\n🔍 Validating audio format...")
    validate_audio_file(audio_path)

    print("\n🎙️ Transcribing...")
    segments, detected_lang = stage_transcribe(audio_path, work_dir, config,
                                                model_size=args.model_size)
    print(f"   ✓ {len(segments)} segments, language={detected_lang}")

    print("\n📝 Writing full transcript...")
    full_path = write_full_transcript(segments, work_dir)
    full_tokens = estimate_tokens(full_path.read_text(encoding="utf-8"))
    print(f"   ✓ {full_tokens:,} estimated tokens")

    # Determine PASS2 strategy based on transcript length
    if full_tokens <= PASS2_FULL_THRESHOLD:
        pass2_strategy = "full"
        pass2_tokens_per_chunk = None
    elif full_tokens <= PASS2_CHUNKED_THRESHOLD:
        pass2_strategy = "chunked"
        pass2_tokens_per_chunk = min(full_tokens // 4, 50_000)
    else:
        pass2_strategy = "reduce_and_rechunk"
        pass2_tokens_per_chunk = min(full_tokens // 8, 50_000)

    if pass2_strategy != "full":
        print(f"\n⚠️  WARNING: Transcript is {full_tokens:,} tokens. "
              f"PASS2 strategy: {pass2_strategy}.", file=sys.stderr)
        if pass2_strategy == "reduce_and_rechunk":
            print(f"   Consider rerunning with --chunk-target 15000 for better results.",
                  file=sys.stderr)

    print("\n✂️ Chunking...")
    chunks = chunk_segments(segments, chunk_target_tokens=args.chunk_target)
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
    metadata["webpage_url"] = metadata.get("webpage_url", args.url or str(Path(args.audio_file).absolute()))

    # Default output_language tracks the spoken language unless agent overrides.
    # If transcript is in Chinese, output Chinese notes; if English, English.
    output_language = "中文" if detected_lang == "zh" else "English"

    duration = metadata.get("duration", 0)
    section_range = compute_section_range(duration, full_tokens)

    prep = {
        "url": args.url or str(Path(args.audio_file).absolute()),
        "metadata": metadata,
        "transcript_language": detected_lang,
        "output_language": output_language,
        "section_range": section_range,
        "full_transcript_path": str(full_path.absolute()),
        "full_transcript_tokens": full_tokens,
        "chunks": chunks,
        "is_long_episode": len(chunks) > 1,
        "pass2_strategy": pass2_strategy,
        "pass2_tokens_per_chunk": pass2_tokens_per_chunk,
        "thresholds": {
            "long_episode_tokens": LONG_THRESHOLD_TOKENS,
            "chunk_target_tokens": args.chunk_target,
            "overlap_seconds": OVERLAP_SECONDS,
        },
        "work_dir": str(work_dir.absolute()),
    }

    # For multi-chunk episodes, tell the calling agent how to auto-merge
    if len(chunks) > 1:
        prep["pass2_merge_hint"] = {
            "description": "After running PASS1 on each chunk, merge outlines automatically.",
            "command": (
                f"python scripts/prepare.py --merge-chunks"
                f" {output_dir}/chunk_outline_*.json"
                f" --output-dir {output_dir}"
            ),
            "note": "The merged tldr is a concatenation; rewrite it for coherence.",
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
        print(f"     Save each as {output_dir}/chunk_outline_00.json, chunk_outline_01.json, etc.")
        print(f"  4. Merge chunk outlines automatically:")
        print(f"     python scripts/prepare.py --merge-chunks {output_dir}/chunk_outline_*.json --output-dir {output_dir}")
        print(f"     Then rewrite the merged tldr for coherence.")
        print(f"  5. Run PASS2 on the full transcript (or chunked, if too long).")
    else:
        print(f"  3. Run PASS1 on the full transcript → save as outline.json")
        print(f"  4. Run PASS2 on the full transcript → save as insights.md")
    if pass2_strategy == "chunked":
        print(f"  ⚠️  PASS2 strategy='{pass2_strategy}': split transcript into ~{pass2_tokens_per_chunk:,}-token chunks for PASS2,")
        print(f"     then select/merge the best insights. Do NOT feed the full transcript at once.")
    elif pass2_strategy == "reduce_and_rechunk":
        print(f"  ⚠️  PASS2 strategy='{pass2_strategy}': transcript is very long ({full_tokens:,} tokens).")
        print(f"     Re-chunk with smaller windows (~{pass2_tokens_per_chunk:,} tokens) for PASS2.")
        print(f"     Consider rerunning: prepare.py ... --chunk-target 15000")
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
