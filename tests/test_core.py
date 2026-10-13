import numpy as np

from notetaker import cleanup, config
from notetaker.audio import AudioBuffer, SAMPLE_RATE, find_split_point, load_audio, to_mono_16k, write_wav
from notetaker.context import ActiveApp, style_for
from notetaker.dictation import DictationEngine, Hooks
from notetaker.hotkey import ChordState, parse_combo
from notetaker.llm import LLMError
from notetaker.meeting import MeetingRecorder, dedupe_echo, format_transcript, merge_segments
from notetaker.stt import Segment, vocab_prompt


class FakeLLM:
    def __init__(self, reply="Opgeschoonde tekst.", fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    def complete(self, system, prompt, *, model, effort, max_tokens=16000):
        self.calls.append((system, prompt, model, effort))
        if self.fail:
            raise LLMError("offline")
        return self.reply


class FakeTranscriber:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio, prompt=""):
        return [Segment(0.0, audio.size / SAMPLE_RATE, self.text)] if self.text else []


def tone(seconds, amp=0.1):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


# --- cleanup ---------------------------------------------------------------

def test_basic_cleanup_removes_fillers_and_punctuates():
    assert cleanup.basic_cleanup("euh ik denk dat dat dat ehm goed is") == "Ik denk dat goed is."


def test_snippet_whole_utterance_skips_llm():
    llm = FakeLLM()
    out = cleanup.clean_dictation("Mijn handtekening.", llm=llm, model="m", effort="low",
                                  style="default", dictionary=[], replacements={},
                                  snippets={"mijn handtekening": "Groeten,\nJan"})
    assert out == "Groeten,\nJan" and llm.calls == []


def test_snippet_inline_is_protected_and_restored():
    text, mapping, whole = cleanup.protect_snippets("stuur mijn agenda link door",
                                                    {"mijn agenda link": "https://cal.example/jan"})
    assert not whole and "⟦S0⟧" in text
    assert cleanup.restore_snippets("Stuur ⟦S0⟧ door.", mapping) == "Stuur https://cal.example/jan door."


def test_dictionary_and_replacements():
    out = cleanup.apply_dictionary("de lei dal en het rup", ["RUP"], {"lei dal": "Leiedal"})
    assert out == "de Leiedal en het RUP"


def test_llm_failure_falls_back_to_rules():
    out = cleanup.clean_dictation("euh hallo allemaal", llm=FakeLLM(fail=True), model="m",
                                  effort="low", style="chat", dictionary=[], replacements={},
                                  snippets={})
    assert out == "Hallo allemaal."


def test_cleanup_prompt_wraps_transcript_and_style():
    prompt = cleanup.build_cleanup_prompt("schrijf een mail", "email", ["Leiedal"])
    assert "<transcript>\nschrijf een mail\n</transcript>" in prompt and "Leiedal" in prompt


# --- hotkeys ---------------------------------------------------------------

def make_state():
    return ChordState(parse_combo("ctrl+cmd"), parse_combo("ctrl+cmd+alt"))


def test_hold_to_talk():
    s = make_state()
    assert s.press("ctrl_l", 0.0) == []
    assert s.press("cmd", 0.05) == [("start", "dictate")]
    assert s.release("cmd", 2.0) == [("stop", None)]
    assert s.release("ctrl_l", 2.01) == []


def test_double_tap_latches_handsfree_until_next_press():
    s = make_state()
    s.press("ctrl", 0.0); s.press("cmd", 0.01)
    assert s.release("cmd", 0.1) == [("cancel", None)]
    s.release("ctrl", 0.11)
    s.press("ctrl", 0.2)
    assert s.press("cmd", 0.21) == [("start", "dictate"), ("latch", "dictate")]
    assert s.release("cmd", 0.3) == [] and s.release("ctrl", 0.3) == []
    s.press("ctrl", 5.0)
    assert s.press("cmd", 5.01) == [("stop", None)]
    assert s.release("cmd", 5.1) == [] and s.release("ctrl", 5.1) == []
    assert s.mode is None


def test_space_latches_and_escape_cancels():
    s = make_state()
    s.press("ctrl", 0.0); s.press("cmd", 0.0)
    assert s.press("space", 0.5) == [("latch", "dictate")]
    for k in ("space", "cmd", "ctrl"):
        assert s.release(k, 1.0) == []
    assert s.press("esc", 3.0) == [("cancel", None)]
    assert s.mode is None


def test_command_mode_upgrade():
    s = make_state()
    s.press("ctrl", 0.0)
    assert s.press("cmd", 0.0) == [("start", "dictate")]
    assert s.press("alt_l", 0.05) == [("mode", "command")]
    assert s.release("alt_l", 1.0) == [("stop", None)]


# --- audio -----------------------------------------------------------------

