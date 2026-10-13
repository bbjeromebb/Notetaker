"""Dicteerflow: sneltoets -> opname -> transcriptie -> opkuis -> plakken in de actieve app."""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import cleanup
from .audio import SAMPLE_RATE, rms
from .config import Config
from .context import ActiveApp, style_for
from .llm import LLM, LLMError
from .stt import Transcriber, vocab_prompt

log = logging.getLogger(__name__)


@dataclass
class Hooks:
    """Afhankelijkheden met I/O, los te vervangen in tests."""

    start_recording: Callable[[], None]
    stop_recording: Callable[[], np.ndarray]
    paste: Callable[[str], None]
    copy_selection: Callable[[], str]
    active_app: Callable[[], ActiveApp]
    wait_for_release: Callable[[], None] = lambda: None
    on_state: Callable[[str], None] = lambda state: None


class DictationEngine:
    def __init__(self, cfg: Config, transcriber: Transcriber, llm: LLM | None, hooks: Hooks,
                 history_path: Path | None = None) -> None:
        self.cfg = cfg
        self.transcriber = transcriber
        self.llm = llm
        self.hooks = hooks
        self.history_path = history_path
        self.mode: str | None = None
        self._app = ActiveApp()
        self._lock = threading.Lock()

    def handle(self, event: str, mode: str | None) -> None:
        with self._lock:
            if event == "start":
                self.mode = mode
                self._app = self.hooks.active_app()
                self.hooks.start_recording()
                self.hooks.on_state("recording")
            elif event == "mode":
                self.mode = mode
            elif event == "latch":
                self.hooks.on_state("handsfree")
            elif event == "cancel":
                if self.mode is not None:
                    self.hooks.stop_recording()
                self.mode = None
                self.hooks.on_state("idle")
            elif event == "stop":
                if self.mode is None:
                    return
                mode, self.mode = self.mode, None
                audio = self.hooks.stop_recording()
                self.hooks.on_state("processing")
                try:
                    self.process(audio, mode)
                finally:
                    self.hooks.on_state("idle")

    def process(self, audio: np.ndarray, mode: str) -> str:
        d = self.cfg.dictation
        if audio.size < d.min_seconds * SAMPLE_RATE or rms(audio) < d.silence_rms:
            log.info("Opname te kort of stil, genegeerd.")
            return ""
        t0 = time.perf_counter()
        segments = self.transcriber.transcribe(audio, prompt=vocab_prompt(self.cfg.dictionary))
        raw = " ".join(s.text for s in segments).strip()
        if not raw:
            return ""
        t1 = time.perf_counter()
        if mode == "command":
            output = self._command(raw)
        else:
            output = cleanup.clean_dictation(
                raw, llm=self.llm, model=self.cfg.llm.cleanup_model,
                effort=self.cfg.llm.cleanup_effort, style=style_for(self._app, self.cfg.styles),
                dictionary=self.cfg.dictionary, replacements=self.cfg.replacements,
                snippets=self.cfg.snippets,
            )
        t2 = time.perf_counter()
        if output:
            self.hooks.wait_for_release()
            self.hooks.paste(output)
        self._log(raw, output, mode, stt=t1 - t0, llm=t2 - t1)
        return output

    def _command(self, instruction: str) -> str:
        if self.llm is None:
            log.warning("Commandomodus vereist een LLM-backend.")
            return ""
        self.hooks.wait_for_release()
        selection = self.hooks.copy_selection()
        try:
            return cleanup.run_command(selection, instruction, llm=self.llm,
                                       model=self.cfg.llm.cleanup_model, effort="medium")
        except LLMError as e:
            log.error("Commando mislukt: %s", e)
            return ""

    def _log(self, raw: str, output: str, mode: str, **timings: float) -> None:
        log.info("[%s] stt %.2fs, llm %.2fs: %s", mode, timings["stt"], timings["llm"], output[:80])
        if self.history_path is None:
            return
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": mode, "app": self._app.process,
                 "raw": raw, "output": output, **{k: round(v, 3) for k, v in timings.items()}}
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
