"""Obsidian-koppeling: bekende vaults vinden, een vault kiezen en notities met frontmatter schrijven."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


def obsidian_config_file() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "obsidian" / "obsidian.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "obsidian" / "obsidian.json"


def known_vaults(config_file: Path | None = None) -> list[Path]:
    """Vaults die Obsidian zelf kent, meest recent geopend eerst."""
    path = config_file or obsidian_config_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    vaults = sorted((data.get("vaults") or {}).values(), key=lambda v: v.get("ts", 0), reverse=True)
    return [Path(v["path"]) for v in vaults if v.get("path") and Path(v["path"]).is_dir()]


def is_vault(path: Path) -> bool:
    return (Path(path) / ".obsidian").is_dir()


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_vault(config_path: Path, vault: str) -> None:
    """Zet `[obsidian] vault` in config.toml en laat de rest van het bestand ongemoeid."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    line = f"vault = {_toml_str(vault)}"
    section = re.search(r"(?ms)^\[obsidian\][ \t]*\n(.*?)(?=^\[|\Z)", text)
    if section is None:
        text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + f"[obsidian]\n{line}\n"
    else:
        body = section.group(1)
        if re.search(r"(?m)^vault\s*=", body):
            body = re.sub(r"(?m)^vault\s*=.*$", lambda _: line, body, count=1)
        else:
            body = line + "\n" + body
        text = text[: section.start(1)] + body + text[section.end(1):]
    config_path.write_text(text, encoding="utf-8")


def atomic_write_text(path: Path, text: str) -> None:
    """Schrijft eerst naar een verborgen tijdelijk bestand en hernoemt dan in één stap.
    Syncthing en Obsidian zien zo nooit een half geschreven notitie."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.notetaker-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def frontmatter(title: str, started: datetime, duration: str, tags: list[str],
                audio: str = "") -> str:
    lines = ["---", f"title: {json.dumps(title, ensure_ascii=False)}",
             f"date: {started:%Y-%m-%d}", f"time: {json.dumps(f'{started:%H:%M}')}",
             f"duration: {json.dumps(duration)}", "type: vergadering"]
    if tags:
        lines.append("tags:")
        lines += [f"  - {t}" for t in tags]
    if audio:
        lines.append(f"audio: {json.dumps(audio, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"
