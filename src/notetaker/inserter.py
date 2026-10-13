"""Tekst invoegen in het actieve venster via het klembord, en het klembord nadien herstellen."""
from __future__ import annotations

import time


def _ctrl(char: str) -> None:
    from pynput.keyboard import Controller, Key

    kb = Controller()
    with kb.pressed(Key.ctrl):
        kb.press(char)
        kb.release(char)


def paste_text(text: str, delay: float = 0.12) -> None:
    import pyperclip

    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = None
    pyperclip.copy(text)
    time.sleep(0.03)
    _ctrl("v")
    # Doelapp moet het klembord gelezen hebben voor we het terugzetten
    time.sleep(max(delay, 0.05) + 0.2)
    if previous is not None:
        pyperclip.copy(previous)


def copy_selection(delay: float = 0.15) -> str:
    """Kopieert de huidige selectie; geeft "" terug als er niets geselecteerd was."""
    import pyperclip

    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = ""
    sentinel = "⁣notetaker-leeg⁣"
    pyperclip.copy(sentinel)
    _ctrl("c")
    time.sleep(delay)
    selection = pyperclip.paste()
    pyperclip.copy(previous)
    return "" if selection == sentinel else selection
