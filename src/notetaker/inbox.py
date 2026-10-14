"""Opnames van de telefoon verwerken.

De Android-app zet elke vergadering als `<naam>.ogg` (Opus) in een map die Syncthing naar deze pc
synchroniseert, met ernaast `<naam>.json` (titel, starttijd, duur, eigen notities). Deze module houdt
die map in de gaten, transcribeert en vat samen, schrijft de notitie in de vault en haalt de opname
uit de inbox. Syncthing verwijdert ze dan ook op de telefoon: zo ziet de app dat ze verwerkt is.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .audio import SAMPLE_RATE, load_audio
from .config import Config
from .llm import LLM, LLMError
from .meeting import format_transcript, render_note, safe_filename, summarize, transcribe_file
from .obsidian import atomic_write_text
from .stt import Transcriber

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".ogg", ".opus", ".m4a", ".mp3", ".wav", ".webm", ".aac", ".flac"}
FAILED_SUFFIX = ".fout.txt"


@dataclass
class Recording:
    audio: Path
    title: str
    started: datetime
    own_notes: str = ""
    sidecar: Path | None = None


def read_recording(audio: Path) -> Recording:
    """Leest de metadata van de telefoon; zonder sidecar: bestandsnaam en wijzigingstijd."""
    sidecar = audio.with_suffix(".json")
    meta: dict = {}
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("Sidecar %s onleesbaar: %s", sidecar.name, e)
    started = datetime.fromtimestamp(audio.stat().st_mtime)
    if meta.get("started"):
        try:
            started = datetime.fromisoformat(str(meta["started"]).replace("Z", "+00:00"))
            if started.tzinfo is not None:
                started = started.astimezone().replace(tzinfo=None)
        except ValueError:
            pass
    return Recording(audio=audio, title=str(meta.get("title") or audio.stem), started=started,
                     own_notes=str(meta.get("notes") or ""),
                     sidecar=sidecar if sidecar.exists() else None)


def pending(folder: Path, min_age: float = 10.0, now: float | None = None) -> list[Path]:
    """Opnames die klaar zijn: geen tijdelijk bestand, niet eerder mislukt en een tijdje ongewijzigd."""
    if not folder.is_dir():
        return []
    now = time.time() if now is None else now
    out = []
    for p in sorted(folder.iterdir()):
        if p.name.startswith(".") or p.suffix.lower() not in AUDIO_EXTENSIONS:
            continue  # Syncthing en de app schrijven eerst naar verborgen tijdelijke bestanden
        if p.with_name(p.name + FAILED_SUFFIX).exists():
            continue
        try:
            if now - p.stat().st_mtime < min_age:
                continue
        except OSError:
            continue
        out.append(p)
    return out


def process(cfg: Config, transcriber: Transcriber, llm: LLM | None, rec: Recording) -> Path:
    audio = load_audio(rec.audio)
    duration = audio.size / SAMPLE_RATE
    segments = transcribe_file(cfg, transcriber, audio, speaker=cfg.meeting.mic_label)
    transcript = format_transcript(segments)
    summary = ""
    if llm is not None and transcript.strip():
        try:
            summary = summarize(llm, transcript, model=cfg.llm.summary_model,
                                effort=cfg.llm.summary_effort, title=rec.title,
                                chunk_chars=cfg.llm.summary_chunk_chars,
                                tasks=bool(cfg.obsidian.vault and cfg.obsidian.tasks),
                                own_notes=rec.own_notes)
        except LLMError as e:
            log.error("Samenvatting mislukt: %s", e)
            summary = f"_Samenvatting mislukt: {e}. Gebruik later 'notetaker summarize'._"

    stem = f"{rec.started:%Y-%m-%d_%H%M}_{safe_filename(rec.title)}"
    cfg.meetings_dir.mkdir(parents=True, exist_ok=True)
    note = cfg.meetings_dir / f"{stem}.md"
    n = 2
    while note.exists():
        note = cfg.meetings_dir / f"{stem}-{n}.md"
        n += 1

    audio_target = None
    if cfg.meeting.keep_audio:
        cfg.audio_dir.mkdir(parents=True, exist_ok=True)
        audio_target = cfg.audio_dir / (note.stem + rec.audio.suffix.lower())
    atomic_write_text(note, render_note(cfg, rec.title, rec.started, duration, summary, transcript,
                                        audio_target, own_notes=rec.own_notes))
    # Pas na een geslaagde notitie de inbox leegmaken
    if audio_target is not None:
        shutil.move(str(rec.audio), str(audio_target))
    else:
        rec.audio.unlink()
    if rec.sidecar is not None:
        rec.sidecar.unlink(missing_ok=True)
    return note


def process_folder(cfg: Config, transcriber: Transcriber, llm: LLM | None,
                   min_age: float = 10.0) -> list[Path]:
    notes = []
    for audio in pending(Path(cfg.inbox.folder).expanduser(), min_age):
        rec = read_recording(audio)
        log.info("Telefoonopname verwerken: %s", audio.name)
        try:
            notes.append(process(cfg, transcriber, llm, rec))
        except Exception as e:  # één kapotte opname mag de rest niet blokkeren
            log.exception("Verwerken van %s mislukt", audio.name)
            audio.with_name(audio.name + FAILED_SUFFIX).write_text(
                f"{datetime.now():%Y-%m-%d %H:%M} {e}\nVerwijder dit bestand om opnieuw te proberen.\n",
                encoding="utf-8")
    return notes


class InboxWatcher:
    """Controleert de inbox op de achtergrond; `busy` houdt dicteren en verwerken uit elkaar."""

    def __init__(self, cfg: Config, transcriber: Transcriber, llm: LLM | None,
                 on_note=None, busy: threading.Lock | None = None) -> None:
        self.cfg = cfg
        self.transcriber = transcriber
        self.llm = llm
        self.on_note = on_note or (lambda path: None)
        self.busy = busy or threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.cfg.inbox.folder:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("Inbox voor telefoonopnames: %s", self.cfg.inbox.folder)

    def _loop(self) -> None:
        while not self._stop.wait(self.cfg.inbox.interval_seconds):
            if not pending(Path(self.cfg.inbox.folder).expanduser()):
                continue
            with self.busy:
                for note in process_folder(self.cfg, self.transcriber, self.llm):
                    self.on_note(note)

    def stop(self) -> None:
        self._stop.set()
