"""Vensters 'Over Notetaker' en 'Instellingen' (Tkinter, op de UI-thread van het opnamevenster)."""
from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Callable

from .about import REPO_URL, about_rows
from .config import Config, load
from .providers import (PROVIDERS, STT_LOCAL, WHISPER_MODELS, apply_choice, get_key, parse_folders,
                        keyring_available, list_models, mask, set_key, stt_choices)

PAD = {"padx": 10, "pady": 4}


def _front(win) -> None:
    win.lift()
    win.attributes("-topmost", True)
    win.after(300, lambda: win.attributes("-topmost", False))
    win.focus_force()


def show_about(root, cfg: Config, whisper_device: str, on_update: Callable[[], None]) -> None:
    import tkinter as tk
    from tkinter import ttk

    win = tk.Toplevel(root)
    win.title("Over Notetaker")
    win.resizable(False, False)
    frame = ttk.Frame(win, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Notetaker", font=("Segoe UI", 16, "bold")).grid(row=0, column=0,
                                                                         columnspan=2, sticky="w")
    ttk.Label(frame, text="Dicteren en vergaderingen transcriberen, volledig lokaal mogelijk.").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
    rows = about_rows(cfg, whisper_device)
    for i, (key, value) in enumerate(rows, start=2):
        ttk.Label(frame, text=key, foreground="#6b7280").grid(row=i, column=0, sticky="nw", padx=(0, 12))
        ttk.Label(frame, text=value, wraplength=420).grid(row=i, column=1, sticky="w")

    def copy():
        win.clipboard_clear()
        win.clipboard_append("\n".join(f"{k}: {v}" for k, v in rows))

    buttons = ttk.Frame(frame)
    buttons.grid(row=len(rows) + 2, column=0, columnspan=2, sticky="e", pady=(14, 0))
    ttk.Button(buttons, text="Gegevens kopiëren", command=copy).pack(side="left", padx=4)
    ttk.Button(buttons, text="Controleren op updates",
               command=lambda: threading.Thread(target=on_update, daemon=True).start()).pack(side="left", padx=4)
    ttk.Button(buttons, text="GitHub", command=lambda: webbrowser.open(REPO_URL)).pack(side="left", padx=4)
    ttk.Button(buttons, text="Sluiten", command=win.destroy).pack(side="left", padx=4)
    _front(win)


