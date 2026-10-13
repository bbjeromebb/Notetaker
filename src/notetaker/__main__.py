"""Opdrachtregel: `notetaker run | meeting | transcribe | summarize | init-config | devices`."""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config as config_mod


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")


def cmd_run(cfg, args) -> None:
    from .app import TrayApp

    TrayApp(cfg).run()


def cmd_meeting(cfg, args) -> None:
    from .audio import LoopbackRecorder, MicRecorder
    from .llm import build_llm
    from .meeting import MeetingRecorder
    from .stt import build_transcriber

    m = cfg.meeting
    loop = None if args.mic_only else LoopbackRecorder(m.loopback_device)
    rec = MeetingRecorder(cfg, build_transcriber(cfg.stt), build_llm(cfg.llm.backend),
                          MicRecorder(m.mic_device), loop)
    path = rec.start(args.title or "")
    print(f"Opname loopt, live transcriptie in {path}. Ctrl+C om te stoppen.")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStoppen en verslag maken...")
    print(f"Klaar: {rec.stop()}")


def cmd_transcribe(cfg, args) -> None:
    from .audio import load_audio
    from .llm import build_llm
    from .meeting import format_transcript, render_markdown, summarize, transcribe_file
    from .stt import build_transcriber

    src = Path(args.file)
    audio = load_audio(src)
    segs = transcribe_file(cfg, build_transcriber(cfg.stt), audio)
    transcript = format_transcript(segs)
    summary = ""
    if args.summarize:
        llm = build_llm(cfg.llm.backend)
        if llm is not None:
            summary = summarize(llm, transcript, model=cfg.llm.summary_model,
                                effort=cfg.llm.summary_effort, title=src.stem)
    out = Path(args.output) if args.output else src.with_suffix(".md")
    started = datetime.fromtimestamp(src.stat().st_mtime)
    out.write_text(render_markdown(src.stem, started, audio.size / 16000, summary, transcript),
                   encoding="utf-8")
    print(f"Geschreven: {out}")


def cmd_summarize(cfg, args) -> None:
    from .llm import build_llm
    from .meeting import summarize

    path = Path(args.file)
    text = path.read_text(encoding="utf-8")
    match = re.search(r"## Transcriptie\n(.*)", text, re.S)
    transcript = match.group(1) if match else text
    llm = build_llm(cfg.llm.backend)
    if llm is None:
        sys.exit("Samenvatten vereist [llm] backend = \"anthropic\".")
    summary = summarize(llm, transcript, model=cfg.llm.summary_model, effort=cfg.llm.summary_effort,
                        title=path.stem)
    out = path.with_name(path.stem + "_verslag.md")
    out.write_text(summary + "\n", encoding="utf-8")
    print(f"Geschreven: {out}")


def cmd_init(cfg, args) -> None:
    print(f"Configuratie: {config_mod.write_example()}")


def cmd_devices(cfg, args) -> None:
    import sounddevice as sd

    print(sd.query_devices())


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="notetaker", description=__doc__)
    p.add_argument("--config", type=Path, help="pad naar config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="achtergrondapp: dicteren + vergaderingen via systeemvak").set_defaults(fn=cmd_run)
    m = sub.add_parser("meeting", help="vergadering opnemen tot Ctrl+C")
    m.add_argument("--title")
    m.add_argument("--mic-only", action="store_true", help="geen systeemgeluid opnemen")
    m.set_defaults(fn=cmd_meeting)
    t = sub.add_parser("transcribe", help="audio- of videobestand transcriberen")
    t.add_argument("file")
    t.add_argument("--summarize", action="store_true")
    t.add_argument("-o", "--output")
    t.set_defaults(fn=cmd_transcribe)
    s = sub.add_parser("summarize", help="verslag maken van een bestaande transcriptie")
    s.add_argument("file")
    s.set_defaults(fn=cmd_summarize)
    sub.add_parser("init-config", help="voorbeeldconfiguratie aanmaken").set_defaults(fn=cmd_init)
    sub.add_parser("devices", help="audioapparaten tonen").set_defaults(fn=cmd_devices)
    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    args.fn(config_mod.load(args.config), args)


if __name__ == "__main__":
    main()