def test_resample_and_buffer(tmp_path):
    stereo48 = np.zeros((48000, 2), dtype=np.float32)
    assert to_mono_16k(stereo48, 48000).size == 16000
    assert to_mono_16k(np.zeros(44100, dtype=np.float32), 44100).size == 16000
    buf = AudioBuffer()
    buf.append(np.ones(10, dtype=np.float32)); buf.append(np.ones(5, dtype=np.float32))
    assert buf.drain().size == 15 and buf.drain().size == 0
    audio = tone(1.0)
    write_wav(tmp_path / "a.wav", audio)
    assert np.allclose(load_audio(tmp_path / "a.wav"), audio, atol=1e-3)


def test_split_point_lands_in_silence():
    audio = np.concatenate([tone(5), np.zeros(SAMPLE_RATE // 2, np.float32), tone(1)])
    cut = find_split_point(audio)
    assert 5 * SAMPLE_RATE <= cut <= 5.5 * SAMPLE_RATE


# --- context / config --------------------------------------------------------

def test_style_for():
    assert style_for(ActiveApp("outlook.exe", ""), {"outlook.exe": "email"}) == "email"
    assert style_for(ActiveApp("chrome.exe", "Inbox - Gmail"), {}) == "email"
    assert style_for(ActiveApp(), {}) == "default"


def test_config_load(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('dictionary=["X"]\n[llm]\nbackend="none"\n[snippets]\n"a"="b"\n', encoding="utf-8")
    cfg = config.load(p)
    assert cfg.dictionary == ["X"] and cfg.llm.backend == "none" and cfg.snippets == {"a": "b"}
    assert cfg.stt.language == "nl"
    assert config.load(config.EXAMPLE).dictation.hotkey == "ctrl+cmd"


def test_vocab_prompt_truncates():
    assert vocab_prompt([]) == ""
    assert len(vocab_prompt(["woord"] * 500)) < 650


# --- dictation pipeline ------------------------------------------------------

def make_engine(text, llm=None, selection=""):
    pasted, audio = [], tone(1.0)
    hooks = Hooks(start_recording=lambda: None, stop_recording=lambda: audio,
                  paste=pasted.append, copy_selection=lambda: selection,
                  active_app=lambda: ActiveApp("slack.exe", ""))
    cfg = config.Config(styles={"slack.exe": "chat"})
    return DictationEngine(cfg, FakeTranscriber(text), llm, hooks), pasted


def test_dictation_flow_pastes_cleaned_text():
    llm = FakeLLM("Hallo team.")
    engine, pasted = make_engine("euh hallo team", llm)
    engine.handle("start", "dictate")
    engine.handle("stop", None)
    assert pasted == ["Hallo team."]
    assert "Chatbericht" in llm.calls[0][1]


def test_dictation_ignores_silence_and_cancel():
    engine, pasted = make_engine("iets")
    assert engine.process(np.zeros(SAMPLE_RATE, np.float32), "dictate") == ""
    engine.handle("start", "dictate"); engine.handle("cancel", None)
    engine.handle("stop", None)
    assert pasted == []


def test_command_mode_uses_selection():
    llm = FakeLLM("Korte versie.")
    engine, pasted = make_engine("maak dit korter", llm, selection="Een hele lange tekst.")
    engine.handle("start", "command"); engine.handle("stop", None)
    assert pasted == ["Korte versie."]
    assert "<selectie>\nEen hele lange tekst.\n</selectie>" in llm.calls[0][1]


# --- meetings ----------------------------------------------------------------

def test_echo_dedupe_and_merge():
    mine = [Segment(0, 2, "Goeiemorgen allemaal", "Ik"), Segment(5, 7, "Dat is goed voor mij.", "Ik")]
    others = [Segment(4.8, 6.9, "dat is goed voor mij", "Anderen"), Segment(2.5, 4, "Hallo", "Anderen")]
    kept = dedupe_echo(mine, others)
    assert [s.text for s in kept] == ["Goeiemorgen allemaal"]
    merged = merge_segments(kept, others)
    assert [s.speaker for s in merged] == ["Ik", "Anderen"]
    assert "Hallo dat is goed voor mij" in merged[1].text
    assert format_transcript(merged).startswith("**[00:00:00] Ik:** Goeiemorgen")


class FakeRecorder:
    def __init__(self, audio):
        self.audio, self.buffer = audio, AudioBuffer()

    def start(self):
        self.buffer.append(self.audio)

    def stop(self):
        return np.zeros(0, np.float32)


def test_meeting_recorder_end_to_end(tmp_path):
    cfg = config.Config()
    cfg.meeting.chunk_seconds = 3600
    llm = FakeLLM("## Samenvatting\nKort overleg.")
    rec = MeetingRecorder(cfg, FakeTranscriber("We plannen de workshop."), llm,
                          FakeRecorder(tone(3)), FakeRecorder(tone(3)), out_dir=tmp_path)
    rec.start("Projectoverleg")
    path = rec.stop()
    md = path.read_text(encoding="utf-8")
    assert "# Projectoverleg" in md and "## Samenvatting" in md
    assert "## Transcriptie" in md and "We plannen de workshop." in md
    assert path.with_suffix(".wav").exists()
    assert "<transcriptie>" in llm.calls[0][1]
