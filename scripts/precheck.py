"""
First-run environment check.
Detects OS, CPU/GPU, transcription backends, and writes a config to
~/.podcast-to-notes/env.json so subsequent runs skip detection.

Note: this skill does NOT call any LLM API itself — the calling agent
(Claude/Codex/etc.) does that. So precheck only validates transcription,
not LLM connectivity.
"""

import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".podcast-to-notes"
CONFIG_FILE = CONFIG_DIR / "env.json"
ENV_FILE = CONFIG_DIR / ".env"


def check_command(cmd):
    return shutil.which(cmd)


def check_python_pkg(pkg_import_name):
    """Try importing a package by its import name (e.g. 'pyannote.audio')."""
    try:
        mod = importlib.import_module(pkg_import_name)
        return getattr(mod, "__version__", "installed")
    except ImportError:
        return None


def detect_gpu():
    info = {"available": False, "vendor": None, "name": None,
            "cuda_version": None, "cuda_available": False}
    if check_command("nvidia-smi"):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                info["available"] = True
                info["vendor"] = "nvidia"
                info["name"] = r.stdout.strip().split("\n")[0]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    try:
        import torch
        info["cuda_version"] = torch.version.cuda
        info["cuda_available"] = torch.cuda.is_available()
    except ImportError:
        pass
    return info


def detect_apple_silicon():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return None
    info = {"chip": "Apple Silicon"}
    try:
        r = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            info["chip"] = r.stdout.strip()
    except Exception:
        pass
    return info


def load_env_file():
    if not ENV_FILE.exists():
        return {}
    result = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def determine_transcription_backend(apple_silicon, gpu, pkgs, env_vars):
    """
    Pick the best available transcription backend.
    Cloud APIs win if user opted in. Then fastest local backend.
    """
    if env_vars.get("GROQ_API_KEY"):
        return {"type": "cloud", "provider": "groq",
                "note": "Using Groq Whisper API (fastest, ~$0.04/hr)"}
    if env_vars.get("DEEPGRAM_API_KEY"):
        return {"type": "cloud", "provider": "deepgram",
                "note": "Using Deepgram Nova-3 API"}
    if env_vars.get("ASSEMBLYAI_API_KEY"):
        return {"type": "cloud", "provider": "assemblyai",
                "note": "Using AssemblyAI"}

    if apple_silicon and pkgs.get("mlx_whisper"):
        return {"type": "local", "provider": "mlx",
                "note": "Using MLX Whisper on Apple Silicon (GPU accelerated)"}
    if gpu.get("cuda_available") and pkgs.get("whisperx"):
        return {"type": "local", "provider": "whisperx",
                "note": "Using WhisperX on CUDA (with diarization)"}
    if gpu.get("cuda_available") and pkgs.get("faster_whisper"):
        return {"type": "local", "provider": "faster_whisper_cuda",
                "note": "Using faster-whisper on CUDA"}
    if pkgs.get("faster_whisper"):
        return {"type": "local", "provider": "faster_whisper_cpu",
                "note": "Using faster-whisper on CPU (SLOW; consider cloud API)"}

    return {"type": "none", "provider": None,
            "note": "No transcription backend installed"}


def determine_status(env):
    issues = []

    if not env["tools"]["yt_dlp"]:
        issues.append("yt-dlp not installed")
    if not env["tools"]["ffmpeg"]:
        issues.append("ffmpeg not installed")

    if env["transcription"]["type"] == "none":
        issues.append("no transcription backend (need either local Whisper or cloud API key)")
        if not issues[:-1]:  # only this issue
            return "needs_config", issues

    if issues:
        return "needs_install", issues

    return "ready", []


def recommend_install_path(env):
    if env["apple_silicon"]:
        return "mac_mlx"
    if env["gpu"]["available"] and env["gpu"]["vendor"] == "nvidia":
        return "windows_cuda" if env["os"] == "Windows" else "linux_cuda"
    return "cpu_fallback"


def detect_china_network():
    """
    Detect whether the user is likely on a network that has trouble reaching
    Hugging Face. We can't reliably probe the user's actual IP from inside
    Python without network calls (which themselves can hang). Heuristics:

    1. Check if HF_ENDPOINT or HF_MIRROR is already set → user knows.
    2. Try a short timeout request to huggingface.co. If it fails, suggest
       a mirror. If it succeeds, all good.
    3. As a fallback, check system locale / timezone for China indicators.

    Returns dict: {"likely_china": bool, "hf_reachable": bool|None, "mirror_set": str|None}
    """
    import socket

    mirror_set = os.environ.get("HF_ENDPOINT") or os.environ.get("HF_MIRROR")
    if mirror_set and "hf-mirror" in mirror_set.lower():
        return {"likely_china": True, "hf_reachable": None, "mirror_set": mirror_set}

    # Quick HF reachability test (2s timeout, no actual HTTP)
    hf_reachable = None
    try:
        socket.setdefaulttimeout(2)
        socket.gethostbyname("huggingface.co")
        # DNS resolved — try a TCP connect to confirm not just DNS poisoning
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        try:
            s.connect(("huggingface.co", 443))
            hf_reachable = True
        except (socket.timeout, OSError):
            hf_reachable = False
        finally:
            s.close()
    except (socket.gaierror, socket.timeout, OSError):
        hf_reachable = False
    finally:
        socket.setdefaulttimeout(None)

    # Locale/timezone hint (low-confidence signal)
    likely_china = False
    try:
        import locale
        lang = (locale.getlocale()[0] or "").lower()
        if "zh_cn" in lang or "zh-cn" in lang:
            likely_china = True
    except Exception:
        pass
    try:
        tz_path = Path("/etc/timezone")
        if tz_path.exists():
            tz = tz_path.read_text().strip()
            if tz in ("Asia/Shanghai", "Asia/Chongqing", "Asia/Urumqi"):
                likely_china = True
    except Exception:
        pass
    # macOS timezone
    if platform.system() == "Darwin":
        try:
            r = subprocess.run(["readlink", "/etc/localtime"],
                               capture_output=True, text=True, timeout=2)
            if "Shanghai" in r.stdout or "Chongqing" in r.stdout:
                likely_china = True
        except Exception:
            pass

    # The strong signal: HF was unreachable.
    if hf_reachable is False:
        likely_china = True

    return {"likely_china": likely_china,
            "hf_reachable": hf_reachable,
            "mirror_set": mirror_set}


