"""Tujuan penyimpanan + rotasi: folder lokal, atau remote rclone."""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Target


class StoreError(Exception):
    """Operasi penyimpanan gagal."""


def _rclone(*args: str) -> subprocess.CompletedProcess:
    if shutil.which("rclone") is None:
        raise StoreError("rclone tidak terpasang")
    return subprocess.run(["rclone", *args], capture_output=True, text=True)


def push(local: Path, target: Target) -> str:
    """Kirim satu file ke target. Kembalikan lokasi tujuan."""
    if target.kind == "local":
        dest = Path(target.path).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, dest / local.name)
        return str(dest / local.name)

    proc = _rclone("copy", str(local), target.path, "--no-traverse", "--quiet",
                   "--retries", "3", "--low-level-retries", "10")
    if proc.returncode != 0:
        raise StoreError(f"rclone copy gagal: {proc.stderr.strip()[:300]}")
    return f"{target.path.rstrip('/')}/{local.name}"


def list_backups(target: Target) -> list[dict]:
    """Daftar backup di target, urut terbaru dulu."""
    if target.kind == "local":
        d = Path(target.path).expanduser()
        if not d.is_dir():
            return []
        out = []
        for p in d.glob("*.tar.gz*"):
            st = p.stat()
            out.append({"name": p.name, "path": str(p), "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc)})
        return sorted(out, key=lambda x: x["mtime"], reverse=True)

    proc = _rclone("lsjson", target.path, "--files-only")
    if proc.returncode != 0:
        return []
    import json
    try:
        items = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for it in items:
        name = it.get("Name", "")
        if ".tar.gz" not in name:
            continue
        mtime = it.get("ModTime")
        try:
            ts = datetime.fromisoformat(mtime.replace("Z", "+00:00"))
        except Exception:                  # noqa: BLE001
            ts = datetime.now(timezone.utc)
        out.append({"name": name, "path": f"{target.path.rstrip('/')}/{name}",
                    "size": it.get("Size", 0), "mtime": ts})
    return sorted(out, key=lambda x: x["mtime"], reverse=True)


def prune(target: Target) -> list[str]:
    """Terapkan retensi. Kembalikan daftar nama yang dihapus."""
    removed: list[str] = []
    entries = list_backups(target)

    if target.keep and len(entries) > target.keep:
        for e in entries[target.keep:]:
            _delete(target, e)
            removed.append(e["name"])
        entries = entries[:target.keep]

    if target.keep_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=target.keep_days)
        for e in list(entries):
            if e["mtime"] < cutoff:
                _delete(target, e)
                removed.append(e["name"])
    return removed


def _delete(target: Target, entry: dict) -> None:
    if target.kind == "local":
        Path(entry["path"]).unlink(missing_ok=True)
        return
    proc = _rclone("deletefile", entry["path"], "--quiet")
    if proc.returncode != 0:
        raise StoreError(f"gagal hapus {entry['name']}: {proc.stderr.strip()[:200]}")


def fetch(target: Target, name: str, dest_dir: Path) -> Path:
    """Ambil satu backup dari target ke folder lokal."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if target.kind == "local":
        src = Path(target.path).expanduser() / name
        if not src.is_file():
            raise StoreError(f"backup tidak ada: {src}")
        out = dest_dir / name
        shutil.copy2(src, out)
        return out

    proc = _rclone("copy", f"{target.path.rstrip('/')}/{name}", str(dest_dir),
                   "--no-traverse", "--quiet")
    out = dest_dir / name
    if proc.returncode != 0 or not out.is_file():
        raise StoreError(f"gagal mengunduh {name}: {proc.stderr.strip()[:200]}")
    return out
