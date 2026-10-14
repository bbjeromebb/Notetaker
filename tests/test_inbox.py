import json
import os
import time

import numpy as np

from notetaker import config
from notetaker.audio import SAMPLE_RATE, write_wav
from notetaker.inbox import FAILED_SUFFIX, pending, process_folder, read_recording
from notetaker.stt import Segment, SerializedTranscriber, transcribe_chunked

from test_core import FakeLLM, FakeTranscriber, tone


def _old(path, seconds=60):
    t = time.time() - seconds
    os.utime(path, (t, t))


def make_cfg(tmp_path):
    cfg = config.Config()
    cfg.inbox.folder = str(tmp_path / "inbox")
    cfg.meeting.output_dir = str(tmp_path / "docs")
    cfg.obsidian.vault = str(tmp_path / "Vault")
    cfg.obsidian.audio_in_vault = True
    (tmp_path / "inbox").mkdir()
    return cfg


def test_pending_skips_temp_young_and_failed(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for name in ("a.ogg", ".syncthing.b.ogg.tmp", ".c.ogg.notetaker-upload", "d.json", "e.ogg", "f.ogg"):
        (inbox / name).write_bytes(b"x")
    for name in ("a.ogg", "f.ogg"):
        _old(inbox / name)
    (inbox / ("f.ogg" + FAILED_SUFFIX)).write_text("fout")
    assert [p.name for p in pending(inbox)] == ["a.ogg"]
    assert pending(tmp_path / "bestaat-niet") == []


def test_read_recording_sidecar(tmp_path):
    audio = tmp_path / "x.ogg"
    audio.write_bytes(b"x")
    assert read_recording(audio).title == "x" and read_recording(audio).sidecar is None
    (tmp_path / "x.json").write_text(json.dumps(
        {"title": "Werfoverleg", "started": "2026-10-14T09:30:00", "notes": "- foto's nemen"}))
    rec = read_recording(audio)
    assert (rec.title, rec.started.hour, rec.own_notes) == ("Werfoverleg", 9, "- foto's nemen")


def test_process_folder_writes_note_and_empties_inbox(tmp_path):
    cfg = make_cfg(tmp_path)
    inbox = tmp_path / "inbox"
    write_wav(inbox / "opname.wav", tone(3))
    (inbox / "opname.json").write_text(json.dumps(
        {"title": "Werfoverleg", "started": "2026-10-14T09:30:00", "notes": "- foto's nemen"}))
    _old(inbox / "opname.wav")
    llm = FakeLLM("## Samenvatting\nOk.")
    notes = process_folder(cfg, FakeTranscriber("We bekijken de werf."), llm)
    assert len(notes) == 1
    note = notes[0]
    assert note.parent == tmp_path / "Vault" / "Vergaderingen"
    assert note.name == "2026-10-14_0930_Werfoverleg.md"
    md = note.read_text(encoding="utf-8")
    assert "We bekijken de werf." in md and "- foto's nemen" in md and "![[" in md
    assert "<notities_gebruiker>" in llm.calls[0][1]
    assert list(inbox.iterdir()) == []
    assert (note.parent / "2026-10-14_0930_Werfoverleg.wav").exists()


def test_failed_recording_is_marked_and_skipped(tmp_path):
    cfg = make_cfg(tmp_path)
    inbox = tmp_path / "inbox"
    (inbox / "kapot.wav").write_bytes(b"geen audio")
    _old(inbox / "kapot.wav")
    assert process_folder(cfg, FakeTranscriber("x"), None) == []
    assert (inbox / ("kapot.wav" + FAILED_SUFFIX)).exists()
    assert pending(inbox) == []


def test_transcribe_chunked_offsets():
    class Recorder:
        def __init__(self):
            self.sizes = []

        def transcribe(self, audio, prompt="", **kwargs):
            self.sizes.append(audio.size)
            return [Segment(0.5, 1.0, f"stuk{len(self.sizes)}")]

    audio = np.concatenate([tone(25), np.zeros(SAMPLE_RATE, np.float32), tone(20)])
    r = Recorder()
    segs = transcribe_chunked(SerializedTranscriber(r), audio, chunk_seconds=30)
    assert sum(r.sizes) == audio.size and len(r.sizes) == 2
    assert segs[1].start > 25 and segs[1].text == "stuk2"
