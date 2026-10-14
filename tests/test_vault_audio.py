import json
import os
import time
from datetime import datetime

from notetaker import config
from notetaker.audio import write_wav
from notetaker.inbox import (ProcessedLog, ensure_vault_since, process_file, process_vault,
                             started_from_name, vault_candidates)

from test_core import FakeLLM, FakeTranscriber, tone


def _age(path, seconds=60):
    t = time.time() - seconds
    os.utime(path, (t, t))


def setup(tmp_path, since=""):
    vault = tmp_path / "Vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "Opnames").mkdir()
    cfg = config.Config()
    cfg.obsidian.vault = str(vault)
    cfg.obsidian.audio_in_vault = True
    cfg.meeting.output_dir = str(tmp_path / "docs")
    cfg.inbox.vault_since = since
    return cfg, vault, ProcessedLog(tmp_path / "verwerkt.json")


def test_started_from_name():
    assert started_from_name(__import__("pathlib").Path("Recording 20261014093015.webm")) == \
        datetime(2026, 10, 14, 9, 30, 15)
    assert started_from_name(__import__("pathlib").Path("2026-10-14_0930_Werf.ogg")) == \
        datetime(2026, 10, 14, 9, 30)
    assert started_from_name(__import__("pathlib").Path("opname.ogg")) is None


def test_android_recording_in_vault_is_processed_and_removed(tmp_path):
    cfg, vault, log_ = setup(tmp_path)
    audio = vault / "Opnames" / "2026-10-14_0930_Werfoverleg.wav"
    write_wav(audio, tone(2))
    audio.with_suffix(".json").write_text(json.dumps(
        {"title": "Werfoverleg", "started": "2026-10-14T09:30:00", "notes": "- foto's"}))
    _age(audio)
    notes = process_vault(cfg, FakeTranscriber("We bekijken de werf."), FakeLLM("## Samenvatting\nOk."),
                          log_=log_)
    assert [n.name for n in notes] == ["2026-10-14_0930_Werfoverleg.md"]
    assert list((vault / "Opnames").iterdir()) == []  # telefoon ziet 'verwerkt'
    md = notes[0].read_text(encoding="utf-8")
    assert "- foto's" in md and "![[2026-10-14_0930_Werfoverleg.wav]]" in md


def test_obsidian_recording_stays_and_uses_its_note(tmp_path):
    cfg, vault, log_ = setup(tmp_path)
    audio = vault / "Opnames" / "Recording 20261014101500.wav"
    write_wav(audio, tone(2))
    _age(audio)
    (vault / "Overleg Kortrijk.md").write_text(
        "---\ntags: [werk]\n---\nVragen over de planning.\n\n![[Recording 20261014101500.wav]]\n",
        encoding="utf-8")
    llm = FakeLLM("## Samenvatting\nOk.")
    notes = process_vault(cfg, FakeTranscriber("De planning schuift op."), llm, log_=log_)
    assert len(notes) == 1 and audio.exists()
    md = notes[0].read_text(encoding="utf-8")
    assert notes[0].name == "2026-10-14_1015_Overleg-Kortrijk.md"
    assert "Bron: [[Overleg Kortrijk]]" in md
    assert "![[Opnames/Recording 20261014101500.wav]]" in md
    assert "Vragen over de planning." in md and "<notities_gebruiker>" in llm.calls[0][1]
    # Tweede ronde: al verwerkt, niets opnieuw
    assert process_vault(cfg, FakeTranscriber("x"), None, log_=log_) == []
    assert ProcessedLog(log_.path).done(audio)


def test_only_new_recordings_after_first_start(tmp_path):
    cfg, vault, log_ = setup(tmp_path, since=datetime.now().isoformat(timespec="seconds"))
    old = vault / "Opnames" / "oud.wav"
    write_wav(old, tone(1))
    _age(old, 3600 * 24)
    assert vault_candidates(cfg, log_) == []
    assert vault_candidates(cfg, log_, include_old=True) == [old]


def test_own_meetings_folder_is_never_reprocessed(tmp_path):
    cfg, vault, log_ = setup(tmp_path)
    cfg.inbox.vault_folders = ["*"]
    own = vault / "Vergaderingen"
    own.mkdir()
    write_wav(own / "2026-10-14_0900_Overleg.wav", tone(1))
    write_wav(vault / "losse opname.wav", tone(1))
    for p in (own / "2026-10-14_0900_Overleg.wav", vault / "losse opname.wav"):
        _age(p)
    assert [p.name for p in vault_candidates(cfg, log_)] == ["losse opname.wav"]


def test_process_file_outside_vault_stays(tmp_path):
    cfg, vault, log_ = setup(tmp_path)
    audio = tmp_path / "dictafoon.wav"
    write_wav(audio, tone(2))
    note = process_file(cfg, FakeTranscriber("Hallo."), None, audio, log_)
    assert audio.exists() and note.parent == vault / "Vergaderingen"
    assert str(audio) in note.read_text(encoding="utf-8")


def test_ensure_vault_since_written_once(tmp_path):
    cfg, vault, _ = setup(tmp_path)
    p = tmp_path / "config.toml"
    ensure_vault_since(cfg, p)
    first = cfg.inbox.vault_since
    assert first and config.load(p).inbox.vault_since == first
    ensure_vault_since(cfg, p)
    assert cfg.inbox.vault_since == first
