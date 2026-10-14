import json
import os
import time
from datetime import datetime

from notetaker import config
from notetaker.audio import write_wav
from notetaker.inbox import (InboxWatcher, ProcessedLog, consumed_by_watcher, ensure_vault_folder,
                             ensure_vault_since, folder_warning, pending, process_file, process_vault,
                             started_from_name, vault_audio_folders, vault_candidates, vault_changes)

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
    assert config.load(p).inbox.vault_since_for == str(vault)
    ensure_vault_since(cfg, p)
    assert cfg.inbox.vault_since == first


def test_default_watches_notetaker_folder_and_creates_it(tmp_path):
    cfg, vault, _ = setup(tmp_path)
    assert config.Config().inbox.vault_folders == ["Notetaker", "Opnames"]
    assert ensure_vault_folder(cfg) == vault / "Notetaker" and (vault / "Notetaker").is_dir()
    cfg.inbox.vault_folders = ["*", "Telefoon/Opnames"]
    assert ensure_vault_folder(cfg) == vault / "Telefoon" / "Opnames"
    cfg.obsidian.vault = str(tmp_path / "bestaat-niet")
    assert ensure_vault_folder(cfg) is None and not (tmp_path / "bestaat-niet").exists()


def test_vault_folders_as_single_string(tmp_path):
    cfg, vault, _ = setup(tmp_path)
    cfg.inbox.vault_folders = "Notetaker"
    assert vault_audio_folders(cfg) == [vault / "Notetaker"]


def test_syncthing_conflict_copies_are_skipped(tmp_path):
    cfg, vault, log_ = setup(tmp_path)
    good = vault / "Opnames" / "opname.wav"
    conflict = vault / "Opnames" / "opname.sync-conflict-20261014-093000-ABCDEFG.wav"
    for p in (good, conflict):
        write_wav(p, tone(1))
        _age(p)
    assert vault_candidates(cfg, log_) == [good]
    assert pending(vault / "Opnames") == [good]


def test_recordings_folder_inside_meetings_folder_is_refused(tmp_path):
    cfg, vault, log_ = setup(tmp_path)
    cfg.obsidian.folder = "Notetaker"
    folder = vault / "Notetaker"
    folder.mkdir()
    audio = folder / "2026-10-14_0930_Werfoverleg.wav"
    write_wav(audio, tone(2))
    audio.with_suffix(".json").write_text(json.dumps({"title": "Werfoverleg"}))
    _age(audio)
    # Zelfde map voor verslagen en opnames: eigen opnames zouden opnieuw verwerkt worden
    assert "Notetaker" in folder_warning(cfg)
    assert vault_candidates(cfg, log_) == []
    cfg.obsidian.folder = "Vergaderingen"
    assert folder_warning(cfg) == "" and vault_candidates(cfg, log_) == [audio]


def test_failed_android_recording_waits_for_user(tmp_path):
    cfg, vault, log_ = setup(tmp_path)
    audio = vault / "Opnames" / "2026-10-14_0930_Kapot.wav"
    audio.write_bytes(b"geen geluid")
    audio.with_suffix(".json").write_text(json.dumps({"title": "Kapot"}))
    _age(audio)
    assert process_vault(cfg, FakeTranscriber("x"), None, log_=log_) == []
    marker = audio.with_name(audio.name + ".fout.txt")
    assert marker.exists() and vault_candidates(cfg, log_) == []  # niet elke 15 s opnieuw
    # Met de hand opnieuw verwerken (menu): zoals een gewone telefoonopname, alles verdwijnt
    write_wav(audio, tone(2))
    _age(audio)
    note = process_file(cfg, FakeTranscriber("Hallo."), None, audio, log_)
    assert note.name == "2026-10-14_0930_Kapot.md"
    assert list((vault / "Opnames").iterdir()) == []  # ook de .fout.txt: telefoon toont 'verwerkt'


def test_process_file_keeps_recordings_outside_watched_folders(tmp_path):
    cfg, vault, log_ = setup(tmp_path)
    elders = vault / "Elders"
    elders.mkdir()
    audio = elders / "2026-10-14_0930_Overleg.wav"
    write_wav(audio, tone(2))
    audio.with_suffix(".json").write_text(json.dumps({"title": "Overleg"}))
    process_file(cfg, FakeTranscriber("Hallo."), None, audio, log_)
    assert audio.exists() and audio.with_suffix(".json").exists()


