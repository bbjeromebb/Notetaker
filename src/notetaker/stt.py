"""Spraakherkenning: lokale faster-whisper of een OpenAI-compatibele API (Groq, OpenAI, eigen server)."""
from __future__ import annotations

import io
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .audio import SAMPLE_RATE, write_wav
from .config import STTConfig

log = logging.getLogger(__name__)

# Bibliotheken die CTranslate2 (de motor onder faster-whisper) op Windows nodig heeft voor de GPU
WINDOWS_CUDA_DLLS = ("cublas64_12.dll", "cudnn_ops64_9.dll")


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str = ""


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, prompt: str = "") -> list[Segment]: ...


def vocab_prompt(words: list[str], max_chars: int = 600) -> str:
    """Whisper gebruikt de prompt als 'vorige tekst': een woordenlijst stuurt de schrijfwijze."""
    if not words:
        return ""
    out = ""
    for w in words:
        candidate = f"{out}, {w}" if out else w
        if len(candidate) > max_chars:
            break
        out = candidate
    return f"Woordenlijst: {out}."


def add_nvidia_dll_dirs() -> list[str]:
    """Windows: maakt de DLL's uit de pip-pakketten nvidia-cublas-cu12 en nvidia-cudnn-cu12 vindbaar."""
    if sys.platform != "win32":
        return []
    import site

    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    added = []
    for root in roots:
        for bin_dir in Path(root, "nvidia").glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            added.append(str(bin_dir))
    return added


def cuda_status() -> tuple[bool, str]:
    """Controleert vooraf of de GPU bruikbaar is. Een ontbrekende cuDNN-DLL laat CTranslate2
    anders het hele proces afsluiten in plaats van een fout te geven."""
    try:
        import ctranslate2

        count = ctranslate2.get_cuda_device_count()
    except Exception as e:
        return False, f"CTranslate2 niet bruikbaar: {e}"
    if count == 0:
        return False, "Geen CUDA-GPU gevonden (NVIDIA-driver geïnstalleerd?)"
    if sys.platform == "win32":
        import ctypes

        add_nvidia_dll_dirs()
        for dll in WINDOWS_CUDA_DLLS:
            try:
                ctypes.WinDLL(dll)
            except OSError:
                return False, f"{dll} ontbreekt: pip install -e \".[gpu]\" (zie README)"
    return True, f"{count} CUDA-GPU gevonden"


def resolve_device(cfg: STTConfig, status=cuda_status) -> tuple[str, str, str]:
    """Kiest apparaat en rekenprecisie. Geeft (device, compute_type, uitleg) terug."""
    device, reason = cfg.device, ""
    if device in ("auto", "cuda"):
        ok, reason = status()
        if ok:
            device = "cuda"
        else:
            if cfg.device == "cuda":
                log.warning("GPU gevraagd maar niet bruikbaar, CPU wordt gebruikt: %s", reason)
            device = "cpu"
    compute = cfg.compute_type
    if compute == "auto":
        # int8_float16: gewichten in 8 bit, rekenen in 16 bit; past in 2 GB VRAM
        compute = "int8_float16" if device == "cuda" else "int8"
    return device, compute, reason


class FasterWhisperTranscriber:
    def __init__(self, cfg: STTConfig) -> None:
        from faster_whisper import WhisperModel

        self.language = cfg.language or None
        self.device, compute, reason = resolve_device(cfg)
        log.info("Whisper %s op %s (%s). %s", cfg.model, self.device, compute, reason)
        try:
            self.model = WhisperModel(cfg.model, device=self.device, compute_type=compute)
            if self.device == "cuda":
                # Proefrun: vangt een tekort aan VRAM op bij de start in plaats van bij het eerste dictee
                list(self.model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="nl")[0])
        except Exception as e:
            if self.device != "cuda":
                raise
            log.warning("GPU faalde (%s), terugval op CPU.", e)
            self.device = "cpu"
            self.model = WhisperModel(cfg.model, device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray, prompt: str = "") -> list[Segment]:
        segments, _ = self.model.transcribe(
            audio, language=self.language, initial_prompt=prompt or None,
            vad_filter=True, beam_size=5, condition_on_previous_text=False,
        )
        return [Segment(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]


class OpenAICompatibleTranscriber:
    def __init__(self, cfg: STTConfig) -> None:
        from openai import OpenAI

        key = os.environ.get(cfg.api_key_env)
        if not key:
            raise RuntimeError(f"Omgevingsvariabele {cfg.api_key_env} ontbreekt.")
        self.client = OpenAI(api_key=key, base_url=cfg.api_base)
        self.model = cfg.api_model
        self.language = cfg.language

    def transcribe(self, audio: np.ndarray, prompt: str = "") -> list[Segment]:
        buf = io.BytesIO()
        write_wav(buf, audio)  # wave accepteert file-achtige objecten
        buf.seek(0)
        kwargs = {"model": self.model, "file": ("audio.wav", buf), "response_format": "verbose_json"}
        if self.language:
            kwargs["language"] = self.language
        if prompt:
            kwargs["prompt"] = prompt
        result = self.client.audio.transcriptions.create(**kwargs)
        segs = getattr(result, "segments", None) or []
        if not segs:
            text = (result.text or "").strip()
            return [Segment(0.0, audio.size / SAMPLE_RATE, text)] if text else []
        out = []
        for s in segs:
            get = s.get if isinstance(s, dict) else lambda k, s=s: getattr(s, k)
            text = (get("text") or "").strip()
            if text:
                out.append(Segment(float(get("start")), float(get("end")), text))
        return out


def build_transcriber(cfg: STTConfig) -> Transcriber:
    if cfg.backend == "faster-whisper":
        return FasterWhisperTranscriber(cfg)
    if cfg.backend == "openai-api":
        return OpenAICompatibleTranscriber(cfg)
    raise ValueError(f"Onbekende STT-backend: {cfg.backend}")
