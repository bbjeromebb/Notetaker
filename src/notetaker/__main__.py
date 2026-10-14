"""Opdrachtregel: `notetaker run | meeting | transcribe | summarize | vault | check | init-config | devices`."""
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

    TrayApp(cfg, args.config).run()


def cmd_meeting(cfg, args) -> None:
    from .audio import LoopbackRecorder, MicRecorder
    from .llm import build_llm
    from .meeting import MeetingRecorder
    from .stt import build_transcriber

    m = cfg.meeting
    loop = None if args.mic_only else LoopbackRecorder(m.loopback_device)
    rec = MeetingRecorder(cfg, build_transcriber(cfg.stt), build_llm(cfg.llm),
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
    from .meeting import format_transcript, render_note, safe_filename, summarize, transcribe_file
    from .stt import build_transcriber

    src = Path(args.file)
    audio = load_audio(src)
    segs = transcribe_file(cfg, build_transcriber(cfg.stt), audio)
    transcript = format_transcript(segs)
    summary = ""
    if args.summarize:
        llm = build_llm(cfg.llm)
        if llm is not None:
            summary = summarize(llm, transcript, model=cfg.llm.summary_model,
                                effort=cfg.llm.summary_effort, title=src.stem,
                                chunk_chars=cfg.llm.summary_chunk_chars,
                                tasks=bool(cfg.obsidian.vault and cfg.obsidian.tasks))
    started = datetime.fromtimestamp(src.stat().st_mtime)
    if args.output:
        out = Path(args.output)
    elif cfg.obsidian.vault:
        cfg.meetings_dir.mkdir(parents=True, exist_ok=True)
        out = cfg.meetings_dir / f"{started:%Y-%m-%d_%H%M}_{safe_filename(src.stem)}.md"
    else:
        out = src.with_suffix(".md")
    out.write_text(render_note(cfg, src.stem, started, audio.size / 16000, summary, transcript,
                               src.resolve()), encoding="utf-8")
    print(f"Geschreven: {out}")


def cmd_summarize(cfg, args) -> None:
    from .llm import build_llm
    from .meeting import summarize

    path = Path(args.file)
    text = path.read_text(encoding="utf-8")
    match = re.search(r"## Transcriptie\n(.*)", text, re.S)
    transcript = match.group(1) if match else text
    llm = build_llm(cfg.llm)
    if llm is None:
        sys.exit("Samenvatten vereist een [llm] backend, bv. \"ollama\".")
    summary = summarize(llm, transcript, model=cfg.llm.summary_model, effort=cfg.llm.summary_effort,
                        title=path.stem, chunk_chars=cfg.llm.summary_chunk_chars,
                        tasks=bool(cfg.obsidian.vault and cfg.obsidian.tasks))
    out = path.with_name(path.stem + "_verslag.md")
    out.write_text(summary + "\n", encoding="utf-8")
    print(f"Geschreven: {out}")


def cmd_vault(cfg, args) -> None:
    from .obsidian import is_vault, known_vaults, set_vault

    path = args.config or config_mod.default_path()
    if args.off:
        set_vault(path, "")
        print("Verslagen gaan weer naar", cfg.default_dir)
        return
    if not args.path:
        print("Huidige vault:", cfg.obsidian.vault or "(geen)")
        for v in known_vaults():
            print("  bekend:", v)
        return
    vault = Path(args.path).expanduser().resolve()
    if not vault.is_dir():
        sys.exit(f"Map bestaat niet: {vault}")
    if not is_vault(vault):
        print(f"Let op: {vault} bevat geen .obsidian-map. Open ze eerst als vault in Obsidian.")
    set_vault(path, str(vault))
    print(f"Verslagen gaan voortaan naar {vault / cfg.obsidian.folder}")


def cmd_check(cfg, args) -> None:
    """Controleert GPU, Ollama-modellen en opslagmap, zonder iets op te nemen."""
    from .llm import LLMError, OllamaLLM
    from .stt import resolve_device

    print("Spraakherkenning")
    if cfg.stt.backend == "faster-whisper":
        device, compute, reason = resolve_device(cfg.stt)
        print(f"  {cfg.stt.model} op {device} ({compute})")
        if reason:
            print(f"  {reason}")
    else:
        print(f"  {cfg.stt.backend}")
    print("Taalmodel")
    if cfg.llm.backend == "ollama":
        try:
            installed = OllamaLLM(cfg.llm).models()
            for model in {cfg.llm.cleanup_model, cfg.llm.summary_model}:
                ok = model in installed or f"{model}:latest" in installed
                print(f"  {model}: {'aanwezig' if ok else 'ontbreekt, voer ollama pull ' + model + ' uit'}")
            gpu = {-1: "Ollama beslist", 0: "enkel CPU"}.get(cfg.llm.num_gpu, f"{cfg.llm.num_gpu} lagen")
            print(f"  GPU-gebruik: {gpu}")
        except LLMError as e:
            print(f"  {e}")
    else:
        print(f"  {cfg.llm.backend}")
    print("Opslag")
    print(f"  verslagen: {cfg.meetings_dir}")
    print(f"  opnames:   {cfg.audio_dir if cfg.meeting.keep_audio else 'niet bewaard'}"
          f" ({cfg.meeting.audio_format})")


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
    v = sub.add_parser("vault", help="Obsidian-vault kiezen voor verslagen")
    v.add_argument("path", nargs="?", help="map van de vault; leeg = huidige en bekende tonen")
    v.add_argument("--off", action="store_true", help="niet meer in Obsidian opslaan")
    v.set_defaults(fn=cmd_vault)
    sub.add_parser("check", help="GPU, Ollama-modellen en opslag controleren").set_defaults(fn=cmd_check)
    sub.add_parser("init-config", help="voorbeeldconfiguratie aanmaken").set_defaults(fn=cmd_init)
    sub.add_parser("devices", help="audioapparaten tonen").set_defaults(fn=cmd_devices)
    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    args.fn(config_mod.load(args.config), args)


if __name__ == "__main__":
    main()
