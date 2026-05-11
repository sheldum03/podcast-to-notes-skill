# Installation

After running `scripts/precheck.py`, follow the section matching your `recommended_install_path`. You only need ONE of these.

## 🇨🇳 If you're in mainland China (or HF is blocked)

Local Whisper backends (MLX, WhisperX, faster-whisper) need to download model weights from Hugging Face on first use. From mainland China, this often times out. **Before installing**, configure the HF mirror:

```bash
# Add to ~/.zshrc (Mac) or ~/.bashrc (Linux) or ~/.podcast-to-notes/.env
export HF_ENDPOINT=https://hf-mirror.com
```

Reload shell, then proceed with installation below. `precheck.py` will auto-detect whether HF is reachable and remind you if it isn't.

Alternative: use a **cloud transcription API** instead (Groq / Deepgram / AssemblyAI) — no HF download needed. See `transcription_backends.md`.

## mac_mlx (Mac Apple Silicon)

```bash
# Audio tools
brew install yt-dlp ffmpeg

# Python: MLX Whisper (uses M-series GPU natively)
pip install mlx-whisper

# Optional: speaker diarization
pip install pyannote.audio
# Then: get a HuggingFace token, click "Agree" on
#   https://huggingface.co/pyannote/speaker-diarization-3.1
#   https://huggingface.co/pyannote/segmentation-3.0
# Save: echo "HUGGINGFACE_TOKEN=hf_xxx" >> ~/.podcast-to-notes/.env
```

The first transcription will download `mlx-community/whisper-large-v3-turbo` (~1.6 GB). If you already have any of these models cached, `prepare.py` auto-detects and reuses:
- `mlx-community/whisper-large-v3-turbo` (preferred — fastest)
- `mlx-community/whisper-large-v3-mlx`
- `mlx-community/whisper-large-v3`

To pre-download via the HF mirror (China users):
```bash
export HF_ENDPOINT=https://hf-mirror.com
huggingface-cli download mlx-community/whisper-large-v3-turbo
```

Speed: 1-hour English podcast → 4-8 min on M2 Pro, 2-4 min on M4 Max.

## windows_cuda (Windows + NVIDIA RTX 30/40/50)

```powershell
pip install yt-dlp
# ffmpeg: download from https://www.gyan.dev/ffmpeg/builds/, add to PATH

# Check CUDA: nvidia-smi (look for "CUDA Version: 12.x")
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install whisperx
```

Speed: 1-hour podcast → 1-3 min on RTX 4090, 3-5 min on RTX 3060.

Common gotcha: if `Could not load library cudnn_ops_infer64_8.dll`, install cuDNN 9 from NVIDIA Developer site and add to PATH.

## linux_cuda (Linux + NVIDIA)

```bash
sudo apt install ffmpeg   # or dnf install ffmpeg on Fedora
pip install yt-dlp torch torchvision torchaudio whisperx
```

## cpu_fallback (no GPU)

Slow. Strongly recommend a cloud API instead — see `transcription_backends.md`.

```bash
brew install yt-dlp ffmpeg   # Mac, or apt on Linux
pip install faster-whisper
```

Speed: 1-hour podcast → 30-60 minutes on CPU.

## Verification

```bash
python scripts/precheck.py
```

Status should now be `ready`. If still `needs_config`, you have install but no transcription backend selected — see `transcription_backends.md`.
