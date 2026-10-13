"""Spraakherkenning: lokale faster-whisper of een OpenAI-compatibele API (Groq, OpenAI, eigen server)."""
from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .audio import SAMPLE_RATE, write_wav
from .config import STTConfig


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


class FasterWhisperTranscriber:
    def __init__(self, cfg: STTConfig) -> None:
        from faster_whisper import WhisperModel

        self.language = cfg.language or None
        self.model = WhisperModel(cfg.model, device=cfg.device, compute_type=cfg.compute_type)

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
