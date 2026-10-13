"""Globale sneltoetsen: vasthouden = praten, dubbeltik = hands-free, extra toets = commandomodus.

`ChordState` bevat de logica zonder I/O (testbaar); `HotkeyListener` koppelt die aan pynput.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

_ALIASES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl", "control": "ctrl",
    "cmd_l": "cmd", "cmd_r": "cmd", "win": "cmd", "super": "cmd", "meta": "cmd",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift_l": "shift", "shift_r": "shift",
    "esc": "escape",
}


def canonical(name: str) -> str:
    name = name.lower()
    return _ALIASES.get(name, name)


def parse_combo(spec: str) -> frozenset[str]:
    return frozenset(canonical(p.strip()) for p in spec.split("+") if p.strip())


@dataclass
class ChordState:
    dictate: frozenset[str]
    command: frozenset[str]
    handsfree_key: str = "space"
    tap_max: float = 0.3
    double_tap_window: float = 0.4

    pressed: set[str] = field(default_factory=set)
    mode: str | None = None  # None, "dictate", "command"
    latched: bool = False
    press_time: float = 0.0
    last_tap: float = -1e9
    _ignore_release: bool = False

    def _satisfied(self, combo: frozenset[str]) -> bool:
        return bool(combo) and combo <= self.pressed

    def press(self, key: str, now: float) -> list[tuple[str, str | None]]:
        key = canonical(key)
        was_dictate = self._satisfied(self.dictate)
        was_command = self._satisfied(self.command)
        self.pressed.add(key)
        events: list[tuple[str, str | None]] = []

        if self.mode is not None and key == "escape":
            self._reset()
            return [("cancel", None)]

        if self.mode is None:
            if self._satisfied(self.command) and not was_command:
                mode = "command"
            elif self._satisfied(self.dictate) and not was_dictate:
                mode = "dictate"
            else:
                return events
            self.mode, self.press_time = mode, now
            events.append(("start", mode))
            if now - self.last_tap <= self.double_tap_window:
                self.latched = True
                self._ignore_release = True
                events.append(("latch", mode))
            return events

        if self.latched:
            if self._satisfied(self.dictate) and not was_dictate:
                self._ignore_release = True
                self._finish()
                events.append(("stop", None))
            return events

        # Vastgehouden opname
        if self.mode == "dictate" and self._satisfied(self.command) and not was_command:
            self.mode = "command"
            events.append(("mode", "command"))
        elif key == canonical(self.handsfree_key):
            self.latched = True
            self._ignore_release = True
            events.append(("latch", self.mode))
        return events

    def release(self, key: str, now: float) -> list[tuple[str, str | None]]:
        key = canonical(key)
        self.pressed.discard(key)
        if self._ignore_release:
            if not self.pressed:
                self._ignore_release = False
            return []
        if self.mode is None or self.latched:
            return []
        combo = self.command if self.mode == "command" else self.dictate
        if not self._satisfied(combo):
            held = now - self.press_time
            self._finish()
            if held < self.tap_max:
                self.last_tap = now
                return [("cancel", None)]
            return [("stop", None)]
        return []

    def _finish(self) -> None:
        self.mode = None
        self.latched = False

    def _reset(self) -> None:
        self._finish()
        self._ignore_release = True


def key_name(key) -> str:
    name = getattr(key, "name", None)
    if name:
        return name
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    vk = getattr(key, "vk", None)
    return f"vk{vk}"


class HotkeyListener:
    """Luistert systeembreed via pynput en roept `handler(event, mode)` op."""

    def __init__(self, state: ChordState, handler: Callable[[str, str | None], None]) -> None:
        self.state = state
        self.handler = handler
        self._lock = threading.Lock()
        self._released = threading.Event()
        self._released.set()
        self._listener = None

    def _dispatch(self, events) -> None:
        for event, mode in events:
            threading.Thread(target=self.handler, args=(event, mode), daemon=True).start()

    def _on_press(self, key) -> None:
        with self._lock:
            events = self.state.press(key_name(key), time.monotonic())
            self._released.clear()
        self._dispatch(events)

    def _on_release(self, key) -> None:
        with self._lock:
            events = self.state.release(key_name(key), time.monotonic())
            if not self.state.pressed:
                self._released.set()
        self._dispatch(events)

    def wait_for_release(self, timeout: float = 1.5) -> None:
        """Wacht tot alle toetsen los zijn, anders wordt Ctrl+V bv. Ctrl+Win+V."""
        self._released.wait(timeout)

    def start(self) -> None:
        from pynput import keyboard

        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
