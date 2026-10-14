"""Achtergrondapp met systeemvak-icoon: dicteren via sneltoets, vergaderingen starten en stoppen via het menu."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from .audio import LoopbackRecorder, MicRecorder
from .config import Config, app_dir, default_path
from .context import active_app
from .dictation import DictationEngine, Hooks
from .hotkey import ChordState, HotkeyListener, display_combo, parse_combo
from .inserter import copy_selection, paste_text
from .llm import build_llm
from .meeting import MeetingRecorder
from .obsidian import is_vault, known_vaults, set_vault
from .overlay import Overlay, UiThread
from .inbox import InboxWatcher, ensure_vault_since, folder_warning
from .stt import SerializedTranscriber, build_transcriber
from . import updater

log = logging.getLogger(__name__)

COLORS = {"idle": "#6b7280", "recording": "#dc2626", "handsfree": "#ea580c",
          "processing": "#2563eb", "meeting": "#16a34a"}
TONES = {"recording": (880, 60), "handsfree": (1175, 60), "processing": (660, 60)}


def _icon_image(color: str):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, 58, 58), fill=color)
    draw.rounded_rectangle((25, 14, 39, 38), radius=7, fill="white")
    draw.arc((19, 24, 45, 46), 0, 180, fill="white", width=3)
    draw.line((32, 46, 32, 52), fill="white", width=3)
    return img


def _beep(state: str) -> None:
    if sys.platform != "win32" or state not in TONES:
        return
    import winsound

    threading.Thread(target=winsound.Beep, args=TONES[state], daemon=True).start()


def _open_folder(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])


class TrayApp:
    def __init__(self, cfg: Config, config_path: Path | None = None) -> None:
        self.cfg = cfg
        self.config_path = config_path or default_path()
        log.info("Spraakmodel laden (%s, %s)...", cfg.stt.backend, cfg.stt.model)
        self.transcriber = SerializedTranscriber(build_transcriber(cfg.stt))
        self.llm = build_llm(cfg.llm)
        if hasattr(self.llm, "warmup"):
            threading.Thread(target=self._warmup, daemon=True).start()
        self.mic = MicRecorder(cfg.dictation.mic_device)
        d = cfg.dictation
        self.state = ChordState(parse_combo(d.hotkey), parse_combo(d.command_hotkey),
                                d.handsfree_key, d.tap_max_seconds, d.double_tap_window)
        self.listener = HotkeyListener(self.state, self._on_hotkey)
        self.engine = DictationEngine(
            cfg, self.transcriber, self.llm,
            Hooks(start_recording=self.mic.start, stop_recording=self.mic.stop,
                  paste=lambda t: paste_text(t, d.paste_delay), copy_selection=copy_selection,
                  active_app=active_app, wait_for_release=self.listener.wait_for_release,
                  on_state=self._set_state),
            history_path=app_dir() / "history.jsonl",
        )
        self.meeting: MeetingRecorder | None = None
        self.icon = None
        self.ui = UiThread()
        self.inbox = InboxWatcher(cfg, self.transcriber, self.llm,
                                  on_note=lambda p: self._notify(f"Opname verwerkt: {p.name}"),
                                  config_path=self.config_path)
        self.overlay: Overlay | None = None

    def _warmup(self) -> None:
        try:
            self.llm.warmup(self.cfg.llm.cleanup_model)
            log.info("Taalmodel %s geladen.", self.cfg.llm.cleanup_model)
        except Exception as e:
            log.warning("Taalmodel opwarmen mislukt: %s", e)

    def _on_hotkey(self, event: str, mode: str | None) -> None:
        if self.meeting is not None and self.meeting.running:
            return  # microfoon is bezet door de vergadering
        try:
            self.engine.handle(event, mode)
        except Exception:
            log.exception("Fout tijdens dicteren")
            self._set_state("idle")

    def _set_state(self, state: str) -> None:
        if self.cfg.dictation.sounds:
            _beep(state)
        if self.overlay is not None:
            self.overlay.set_state(state, self.engine.mode)
        if self.meeting is not None and self.meeting.running and state == "idle":
            state = "meeting"
        if self.icon is not None:
            self.icon.icon = _icon_image(COLORS.get(state, COLORS["idle"]))
            self.icon.title = f"Notetaker - {state}"

    def toggle_meeting(self, icon=None, item=None) -> None:
        if self.meeting is not None and self.meeting.running:
            self._set_state("processing")
            path = self.meeting.stop()
            self.meeting = None
            self._set_state("idle")
            self._notify(f"Verslag klaar: {path.name}")
            self._refresh_menu()
            return
        m = self.cfg.meeting
        loop = LoopbackRecorder(m.loopback_device)
        self.meeting = MeetingRecorder(self.cfg, self.transcriber, self.llm,
                                       MicRecorder(m.mic_device), loop)
        try:
            path = self.meeting.start()
        except Exception as e:
            log.exception("Loopback niet beschikbaar, enkel microfoon")
            self.meeting = MeetingRecorder(self.cfg, self.transcriber, self.llm,
                                           MicRecorder(m.mic_device), None)
            path = self.meeting.start()
            self._notify(f"Enkel microfoon: {e}")
        self._set_state("meeting")
        self._notify(f"Opname gestart: {path.name}")
        self._refresh_menu()

    def _refresh_menu(self) -> None:
        if self.icon is not None:
            self.icon.update_menu()

    def choose_vault(self, vault: str) -> None:
        try:
            set_vault(self.config_path, vault)
        except (OSError, ValueError) as e:
            self._notify(f"Vault niet gewijzigd: {e}")
            return
        changed = vault != self.cfg.obsidian.vault
        self.cfg.obsidian.vault = vault
        self._refresh_menu()
        self._notify(f"Verslagen naar {self.cfg.meetings_dir}")
        if changed:  # oudere opnames in een andere vault niet massaal verwerken
            ensure_vault_since(self.cfg, self.config_path, reset=True)
        try:
            self.inbox.start()  # opnamemap aanmaken en opvolgen, ook als er bij de start geen vault was
        except Exception:
            log.exception("Opvolgen van opnames starten mislukt")
        self._warn_folders()

    def _warn_folders(self) -> None:
        warning = folder_warning(self.cfg)
        if warning:
            self._notify(warning)

    def browse_vault(self) -> None:
        folder = self.ui.ask_directory("Kies een Obsidian-vault", self.cfg.obsidian.vault)
        if not folder:
            return
        if not is_vault(Path(folder)):
            self._notify("Let op: geen .obsidian-map gevonden. Open de map eerst als vault in Obsidian.")
        self.choose_vault(str(Path(folder)))

    def _vault_items(self):
        import pystray

        current = self.cfg.obsidian.vault
        options = [str(v) for v in known_vaults()]
        if current and current not in options:
            options.insert(0, current)

        def pick(value):
            return lambda icon, item: self.choose_vault(value)

        def checked(value):
            return lambda item: self.cfg.obsidian.vault == value

        yield pystray.MenuItem("Niet in Obsidian (Documenten)", pick(""), checked=checked(""), radio=True)
        for v in options:
            yield pystray.MenuItem(Path(v).name or v, pick(v), checked=checked(v), radio=True)
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Andere map kiezen...", lambda i, it: threading.Thread(
            target=self.browse_vault, daemon=True).start())

    def _notify(self, msg: str) -> None:
        log.info(msg)
        if self.icon is not None:
            try:
                self.icon.notify(msg, "Notetaker")
            except Exception:
                pass

    def run(self) -> None:
        import pystray

        def meeting_label(item):
            running = self.meeting is not None and self.meeting.running
            return "Vergadering stoppen + verslag" if running else "Vergadering opnemen"

        d = self.cfg.dictation
        menu = pystray.Menu(
            pystray.MenuItem(f"Dicteren: houd {display_combo(d.hotkey)} ingedrukt", None, enabled=False),
            pystray.MenuItem(f"Hands-free: dubbeltik {display_combo(d.hotkey)}", None, enabled=False),
            pystray.MenuItem(f"Selectie herschrijven: {display_combo(d.command_hotkey)}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(meeting_label, lambda i, it: threading.Thread(
                target=self.toggle_meeting, daemon=True).start()),
            pystray.MenuItem("Opname verwerken...", lambda i, it: threading.Thread(
                target=self.process_recording, daemon=True).start()),
            pystray.MenuItem("Verslagen opslaan in", pystray.Menu(self._vault_items)),
            pystray.MenuItem("Map met verslagen openen", lambda i, it: _open_folder(self.cfg.meetings_dir)),
            pystray.MenuItem("Instellingen...", lambda i, it: self.open_settings()),
            pystray.MenuItem("Configuratie openen", lambda i, it: _open_folder(app_dir())),
            pystray.MenuItem("Bijwerken", lambda i, it: threading.Thread(
                target=self.update, daemon=True).start()),
            pystray.MenuItem("Over Notetaker", lambda i, it: self.open_about()),
            pystray.MenuItem("Afsluiten", self._quit),
        )
        self.icon = pystray.Icon("notetaker", _icon_image(COLORS["idle"]), "Notetaker", menu)
        self.ui.start()
        if d.overlay:
            try:
                self.overlay = Overlay(self.ui, d.hotkey, lambda: self.mic.level)
                self.overlay.start()
            except Exception:
                log.exception("Opnamevenster kon niet starten; verder zonder")
                self.overlay = None
        self.listener.start()
        self.inbox.start()
        warn = threading.Timer(5, self._warn_folders)  # pas als het pictogram in het systeemvak staat
        warn.daemon = True
        warn.start()
        check = threading.Timer(60, self._check_updates_quietly)
        check.daemon = True
        check.start()
        log.info("Klaar (versie %s). Houd %s ingedrukt om te dicteren.",
                 updater.current_version(), display_combo(d.hotkey))
        self.icon.run()

    def process_recording(self) -> None:
        """Een gekozen geluidsbestand transcriberen en samenvatten; het bestand blijft staan."""
        from .inbox import process_file

        start = self.cfg.obsidian.vault or str(self.cfg.default_dir)
        chosen = self.ui.ask_audio_file("Opname kiezen om te transcriberen", start)
        if not chosen:
            return
        self._notify(f"Bezig met {Path(chosen).name}. Je krijgt een melding als het verslag klaar is.")
        self._set_state("processing")
        try:
            with self.inbox.busy:
                note = process_file(self.cfg, self.transcriber, self.llm, Path(chosen), self.inbox.log)
            self._notify(f"Verslag klaar: {note.name}")
        except Exception as e:
            log.exception("Verwerken van %s mislukt", chosen)
            self._notify(f"Verwerken mislukt: {e}")
        finally:
            self._set_state("idle")

    def open_about(self) -> None:
        from .settings_ui import show_about

        device = getattr(self.transcriber, "device", "")
        self.ui.call(lambda: show_about(self.ui.root, self.cfg, device, self.update))

    def open_settings(self) -> None:
        from .settings_ui import SettingsWindow

        def saved():
            self._notify("Instellingen bewaard. Notetaker start opnieuw met de nieuwe modellen.")
            threading.Thread(target=self.restart, daemon=True).start()

        self.ui.call(lambda: SettingsWindow(self.ui.root, self.config_path, saved))

    def restart(self) -> None:
        """Nieuwe modellen worden enkel bij het starten geladen: nieuw proces, dit afsluiten."""
        import subprocess

        if self.meeting is not None and self.meeting.running:
            self._notify("Herstart uitgesteld: eerst de vergadering stoppen.")
            return
        code = ("import time; time.sleep(2); "
                "from notetaker.__main__ import run_app; run_app()")
        flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
        subprocess.Popen([sys.executable, "-c", code], creationflags=flags, close_fds=True)
        self._quit(self.icon, None)

    def _check_updates_quietly(self) -> None:
        try:
            changes = updater.pending_updates()
        except Exception as e:  # geen netwerk of geen git: niet storen
            log.info("Updatecontrole overgeslagen: %s", e)
            return
        if changes:
            self._notify(f"Update beschikbaar ({len(changes)} wijziging(en)). Kies 'Bijwerken' in het menu.")

    def update(self) -> None:
        if self.meeting is not None and self.meeting.running:
            self._notify("Eerst de vergadering stoppen, daarna bijwerken.")
            return
        try:
            changes = updater.pending_updates()
        except Exception as e:
            self._notify(f"Controleren op updates mislukt: {e}")
            return
        if not changes:
            self._notify("Je hebt de nieuwste versie.")
            return
        try:
            updater.start_update()
        except Exception as e:
            self._notify(f"Bijwerken starten mislukt: {e}")
            return
        self._notify(f"{len(changes)} wijziging(en) installeren. Notetaker start zo opnieuw.")
        # Afsluiten: het updatescript vervangt bestanden en start de app daarna opnieuw
        self._quit(self.icon, None)

    def _quit(self, icon, item) -> None:
        self.inbox.stop()
        if self.meeting is not None and self.meeting.running:
            self.meeting.stop()
        self.listener.stop()
        icon.stop()
