"""Opnames van de telefoon en uit de Obsidian-vault verwerken.

Twee soorten bronnen:

1. Opnames van de Android-app: `<naam>.ogg` met `<naam>.json` (titel, starttijd, eigen notities),
   in een aparte Syncthing-map of in een map van de vault (standaard `Notetaker`). Na verwerking
   verdwijnen ze uit die map; Syncthing verwijdert ze dan ook op de telefoon, zo ziet de app dat
   ze verwerkt zijn.
2. Andere opnames in de vault (Obsidian-opnamefunctie, dictafoon-app, gekopieerde bestanden):
   die blijven staan. Het verslag sluit ze in, en `verwerkt.json` in de app-map onthoudt welke
   al verwerkt zijn. Staat de opname in een notitie, dan leveren titel en tekst van die notitie
   de titel en de eigen notities.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .audio import SAMPLE_RATE, load_audio
from .config import Config, app_dir, default_path
from .llm import LLM, LLMError
from .meeting import extract_own_notes, format_transcript, render_note, safe_filename, summarize, transcribe_file
from .obsidian import atomic_write_text, set_toml_value
from .stt import Transcriber

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".ogg", ".opus", ".m4a", ".mp3", ".wav", ".webm", ".aac", ".flac", ".3gp", ".mp4"}
FAILED_SUFFIX = ".fout.txt"
SKIP_DIRS = {".obsidian", ".trash", ".git", ".stfolder", ".stversions"}
CONFLICT_MARK = ".sync-conflict-"  # Syncthing: "opname.sync-conflict-20261014-093000-ABCDEFG.ogg"
WHOLE_VAULT = ("*", "", ".")


def _skip_name(name: str) -> bool:
    """Verborgen tijdelijke bestanden en conflictkopieën van Syncthing nooit verwerken."""
    return name.startswith(".") or CONFLICT_MARK in name


@dataclass
class Recording:
    audio: Path
    title: str
    started: datetime
    own_notes: str = ""
    sidecar: Path | None = None
    in_place: bool = False  # opname blijft staan (geen Android-inbox)
    source_note: str = ""  # notitie in de vault waarin de opname staat


# --- metadata ------------------------------------------------------------------------

_STAMP_RE = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})[ _T-]?(\d{2})[-_:]?(\d{2})(?:[-_:]?(\d{2}))?")


def started_from_name(path: Path) -> datetime | None:
    """Tijdstip uit de bestandsnaam, bv. 'Recording 20261014093015.webm' of '2026-10-14_0930_x.ogg'."""
    m = _STAMP_RE.search(path.stem)
    if not m:
        return None
    try:
        return datetime(*(int(g) for g in m.groups(default="0")))
    except ValueError:
        return None


def read_recording(audio: Path) -> Recording:
    """Leest de metadata van de telefoon; zonder sidecar: bestandsnaam en wijzigingstijd."""
    sidecar = audio.with_suffix(".json")
    meta: dict = {}
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("Sidecar %s onleesbaar: %s", sidecar.name, e)
    started = started_from_name(audio) or datetime.fromtimestamp(audio.stat().st_mtime)
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


def find_embedding_note(vault: Path, audio: Path, exclude: Path | None = None) -> Path | None:
    """Notitie die de opname insluit of ernaar linkt (zoals bij Obsidians opnamefunctie)."""
    rel = audio.relative_to(vault).as_posix()
    targets = {f"[[{audio.name}", f"[[{rel}", f"({audio.name.replace(' ', '%20')})"}
    for note in _walk(vault, {".md"}, exclude):
        try:
            if note.stat().st_size > 2_000_000:
                continue
            text = note.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(t in text for t in targets):
            return note
    return None


def note_context(note: Path) -> tuple[str, str]:
    """Titel en eigen tekst van een notitie, zonder frontmatter en ingesloten bestanden."""
    text = note.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    own = extract_own_notes(text) or text
    own = re.sub(r"!\[\[[^\]]+\]\]", "", own)
    return note.stem, own.strip()


# --- bestanden zoeken ------------------------------------------------------------------

def _walk(root: Path, extensions: set[str], exclude: Path | None = None):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        here = Path(dirpath)
        if exclude is not None and (here == exclude or exclude in here.parents):
            continue
        for name in filenames:
            if not _skip_name(name) and Path(name).suffix.lower() in extensions:
                yield here / name


def _failed_marker(p: Path) -> Path:
    return p.with_name(p.name + FAILED_SUFFIX)


def _failed(p: Path) -> bool:
    """Eerder mislukt: pas opnieuw proberen als de gebruiker de .fout.txt verwijdert."""
    return _failed_marker(p).exists()


def _is_ready(p: Path, now: float, min_age: float) -> bool:
    try:
        return now - p.stat().st_mtime >= min_age
    except OSError:
        return False


def pending(folder: Path, min_age: float = 10.0, now: float | None = None) -> list[Path]:
    """Opnames die klaar zijn: geen tijdelijk bestand, niet eerder mislukt en een tijdje ongewijzigd."""
    if not folder.is_dir():
        return []
    now = time.time() if now is None else now
    out = []
    for p in sorted(folder.iterdir()):
        if _skip_name(p.name) or p.suffix.lower() not in AUDIO_EXTENSIONS:
            continue  # Syncthing en de app schrijven eerst naar verborgen tijdelijke bestanden
        if _failed(p):
            continue
        if _is_ready(p, now, min_age):
            out.append(p)
    return out


class ProcessedLog:
    """Welke opnames in de vault al verwerkt zijn (die blijven immers staan)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_dir() / "verwerkt.json"
        try:
            self.data: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def done(self, audio: Path) -> bool:
        entry = self.data.get(str(audio))
        try:
            return bool(entry) and abs(entry["mtime"] - audio.stat().st_mtime) < 1
        except OSError:
            return True

    def mark(self, audio: Path, note: str = "", error: str = "") -> None:
        self.data[str(audio)] = {"mtime": audio.stat().st_mtime, "note": note, "error": error,
                                 "at": datetime.now().isoformat(timespec="seconds")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(self.data, ensure_ascii=False, indent=1))


