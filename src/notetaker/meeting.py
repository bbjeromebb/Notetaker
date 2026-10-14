"""Vergaderingen opnemen: microfoon (jij) + systeemgeluid (online deelnemers), live transcriptie, verslag."""
from __future__ import annotations

import difflib
import logging
import re
import threading
from datetime import datetime
from pathlib import Path

import numpy as np

from .audio import SAMPLE_RATE, find_split_point, rms, write_audio
from .config import Config
from .llm import LLM, LLMError
from .obsidian import frontmatter
from .stt import Segment, Transcriber, vocab_prompt

log = logging.getLogger(__name__)

SUMMARY_SYSTEM = """Je maakt een verslag van een vergadering op basis van een automatische transcriptie.
De transcriptie bevat fouten van de spraakherkenning: corrigeer duidelijke misverstanden stilzwijgend, verzin niets.
Sprekerlabels zijn beperkt: het microfoonkanaal is de gebruiker, het systeemgeluid bevat alle andere deelnemers samen.
Schrijf in het Nederlands (Vlaamse context), zakelijk en beknopt, in Markdown met exact deze secties:

## Samenvatting
3 tot 6 zinnen.

## Besproken punten
Per onderwerp een kopje of opsommingsteken met de kern.

## Beslissingen
Enkel wat expliciet beslist werd. Geen beslissingen: schrijf "Geen expliciete beslissingen."

## Actiepunten
{actions}

## Open vragen
Wat onbeantwoord bleef.

Geef enkel het verslag terug, zonder inleiding."""

ACTIONS_TABLE = 'Tabel met kolommen Wie | Wat | Deadline. Onbekend = "-".'
ACTIONS_TASKS = ('Elke actie als taak op een eigen regel: "- [ ] Wat (wie, deadline)". '
                 'Laat wie of deadline weg als die onbekend is.')


def summary_system(tasks: bool = False) -> str:
    return SUMMARY_SYSTEM.replace("{actions}", ACTIONS_TASKS if tasks else ACTIONS_TABLE)


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _norm(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower())


def dedupe_echo(mine: list[Segment], others: list[Segment], threshold: float = 0.75,
                slack: float = 2.0) -> list[Segment]:
    """Zonder hoofdtelefoon vangt de microfoon de luidsprekers op: zulke echo's verwijderen we."""
    kept = []
    for m in mine:
        echo = False
        for o in others:
            if o.end + slack < m.start or o.start - slack > m.end:
                continue
            if difflib.SequenceMatcher(None, _norm(m.text), _norm(o.text)).ratio() >= threshold:
                echo = True
                break
        if not echo:
            kept.append(m)
    return kept


def merge_segments(*streams: list[Segment]) -> list[Segment]:
    merged = [s for stream in streams for s in stream]
    merged.sort(key=lambda s: (s.start, s.end))
    # Opeenvolgende segmenten van dezelfde spreker samenvoegen tot leesbare blokken
    out: list[Segment] = []
    for s in merged:
        if out and out[-1].speaker == s.speaker and s.start - out[-1].end < 1.5:
            out[-1] = Segment(out[-1].start, s.end, f"{out[-1].text} {s.text}", s.speaker)
        else:
            out.append(Segment(s.start, s.end, s.text, s.speaker))
    return out


def format_transcript(segments: list[Segment]) -> str:
    return "\n\n".join(f"**[{fmt_ts(s.start)}] {s.speaker}:** {s.text}" for s in segments)


NOTES_SYSTEM = """Je krijgt een deel van een vergadertranscriptie. Noteer beknopt in het Nederlands, als opsomming:
besproken onderwerpen met de kern, beslissingen, actiepunten (wie, wat, deadline) en open vragen.
Verzin niets. Geef enkel de notities terug."""


def split_transcript(transcript: str, max_chars: int) -> list[str]:
    """Knipt op blokgrenzen (lege regels), zodat geen spreekbeurt doormidden gaat."""
    if len(transcript) <= max_chars:
        return [transcript]
    parts, current = [], ""
    for block in transcript.split("\n\n"):
        while len(block) > max_chars:  # extreem lange spreekbeurt
            parts.append(block[:max_chars])
            block = block[max_chars:]
        if current and len(current) + len(block) + 2 > max_chars:
            parts.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        parts.append(current)
    return parts


