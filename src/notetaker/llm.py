"""Taalmodel-laag (Claude) voor opkuis van dictee, stemcommando's en vergaderverslagen."""
from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)

# Modellen waarvoor de API server-side fallback bij een weigering ondersteunt
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def complete(self, system: str, prompt: str, *, model: str, effort: str,
                 max_tokens: int = 16000) -> str: ...


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


def build_llm(backend: str) -> LLM | None:
    if backend == "anthropic":
        return ClaudeLLM()
    if backend == "none":
        return None
    raise ValueError(f"Onbekende LLM-backend: {backend}")
