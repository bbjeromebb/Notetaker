"""Echte test van korte dictees: Whisper large-v3-turbo + Ollama gemma3:4b + synthetische Vlaamse spraak.

Vergelijkt de oude werkwijze (volledige woordenlijst in Whisper-prompt en in de opkuisopdracht,
stiltedetectie aan, geen filters) met de huidige code. Draait in GitHub Actions, niet in pytest.
Gebruik: python tests/real/short_dictation.py <map-met-piper-stem> <uitvoermap>
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

from notetaker import cleanup, config
from notetaker.audio import SAMPLE_RATE, load_audio
from notetaker.context import ActiveApp
from notetaker.dictation import DictationEngine, Hooks
from notetaker.llm import OllamaLLM
from notetaker.stt import FasterWhisperTranscriber, vocab_prompt

# (wat ingesproken wordt, wat er verwacht wordt)
SENTENCES = [
    ("Kom je mee?", "Kom je mee?"),
    ("Ja, prima.", "Ja, prima."),
    ("Nee.", "Nee."),
    ("Dank je wel.", "Dank je wel."),
    ("Oké, tot straks.", "Oké, tot straks."),
    ("Bel me morgen terug.", "Bel me morgen terug."),
    ("Ik ben er over vijf minuten.", "Ik ben er over vijf minuten."),
    ("Het RUP is klaar.", "Het RUP is klaar."),
    ("Stuur dat naar Leiedal.", "Stuur dat naar Leiedal."),
    ("Morgen om negen uur in Kortrijk.", "Morgen om negen uur in Kortrijk."),
    ("Kan je het verslag van de stuurgroep nog eens nalezen voor vrijdag?",
     "Kan je het verslag van de stuurgroep nog eens nalezen voor vrijdag?"),
    ("We bespreken volgende week de effecten van het MER op de planning van het project.",
     "We bespreken volgende week de effecten van het MER op de planning van het project."),
    ("Euh, kan je mij morgen, euh, even bellen?", "Kan je mij morgen even bellen?"),
    ("We vertrekken om drie uur, nee wacht, om vier uur.", "We vertrekken om vier uur."),
]


def synthesize(voice_dir: Path, text: str, path: Path) -> None:
    """Piper (Vlaamse stem) als die werkt, anders espeak-ng."""
    onnx = next(voice_dir.glob("*.onnx"), None)
    if onnx is not None:
        try:
            from piper import PiperVoice

            voice = PiperVoice.load(str(onnx))
            with wave.open(str(path), "wb") as wf:
                if hasattr(voice, "synthesize_wav"):
                    voice.synthesize_wav(text, wf)
                else:
                    voice.synthesize(text, wf)
            return
        except Exception as e:  # val terug op espeak-ng
            print(f"piper mislukt ({e}), espeak-ng", file=sys.stderr)
    subprocess.run(["espeak-ng", "-v", "nl", "-s", "150", "-w", str(path), text], check=True)


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def score(expected: str, got: str) -> float:
    """Op letters, zodat 'Dankjewel' en 'Dank je wel' of '9' en 'negen' niet als fout tellen."""
    def norm(t: str) -> str:
        t = t.lower().replace("9", "negen").replace("5", "vijf")
        return "".join(re.findall(r"\w", t))
    return difflib.SequenceMatcher(None, norm(expected), norm(got)).ratio()


def leaked(expected: str, got: str, terms: list[str]) -> bool:
    exp = set(words(expected))
    extra = [t for t in terms if t.lower() in words(got) and t.lower() not in exp]
    return len(extra) >= 2


def old_pipeline(t: FasterWhisperTranscriber, llm: OllamaLLM, cfg, audio: np.ndarray) -> tuple[str, str]:
    """Gedrag van vóór de fixes, nagebouwd met dezelfde modellen."""
    prompt = vocab_prompt(cfg.dictionary)
    segs, _ = t.model.transcribe(audio, language="nl", initial_prompt=prompt, vad_filter=True,
                                 beam_size=5, condition_on_previous_text=False)
    raw = " ".join(s.text.strip() for s in segs).strip()
    if not raw:
        return raw, ""
    old_prompt = "\n\n".join([
        f"Stijl: {cleanup.STYLE_INSTRUCTIONS['default']}",
        "Schrijf deze termen exact zo: " + ", ".join(cfg.dictionary),
        f"<transcript>\n{raw}\n</transcript>",
    ])
    out = llm.complete(cleanup.CLEANUP_SYSTEM, old_prompt, model=cfg.llm.cleanup_model,
                       effort="low", max_tokens=400)
    return raw, cleanup.apply_dictionary(out, cfg.dictionary, cfg.replacements)


def new_pipeline(engine: DictationEngine, pasted: list, audio: np.ndarray) -> tuple[str, str]:
    pasted.clear()
    engine.process(audio, "dictate")
    raw = engine.last_raw if hasattr(engine, "last_raw") else ""
    return raw, pasted[0] if pasted else ""


def main() -> None:
    voice_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = config.load(config.EXAMPLE)
    cfg.stt.device = "cpu"
    print("Woordenlijst:", cfg.dictionary, "| prompt:", vocab_prompt(cfg.dictionary))

    t0 = time.time()
    transcriber = FasterWhisperTranscriber(cfg.stt)
    print(f"Whisper {cfg.stt.model} geladen op {transcriber.device} in {time.time() - t0:.0f} s")
    llm = OllamaLLM(cfg.llm)
    llm.warmup(cfg.llm.cleanup_model)

    pasted: list[str] = []
    engine = DictationEngine(cfg, transcriber, llm, Hooks(
        start_recording=lambda: None, stop_recording=lambda: np.zeros(0, np.float32),
        paste=pasted.append, copy_selection=lambda: "", active_app=lambda: ActiveApp()))
    # Ruwe tekst bijhouden voor het rapport
    original = engine.transcriber.transcribe

    def spy(audio, prompt="", **kw):
        segs = original(audio, prompt, **kw)
        engine.last_raw = " ".join(s.text for s in segs)
        return segs

    engine.transcriber.transcribe = spy

    rows, results = [], []
    for i, (sentence, expected) in enumerate(SENTENCES, 1):
        wav = out_dir / f"zin{i:02d}.wav"
        synthesize(voice_dir, sentence, wav)
        audio = load_audio(wav)
        # Zoals bij een sneltoets: een fractie stilte voor en na
        audio = np.concatenate([np.zeros(int(0.15 * SAMPLE_RATE), np.float32), audio,
                                np.zeros(int(0.25 * SAMPLE_RATE), np.float32)])
        engine.last_raw = ""
        old_raw, old_out = old_pipeline(transcriber, llm, cfg, audio)
        new_raw, new_out = new_pipeline(engine, pasted, audio)
        r = {
            "zin": sentence, "duur_s": round(audio.size / SAMPLE_RATE, 1),
            "oud_whisper": old_raw, "oud_resultaat": old_out,
            "oud_score": round(score(expected, old_out), 2), "oud_lijst": leaked(sentence, old_out, cfg.dictionary),
            "nieuw_whisper": new_raw, "nieuw_resultaat": new_out,
            "nieuw_score": round(score(expected, new_out), 2),
            "nieuw_lijst": leaked(sentence, new_out, cfg.dictionary),
        }
        results.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        cell = lambda t: (t or "(leeg)").replace("\n", " / ")  # noqa: E731
        rows.append(f"| {sentence} | {r['duur_s']} | {cell(old_out)} | {r['oud_score']} | "
                    f"{cell(new_out)} | {r['nieuw_score']} |")

    ok_old = sum(r["oud_score"] >= 0.85 and not r["oud_lijst"] for r in results)
    ok_new = sum(r["nieuw_score"] >= 0.85 and not r["nieuw_lijst"] for r in results)
    leak_old = sum(r["oud_lijst"] for r in results)
    leak_new = sum(r["nieuw_lijst"] for r in results)
    report = "\n".join([
        "## Korte dictees: echte modellen",
        f"Whisper {cfg.stt.model} (cpu) + {cfg.llm.cleanup_model} via Ollama, woordenlijst {cfg.dictionary}",
        "",
        f"- Oud: {ok_old}/{len(results)} correct, woordenlijst opgedoken in {leak_old} zinnen",
        f"- Nieuw: {ok_new}/{len(results)} correct, woordenlijst opgedoken in {leak_new} zinnen",
        "",
        "Correct = minstens 85 % gelijk aan de verwachte tekst (op letters) en geen woordenlijst.",
        "",
        "| Zin | Duur (s) | Oud resultaat | Score | Nieuw resultaat | Score |",
        "|---|---|---|---|---|---|",
        *rows,
    ])
    print(report)
    (out_dir / "rapport.md").write_text(report, encoding="utf-8")
    (out_dir / "resultaten.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    if leak_new:
        sys.exit(f"Woordenlijst dook nog op in {leak_new} zinnen")
    if ok_new < ok_old:
        sys.exit(f"Nieuwe werkwijze scoort slechter ({ok_new} tegen {ok_old})")


if __name__ == "__main__":
    main()
