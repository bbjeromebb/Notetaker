import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from notetaker import config
from notetaker.audio import load_audio, write_audio
from notetaker.llm import OllamaLLM
from notetaker.stt import resolve_device

from test_core import tone


def test_resolve_device_uses_gpu_when_available():
    cfg = config.STTConfig()
    assert resolve_device(cfg, lambda: (True, "ok"))[:2] == ("cuda", "int8_float16")
    assert resolve_device(cfg, lambda: (False, "cudnn ontbreekt"))[:2] == ("cpu", "int8")
    cfg.device, cfg.compute_type = "cuda", "float16"
    assert resolve_device(cfg, lambda: (False, "x"))[:2] == ("cpu", "float16")
    cfg.device = "cpu"
    assert resolve_device(cfg, lambda: pytest.fail("mag GPU niet testen"))[0] == "cpu"


def test_opus_is_small_and_readable(tmp_path):
    pytest.importorskip("av")
    audio = tone(10)
    ogg = write_audio(tmp_path / "opname.wav", audio, "opus")
    wav = write_audio(tmp_path / "opname2", audio, "wav")
    assert ogg.suffix == ".ogg" and wav.suffix == ".wav"
    assert ogg.stat().st_size * 5 < wav.stat().st_size
    import av

    with av.open(str(ogg)) as c:
        assert c.streams.audio[0].codec_context.name == "opus"
        assert abs(float(c.streams.audio[0].duration * c.streams.audio[0].time_base) - 10) < 0.1
    assert load_audio(wav).size == audio.size


def test_opus_failure_falls_back_to_wav(tmp_path, monkeypatch):
    import notetaker.audio as audio_mod

    def boom(*a, **k):
        raise RuntimeError("geen encoder")

    monkeypatch.setattr(audio_mod, "write_opus", boom)
    out = write_audio(tmp_path / "x.ogg", np.zeros(16000, np.float32), "opus")
    assert out.suffix == ".wav" and out.exists() and not (tmp_path / "x.ogg").exists()


def test_ollama_num_gpu_and_models():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            out = json.dumps({"models": [{"name": "gemma3:4b"}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(out)

        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            out = json.dumps({"message": {"content": "ok"}}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(out)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cfg = config.Config().llm
        cfg.ollama_url = f"http://127.0.0.1:{server.server_port}"
        llm = OllamaLLM(cfg)
        assert llm.models() == ["gemma3:4b"]
        llm.complete("s", "p", model="gemma3:4b", effort="low")
        assert "num_gpu" not in seen[-1]["options"]
        cfg.num_gpu = 0
        OllamaLLM(cfg).warmup("gemma3:4b")
        assert seen[-1]["options"]["num_gpu"] == 0
    finally:
        server.shutdown()


def test_ogg_and_m4a_decode_without_faster_whisper(tmp_path):
    import av
    import numpy as np

    from notetaker.audio import SAMPLE_RATE, load_audio, write_opus

    sig = (0.2 * np.sin(2 * np.pi * 440 * np.arange(3 * SAMPLE_RATE) / SAMPLE_RATE)).astype(np.float32)
    ogg = tmp_path / "opname.ogg"
    write_opus(ogg, sig)
    out = load_audio(ogg)
    assert abs(len(out) / SAMPLE_RATE - 3) < 0.2 and out.dtype == np.float32 and np.abs(out).max() > 0.1

    m4a = tmp_path / "telefoon.m4a"  # zoals een dictafoon-app, 44,1 kHz stereo AAC
    with av.open(str(m4a), "w") as c:
        st = c.add_stream("aac", rate=44100, layout="stereo")
        pcm = (np.tile(sig[:SAMPLE_RATE], 3) * 32767).astype(np.int16)
        pcm = np.repeat(pcm, 44100 // SAMPLE_RATE + 1)[: 44100 * 2]
        frame = av.AudioFrame.from_ndarray(np.stack([pcm, pcm]).reshape(1, -1), format="s16", layout="stereo")
        frame.rate = 44100
        for pkt in st.encode(frame):
            c.mux(pkt)
        for pkt in st.encode(None):
            c.mux(pkt)
    out = load_audio(m4a)
    assert 1.5 < len(out) / SAMPLE_RATE < 2.5
