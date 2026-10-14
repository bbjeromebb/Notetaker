from datetime import datetime

from notetaker import config
from notetaker.audio import write_audio
from notetaker.meeting import LIVE_PLACEHOLDER, MeetingRecorder, extract_own_notes, render_note
from notetaker.obsidian import atomic_write_text

from test_core import FakeLLM, FakeRecorder, FakeTranscriber, tone


def test_atomic_write_leaves_no_temp_file(tmp_path):
    p = tmp_path / "notitie.md"
    atomic_write_text(p, "een")
    atomic_write_text(p, "twee")
    assert p.read_text(encoding="utf-8") == "twee"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["notitie.md"]


def test_audio_written_elsewhere_then_moved(tmp_path):
    out = write_audio(tmp_path / "vault" / "opname.ogg", tone(2), "wav")
    assert out == tmp_path / "vault" / "opname.wav" and out.exists()
    assert [x.name for x in out.parent.iterdir()] == ["opname.wav"]


def test_extract_own_notes_stops_at_managed_heading():
    cfg = config.Config()
    note = render_note(cfg, "T", datetime(2026, 1, 1), 5, LIVE_PLACEHOLDER, "tekst",
                       own_notes="- budget nakijken\n\n## Eigen kop\nblijft staan")
    assert extract_own_notes(note) == "- budget nakijken\n\n## Eigen kop\nblijft staan"
    assert extract_own_notes(render_note(cfg, "T", datetime(2026, 1, 1), 5, "", "x")) == ""
    assert extract_own_notes("# geen sectie") == ""


def test_own_notes_survive_live_rewrites_and_reach_summary(tmp_path):
    cfg = config.Config()
    cfg.meeting.chunk_seconds = 3600
    cfg.obsidian.vault = str(tmp_path / "Vault")
    cfg.meeting.output_dir = str(tmp_path / "docs")
    llm = FakeLLM("## Samenvatting\nKlaar.")
    rec = MeetingRecorder(cfg, FakeTranscriber("Hallo."), llm, FakeRecorder(tone(2)), None)
    path = rec.start("Overleg")
    rec._write_live()
    text = path.read_text(encoding="utf-8")
    assert "## Mijn notities" in text and "_Opname loopt" in text
    # gebruiker typt in Obsidian
    path.write_text(text.replace("## Mijn notities\n\n", "## Mijn notities\n\n- vraag naar planning\n\n"),
                    encoding="utf-8")
    rec._write_live()
    assert "- vraag naar planning" in path.read_text(encoding="utf-8")
    rec.stop()
    final = path.read_text(encoding="utf-8")
    assert "- vraag naar planning" in final and "## Samenvatting\nKlaar." in final
    assert "_Opname loopt" not in final
    assert "<notities_gebruiker>\n- vraag naar planning\n</notities_gebruiker>" in llm.calls[0][1]
