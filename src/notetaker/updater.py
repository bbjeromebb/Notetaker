"""Updates via Git: controleren of er een nieuwe versie is en het updatescript starten."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # geen zwart venster tijdens git fetch


def _git(repo: Path, *args: str, timeout: float = 60) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            timeout=timeout, creationflags=_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} mislukt")
    return result.stdout.strip()


def is_git_checkout(repo: Path = REPO_ROOT) -> bool:
    return (repo / ".git").exists()


def pending_updates(repo: Path = REPO_ROOT, fetch: bool = True) -> list[str]:
    """Onderwerpen van de commits die op GitHub staan maar nog niet lokaal. Leeg = up-to-date."""
    if not is_git_checkout(repo):
        return []
    if fetch:
        _git(repo, "fetch", "--quiet")
    out = _git(repo, "log", "--format=%s", "HEAD..@{u}")
    return [line for line in out.splitlines() if line.strip()]


def start_update(repo: Path = REPO_ROOT) -> None:
    """Start update.ps1 los van deze app: het script stopt de app, werkt bij en start ze opnieuw."""
    script = repo / "update.ps1"
    if sys.platform != "win32" or not script.exists():
        raise RuntimeError("Automatisch bijwerken werkt enkel op Windows met update.ps1.")
    DETACHED, NEW_GROUP = 0x00000008, 0x00000200
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Restart"],
        cwd=str(repo), creationflags=DETACHED | NEW_GROUP | _NO_WINDOW, close_fds=True,
    )


def current_version(repo: Path = REPO_ROOT) -> str:
    """Korte commit en datum, bv. 'a7574db van 2026-10-14 16:29'."""
    if not is_git_checkout(repo):
        return "onbekend (geen Git-installatie)"
    try:
        return _git(repo, "log", "-1", "--format=%h van %cd", "--date=format:%Y-%m-%d %H:%M")
    except Exception as e:
        return f"onbekend ({e})"