def _folder_names(cfg: Config) -> list[str]:
    folders = cfg.inbox.vault_folders
    if isinstance(folders, str):  # vault_folders = "Notetaker" in plaats van een lijst
        folders = [folders]
    return [str(f) for f in folders]


def vault_audio_folders(cfg: Config) -> list[Path]:
    if not cfg.obsidian.vault:
        return []
    vault = Path(cfg.obsidian.vault).expanduser()
    return [vault if f in WHOLE_VAULT else vault / f for f in _folder_names(cfg)]


def folder_warning(cfg: Config) -> str:
    """Opnamemappen in de verslagenmap worden overgeslagen: daar staan ook Notetakers eigen
    opnames, en een Android-opname zou er na verwerking onder dezelfde naam blijven staan."""
    own = cfg.meetings_dir
    clash = [f for f in vault_audio_folders(cfg) if f == own or own in f.parents]
    if not clash:
        return ""
    return (f"Opnamemap {clash[0].name} valt samen met de verslagenmap {own.name}; opnames daarin "
            "worden niet verwerkt. Kies een andere map voor opnames of voor verslagen.")


def ensure_vault_folder(cfg: Config) -> Path | None:
    """Maakt de eerste opnamemap in de vault aan (standaard `Notetaker`). Syncthing brengt de lege
    map naar de telefoon, zodat ze daar als inboxmap te kiezen is. De vault zelf wordt nooit aangemaakt."""
    if not cfg.obsidian.vault:
        return None
    vault = Path(cfg.obsidian.vault).expanduser()
    names = [f for f in _folder_names(cfg) if f not in WHOLE_VAULT]
    if not vault.is_dir() or not names:
        return None
    folder = vault / names[0]
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("Opnamemap %s niet aangemaakt: %s", folder, e)
        return None
    return folder


