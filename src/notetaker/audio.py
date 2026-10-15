"""Audio-opname: microfoon en systeemgeluid (loopback), altijd genormaliseerd naar 16 kHz mono float32."""
from __future__ import annotations

import sys
import threading
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


def to_mono_16k(data: np.ndarray, sr: int) -> np.ndarray:
    data = np.asarray(data, dtype=np.float32)
    if data.ndim == 2:
        data = data.mean(axis=1)
    if sr == SAMPLE_RATE or data.size == 0:
        return data
    ratio = sr / SAMPLE_RATE
    if ratio.is_integer():
        # Boxcar-gemiddelde als eenvoudige low-pass voor decimatie (48k -> 16k)
        n = int(ratio)
        usable = data.size - data.size % n
        return data[:usable].reshape(-1, n).mean(axis=1)
    target = int(round(data.size / ratio))
    x_old = np.linspace(0.0, 1.0, data.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, target, endpoint=False)
    return np.interp(x_new, x_old, data).astype(np.float32)


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


class AudioBuffer:
    """Thread-veilige buffer: de audio-callback schrijft, de verwerker leest leeg."""

    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self.total_samples = 0

    def append(self, chunk: np.ndarray) -> None:
        with self._lock:
            self._chunks.append(chunk)
            self.total_samples += chunk.size

    def drain(self) -> np.ndarray:
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32)


class MicRecorder:
    def __init__(self, device: str = "") -> None:
        self.device = device or None
        self.buffer = AudioBuffer()
        self._stream = None
        self.level = 0.0

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = indata[:, 0].copy()
        self.level = rms(mono)
        self.buffer.append(mono)

    def start(self) -> None:
        import sounddevice as sd

        self.buffer = AudioBuffer()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            device=self.device, callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return self.buffer.drain()


