"""Pembuatan arsip + manifest (daftar file, ukuran, sha256)."""
from __future__ import annotations

import hashlib
import json
import platform
import socket
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = "backupkit-manifest.json"


class ArchiveError(Exception):
    """Operasi arsip gagal."""


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def make_manifest(staging: Path, meta: dict) -> dict:
    """Hitung hash semua file di staging dan tulis manifest ke dalamnya."""
    files = []
    for p in sorted(staging.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(staging).as_posix()
        if rel == MANIFEST_NAME:
            continue
        files.append({"path": rel, "size": p.stat().st_size, "sha256": sha256_file(p)})

    manifest = dict(meta)
    manifest.update({
        "tool": "backupkit",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "total_files": len(files),
        "total_bytes": sum(f["size"] for f in files),
        "files": files,
    })
    (staging / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def pack(staging: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["tar", "-C", str(staging), "-czf", str(out), "."],
        capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        raise ArchiveError(f"tar gagal: {proc.stderr.strip()[:300]}")
    return out


def members(archive: Path) -> list[str]:
    proc = subprocess.run(["tar", "-tzf", str(archive)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise ArchiveError(f"tidak bisa membaca arsip: {proc.stderr.strip()[:200]}")
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def read_manifest(archive: Path) -> dict:
    name = next((m for m in members(archive)
                 if m.lstrip("./") == MANIFEST_NAME), None)
    if not name:
        raise ArchiveError(f"manifest {MANIFEST_NAME} tidak ada di arsip")
    proc = subprocess.run(["tar", "-xzOf", str(archive), name],
                          capture_output=True)
    if proc.returncode != 0:
        raise ArchiveError("gagal membaca manifest dari arsip")
    return json.loads(proc.stdout.decode("utf-8"))


def extract(archive: Path, dest: Path, member: str | None = None) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["tar", "-xzf", str(archive), "-C", str(dest)]
    if member:
        cmd.append(member)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ArchiveError(f"ekstrak gagal: {proc.stderr.strip()[:200]}")


def is_valid_tar(path: Path) -> bool:
    try:
        return tarfile.is_tarfile(path)
    except Exception:                      # noqa: BLE001
        return False
