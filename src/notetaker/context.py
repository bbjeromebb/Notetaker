"""Actieve app bepalen (Windows) om de schrijfstijl aan te passen. Leest enkel procesnaam en venstertitel."""
from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass
class ActiveApp:
    process: str = ""
    title: str = ""


def active_app() -> ActiveApp:
    if sys.platform != "win32":
        return ActiveApp()
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ActiveApp()
    length = user32.GetWindowTextLengthW(hwnd)
    title_buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title_buf, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    process = ""
    if handle:
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                process = buf.value.rsplit("\\", 1)[-1].lower()
        finally:
            kernel32.CloseHandle(handle)
    return ActiveApp(process=process, title=title_buf.value)


_TITLE_HINTS = {
    "email": ("outlook", "gmail", "inbox", "postvak"),
    "chat": ("slack", "teams", "whatsapp", "discord", "messenger"),
    "code": ("visual studio code", "pycharm", "github"),
}


def style_for(app: ActiveApp, styles: dict[str, str]) -> str:
    if app.process and app.process in styles:
        return styles[app.process]
    title = app.title.lower()
    for style, hints in _TITLE_HINTS.items():
        if any(h in title for h in hints):
            return style
    return "default"
