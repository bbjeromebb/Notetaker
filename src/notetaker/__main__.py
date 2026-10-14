"""Opdrachtregel: `notetaker run | meeting | transcribe | summarize | vault | inbox | update | about | key | check | set | init-config | devices`."""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from . import config as config_mod
from .obsidian import atomic_write_text


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
    atomic_write_text(out, render_note(cfg, src.stem, started, audio.size / 16000, summary,
                                       transcript, src.resolve()))
    print(f"Geschreven: {out}")


def cmd_summarize(cfg, args) -> None:
    from .llm import build_llm
    from .meeting import extract_own_notes, summarize

    path = Path(args.file)
    text = path.read_text(encoding="utf-8")
    match = re.search(r"## Transcriptie\n(.*)", text, re.S)
    transcript = match.group(1) if match else text
    llm = build_llm(cfg.llm)
    if llm is None:
        sys.exit("Samenvatten vereist een [llm] backend, bv. \"ollama\".")
    summary = summarize(llm, transcript, model=cfg.llm.summary_model, effort=cfg.llm.summary_effort,
                        title=path.stem, chunk_chars=cfg.llm.summary_chunk_chars,
                        tasks=bool(cfg.obsidian.vault and cfg.obsidian.tasks),
                        own_notes=extract_own_notes(text))
    out = path.with_name(path.stem + "_verslag.md")
    atomic_write_text(out, summary + "\n")
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

    from .updater import current_version

    print(f"Versie: {current_version()}")
    print("Spraakherkenning")
    if cfg.stt.backend == "faster-whisper":
        device, compute, reason = resolve_device(cfg.stt)
        print(f"  {cfg.stt.model} op {device} ({compute})")
        if reason:
            print(f"  {reason}")
        if getattr(args, "load", False):
            from .stt import FasterWhisperTranscriber

            print("  model laden (de eerste keer ongeveer 1,6 GB downloaden)...")
            loaded = FasterWhisperTranscriber(cfg.stt)
            print(f"  geladen en getest op: {loaded.device}")
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
    from .inbox import vault_audio_folders

    print("Opnames opvolgen")
    print(f"  aparte inbox: {cfg.inbox.folder or 'geen'}")
    folders = vault_audio_folders(cfg)
    print("  vaultmappen:  " + (", ".join(str(p) for p in folders) if folders else "geen (geen vault)"))
    print("Opslag")
    print(f"  verslagen: {cfg.meetings_dir}")
    print(f"  opnames:   {cfg.audio_dir if cfg.meeting.keep_audio else 'niet bewaard'}"
          f" ({cfg.meeting.audio_format})")


def cmd_inbox(cfg, args) -> None:
    from .inbox import ProcessedLog, process_file, process_folder, process_vault, vault_audio_folders
    from .llm import build_llm
    from .stt import build_transcriber

    if args.folder:
        cfg.inbox.folder = str(Path(args.folder).expanduser().resolve())
    if not cfg.inbox.folder and not cfg.obsidian.vault and not args.file:
        sys.exit("Geen inbox en geen vault ingesteld: notetaker vault <map> of "
                 "notetaker set inbox.folder <Syncthing-map>")
    transcriber, llm = build_transcriber(cfg.stt), build_llm(cfg.llm)
    log_ = ProcessedLog()
    if args.file:
        print(f"Verslag: {process_file(cfg, transcriber, llm, Path(args.file).expanduser().resolve(), log_)}")
        return
    print("Inbox:", cfg.inbox.folder or "-")
    print("Vaultmappen:", ", ".join(str(p) for p in vault_audio_folders(cfg)) or "-")
    while True:
        min_age = 10 if args.watch else 0
        notes = process_folder(cfg, transcriber, llm, min_age=min_age)
        notes += process_vault(cfg, transcriber, llm, min_age=min_age, include_old=args.all, log_=log_)
        for note in notes:
            print(f"Verslag: {note}")
        if not args.watch:
            if not notes:
                print("Geen nieuwe opnames.")
            return
        time.sleep(cfg.inbox.interval_seconds)


def cmd_update(cfg, args) -> None:
    import subprocess

    from . import updater

    if not updater.is_git_checkout():
        sys.exit("Geen Git-installatie: bijwerken kan enkel na installatie via git clone.")
    changes = updater.pending_updates()
    if not changes:
        print("Al de nieuwste versie.")
        return
    print(f"{len(changes)} nieuwe wijziging(en):")
    for line in changes:
        print(f"  - {line}")
    if args.check:
        return
    if sys.platform == "win32":
        script = updater.REPO_ROOT / "update.ps1"
        sys.exit(subprocess.call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                  "-File", str(script)]))
    sys.exit(subprocess.call(["git", "-C", str(updater.REPO_ROOT), "pull", "--ff-only"]))


