"""Aanbieders van taal- en spraakmodellen, API-sleutels en het wegschrijven van een modelkeuze.

API-sleutels staan in de Windows-kluis (Credential Manager, via keyring), nooit in config.toml.
Een omgevingsvariabele werkt als terugvaloptie.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

KEYRING_SERVICE = "Notetaker"


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    backend: str  # "ollama", "openai-compatible", "anthropic" of "none"
    api_base: str = ""
    env: str = ""
    models: tuple[str, ...] = ()  # suggesties; de lijst is altijd aan te vullen
    stt_model: str = ""  # leeg = biedt geen spraakherkenning aan
    key_url: str = ""

    @property
    def needs_key(self) -> bool:
        return bool(self.env)


PROVIDERS: dict[str, Provider] = {p.key: p for p in (
    Provider("ollama", "Ollama (lokaal, gratis)", "ollama",
             models=("gemma3:4b", "gemma3:12b", "qwen3:8b", "llama3.1:8b")),
    Provider("groq", "Groq (gratis tier)", "openai-compatible", "https://api.groq.com/openai/v1",
             "GROQ_API_KEY", ("llama-3.1-8b-instant", "llama-3.3-70b-versatile"),
             stt_model="whisper-large-v3-turbo", key_url="https://console.groq.com/keys"),
    Provider("gemini", "Google Gemini", "openai-compatible",
             "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY",
             ("gemini-2.5-flash-lite", "gemini-2.5-flash"), key_url="https://aistudio.google.com/apikey"),
    Provider("openai", "OpenAI", "openai-compatible", "https://api.openai.com/v1", "OPENAI_API_KEY",
             stt_model="whisper-1", key_url="https://platform.openai.com/api-keys"),
    Provider("openrouter", "OpenRouter", "openai-compatible", "https://openrouter.ai/api/v1",
             "OPENROUTER_API_KEY", key_url="https://openrouter.ai/keys"),
    Provider("anthropic", "Anthropic Claude (betalend)", "anthropic", env="ANTHROPIC_API_KEY",
             models=("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"),
             key_url="https://console.anthropic.com/settings/keys"),
    Provider("none", "Geen taalmodel (enkel basisopkuis)", "none"),
)}

WHISPER_MODELS = ("large-v3-turbo", "large-v3", "medium", "small", "base", "tiny")
STT_LOCAL = "lokaal"


def stt_choices() -> dict[str, str]:
    """Keuzes voor spraakherkenning: lokaal of een API die Whisper aanbiedt."""
    out = {STT_LOCAL: "Lokaal (Whisper op deze pc, privé)"}
    out.update({p.key: f"{p.label} via API" for p in PROVIDERS.values() if p.stt_model})
    return out


# --- API-sleutels ------------------------------------------------------------------

def _keyring():
    try:
        import keyring

        return keyring
    except Exception:
        return None


def keyring_available() -> bool:
    return _keyring() is not None


def get_key(provider: str, env: str = "") -> str:
    """Sleutel uit de kluis, anders uit de omgevingsvariabele."""
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(KEYRING_SERVICE, provider)
        except Exception:
            value = None
        if value:
            return value
    env = env or (PROVIDERS[provider].env if provider in PROVIDERS else "")
    return os.environ.get(env, "") if env else ""


def has_key(provider: str) -> bool:
    return bool(get_key(provider))


def set_key(provider: str, value: str) -> None:
    kr = _keyring()
    if kr is None:
        raise RuntimeError("Sleutels bewaren vereist het pakket 'keyring'.")
    if value:
        kr.set_password(KEYRING_SERVICE, provider, value.strip())
    else:
        try:
            kr.delete_password(KEYRING_SERVICE, provider)
        except Exception:
            pass


def mask(value: str) -> str:
    return "" if not value else ("•" * 8 + value[-4:] if len(value) > 8 else "•" * len(value))


# --- Modelkeuze wegschrijven ----------------------------------------------------------

def apply_choice(config_path: Path, *, stt: str, whisper_model: str, llm_provider: str,
                 cleanup_model: str, summary_model: str) -> None:
    """Schrijft een keuze uit het instellingenvenster naar config.toml (rest blijft staan)."""
    from .obsidian import set_toml_value

    if stt == STT_LOCAL:
        set_toml_value(config_path, "stt", "backend", "faster-whisper")
        set_toml_value(config_path, "stt", "model", whisper_model)
    else:
        p = PROVIDERS[stt]
        set_toml_value(config_path, "stt", "backend", "openai-api")
        set_toml_value(config_path, "stt", "provider", p.key)
        set_toml_value(config_path, "stt", "api_base", p.api_base)
        set_toml_value(config_path, "stt", "api_model", p.stt_model)
        set_toml_value(config_path, "stt", "api_key_env", p.env)

    p = PROVIDERS[llm_provider]
    set_toml_value(config_path, "llm", "provider", p.key)
    set_toml_value(config_path, "llm", "backend", p.backend)
    if p.backend == "openai-compatible":
        set_toml_value(config_path, "llm", "api_base", p.api_base)
        set_toml_value(config_path, "llm", "api_key_env", p.env)
    if p.backend != "none":
        set_toml_value(config_path, "llm", "cleanup_model", cleanup_model.strip())
        set_toml_value(config_path, "llm", "summary_model", summary_model.strip())


def list_models(provider: str, cfg_llm) -> list[str]:
    """Live modellenlijst: geïnstalleerde Ollama-modellen of de /models-lijst van de API."""
    p = PROVIDERS[provider]
    if p.backend == "ollama":
        from .llm import OllamaLLM

        return sorted(OllamaLLM(cfg_llm).models())
    if p.backend == "openai-compatible":
        import openai

        client = openai.OpenAI(api_key=get_key(p.key), base_url=p.api_base, max_retries=1, timeout=15)
        return sorted(m.id.removeprefix("models/") for m in client.models.list())
    if p.backend == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=get_key(p.key) or None, max_retries=1, timeout=15)
        return [m.id for m in client.models.list()]
    return []
