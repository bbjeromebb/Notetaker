"""Informatie voor 'Over Notetaker': versie, modellen en waar alles staat."""
from __future__ import annotations

import platform

from . import __version__
from .config import Config, app_dir, default_path
from .providers import PROVIDERS
from .updater import current_version

REPO_URL = "https://github.com/bbjeromebb/Notetaker"


def about_rows(cfg: Config, whisper_device: str = "") -> list[tuple[str, str]]:
    llm = PROVIDERS.get(cfg.llm.provider)
    if cfg.stt.backend == "faster-whisper":
        stt = f"Whisper {cfg.stt.model} lokaal" + (f" op {whisper_device}" if whisper_device else "")
    else:
        stt = f"{cfg.stt.api_model} via {cfg.stt.provider}"
    rows = [
        ("Versie", f"{__version__} ({current_version()})"),
        ("Spraakherkenning", stt),
        ("Taalmodel", llm.label if llm else cfg.llm.backend),
    ]
    if cfg.llm.backend != "none":
        rows += [("Model dictee", cfg.llm.cleanup_model), ("Model verslagen", cfg.llm.summary_model)]
    rows += [
        ("Verslagen", str(cfg.meetings_dir)),
        ("Configuratie", str(default_path())),
        ("Logbestand", str(app_dir() / "notetaker.log")),
        ("Python", platform.python_version()),
        ("Broncode", REPO_URL),
    ]
    return rows


def about_text(cfg: Config, whisper_device: str = "") -> str:
    rows = about_rows(cfg, whisper_device)
    width = max(len(k) for k, _ in rows)
    return "Notetaker\n\n" + "\n".join(f"{k.ljust(width)}  {v}" for k, v in rows)