class LoopbackRecorder:
    """Neemt op wat de luidsprekers afspelen: de andere deelnemers van een online vergadering.

    Windows: WASAPI-loopback via PyAudioWPatch (werkt met Teams, Zoom, Meet in de browser, ...).
    Linux/macOS: een monitor- of virtueel apparaat via sounddevice (bv. PulseAudio-monitor, BlackHole).
    """

    def __init__(self, device: str = "") -> None:
        self.device = device
        self.buffer = AudioBuffer()
        self._pa = None
        self._stream = None
        self._sr = SAMPLE_RATE
        self._channels = 1

    def _push(self, data: np.ndarray) -> None:
        if self._channels > 1:
            data = data.reshape(-1, self._channels)
        self.buffer.append(to_mono_16k(data, self._sr))

    def start(self) -> None:
        self.buffer = AudioBuffer()
        if sys.platform == "win32" and not self.device:
            self._start_wasapi()
        else:
            self._start_sounddevice()

    def _start_wasapi(self) -> None:
        import pyaudiowpatch as pyaudio

        self._pa = pyaudio.PyAudio()
        info = self._pa.get_default_wasapi_loopback()
        self._sr = int(info["defaultSampleRate"])
        self._channels = int(info["maxInputChannels"])

        def cb(in_data, frame_count, time_info, status):
            self._push(np.frombuffer(in_data, dtype=np.float32))
            return (None, pyaudio.paContinue)

        self._stream = self._pa.open(
            format=pyaudio.paFloat32, channels=self._channels, rate=self._sr,
            input=True, input_device_index=info["index"],
            frames_per_buffer=1024, stream_callback=cb,
        )
        self._stream.start_stream()

    def _start_sounddevice(self) -> None:
        import sounddevice as sd

        if not self.device:
            raise RuntimeError(
                "Geen loopback-apparaat ingesteld. Zet [meeting] loopback_device "
                "(zie 'notetaker devices')."
            )
        info = sd.query_devices(self.device, "input")
        self._sr = int(info["default_samplerate"])
        self._channels = min(2, int(info["max_input_channels"]))

        def cb(indata, frames, time_info, status):
            self.buffer.append(to_mono_16k(indata.copy(), self._sr))

        self._stream = sd.InputStream(
            samplerate=self._sr, channels=self._channels, dtype="float32",
            device=self.device, callback=cb,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            if self._pa is not None:
                self._stream.stop_stream()
                self._stream.close()
                self._pa.terminate()
                self._pa = None
            else:
                self._stream.stop()
                self._stream.close()
            self._stream = None
        return self.buffer.drain()


def write_wav(target, audio: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    """Schrijft 16-bit mono WAV naar een pad of een binair bestandsobject."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    if isinstance(target, (str, Path)):
        target = str(target)
    with wave.open(target, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def write_opus(path: Path, audio: np.ndarray, sr: int = SAMPLE_RATE, bitrate: int = 32000) -> None:
    """Opus in Ogg via PyAV (zit al bij faster-whisper): ongeveer 15 MB per uur in plaats van 115 MB WAV."""
    import av

    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with av.open(str(path), "w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=sr, layout="mono")
        stream.bit_rate = bitrate
        for start in range(0, pcm.size, sr):
            frame = av.AudioFrame.from_ndarray(pcm[start:start + sr].reshape(1, -1), format="s16",
                                               layout="mono")
            frame.sample_rate = sr
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


def write_audio(path: Path, audio: np.ndarray, fmt: str = "opus", bitrate: int = 32000) -> Path:
    """Schrijft Opus (.ogg) of WAV; valt terug op WAV als Opus niet lukt. Geeft het echte pad terug.

    Het bestand wordt eerst volledig in de tijdelijke map van het systeem geschreven en pas daarna
    naar de doelmap verplaatst, zodat Syncthing geen half gecodeerde opname synchroniseert."""
    import logging
    import shutil
    import tempfile

    path = Path(path)
    with tempfile.TemporaryDirectory(prefix="notetaker-") as tmpdir:
        tmp = Path(tmpdir) / "opname"
        target = path.with_suffix(".wav")
        if fmt == "opus":
            try:
                write_opus(tmp, audio, bitrate=bitrate)
                target = path.with_suffix(".ogg")
            except Exception as e:  # PyAV ontbreekt of encoder niet beschikbaar
                logging.getLogger(__name__).warning("Opus mislukt (%s), opslaan als WAV.", e)
                tmp.unlink(missing_ok=True)
        if not tmp.exists():
            write_wav(tmp, audio)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), str(target))
    return target


def load_audio(path: Path) -> np.ndarray:
    """Leest WAV zelf; andere formaten (mp3, m4a, mp4, ...) via de decoder van faster-whisper."""
    if Path(path).suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as wf:
            sr, ch, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())
        if width != 2:
            raise ValueError("Enkel 16-bit WAV wordt rechtstreeks ondersteund.")
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if ch > 1:
            data = data.reshape(-1, ch)
        return to_mono_16k(data, sr)
    return decode_with_av(path)


def decode_with_av(path: Path) -> np.ndarray:
    """Elk formaat dat FFmpeg kent (ogg/opus, m4a, mp3, webm, ...) naar 16 kHz mono float32.
    Eigen decoder: faster-whisper geeft PyAV een parameter (metadata_errors) die nieuwere
    PyAV-versies niet meer kennen."""
    import av

    chunks = []
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    with av.open(str(path), mode="r") as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError(f"Geen geluidsspoor in {Path(path).name}")
        for frame in container.decode(stream):
            frame.pts = None
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # rest uit de resampler
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def find_split_point(audio: np.ndarray, search_seconds: float = 3.0, window: float = 0.1) -> int:
    """Kiest de stilste plek in de laatste seconden, zodat een chunk niet midden in een woord eindigt."""
    win = int(window * SAMPLE_RATE)
    start = max(0, audio.size - int(search_seconds * SAMPLE_RATE))
    if audio.size - start < 2 * win:
        return audio.size
    best, best_energy = audio.size, float("inf")
    for pos in range(start, audio.size - win, win // 2):
        energy = float(np.mean(np.square(audio[pos:pos + win])))
        if energy < best_energy:
            best, best_energy = pos + win // 2, energy
    return best