def main():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    apple_silicon = detect_apple_silicon()
    gpu = detect_gpu()

    pkgs = {
        "mlx_whisper": check_python_pkg("mlx_whisper"),
        "whisperx": check_python_pkg("whisperx"),
        "faster_whisper": check_python_pkg("faster_whisper"),
        "torch": check_python_pkg("torch"),
        "pyannote_audio": check_python_pkg("pyannote.audio"),
        "groq": check_python_pkg("groq"),
        "deepgram": check_python_pkg("deepgram"),
    }

    tools = {
        "yt_dlp": check_command("yt-dlp"),
        "ffmpeg": check_command("ffmpeg"),
    }

    env_vars = {**load_env_file(),
                **{k: v for k, v in os.environ.items()
                   if k.endswith("_API_KEY") or k == "HUGGINGFACE_TOKEN"}}

    transcription = determine_transcription_backend(apple_silicon, gpu, pkgs, env_vars)

    api_keys_present = {
        "groq": bool(env_vars.get("GROQ_API_KEY")),
        "deepgram": bool(env_vars.get("DEEPGRAM_API_KEY")),
        "assemblyai": bool(env_vars.get("ASSEMBLYAI_API_KEY")),
        "huggingface": bool(env_vars.get("HUGGINGFACE_TOKEN")),
    }

    print("🌐 Checking network reachability to Hugging Face...")
    network = detect_china_network()

    env = {
        "os": platform.system(),
        "os_version": platform.release(),
        "python_version": platform.python_version(),
        "machine": platform.machine(),
        "apple_silicon": apple_silicon,
        "gpu": gpu,
        "tools": tools,
        "python_pkgs": pkgs,
        "transcription": transcription,
        "api_keys_present": api_keys_present,
        "network": network,
    }

    status, issues = determine_status(env)
    env["status"] = status
    env["issues"] = issues
    env["recommended_install_path"] = recommend_install_path(env)

    CONFIG_FILE.write_text(json.dumps(env, indent=2, default=str), encoding="utf-8")

    print("=" * 64)
    print(f"OS: {env['os']} {env['os_version']} ({env['machine']})")
    print(f"Python: {env['python_version']}")
    if apple_silicon:
        print(f"Apple Silicon: {apple_silicon['chip']}")
    if gpu["available"]:
        cuda = f"CUDA {gpu['cuda_version']}" if gpu["cuda_version"] else "CUDA n/a"
        print(f"GPU: {gpu['name']} ({cuda}, available={gpu['cuda_available']})")
    if not apple_silicon and not gpu["available"]:
        print("GPU: none detected (CPU mode)")
    print()
    print(f"yt-dlp: {'✓' if tools['yt_dlp'] else '✗'}")
    print(f"ffmpeg: {'✓' if tools['ffmpeg'] else '✗'}")
    print()
    print(f"Transcription backend: {transcription['provider'] or '✗ none'}")
    print(f"  → {transcription['note']}")
    print()
    print("API keys (optional, for cloud transcription / speaker diarization):")
    for k, present in api_keys_present.items():
        mark = "✓" if present else "○"
        env_name = "HUGGINGFACE_TOKEN" if k == "huggingface" else f"{k.upper()}_API_KEY"
        print(f"  {mark} {env_name}")
    print()

    # Network / HF mirror status
    print("Network (Hugging Face access — only matters for local Whisper):")
    if network["mirror_set"]:
        print(f"  ✓ HF mirror configured: {network['mirror_set']}")
    elif network["hf_reachable"] is True:
        print(f"  ✓ huggingface.co reachable directly")
    elif network["hf_reachable"] is False:
        print(f"  ✗ huggingface.co NOT reachable.")
        print(f"    → If in China: set HF_ENDPOINT=https://hf-mirror.com")
        print(f"    → Quick fix:")
        print(f"        echo 'export HF_ENDPOINT=https://hf-mirror.com' >> ~/.zshrc")
        print(f"        (then restart your shell, or run: export HF_ENDPOINT=https://hf-mirror.com)")
        print(f"    → Skip this if you only use cloud transcription (Groq/Deepgram/etc.)")
    else:
        print(f"  ? could not test (skipping)")
    if network["likely_china"] and not network["mirror_set"]:
        print(f"  ⚠️ Your locale/timezone looks like mainland China; mirror is strongly recommended.")
    print()

    print(f"STATUS: {status.upper()}")
    if issues:
        print("Issues:")
        for i in issues:
            print(f"  - {i}")
        print(f"\nRecommended install path: {env['recommended_install_path']}")
        print(f"→ See references/install.md section '{env['recommended_install_path']}'")
        if status == "needs_config":
            print(f"→ Or see references/transcription_backends.md to use a cloud API")
    print("=" * 64)
    print(f"\nFull report: {CONFIG_FILE}")


if __name__ == "__main__":
    main()
