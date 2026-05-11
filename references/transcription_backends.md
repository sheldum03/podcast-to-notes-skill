# Transcription Backends

You need a way to convert audio → text. Two paths:

## Local Whisper (private, slower, no per-use cost)

Best for: privacy-sensitive content, frequent users with capable hardware.

See `install.md` for installation. The skill auto-picks the fastest local backend it finds:

| Hardware | Backend | 1h audio time |
|---|---|---|
| Apple Silicon | MLX Whisper | 4-8 min |
| NVIDIA RTX 30+ | WhisperX (CUDA) | 1-3 min |
| Older NVIDIA | faster-whisper (CUDA) | 5-15 min |
| CPU only | faster-whisper (CPU) | 30-60 min |

## Cloud API (fast, cheap-ish, sends audio to provider)

Best for: speed, low CPU/GPU machines, occasional use.

The skill supports three providers. Pick **one** and add the API key to `~/.podcast-to-notes/.env`:

### Groq (recommended for speed)
- **Cost**: ~$0.04 per hour of audio
- **Speed**: 1-hour audio → ~30 seconds wall-clock
- **Quality**: Whisper Large v3, identical to local
- **Diarization**: ❌ no
- **Setup**: Get key at https://console.groq.com → save as `GROQ_API_KEY=gsk_xxx`

```bash
pip install groq
echo "GROQ_API_KEY=gsk_your_key" >> ~/.podcast-to-notes/.env
```

### Deepgram Nova-3 (recommended for quality)
- **Cost**: ~$0.26 per hour (pay-as-you-go) or cheaper plans
- **Speed**: 1-hour audio → ~20 seconds
- **Quality**: Often higher accuracy than Whisper, better punctuation
- **Diarization**: ✅ built-in
- **Setup**: https://console.deepgram.com → `DEEPGRAM_API_KEY=...`

```bash
pip install deepgram-sdk
echo "DEEPGRAM_API_KEY=your_key" >> ~/.podcast-to-notes/.env
```

### AssemblyAI (recommended for podcasts specifically)
- **Cost**: ~$0.37/hour
- **Speed**: 1-hour audio → ~1-2 minutes
- **Quality**: Tuned for conversational audio (interviews, podcasts)
- **Diarization**: ✅ built-in
- **Setup**: https://www.assemblyai.com → `ASSEMBLYAI_API_KEY=...`

```bash
pip install assemblyai
echo "ASSEMBLYAI_API_KEY=your_key" >> ~/.podcast-to-notes/.env
```

## Decision matrix

| Your situation | Recommendation |
|---|---|
| Apple Silicon Mac, occasional use | MLX local — already fast enough |
| Windows + RTX, daily user | WhisperX local — fastest, free per use |
| Older laptop, no GPU | Groq cloud — better than CPU torture |
| Privacy / NDA content | Always local |
| Hours of backlog to process | Groq cloud — 5-min wallclock vs hours |
| Want best speaker labels | Deepgram or AssemblyAI |

## How precheck picks

If multiple options are available, precheck prefers in this order:
1. Cloud API (if any key is set) — fastest
2. MLX (Apple Silicon)
3. WhisperX (CUDA)
4. faster-whisper (CUDA)
5. faster-whisper (CPU)

To force a specific backend, set the corresponding env var (or unset cloud keys to fall back to local).

## Speaker diarization (who said what)

Local backends WhisperX and MLX (with pyannote) can label speakers, but require:
- A free HuggingFace account
- Click "Agree" at https://huggingface.co/pyannote/speaker-diarization-3.1 and ../segmentation-3.0
- Set `HUGGINGFACE_TOKEN=hf_xxx` in `.env`

Without this, transcripts will not have speaker labels. The pipeline still works fine; quote attribution will just say "speaker" instead of "host" / "guest".

Cloud Deepgram and AssemblyAI do diarization automatically (no HF token needed). Groq does not.
