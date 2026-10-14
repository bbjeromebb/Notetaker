from types import SimpleNamespace

import numpy as np

from notetaker import config
from notetaker.context import ActiveApp
from notetaker.dictation import DictationEngine, Hooks
from notetaker.stt import (FasterWhisperTranscriber, filter_segments, is_hallucination,
                           is_prompt_echo, vocab_prompt)

from test_core import tone

PROMPT = vocab_prompt(["Leiedal", "Kortrijk", "RUP", "MER", "GECORO"])


def seg(text, start=0.0, end=1.0, nsp=0.0, lp=-0.2):
    return SimpleNamespace(text=text, start=start, end=end, no_speech_prob=nsp, avg_logprob=lp)


def test_prompt_echo_detected():
    assert PROMPT == "Woordenlijst: Leiedal, Kortrijk, RUP, MER, GECORO."
    assert is_prompt_echo(" Leiedal, Kortrijk, RUP, MER, GECORO.", PROMPT)
    assert is_prompt_echo("Woordenlijst: Leiedal, Kortrijk.", PROMPT)
    assert not is_prompt_echo("Stuur het RUP naar Leiedal.", PROMPT)
    assert not is_prompt_echo("iets", "")


def test_hallucinations_only_whole_sentences():
    assert is_hallucination("Bedankt voor het kijken!")
    assert is_hallucination("Ondertiteld door de Amara.org gemeenschap")
    assert not is_hallucination("Bedankt voor het kijken naar mijn voorstel over de ondertiteling.")


def test_filter_drops_silence_guess():
    kept = filter_segments([seg("Hallo team."), seg("Ja.", nsp=0.9, lp=-1.5)], PROMPT)
    assert [s.text for s in kept] == ["Hallo team."]


class FakeModel:
    """Doet wat Whisper doet bij een korte opname: met prompt komt de woordenlijst terug."""

    def __init__(self, with_prompt, without_prompt):
        self.answers = {True: with_prompt, False: without_prompt}
        self.calls = []

    def transcribe(self, audio, language=None, initial_prompt=None, vad_filter=True, **kw):
        self.calls.append((initial_prompt, vad_filter))
        return iter([seg(t) for t in self.answers[bool(initial_prompt)]]), None


def make_transcriber(model):
    t = FasterWhisperTranscriber.__new__(FasterWhisperTranscriber)
    t.language, t.device, t.model = "nl", "cpu", model
    return t


def test_echo_triggers_retry_without_prompt():
    model = FakeModel([" Leiedal, Kortrijk, RUP, MER, GECORO."], [" Kom je mee?"])
    out = make_transcriber(model).transcribe(tone(1), PROMPT, vad=False)
    assert [s.text for s in out] == ["Kom je mee?"]
    assert model.calls == [(PROMPT, False), (None, False)]


def test_single_vocab_word_survives_via_retry():
    model = FakeModel([" Kortrijk."], [" Kortrijk."])
    assert [s.text for s in make_transcriber(model).transcribe(tone(1), PROMPT)] == ["Kortrijk."]


def test_no_retry_when_result_is_fine():
    model = FakeModel([" Het RUP is klaar."], ["x"])
    assert [s.text for s in make_transcriber(model).transcribe(tone(1), PROMPT)] == ["Het RUP is klaar."]
    assert len(model.calls) == 1


def test_dictation_disables_vad_and_records_tail():
    seen = {}

    class T:
        def transcribe(self, audio, prompt="", **kwargs):
            seen.update(kwargs)
            return []

    cfg = config.Config()
    cfg.dictation.tail_seconds = 0.0
    hooks = Hooks(start_recording=lambda: None, stop_recording=lambda: tone(1), paste=lambda t: None,
                  copy_selection=lambda: "", active_app=lambda: ActiveApp())
    engine = DictationEngine(cfg, T(), None, hooks)
    engine.handle("start", "dictate")
    engine.handle("stop", None)
    assert seen == {"vad": False}
    assert config.Config().dictation.tail_seconds == 0.25
    assert np.zeros(1).size == 1
