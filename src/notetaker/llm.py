"""Taalmodel-laag voor opkuis van dictee, stemcommando's en vergaderverslagen.

Backends:
- "ollama": lokaal en gratis, geen data naar buiten (standaard)
- "openai-compatible": gratis tiers van Groq, Google Gemini, OpenRouter, ... of een eigen server
- "anthropic": Claude (betalend)
- "none": geen taalmodel, enkel regelgebaseerde opkuis
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Protocol

from .config import LLMConfig

log = logging.getLogger(__name__)

# Modellen waarvoor de Claude-API server-side fallback bij een weigering ondersteunt
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def complete(self, system: str, prompt: str, *, model: str, effort: str,
                 max_tokens: int = 16000) -> str: ...


def strip_think(text: str) -> str:
    """Redeneermodellen (Qwen3, DeepSeek-R1) zetten hun gedachtegang tussen <think>-tags."""
    return _THINK_RE.sub("", text).strip()


class OllamaLLM:
    """Native Ollama-API: laat toe het contextvenster (num_ctx) in te stellen, wat de
    OpenAI-compatibele route van Ollama niet doet. Zonder dat kapt Ollama lange transcripten stil af."""

    def __init__(self, cfg: LLMConfig) -> None:
        self.url = cfg.ollama_url.rstrip("/")
        self.num_ctx = cfg.num_ctx
        self.temperature = cfg.temperature
        self.keep_alive = cfg.keep_alive
        self.num_gpu = cfg.num_gpu

    def _options(self, **extra) -> dict:
        opts = {"num_ctx": self.num_ctx, "temperature": self.temperature, **extra}
        if self.num_gpu >= 0:
            opts["num_gpu"] = self.num_gpu
        return opts

    def _post(self, path: str, payload: dict | None, timeout: float) -> dict:
        req = urllib.request.Request(
            self.url + path, data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code == 404:
                model = (payload or {}).get("model")
                raise LLMError(f"Model niet gevonden in Ollama. Voer 'ollama pull {model}' uit. ({detail})") from e
            raise LLMError(f"Ollama-fout {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise LLMError(f"Ollama niet bereikbaar op {self.url}. Draait Ollama? ({e})") from e

    def complete(self, system: str, prompt: str, *, model: str, effort: str,
                 max_tokens: int = 16000) -> str:
        data = self._post("/api/chat", {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": self._options(num_predict=max_tokens),
        }, timeout=900)
        return strip_think(data.get("message", {}).get("content", ""))

    def warmup(self, model: str) -> None:
        """Laadt het model alvast in het geheugen, zodat het eerste dictee niet traag is."""
        self._post("/api/generate", {"model": model, "keep_alive": self.keep_alive,
                                     "options": self._options()}, timeout=300)

    def models(self) -> list[str]:
        """Namen van de lokaal geïnstalleerde modellen."""
        return [m["name"] for m in self._post("/api/tags", None, timeout=10).get("models", [])]


class OpenAICompatibleLLM:
    def __init__(self, cfg: LLMConfig) -> None:
        import openai

        key = os.environ.get(cfg.api_key_env, "")
        if not key:
            raise LLMError(f"Omgevingsvariabele {cfg.api_key_env} ontbreekt.")
        self._openai = openai
        # Gratis tiers hebben strikte rate limits: de SDK wacht en probeert opnieuw bij 429
        self.client = openai.OpenAI(api_key=key, base_url=cfg.api_base, max_retries=5)
        self.temperature = cfg.temperature

    def complete(self, system: str, prompt: str, *, model: str, effort: str,
                 max_tokens: int = 16000) -> str:
        o = self._openai
        try:
            resp = self.client.chat.completions.create(
                model=model, temperature=self.temperature, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            )
        except o.RateLimitError as e:
            raise LLMError(f"Rate limit van de gratis tier bereikt: {e}") from e
        except o.APIStatusError as e:
            raise LLMError(f"API-fout {e.status_code}: {e.message}") from e
        except o.APIConnectionError as e:
            raise LLMError(f"Geen verbinding met de API: {e}") from e
        return strip_think(resp.choices[0].message.content or "")


class ClaudeLLM:
    def __init__(self) -> None:
        import anthropic

        self._anthropic = anthropic
        # Credentials via ANTHROPIC_API_KEY of een 'ant auth login'-profiel
        self.client = anthropic.Anthropic()

    def complete(self, system: str, prompt: str, *, model: str, effort: str,
                 max_tokens: int = 16000) -> str:
        a = self._anthropic
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort},
        )
        if model in _FALLBACK_MODELS:
            kwargs.update(betas=[_FALLBACK_BETA], fallbacks="default")
        try:
            # Streaming vermijdt HTTP-timeouts bij lange vergadertranscripten
            with self.client.beta.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except a.RateLimitError as e:
            raise LLMError(f"Rate limit bereikt: {e}") from e
        except a.APIStatusError as e:
            raise LLMError(f"API-fout {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError(f"Geen verbinding met de API: {e}") from e
        if msg.stop_reason == "refusal":
            raise LLMError("Het model weigerde deze aanvraag.")
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if msg.stop_reason == "max_tokens":
            log.warning("Antwoord afgekapt op max_tokens.")
        return text


def build_llm(cfg: LLMConfig) -> LLM | None:
    if cfg.backend == "ollama":
        return OllamaLLM(cfg)
    if cfg.backend == "openai-compatible":
        return OpenAICompatibleLLM(cfg)
    if cfg.backend == "anthropic":
        return ClaudeLLM()
    if cfg.backend == "none":
        return None
    raise ValueError(f"Onbekende LLM-backend: {cfg.backend}")