def test_vault_since_survives_unwritable_config_and_resets_for_new_vault(tmp_path):
    cfg, vault, _ = setup(tmp_path)
    p = tmp_path / "config.toml"
    p.write_text('[llm]\nx = """\nmeerdere\n"""\n[inbox]   # telefoon\nvault_folders = ["Notetaker"]\n',
                 encoding="utf-8")
    ensure_vault_since(cfg, p)  # header met commentaar: gewoon bewaard
    assert config.load(p).inbox.vault_since == cfg.inbox.vault_since
    cfg.inbox.vault_since = "2026-01-01T00:00:00"
    ensure_vault_since(cfg, p, reset=True)
    assert cfg.inbox.vault_since > "2026-01-01T00:00:00"
    p.write_text("[inbox\n", encoding="utf-8")  # al ongeldig
    cfg.inbox.vault_since = ""
    ensure_vault_since(cfg, p)  # geen uitzondering, enkel een waarschuwing
    assert cfg.inbox.vault_since


def test_watcher_starts_after_vault_is_chosen(tmp_path):
    cfg = config.Config()
    cfg.meeting.output_dir = str(tmp_path / "docs")
    w = InboxWatcher(cfg, FakeTranscriber("x"), None, config_path=tmp_path / "config.toml")
    w.log = ProcessedLog(tmp_path / "verwerkt.json")
    w.start()
    assert w._thread is None
    vault = tmp_path / "Vault"
    vault.mkdir()
    cfg.obsidian.vault = str(vault)
    w.start()
    thread = w._thread
    assert thread is not None and thread.is_alive() and (vault / "Notetaker").is_dir()
    w.start()
    assert w._thread is thread  # geen tweede lus
    w.stop()


def test_vault_changes_only_reset_for_another_vault(tmp_path):
    vault, other = tmp_path / "Vault", tmp_path / "Ander"
    vault.mkdir(), other.mkdir()
    cfg = config.Config()
    cfg.obsidian.vault = str(vault)
    assert [k for _, k, _ in vault_changes(str(vault) + "/", cfg)] == ["vault"]
    assert [k for _, k, _ in vault_changes("", cfg)] == ["vault", "vault_since_for"]  # oudere config
    # Na 'Niet in Obsidian' en een herstart: vault leeg, maar vault_since hoort nog bij deze vault
    cfg.obsidian.vault, cfg.inbox.vault_since_for = "", str(vault)
    assert [k for _, k, _ in vault_changes(str(vault), cfg)] == ["vault"]
    assert [k for _, k, _ in vault_changes(str(other), cfg)] == ["vault", "vault_since", "vault_since_for"]


def test_cli_vault_switch_resets_vault_since(tmp_path):
    from notetaker.__main__ import main

    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir(), b.mkdir()
    p = tmp_path / "config.toml"
    p.write_text(f'[obsidian]\nvault = "{a.as_posix()}"\n[inbox]\nvault_since = "2026-01-01T00:00:00"\n',
                 encoding="utf-8")
    main(["--config", str(p), "vault", str(a)])
    assert config.load(p).inbox.vault_since == "2026-01-01T00:00:00"
    main(["--config", str(p), "vault", "--off"])
    main(["--config", str(p), "vault", str(a)])  # terug naar dezelfde vault: niets opnieuw
    assert config.load(p).inbox.vault_since == "2026-01-01T00:00:00"
    main(["--config", str(p), "vault", str(b)])
    cfg = config.load(p)
    assert cfg.obsidian.vault == str(b) and cfg.inbox.vault_since > "2026-01-01T00:00:00"
    assert cfg.inbox.vault_since_for == str(b)


def test_manual_processing_in_separate_inbox_is_consumed(tmp_path):
    cfg, vault, log_ = setup(tmp_path)
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    cfg.inbox.folder = str(inbox)
    audio = inbox / "dictafoon.wav"  # zonder .json: de opvolging zou ze ook weghalen
    write_wav(audio, tone(2))
    assert consumed_by_watcher(cfg, audio)
    process_file(cfg, FakeTranscriber("Hallo."), None, audio, log_)
    assert not audio.exists() and pending(inbox, min_age=0) == []


class RacingTranscriber(FakeTranscriber):
    """Een ander proces haalt de opname weg terwijl deze nog getranscribeerd wordt."""

    def __init__(self, text, victim):
        super().__init__(text)
        self.victim = victim

    def transcribe(self, audio, prompt="", **kwargs):
        self.victim.unlink(missing_ok=True)
        return super().transcribe(audio, prompt, **kwargs)


def test_recording_taken_by_another_process_gives_no_duplicate(tmp_path):
    cfg, vault, log_ = setup(tmp_path)
    audio = vault / "Opnames" / "2026-10-14_0930_Werf.wav"
    write_wav(audio, tone(2))
    audio.with_suffix(".json").write_text(json.dumps({"title": "Werf"}))
    _age(audio)
    assert process_vault(cfg, RacingTranscriber("x", audio), None, log_=log_) == []
    assert not (vault / "Vergaderingen").exists() or list((vault / "Vergaderingen").glob("*.md")) == []
    assert not audio.with_name(audio.name + ".fout.txt").exists()