def summarize(llm: LLM, transcript: str, *, model: str, effort: str, title: str = "",
              chunk_chars: int = 24000, tasks: bool = False) -> str:
    """Kort transcript: één oproep. Lang transcript: eerst notities per deel, dan één verslag."""
    head = f"Titel: {title}\n\n" if title else ""
    parts = split_transcript(transcript, chunk_chars)
    if len(parts) == 1:
        return llm.complete(summary_system(tasks), f"{head}<transcriptie>\n{transcript}\n</transcriptie>",
                            model=model, effort=effort, max_tokens=8000)
    notes = []
    for i, part in enumerate(parts, 1):
        log.info("Deel %d/%d samenvatten", i, len(parts))
        notes.append(f"### Deel {i}\n" + llm.complete(
            NOTES_SYSTEM, f"<transcriptie>\n{part}\n</transcriptie>",
            model=model, effort=effort, max_tokens=4000))
    joined = "\n\n".join(notes)
    return llm.complete(
        summary_system(tasks),
        f"{head}De transcriptie was te lang en is per deel genoteerd, in chronologische volgorde.\n\n"
        f"<transcriptie>\n{joined}\n</transcriptie>",
        model=model, effort=effort, max_tokens=8000)


def render_markdown(title: str, started: datetime, duration: float, summary: str,
                    transcript: str) -> str:
    head = (f"# {title}\n\n"
            f"- Datum: {started:%d/%m/%Y %H:%M}\n"
            f"- Duur: {fmt_ts(duration)}\n\n")
    body = summary.strip() + "\n\n" if summary else ""
    return head + body + "## Transcriptie\n\n" + transcript + "\n"


def render_note(cfg: Config, title: str, started: datetime, duration: float, summary: str,
                transcript: str, audio_path: Path | None = None) -> str:
    """Gewone Markdown, of in een Obsidian-vault: met frontmatter en ingesloten audio."""
    if not cfg.obsidian.vault:
        return render_markdown(title, started, duration, summary, transcript)
    audio = ""
    embed = ""
    if audio_path is not None:
        if cfg.obsidian.audio_in_vault:
            audio, embed = audio_path.name, f"![[{audio_path.name}]]\n\n"
        else:
            audio = str(audio_path)
    fm = frontmatter(title, started, fmt_ts(duration), cfg.obsidian.tags, audio)
    body = summary.strip() + "\n\n" if summary else ""
    return fm + f"# {title}\n\n" + embed + body + "## Transcriptie\n\n" + transcript + "\n"


def safe_filename(text: str) -> str:
    text = re.sub(r"[^\w\- ]", "", text).strip().replace(" ", "-")
    return text[:60] or "vergadering"


class _Channel:
    def __init__(self, recorder, label: str) -> None:
        self.recorder = recorder
        self.label = label
        self.pending = np.zeros(0, dtype=np.float32)
        self.offset = 0.0  # seconden sinds start van de opname
        self.segments: list[Segment] = []
        self.full: list[np.ndarray] = []


