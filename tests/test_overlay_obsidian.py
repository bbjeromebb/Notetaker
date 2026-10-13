import json
from datetime import datetime

import numpy as np

from notetaker import config
from notetaker.audio import SAMPLE_RATE
from notetaker.hotkey import display_combo
from notetaker.meeting import MeetingRecorder, render_note, summary_system
from notetaker.obsidian import frontmatter, is_vault, known_vaults, set_vault
from notetaker.overlay import OverlayModel, level_to_height

from test_core import FakeLLM, FakeRecorder, FakeTranscriber, tone


# --- menutekst en opnamevenster ----------------------------------------------

def test_display_combo():
    assert display_combo("ctrl+cmd") == "Ctrl+Win"
    assert display_combo("alt+cmd_l+ctrl") == "Ctrl+Win+Alt"
    assert display_combo("ctrl+shift+d") == "Ctrl+Shift+D"


def test_level_to_height():
    assert level_to_height(0.0) == 0.0
    assert level_to_height(1.0) == 1.0
    assert level_to_height(0.01) == 0.25
    assert 0.7 < level_to_height(0.1) < 0.8


def test_overlay_model_states():
    m = OverlayModel("ctrl+cmd")
    assert not m.visible
    m.set_state("recording", "dictate")
    assert m.visible and m.color_and_text() == ("#dc2626", "Luisteren - Esc annuleert")
    m.set_state("recording", "command")
    assert m.color_and_text()[1].startswith("Opdracht")
    m.set_state("handsfree")
    assert m.color_and_text()[1] == "Hands-free - Ctrl+Win stopt"
    m.push_level(0.1)
    assert m.levels[-1] > 0
    m.set_state("idle")
    assert not m.visible and max(m.levels) == 0


# --- Obsidian ------------------------------------------------------------------

def test_known_vaults(tmp_path):
    a, b = tmp_path / "Werk", tmp_path / "Prive"
    a.mkdir(); b.mkdir()
    cfg_file = tmp_path / "obsidian.json"
    cfg_file.write_text(json.dumps({"vaults": {
        "1": {"path": str(a), "ts": 1}, "2": {"path": str(b), "ts": 5},
        "3": {"path": str(tmp_path / "weg"), "ts": 9}}}))
    assert known_vaults(cfg_file) == [b, a]
    assert known_vaults(tmp_path / "ontbreekt.json") == []


def test_set_vault_preserves_rest(tmp_path):
    p = tmp_path / "config.toml"
    set_vault(p, "C:\\Users\\jan\\Vault")
    assert config.load(p).obsidian.vault == "C:\\Users\\jan\\Vault"
    p.write_text('dictionary = ["RUP"]\n\n[obsidian]\nfolder = "Overleg"\nvault = "oud"\n\n[llm]\nbackend = "none"\n')
    set_vault(p, "D:/Nieuw")
    cfg = config.load(p)
    assert (cfg.obsidian.vault, cfg.obsidian.folder) == ("D:/Nieuw", "Overleg")
    assert cfg.llm.backend == "none" and cfg.dictionary == ["RUP"]
    set_vault(p, "")
    assert config.load(p).obsidian.vault == ""


def test_paths_follow_vault(tmp_path):
    cfg = config.Config()
    cfg.meeting.output_dir = str(tmp_path / "docs")
    assert cfg.meetings_dir == tmp_path / "docs"
    cfg.obsidian.vault = str(tmp_path / "Vault")
    assert cfg.meetings_dir == tmp_path / "Vault" / "Vergaderingen"
    assert cfg.audio_dir == tmp_path / "docs" / "audio"
    cfg.obsidian.audio_in_vault = True
    assert cfg.audio_dir == cfg.meetings_dir


def test_frontmatter_and_note(tmp_path):
    fm = frontmatter('Overleg "RUP"', datetime(2026, 10, 13, 9, 5), "00:45:00", ["vergadering", "rup"])
    assert fm.startswith("---\ntitle: \"Overleg \\\"RUP\\\"\"\ndate: 2026-10-13\n")
    assert "  - rup\n" in fm and fm.endswith("---\n\n")
    cfg = config.Config()
    plain = render_note(cfg, "T", datetime(2026, 1, 1), 60, "", "tekst")
    assert plain.startswith("# T")
    cfg.obsidian.vault = str(tmp_path)
    cfg.obsidian.audio_in_vault = True
    note = render_note(cfg, "T", datetime(2026, 1, 1), 60, "## Samenvatting\nx", "tekst",
                       tmp_path / "a.wav")
    assert note.startswith("---\n") and "![[a.wav]]" in note and 'audio: "a.wav"' in note


def test_summary_tasks_format():
    assert "- [ ]" in summary_system(tasks=True)
    assert "Wie | Wat | Deadline" in summary_system(tasks=False)


def test_meeting_into_vault(tmp_path):
    vault = tmp_path / "Vault"
    (vault / ".obsidian").mkdir(parents=True)
    assert is_vault(vault)
    cfg = config.Config()
    cfg.meeting.chunk_seconds = 3600
    cfg.meeting.output_dir = str(tmp_path / "docs")
    cfg.obsidian.vault = str(vault)
    llm = FakeLLM("## Actiepunten\n- [ ] Uitnodiging sturen (Jan, vrijdag)")
    rec = MeetingRecorder(cfg, FakeTranscriber("Ok."), llm, FakeRecorder(tone(2)), None)
    rec.start("Stuurgroep")
    path = rec.stop()
    assert path.parent == vault / "Vergaderingen"
    md = path.read_text(encoding="utf-8")
    assert md.startswith("---\n") and "type: vergadering" in md and "- [ ] Uitnodiging" in md
    assert rec.audio_path.parent == tmp_path / "docs" / "audio" and rec.audio_path.exists()
    assert str(rec.audio_path) in md
    assert "- [ ]" in llm.calls[0][0]


def test_no_audio_without_recording(tmp_path):
    cfg = config.Config()
    rec = MeetingRecorder(cfg, FakeTranscriber(""), None,
                          FakeRecorder(np.zeros(0, np.float32)), None, out_dir=tmp_path)
    rec.start("Leeg")
    rec.stop()
    assert rec.audio_path is None
    assert SAMPLE_RATE == 16000
