"""Configuratie: TOML-bestand in de app-map, met verstandige standaardwaarden."""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path


def app_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Notetaker"


@dataclass
class STTConfig:
    # "faster-whisper" (lokaal, privé) of "openai-api" (OpenAI-compatibele endpoint, bv. Groq)
    backend: str = "faster-whisper"
    model: str = "large-v3-turbo"
    device: str = "auto"  # "cuda", "cpu" of "auto"
    compute_type: str = "int8"
    language: str = "nl"
    api_base: str = "https://api.groq.com/openai/v1"
    api_model: str = "whisper-large-v3-turbo"
    api_key_env: str = "GROQ_API_KEY"


@dataclass
class LLMConfig:
    # "ollama" (lokaal, gratis), "openai-compatible" (Groq/Gemini/OpenRouter), "anthropic" of "none"
    backend: str = "ollama"
    cleanup_model: str = "gemma3:4b"
    summary_model: str = "gemma3:12b"
    temperature: float = 0.2
    # Ollama
    ollama_url: str = "http://localhost:11434"
    num_ctx: int = 16384
    keep_alive: str = "30m"
    # OpenAI-compatibele API
    api_base: str = "https://api.groq.com/openai/v1"
    api_key_env: str = "GROQ_API_KEY"
    # Enkel voor Anthropic
    cleanup_effort: str = "low"
    summary_effort: str = "high"
    # Langere transcripten worden in delen samengevat (kleine modellen, rate limits van gratis tiers)
    summary_chunk_chars: int = 24000


@dataclass
class DictationConfig:
    hotkey: str = "ctrl+cmd"  # cmd = Windows-toets
    command_hotkey: str = "ctrl+cmd+alt"
    handsfree_key: str = "space"  # tijdens vasthouden: vergrendel hands-free
    tap_max_seconds: float = 0.3
    double_tap_window: float = 0.4
    min_seconds: float = 0.4
    silence_rms: float = 0.004
    paste_delay: float = 0.12
    sounds: bool = True
    mic_device: str = ""


@dataclass
class MeetingConfig:
    chunk_seconds: float = 30.0
    output_dir: str = ""
    mic_label: str = "Ik"
    others_label: str = "Anderen"
    mic_device: str = ""
    loopback_device: str = ""  # leeg = standaard luidspreker (Windows WASAPI-loopback)
    keep_audio: bool = True
    echo_threshold: float = 0.75


@dataclass
class Config:
    stt: STTConfig = field(default_factory=STTConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    dictation: DictationConfig = field(default_factory=DictationConfig)
    meeting: MeetingConfig = field(default_factory=MeetingConfig)
    # Eigennamen, afkortingen en jargon: juiste schrijfwijze + hint voor de spraakherkenning
    dictionary: list[str] = field(default_factory=list)
    # Vaste correcties van misverstane woorden: "lei dal" -> "Leiedal"
    replacements: dict[str, str] = field(default_factory=dict)
    # Spraaksnelkoppelingen: "mijn handtekening" -> volledige tekst
    snippets: dict[str, str] = field(default_factory=dict)
    # Procesnaam (kleine letters) -> stijl ("email", "chat", "code", "formal", "default")
    styles: dict[str, str] = field(default_factory=dict)

    @property
    def meetings_dir(self) -> Path:
        if self.meeting.output_dir:
            return Path(self.meeting.output_dir).expanduser()
        return Path.home() / "Documents" / "Notetaker"


def _fill(dc, data: dict):
    for f in fields(dc):
        if f.name not in data:
            continue
        current = getattr(dc, f.name)
        value = data[f.name]
        if is_dataclass(current) and isinstance(value, dict):
            _fill(current, value)
        else:
            setattr(dc, f.name, value)
    return dc


def default_path() -> Path:
    return app_dir() / "config.toml"


def load(path: Path | None = None) -> Config:
    path = path or default_path()
    cfg = Config()
    if path.exists():
        with path.open("rb") as fh:
            _fill(cfg, tomllib.load(fh))
    return cfg


EXAMPLE = Path(__file__).with_name("config.example.toml")


def write_example(path: Path | None = None) -> Path:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return path