def cmd_about(cfg, args) -> None:
    from .about import about_text

    print(about_text(cfg))


def cmd_key(cfg, args) -> None:
    import getpass

    from .providers import PROVIDERS, get_key, mask, set_key

    keyed = [p for p in PROVIDERS.values() if p.needs_key]
    if args.action == "list" or not args.provider:
        for p in keyed:
            value = get_key(p.key)
            print(f"  {p.key:<11} {mask(value) if value else 'geen'}")
        return
    if args.provider not in PROVIDERS or not PROVIDERS[args.provider].needs_key:
        sys.exit("Onbekende aanbieder. Kies uit: " + ", ".join(p.key for p in keyed))
    if args.action == "delete":
        set_key(args.provider, "")
        print(f"Sleutel voor {args.provider} gewist.")
        return
    value = getpass.getpass(f"API-sleutel voor {PROVIDERS[args.provider].label} (wordt niet getoond): ")
    set_key(args.provider, value)
    print(f"Bewaard in de kluis: {mask(value)}")


def parse_value(raw: str):
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def cmd_set(cfg, args) -> None:
    from .obsidian import set_toml_value

    section, _, key = args.key.rpartition(".")
    path = args.config or config_mod.default_path()
    set_toml_value(path, section, key, parse_value(args.value))
    config_mod.load(path)  # controleert dat het bestand geldig blijft
    print(f"{args.key} = {args.value}  ({path})")


def cmd_init(cfg, args) -> None:
    print(f"Configuratie: {config_mod.write_example()}")


def cmd_devices(cfg, args) -> None:
    import sounddevice as sd

    print(sd.query_devices())


def run_app() -> None:
    """Start zonder consolevenster (pythonw / snelkoppeling): logt naar notetaker.log."""
    log_path = config_mod.app_dir() / "notetaker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log_path, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        from .app import TrayApp

        TrayApp(config_mod.load(), config_mod.default_path()).run()
    except Exception:
        logging.exception("Notetaker gestopt door een fout")
        raise


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="notetaker", description=__doc__)
    p.add_argument("--config", type=Path, help="pad naar config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"notetaker {__version__}")
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
    c = sub.add_parser("check", help="GPU, Ollama-modellen en opslag controleren")
    c.add_argument("--load", action="store_true",
                   help="Whisper echt laden (downloadt het model de eerste keer en test de GPU)")
    c.set_defaults(fn=cmd_check)
    ib = sub.add_parser("inbox", help="opnames uit de inbox en de vault verwerken")
    ib.add_argument("--folder", help="aparte map die Syncthing vult (anders [inbox] folder)")
    ib.add_argument("--file", help="één geluidsbestand verwerken (blijft staan)")
    ib.add_argument("--all", action="store_true", help="ook opnames van voor de eerste start")
    ib.add_argument("--watch", action="store_true", help="blijven wachten op nieuwe opnames")
    ib.set_defaults(fn=cmd_inbox)
    sub.add_parser("about", help="versie, modellen en paden tonen").set_defaults(fn=cmd_about)
    k = sub.add_parser("key", help="API-sleutels beheren (Windows-kluis)")
    k.add_argument("action", choices=["list", "set", "delete"])
    k.add_argument("provider", nargs="?", help="bv. groq, gemini, openai, openrouter, anthropic")
    k.set_defaults(fn=cmd_key)
    up = sub.add_parser("update", help="nieuwste versie ophalen en installeren")
    up.add_argument("--check", action="store_true", help="enkel tonen wat er nieuw is")
    up.set_defaults(fn=cmd_update)
    st = sub.add_parser("set", help="één instelling wijzigen, bv. 'set llm.num_gpu 0'")
    st.add_argument("key", help="sectie.sleutel")
    st.add_argument("value")
    st.set_defaults(fn=cmd_set)
    sub.add_parser("init-config", help="voorbeeldconfiguratie aanmaken").set_defaults(fn=cmd_init)
    sub.add_parser("devices", help="audioapparaten tonen").set_defaults(fn=cmd_devices)
    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    args.fn(config_mod.load(args.config), args)


if __name__ == "__main__":
    main()