class SettingsWindow:
    def __init__(self, root, config_path: Path, on_saved: Callable[[], None]) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.config_path = config_path
        self.cfg = load(config_path)
        self.on_saved = on_saved
        self.win = win = tk.Toplevel(root)
        win.title("Notetaker - Instellingen")
        win.resizable(False, False)
        body = ttk.Frame(win, padding=14)
        body.pack(fill="both", expand=True)

        # Spraakherkenning
        stt_box = ttk.LabelFrame(body, text="Spraakherkenning", padding=8)
        stt_box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.stt_labels = stt_choices()
        current_stt = STT_LOCAL if self.cfg.stt.backend == "faster-whisper" else self.cfg.stt.provider
        self.stt = tk.StringVar(value=self.stt_labels.get(current_stt, self.stt_labels[STT_LOCAL]))
        ttk.Label(stt_box, text="Bron").grid(row=0, column=0, sticky="w", **PAD)
        ttk.Combobox(stt_box, textvariable=self.stt, values=list(self.stt_labels.values()),
                     state="readonly", width=42).grid(row=0, column=1, sticky="w", **PAD)
        ttk.Label(stt_box, text="Whisper-model").grid(row=1, column=0, sticky="w", **PAD)
        self.whisper = tk.StringVar(value=self.cfg.stt.model)
        ttk.Combobox(stt_box, textvariable=self.whisper, values=WHISPER_MODELS,
                     width=42).grid(row=1, column=1, sticky="w", **PAD)
        ttk.Label(stt_box, text="Groter = nauwkeuriger en trager. large-v3-turbo past in 2 GB videogeheugen.",
                  foreground="#6b7280").grid(row=2, column=0, columnspan=2, sticky="w", padx=10)

        # Taalmodel
        llm_box = ttk.LabelFrame(body, text="Taalmodel (opkuis en verslagen)", padding=8)
        llm_box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.provider_labels = {p.key: p.label for p in PROVIDERS.values()}
        self.provider = tk.StringVar(value=self.provider_labels.get(self.cfg.llm.provider,
                                                                    self.provider_labels["ollama"]))
        ttk.Label(llm_box, text="Aanbieder").grid(row=0, column=0, sticky="w", **PAD)
        combo = ttk.Combobox(llm_box, textvariable=self.provider, values=list(self.provider_labels.values()),
                             state="readonly", width=42)
        combo.grid(row=0, column=1, sticky="w", **PAD)
        combo.bind("<<ComboboxSelected>>", lambda e: self._provider_changed())
        self.cleanup = tk.StringVar(value=self.cfg.llm.cleanup_model)
        self.summary = tk.StringVar(value=self.cfg.llm.summary_model)
        ttk.Label(llm_box, text="Model dictee").grid(row=1, column=0, sticky="w", **PAD)
        self.cleanup_box = ttk.Combobox(llm_box, textvariable=self.cleanup, width=42)
        self.cleanup_box.grid(row=1, column=1, sticky="w", **PAD)
        ttk.Label(llm_box, text="Model verslagen").grid(row=2, column=0, sticky="w", **PAD)
        self.summary_box = ttk.Combobox(llm_box, textvariable=self.summary, width=42)
        self.summary_box.grid(row=2, column=1, sticky="w", **PAD)
        row = ttk.Frame(llm_box)
        row.grid(row=3, column=1, sticky="w", padx=6)
        ttk.Button(row, text="Modellen ophalen en verbinding testen",
                   command=self._test_connection).pack(side="left")
        self.status = tk.StringVar(value="")
        ttk.Label(llm_box, textvariable=self.status, foreground="#2563eb", wraplength=430).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=10)

        # Opnames in de vault
        rec_box = ttk.LabelFrame(body, text="Opnames in de Obsidian-vault", padding=8)
        rec_box.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        folders = self.cfg.inbox.vault_folders
        self.loaded_folders = [folders] if isinstance(folders, str) else [str(f) for f in folders]
        self.vault_folders = tk.StringVar(value=", ".join(self.loaded_folders))
        ttk.Label(rec_box, text="Mappen").grid(row=0, column=0, sticky="w", **PAD)
        ttk.Entry(rec_box, textvariable=self.vault_folders, width=44).grid(row=0, column=1, sticky="w", **PAD)
        hint = ("Geluidsbestanden hier worden getranscribeerd en samengevat. Komma's tussen mappen, "
                "* = hele vault. De eerste map wordt aangemaakt: kies ze als inboxmap in de Android-app.")
        if not self.cfg.obsidian.vault:
            hint = "Nog geen vault gekozen (menu 'Verslagen opslaan in'). " + hint
        ttk.Label(rec_box, text=hint, foreground="#6b7280", wraplength=690).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=10)

        # API-sleutels
        key_box = ttk.LabelFrame(body, text="API-sleutels (bewaard in de Windows-kluis)", padding=8)
        key_box.grid(row=3, column=0, sticky="ew")
        self.key_vars: dict[str, tk.StringVar] = {}
        keyed = [p for p in PROVIDERS.values() if p.needs_key]
        for i, p in enumerate(keyed):
            ttk.Label(key_box, text=p.label.split(" (")[0]).grid(row=i, column=0, sticky="w", **PAD)
            var = tk.StringVar()
            self.key_vars[p.key] = var
            entry = ttk.Entry(key_box, textvariable=var, show="•", width=30)
            entry.grid(row=i, column=1, sticky="w", **PAD)
            existing = get_key(p.key)
            ttk.Label(key_box, text=mask(existing) if existing else "geen",
                      foreground="#6b7280", width=14).grid(row=i, column=2, sticky="w")
            ttk.Button(key_box, text="Sleutel aanvragen", width=16,
                       command=lambda u=p.key_url: webbrowser.open(u)).grid(row=i, column=3, **PAD)
        if not keyring_available():
            ttk.Label(key_box, text="Kluis niet beschikbaar: installeer het pakket 'keyring'.",
                      foreground="#dc2626").grid(row=len(keyed), column=0, columnspan=4, sticky="w")
        ttk.Label(key_box, text="Leeg laten = huidige sleutel behouden. Typ één spatie om te wissen.",
                  foreground="#6b7280").grid(row=len(keyed) + 1, column=0, columnspan=4, sticky="w", padx=10)

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Annuleren", command=win.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Opslaan en herstarten", command=self._save).pack(side="left", padx=4)
        self._provider_changed(initial=True)
        _front(win)

    def _key_of(self, label: str, mapping: dict[str, str]) -> str:
        return next(k for k, v in mapping.items() if v == label)

    def _provider_key(self) -> str:
        return self._key_of(self.provider.get(), self.provider_labels)

    def _provider_changed(self, initial: bool = False) -> None:
        p = PROVIDERS[self._provider_key()]
        state = "disabled" if p.backend == "none" else "normal"
        for box in (self.cleanup_box, self.summary_box):
            box.configure(values=p.models, state=state)
        if not initial and p.models:
            self.cleanup.set(p.models[0])
            self.summary.set(p.models[-1])
        if not initial:
            self.status.set("")

    def _test_connection(self) -> None:
        provider = self._provider_key()
        typed = self.key_vars.get(provider)
        if typed is not None and typed.get().strip():
            set_key(provider, typed.get().strip())  # nodig om meteen te kunnen testen
        self.status.set("Bezig...")
        cfg_llm = self.cfg.llm

        def work():
            try:
                models = list_models(provider, cfg_llm)
                msg = (f"Verbinding in orde, {len(models)} modellen gevonden." if models
                       else "Verbinding in orde.")
            except Exception as e:
                models, msg = [], f"Mislukt: {e}"
            self.win.after(0, lambda: self._show_models(models, msg))

        threading.Thread(target=work, daemon=True).start()

    def _show_models(self, models: list[str], msg: str) -> None:
        self.status.set(msg)
        if models:
            for box in (self.cleanup_box, self.summary_box):
                box.configure(values=models)

    def _save(self) -> None:
        folders = parse_folders(self.vault_folders.get())
        unchanged = folders == parse_folders(", ".join(self.loaded_folders))
        try:
            for provider, var in self.key_vars.items():
                value = var.get()
                if value == " ":
                    set_key(provider, "")
                elif value.strip():
                    set_key(provider, value.strip())
            apply_choice(
                self.config_path,
                stt=self._key_of(self.stt.get(), self.stt_labels),
                whisper_model=self.whisper.get().strip() or "large-v3-turbo",
                llm_provider=self._provider_key(),
                cleanup_model=self.cleanup.get(),
                summary_model=self.summary.get(),
                vault_folders=None if unchanged else folders,
            )
        except Exception as e:  # kluis, schijf of ongeldige config: melden in plaats van stil falen
            from tkinter import messagebox

            messagebox.showerror("Notetaker", f"Instellingen niet bewaard: {e}", parent=self.win)
            return
        self.win.destroy()
        self.on_saved()