class MeetingRecorder:
    def __init__(self, cfg: Config, transcriber: Transcriber, llm: LLM | None,
                 mic, loopback, out_dir: Path | None = None) -> None:
        self.cfg = cfg
        self.transcriber = transcriber
        self.llm = llm
        self.channels = [_Channel(mic, cfg.meeting.mic_label)]
        if loopback is not None:
            self.channels.append(_Channel(loopback, cfg.meeting.others_label))
        self.out_dir = out_dir or cfg.meetings_dir
        self.audio_dir = out_dir or cfg.audio_dir
        self.title = ""
        self.started: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tx_lock = threading.Lock()
        self.live_path: Path | None = None
        self.audio_path: Path | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, title: str = "") -> Path:
        self.started = datetime.now()
        self.title = title or f"Vergadering {self.started:%d-%m-%Y %H:%M}"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{self.started:%Y-%m-%d_%H%M}_{safe_filename(self.title)}"
        self.live_path = self.out_dir / f"{stem}.md"
        ext = ".ogg" if self.cfg.meeting.audio_format == "opus" else ".wav"
        self.audio_path = self.audio_dir / f"{stem}{ext}" if self.cfg.meeting.keep_audio else None
        for ch in self.channels:
            ch.recorder.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self.live_path

    def _loop(self) -> None:
        while not self._stop.wait(self.cfg.meeting.chunk_seconds):
            self._process(final=False)

    def _process(self, final: bool) -> None:
        with self._tx_lock:
            for ch in self.channels:
                new = ch.recorder.buffer.drain()
                if self.cfg.meeting.keep_audio and new.size:
                    ch.full.append(new)
                audio = np.concatenate([ch.pending, new]) if ch.pending.size else new
                if audio.size == 0:
                    continue
                cut = audio.size if final else find_split_point(audio)
                chunk, ch.pending = audio[:cut], audio[cut:]
                self._transcribe_chunk(ch, chunk)
                ch.offset += chunk.size / SAMPLE_RATE
            self._write_live()

    def _transcribe_chunk(self, ch: _Channel, chunk: np.ndarray) -> None:
        if chunk.size < SAMPLE_RATE // 2 or rms(chunk) < 0.002:
            return
        try:
            segs = self.transcriber.transcribe(chunk, prompt=vocab_prompt(self.cfg.dictionary))
        except Exception:  # transcriptie van één chunk mag de opname niet stoppen
            log.exception("Transcriptie van chunk mislukt")
            return
        for s in segs:
            ch.segments.append(Segment(s.start + ch.offset, s.end + ch.offset, s.text, ch.label))

    def segments(self) -> list[Segment]:
        mine = self.channels[0].segments
        if len(self.channels) == 1:
            return merge_segments(mine)
        others = self.channels[1].segments
        mine = dedupe_echo(mine, others, self.cfg.meeting.echo_threshold)
        return merge_segments(mine, others)

    def transcript(self) -> str:
        return format_transcript(self.segments())

    def _write_live(self) -> None:
        if self.live_path is None or self.started is None:
            return
        duration = (datetime.now() - self.started).total_seconds()
        self.live_path.write_text(
            render_note(self.cfg, self.title, self.started, duration, "_Opname loopt..._",
                        self.transcript()),
            encoding="utf-8",
        )

    def stop(self) -> Path:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        for ch in self.channels:
            leftover = ch.recorder.stop()
            if leftover.size:
                ch.recorder.buffer.append(leftover)
        self._process(final=True)
        assert self.started is not None and self.live_path is not None
        duration = (datetime.now() - self.started).total_seconds()
        if self.audio_path is not None and not self._save_audio():
            self.audio_path = None
        transcript = self.transcript()
        summary = ""
        if self.llm is not None and transcript.strip():
            try:
                summary = summarize(self.llm, transcript, model=self.cfg.llm.summary_model,
                                    effort=self.cfg.llm.summary_effort, title=self.title,
                                    chunk_chars=self.cfg.llm.summary_chunk_chars,
                                    tasks=bool(self.cfg.obsidian.vault and self.cfg.obsidian.tasks))
            except LLMError as e:
                log.error("Samenvatting mislukt: %s", e)
                summary = f"_Samenvatting mislukt: {e}. Gebruik later 'notetaker summarize'._"
        self.live_path.write_text(
            render_note(self.cfg, self.title, self.started, duration, summary, transcript,
                        self.audio_path), encoding="utf-8")
        return self.live_path

    def _save_audio(self) -> bool:
        assert self.audio_path is not None
        tracks = [np.concatenate(ch.full) for ch in self.channels if ch.full]
        if not tracks:
            return False
        length = max(t.size for t in tracks)
        mix = np.zeros(length, dtype=np.float32)
        for t in tracks:
            mix[: t.size] += t
        peak = float(np.max(np.abs(mix))) or 1.0
        self.audio_path.parent.mkdir(parents=True, exist_ok=True)
        self.audio_path = write_audio(self.audio_path, mix / max(peak, 1.0),
                                      self.cfg.meeting.audio_format, self.cfg.meeting.audio_bitrate)
        return True


def transcribe_file(cfg: Config, transcriber: Transcriber, audio: np.ndarray,
                    speaker: str = "Spreker") -> list[Segment]:
    segs = transcriber.transcribe(audio, prompt=vocab_prompt(cfg.dictionary))
    return merge_segments([Segment(s.start, s.end, s.text, speaker) for s in segs])

