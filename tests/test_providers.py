import keyring
import pytest
from keyring.backend import KeyringBackend

from notetaker import config, providers
from notetaker.about import about_text
from notetaker.llm import LLMError, OpenAICompatibleLLM


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, password):
        self.store[(service, user)] = password

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


@pytest.fixture
def memory_keyring(monkeypatch):
    previous = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    yield kr
    keyring.set_keyring(previous)


def test_keys_in_vault_with_env_fallback(memory_keyring, monkeypatch):
    assert providers.get_key("groq") == ""
    monkeypatch.setenv("GROQ_API_KEY", "uit-omgeving")
    assert providers.get_key("groq") == "uit-omgeving"
    providers.set_key("groq", "  gsk_geheim1234 ")
    assert providers.get_key("groq") == "gsk_geheim1234"
    assert memory_keyring.store[("Notetaker", "groq")] == "gsk_geheim1234"
    providers.set_key("groq", "")
    assert providers.get_key("groq") == "uit-omgeving"
    assert providers.mask("gsk_geheim1234") == "••••••••1234"


def test_missing_key_gives_clear_error(memory_keyring):
    cfg = config.Config().llm
    cfg.provider, cfg.backend = "groq", "openai-compatible"
    with pytest.raises(LLMError, match="Voeg ze toe via Instellingen"):
        OpenAICompatibleLLM(cfg)
    providers.set_key("groq", "gsk_test")
    assert OpenAICompatibleLLM(cfg).client.api_key == "gsk_test"


def test_apply_choice_writes_config(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(config.EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    providers.apply_choice(p, stt="groq", whisper_model="small", llm_provider="gemini",
                           cleanup_model="gemini-2.5-flash-lite", summary_model="gemini-2.5-flash")
    cfg = config.load(p)
    assert (cfg.stt.backend, cfg.stt.provider, cfg.stt.api_model) == ("openai-api", "groq", "whisper-large-v3-turbo")
    assert (cfg.llm.provider, cfg.llm.backend, cfg.llm.api_key_env) == ("gemini", "openai-compatible", "GEMINI_API_KEY")
    assert cfg.llm.summary_model == "gemini-2.5-flash" and cfg.dictionary
    providers.apply_choice(p, stt=providers.STT_LOCAL, whisper_model="medium", llm_provider="ollama",
                           cleanup_model="gemma3:4b", summary_model="gemma3:12b")
    cfg = config.load(p)
    assert (cfg.stt.backend, cfg.stt.model, cfg.llm.backend) == ("faster-whisper", "medium", "ollama")
    assert "Notetaker" in about_text(cfg) and "gemma3:12b" in about_text(cfg)
    assert cfg.inbox.vault_folders == ["Notetaker", "Opnames"]
    providers.apply_choice(p, stt=providers.STT_LOCAL, whisper_model="medium", llm_provider="ollama",
                           cleanup_model="gemma3:4b", summary_model="gemma3:12b",
                           vault_folders=providers.parse_folders(" Notetaker/ ; Telefoon\\Opnames, Notetaker,"))
    assert config.load(p).inbox.vault_folders == ["Notetaker", "Telefoon/Opnames"]


def test_stt_choices_only_providers_with_whisper():
    choices = providers.stt_choices()
    assert set(choices) == {providers.STT_LOCAL, "groq", "openai"}