def vault_candidates(cfg: Config, log_: ProcessedLog, min_age: float = 10.0,
                     include_old: bool = False, now: float | None = None) -> list[Path]:
    """Nieuwe opnames in de vaultmappen, zonder de eigen verslagenmap en wat al verwerkt is."""
    now = time.time() if now is None else now
    since = 0.0
    if cfg.inbox.vault_since and not include_old:
        try:
            since = datetime.fromisoformat(cfg.inbox.vault_since).timestamp()
        except ValueError:
            since = 0.0
    own = cfg.meetings_dir  # opnames naast de eigen verslagen nooit opnieuw verwerken
    seen, out = set(), []
    for folder in vault_audio_folders(cfg):
        if not folder.is_dir():
            continue
        for p in _walk(folder, AUDIO_EXTENSIONS, exclude=own):
            if p in seen:
                continue
            seen.add(p)
            if not _is_ready(p, now, min_age) or log_.done(p) or _failed(p):
                continue
            if p.with_suffix(".json").exists():
                out.append(p)  # Android-opname: altijd verwerken, ze verdwijnt daarna
            elif p.stat().st_mtime >= since:
                out.append(p)
    return sorted(out)


def ensure_vault_since(cfg: Config, config_path: Path | None = None, reset: bool = False) -> None:
    """Bij de eerste start en bij een andere vault (reset): oudere opnames niet massaal verwerken."""
    if cfg.obsidian.vault and (reset or not cfg.inbox.vault_since):
        cfg.inbox.vault_since = datetime.now().isoformat(timespec="seconds")
        try:
            set_toml_value(config_path or default_path(), "inbox", "vault_since", cfg.inbox.vault_since)
        except (OSError, ValueError) as e:  # geldt dan enkel voor deze sessie
            log.warning("vault_since niet bewaard: %s", e)


def recording_for(cfg: Config, audio: Path) -> Recording:
    """Recording voor een bestand: Android-opname (met .json) of een opname die blijft staan."""
    rec = read_recording(audio)
    if rec.sidecar is not None:
        return rec
    rec.in_place = True
    vault = Path(cfg.obsidian.vault).expanduser() if cfg.obsidian.vault else None
    if vault is not None and vault in audio.parents:
        note = find_embedding_note(vault, audio, exclude=cfg.meetings_dir)
        if note is not None:
            title, own = note_context(note)
            rec.title, rec.own_notes = title, own
            rec.source_note = note.relative_to(vault).with_suffix("").as_posix()
    return rec


# --- verwerken -------------------------------------------------------------------------

def process(cfg: Config, transcriber: Transcriber, llm: LLM | None, rec: Recording) -> Path:
    marker = _failed_marker(rec.audio)
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

    if rec.in_place:
        vault = Path(cfg.obsidian.vault).expanduser() if cfg.obsidian.vault else None
        in_vault = vault is not None and vault in rec.audio.parents
        atomic_write_text(note, render_note(
            cfg, rec.title, rec.started, duration, summary, transcript, own_notes=rec.own_notes,
            vault_audio=rec.audio.relative_to(vault).as_posix() if in_vault else "",
            external_audio="" if in_vault else str(rec.audio), source_note=rec.source_note))
        return note  # een .fout.txt blijft: de opname blijft immers ook staan

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
    marker.unlink(missing_ok=True)  # de opname is weg, dus ook een oude foutmelding
    return note


def _mark_failed(audio: Path, error: Exception) -> None:
    _failed_marker(audio).write_text(
        f"{datetime.now():%Y-%m-%d %H:%M} {error}\nVerwijder dit bestand om opnieuw te proberen.\n",
        encoding="utf-8")


def process_folder(cfg: Config, transcriber: Transcriber, llm: LLM | None,
                   min_age: float = 10.0) -> list[Path]:
    """Aparte Syncthing-map: alles wordt verwerkt en daarna weggehaald."""
    if not cfg.inbox.folder:
        return []
    notes = []
    for audio in pending(Path(cfg.inbox.folder).expanduser(), min_age):
        rec = read_recording(audio)
        log.info("Telefoonopname verwerken: %s", audio.name)
        try:
            notes.append(process(cfg, transcriber, llm, rec))
        except Exception as e:  # één kapotte opname mag de rest niet blokkeren
            log.exception("Verwerken van %s mislukt", audio.name)
            _mark_failed(audio, e)
    return notes


