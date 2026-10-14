"""Zwevend opnamevenster onderaan het scherm: toestand, sneltoetshint en microfoonniveau.

Het venster neemt nooit de focus over (anders zou de tekst in het venster belanden in plaats
van in de app waar je typt) en laat muisklikken door. Verbergen gebeurt via transparantie,
zodat Windows het venster nooit opnieuw hoeft te activeren.

Tkinter mag enkel vanuit één thread aangesproken worden: `UiThread` bezit de Tk-root en voert
opdrachten van andere threads uit via een wachtrij.
"""
from __future__ import annotations

import math
import queue
import sys
import threading
from collections import deque
from concurrent.futures import Future
from typing import Callable

from .hotkey import display_combo

WIDTH, HEIGHT = 380, 44
BARS = 14
ALPHA = 0.94

STATES = {
    "recording": ("#dc2626", "Luisteren"),
    "handsfree": ("#ea580c", "Hands-free"),
    "command": ("#7c3aed", "Opdracht"),
    "processing": ("#2563eb", "Verwerken..."),
}


def level_to_height(level: float, floor_db: float = -50.0, ceil_db: float = -10.0) -> float:
    """RMS-niveau naar 0..1 op een logaritmische schaal, zoals het oor volume ervaart.
    Gewone spraak (RMS 0,02 tot 0,1) valt zo in het middelste deel van de schaal."""
    if level <= 0:
        return 0.0
    db = 20 * math.log10(level)
    return max(0.0, min(1.0, (db - floor_db) / (ceil_db - floor_db)))


class OverlayModel:
    """Wat het venster toont, zonder Tk: testbaar."""

    def __init__(self, hotkey: str) -> None:
        self.hotkey = display_combo(hotkey)
        self.state = "idle"
        self.mode = "dictate"
        self.levels: deque[float] = deque([0.0] * BARS, maxlen=BARS)

    @property
    def visible(self) -> bool:
        return self.state in STATES

    def set_state(self, state: str, mode: str | None = None) -> None:
        self.state = state
        if mode:
            self.mode = mode
        if state in ("idle", "processing"):
            self.levels.extend([0.0] * BARS)

    def push_level(self, level: float) -> None:
        self.levels.append(level_to_height(level))

    def color_and_text(self) -> tuple[str, str]:
        key = "command" if self.state == "recording" and self.mode == "command" else self.state
        color, text = STATES.get(key, ("#6b7280", ""))
        if self.state == "handsfree":
            text = f"Hands-free - {self.hotkey} stopt"
        elif self.state == "recording":
            text += " - Esc annuleert"
        return color, text


class UiThread:
    def __init__(self) -> None:
        self._q: queue.Queue[tuple[Callable, Future]] = queue.Queue()
        self._ready = threading.Event()
        self.root = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(5)

    def _run(self) -> None:
        import tkinter as tk

        self.root = tk.Tk()
        self.root.withdraw()
        self._ready.set()
        self._pump()
        self.root.mainloop()

    def _pump(self) -> None:
        while True:
            try:
                fn, fut = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                fut.set_result(fn())
            except Exception as e:  # fout teruggeven aan de oproeper
                fut.set_exception(e)
        self.root.after(30, self._pump)

    def call(self, fn: Callable) -> Future:
        fut: Future = Future()
        self._q.put((fn, fut))
        return fut

    def ask_audio_file(self, title: str, initial: str = "") -> str:
        from tkinter import filedialog

        def ask():
            self.root.attributes("-topmost", True)
            return filedialog.askopenfilename(
                parent=self.root, title=title, initialdir=initial or None,
                filetypes=[("Geluidsbestanden", "*.ogg *.opus *.m4a *.mp3 *.wav *.webm *.aac *.flac *.3gp *.mp4"),
                           ("Alle bestanden", "*.*")]) or ""
        return self.call(ask).result()

    def ask_directory(self, title: str, initial: str = "") -> str:
        from tkinter import filedialog

        def ask():
            self.root.attributes("-topmost", True)
            return filedialog.askdirectory(parent=self.root, title=title, initialdir=initial or None,
                                           mustexist=True) or ""
        return self.call(ask).result()


def _make_noactivate(window) -> None:
    """Windows: geen focus, niet in de taakbalk, klikken gaan door het venster."""
    if sys.platform != "win32":
        return
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80, 0x08000000
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)


class Overlay:
    def __init__(self, ui: UiThread, hotkey: str, level_source: Callable[[], float]) -> None:
        self.ui = ui
        self.model = OverlayModel(hotkey)
        self.level_source = level_source
        self.win = None
        self.canvas = None

    def start(self) -> None:
        self.ui.call(self._build).result()

    def _build(self) -> None:
        import tkinter as tk

        root = self.ui.root
        self.win = win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        bg = "#ff00fe" if sys.platform == "win32" else "#f0f0f0"
        win.configure(bg=bg)
        if sys.platform == "win32":
            win.attributes("-transparentcolor", bg)  # afgeronde hoeken
        x = (win.winfo_screenwidth() - WIDTH) // 2
        y = win.winfo_screenheight() - HEIGHT - 72
        win.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        self.canvas = tk.Canvas(win, width=WIDTH, height=HEIGHT, bg=bg, highlightthickness=0)
        self.canvas.pack()
        win.update_idletasks()
        _make_noactivate(win)
        self._tick()

    def set_state(self, state: str, mode: str | None = None) -> None:
        self.ui.call(lambda: self._apply_state(state, mode))

    def _apply_state(self, state: str, mode: str | None) -> None:
        self.model.set_state(state, mode)
        self.win.attributes("-alpha", ALPHA if self.model.visible else 0.0)
        self._draw()

    def _tick(self) -> None:
        if self.model.state in ("recording", "handsfree"):
            self.model.push_level(self.level_source())
            self._draw()
        self.win.after(50, self._tick)

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        if not self.model.visible:
            return
        r = HEIGHT // 2
        fill = "#18181b"
        c.create_oval(0, 0, HEIGHT, HEIGHT, fill=fill, outline=fill)
        c.create_oval(WIDTH - HEIGHT, 0, WIDTH, HEIGHT, fill=fill, outline=fill)
        c.create_rectangle(r, 0, WIDTH - r, HEIGHT, fill=fill, outline=fill)
        color, text = self.model.color_and_text()
        c.create_oval(16, r - 6, 28, r + 6, fill=color, outline=color)
        c.create_text(38, r, text=text, anchor="w", fill="#f4f4f5", font=("Segoe UI", 10))
        if self.model.state == "processing":
            return
        x0, gap, bw = WIDTH - 22 - BARS * 6, 6, 3
        for i, h in enumerate(self.model.levels):
            bh = max(2, h * (HEIGHT - 18))
            x = x0 + i * gap
            c.create_rectangle(x, r - bh / 2, x + bw, r + bh / 2, fill=color, outline=color)
