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


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    # JSON-tekst is ook geldige TOML: aanhalingstekens, backslashes en stuurtekens worden ontsnapt
    return json.dumps(str(value), ensure_ascii=False).replace("\x7f", "\\u007f")


def _value_end(text: str, pos: int) -> int:
    """Einde van de waarde die op `pos` begint. Een lijst loopt tot de bijhorende ], ook over
    meerdere regels; ] binnen tekst of commentaar telt niet mee. Anders: einde van de regel."""
    if pos >= len(text) or text[pos] != "[":
        nl = text.find("\n", pos)
        return len(text) if nl < 0 else nl
    depth, i = 0, pos
    while i < len(text):
        c = text[i]
        if c == "#":
            nl = text.find("\n", i)
            i = len(text) if nl < 0 else nl
            continue
        if c in "\"'":
            j = i + 1
            while j < len(text) and text[j] != c and text[j] != "\n":
                j += 2 if c == '"' and text[j] == "\\" else 1
            i = j + 1
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _replace_key(body: str, key: str, line: str) -> str | None:
    m = re.search(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=[ \t]*", body)
    if m is None:
        return None
    return body[: m.start()] + line + body[_value_end(body, m.end()):]


def _with_value(text: str, section: str, key: str, value) -> str:
    line = f"{key} = {_toml_value(value)}"
    if not section:
        head_end = re.search(r"(?m)^\[", text)
        head = text[: head_end.start()] if head_end else text
        head = _replace_key(head, key, line) or line + "\n" + head
        return head + (text[head_end.start():] if head_end else "")
    # Ook "[ inbox ]", "[inbox]  # commentaar" en ingesprongen koppen herkennen
    match = re.search(rf"(?ms)^[ \t]*\[[ \t]*{re.escape(section)}[ \t]*\][ \t]*(?:#[^\n]*)?(?:\n|\Z)"
                      rf"(.*?)(?=^[ \t]*\[|\Z)", text)
    if match is None:
        return text.rstrip("\n") + ("\n\n" if text.strip() else "") + f"[{section}]\n{line}\n"
    before, body = text[: match.start(1)], match.group(1)
    body = _replace_key(body, key, line) or line + "\n" + body
    return before + ("" if before.endswith("\n") else "\n") + body + text[match.end(1):]


def _expected(data: dict, changes: list[tuple[str, str, object]]) -> dict:
    import copy

    out = copy.deepcopy(data)
    for section, key, value in changes:
        table = out.setdefault(section, {}) if section else out
        table[key] = list(value) if isinstance(value, tuple) else value
    return out


def update_toml(config_path: Path, changes: list[tuple[str, str, object]]) -> None:
    """Zet sleutels (sectie, sleutel, waarde) in config.toml in één keer; commentaar en overige
    inhoud blijven staan. Het resultaat wordt teruggelezen: bevat het iets anders dan precies deze
    wijzigingen (ongeldig, of een verkeerde regel vervangen), dan blijft het bestand ongewijzigd
    en volgt een ValueError."""
    import tomllib

    config_path.parent.mkdir(parents=True, exist_ok=True)
    old = text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    for section, key, value in changes:
        text = _with_value(text, section, key, value)
    try:
        before = tomllib.loads(old)
    except tomllib.TOMLDecodeError:
        before = None  # was al ongeldig: niet de schuld van deze wijziging
    if before is not None:
        names = ", ".join(f"{s + '.' if s else ''}{k}" for s, k, _ in changes)
        try:
            after = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{names} niet aangepast: {config_path.name} zou ongeldig worden ({e}).") from e
        if after != _expected(before, changes):
            raise ValueError(f"{names} niet aangepast: de opmaak van {config_path.name} is te ongewoon "
                             "om veilig te wijzigen. Pas het bestand met de hand aan.")
    config_path.write_text(text, encoding="utf-8")


def set_toml_value(config_path: Path, section: str, key: str, value) -> None:
    update_toml(config_path, [(section, key, value)])


def set_vault(config_path: Path, vault: str) -> None:
    """Zet `[obsidian] vault` in config.toml en laat de rest van het bestand ongemoeid."""
    set_toml_value(config_path, "obsidian", "vault", vault)


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
