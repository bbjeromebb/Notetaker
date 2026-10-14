from notetaker import config
from notetaker.__main__ import main, parse_value
from notetaker.obsidian import set_toml_value


def test_parse_value():
    assert parse_value("true") is True and parse_value("0") == 0 and parse_value("0.5") == 0.5
    assert parse_value("gemma3:12b") == "gemma3:12b"


def test_set_on_example_config_keeps_comments(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(config.EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    main(["--config", str(p), "set", "llm.num_gpu", "0"])
    main(["--config", str(p), "set", "obsidian.audio_in_vault", "true"])
    set_toml_value(p, "", "dictionary", "x")
    cfg = config.load(p)
    assert cfg.llm.num_gpu == 0 and cfg.obsidian.audio_in_vault is True
    assert cfg.llm.summary_model == "gemma3:12b"
    assert "# Alternatief 1: Groq" in p.read_text(encoding="utf-8")


def test_parse_value_lists():
    assert parse_value('["Notetaker", "Opnames"]') == ["Notetaker", "Opnames"]
    assert parse_value("[Notetaker, Opnames]") == ["Notetaker", "Opnames"]  # PowerShell zonder aanhalingstekens
    assert parse_value("[]") == [] and parse_value('["", "a"]') == ["a"]
    # Windows-paden: backslashes blijven letterlijk, getallen blijven tekst
    assert parse_value('["Notetaker", "Werk\\notulen"]') == ["Notetaker", "Werk\\notulen"]
    assert parse_value("[2026]") == ["2026"]
    assert parse_value('["Werk, privé", Notetaker, \'Opnames\']') == ["Werk, privé", "Notetaker", "Opnames"]


def test_set_list_replaces_multiline_array(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[inbox]\nvault_folders = [\n  "Opnames",\n]  # eigen commentaar\ninterval_seconds = 20\n',
                 encoding="utf-8")
    main(["--config", str(p), "set", "inbox.vault_folders", '["Notetaker", "Bijlagen"]'])
    cfg = config.load(p)
    assert cfg.inbox.vault_folders == ["Notetaker", "Bijlagen"] and cfg.inbox.interval_seconds == 20
    assert "# eigen commentaar" in p.read_text(encoding="utf-8")


def test_set_list_ignores_brackets_in_strings_and_comments(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[inbox]\nvault_folders = [\n  "Notetaker",  # Android-app [inbox]\n  "Opnames [oud]",\n]\n'
                 '[obsidian]\nfolder = "Vergaderingen"\n', encoding="utf-8")
    set_toml_value(p, "inbox", "vault_folders", ["Notetaker", "Werk\\notulen", "a\nb"])
    cfg = config.load(p)
    assert cfg.inbox.vault_folders == ["Notetaker", "Werk\\notulen", "a\nb"]
    assert cfg.obsidian.folder == "Vergaderingen"


def test_set_refuses_to_break_config(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[llm]\nsnippet = """\nmeerdere\nregels\n"""\n', encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    import pytest

    with pytest.raises(ValueError):
        set_toml_value(p, "llm", "snippet", "kort")
    assert p.read_text(encoding="utf-8") == before


def test_set_finds_headers_with_comments_and_spaces(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[ obsidian ]  # verslagen\n  vault = "C:/oud"\n[inbox]', encoding="utf-8")
    set_toml_value(p, "obsidian", "vault", "C:/Vault")
    set_toml_value(p, "inbox", "vault_since", "2026-10-14T10:00:00")
    cfg = config.load(p)
    assert cfg.obsidian.vault == "C:/Vault" and cfg.inbox.vault_since == "2026-10-14T10:00:00"


def test_apply_choice_is_all_or_nothing(tmp_path):
    from notetaker import providers

    p = tmp_path / "config.toml"
    p.write_text('[stt]\nmodel = "small"\n[llm]\nsummary_model = """\nlang\n"""\n', encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    import pytest

    with pytest.raises(ValueError):
        providers.apply_choice(p, stt=providers.STT_LOCAL, whisper_model="medium", llm_provider="ollama",
                               cleanup_model="gemma3:4b", summary_model="gemma3:12b", vault_folders=["X"])
    assert p.read_text(encoding="utf-8") == before


def test_set_with_indented_next_table_hits_the_right_table(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[stt]\nmodel = "small"\n    [llm]\n    api_base = "https://gemini"\n', encoding="utf-8")
    main(["--config", str(p), "set", "stt.api_base", "https://openai"])
    cfg = config.load(p)
    assert cfg.stt.api_base == "https://openai" and cfg.llm.api_base == "https://gemini"


def test_set_refuses_when_a_wrong_line_would_change(tmp_path):
    import pytest

    p = tmp_path / "config.toml"
    p.write_text('[snippets]\nmail = """\nBeste,\n  groet = "x"\n"""\ngroet = "Met vriendelijke groeten"\n',
                 encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        set_toml_value(p, "snippets", "groet", "Groeten")
    assert p.read_text(encoding="utf-8") == before
