"""Pengumpul sumber: file/folder, snapshot SQLite konsisten, dan keluaran perintah."""
from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path

from .config import Source


class SourceError(Exception):
    """Gagal mengumpulkan sumber."""


def _excluded(rel: str, patterns: list[str]) -> bool:
    parts = rel.split("/")
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat) or any(fnmatch.fnmatch(p, pat) for p in parts):
            return True
    return False


def collect(sources: Source, dest: Path) -> dict:
    """Salin semua sumber ke `dest`. Kembalikan ringkasan per-sumber."""
    dest.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict] = {}

    for raw in sources.paths:
        src = Path(os.path.expanduser(os.path.expandvars(raw)))
        if not src.exists():
            raise SourceError(f"sumber tidak ada: {src}")
        out = dest / src.name
        if src.is_dir():
            shutil.copytree(
                src, out, dirs_exist_ok=True, symlinks=True,
                ignore=shutil.ignore_patterns(*sources.exclude) if sources.exclude else None,
            )
            files = sum(1 for _ in out.rglob("*") if _.is_file())
            summary[str(src)] = {"type": "dir", "files": files}
        else:
            shutil.copy2(src, out)
            summary[str(src)] = {"type": "file", "files": 1}

    for raw in sources.sqlite:
        db = Path(os.path.expanduser(os.path.expandvars(raw)))
        if not db.is_file():
            raise SourceError(f"database tidak ada: {db}")
        out = dest / "sqlite" / f"{db.name}.sqlite3"
        out.parent.mkdir(parents=True, exist_ok=True)
        # `.backup` menghasilkan snapshot konsisten walau DB sedang dipakai.
        proc = subprocess.run(
            ["sqlite3", str(db), f".backup '{out}'"],
            capture_output=True, text=True)
        if proc.returncode != 0 or not out.is_file():
            shutil.copy2(db, out)      # fallback: salin mentah (bisa tidak konsisten)
            summary[str(db)] = {"type": "sqlite", "files": 1, "fallback": True,
                                "error": proc.stderr.strip()[:200]}
        else:
            summary[str(db)] = {"type": "sqlite", "files": 1, "fallback": False}

    for name, command in sources.commands.items():
        out = dest / "commands" / f"{name}.out"
        out.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(command, shell=True, capture_output=True)
        if proc.returncode != 0:
            raise SourceError(
                f"perintah {name!r} gagal (exit {proc.returncode}): "
                f"{proc.stderr.decode(errors='replace').strip()[:200]}")
        out.write_bytes(proc.stdout)
        summary[f"command:{name}"] = {"type": "command", "files": 1,
                                      "bytes": len(proc.stdout)}

    return summary