def process_vault(cfg: Config, transcriber: Transcriber, llm: LLM | None, min_age: float = 10.0,
                  include_old: bool = False, log_: ProcessedLog | None = None) -> list[Path]:
    """Opnames in de vaultmappen verwerken."""
    log_ = log_ or ProcessedLog()
    notes = []
    for audio in vault_candidates(cfg, log_, min_age, include_old):
        rec = recording_for(cfg, audio)
        log.info("Opname uit de vault verwerken: %s", audio.name)
        try:
            note = process(cfg, transcriber, llm, rec)
        except Exception as e:
            log.exception("Verwerken van %s mislukt", audio.name)
            if rec.in_place:
                log_.mark(audio, error=str(e))  # niet opnieuw proberen tot het bestand wijzigt
            else:
                _mark_failed(audio, e)
            continue
        if rec.in_place:
            log_.mark(audio, note=str(note))
        notes.append(note)
    return notes


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.abspath(Path(path).expanduser()))


def in_watched_folder(cfg: Config, audio: Path) -> bool:
    """Staat de opname in de aparte inbox of in een opgevolgde vaultmap (niet in de verslagenmap)?"""
    here = _norm(audio)
    own = _norm(cfg.meetings_dir)
    if here.startswith(own + os.sep):
        return False
    folders = [Path(cfg.inbox.folder)] if cfg.inbox.folder else []
    folders += vault_audio_folders(cfg)
    return any(here.startswith(_norm(f) + os.sep) for f in folders)


def process_file(cfg: Config, transcriber: Transcriber, llm: LLM | None, audio: Path,
                 log_: ProcessedLog | None = None) -> Path:
    """Eén gekozen bestand verwerken. Een telefoonopname (met .json) in de inbox of een opnamemap
    wordt verwerkt en weggehaald zoals altijd, ook na een fout; al de rest blijft staan."""
    rec = recording_for(cfg, audio)
    if rec.sidecar is not None and in_watched_folder(cfg, audio):
        return process(cfg, transcriber, llm, rec)
    rec.in_place, rec.sidecar = True, None
    note = process(cfg, transcriber, llm, rec)
    (log_ or ProcessedLog()).mark(audio, note=str(note))
    return note


def has_work(cfg: Config, log_: ProcessedLog) -> bool:
    if cfg.inbox.folder and pending(Path(cfg.inbox.folder).expanduser()):
        return True
    return bool(vault_candidates(cfg, log_))


class InboxWatcher:
    """Controleert inbox en vault op de achtergrond; `busy` houdt verwerkingen uit elkaar."""

    def __init__(self, cfg: Config, transcriber: Transcriber, llm: LLM | None,
                 on_note=None, busy: threading.Lock | None = None,
                 config_path: Path | None = None) -> None:
        self.cfg = cfg
        self.transcriber = transcriber
        self.llm = llm
        self.on_note = on_note or (lambda path: None)
        self.busy = busy or threading.Lock()
        self.config_path = config_path
        self.log = ProcessedLog()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Bij de start en na het kiezen van een vault: maakt de opnamemap aan en start de lus één keer."""
        if not self.cfg.inbox.folder and not self.cfg.obsidian.vault:
            return
        ensure_vault_since(self.cfg, self.config_path)
        ensure_vault_folder(self.cfg)
        warning = folder_warning(self.cfg)
        if warning:
            log.warning(warning)
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("Opnames opvolgen: inbox %s, vaultmappen %s", self.cfg.inbox.folder or "-",
                 [str(p) for p in vault_audio_folders(self.cfg)])

    def run_once(self) -> list[Path]:
        with self.busy:
            notes = process_folder(self.cfg, self.transcriber, self.llm)
            notes += process_vault(self.cfg, self.transcriber, self.llm, log_=self.log)
        for note in notes:
            self.on_note(note)
        return notes

    def _loop(self) -> None:
        while not self._stop.wait(self.cfg.inbox.interval_seconds):
            try:
                if has_work(self.cfg, self.log):
                    self.run_once()
            except Exception:
                log.exception("Opvolgen van opnames mislukt")

    def stop(self) -> None:
        self._stop.set()
