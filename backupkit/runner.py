"""Orkestrasi: backup, verify, restore, list, status, prune."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import archive, crypto, notify, sources, store, verify as verify_mod
from .config import Config

# Bisa diarahkan ke folder lain (dipakai juga oleh test).
STATE_DIR = Path(os.environ.get("BACKUPKIT_STATE_DIR",
                                Path.home() / ".local/state/backupkit"))


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _state_path(cfg: Config) -> Path:
    return STATE_DIR / f"{cfg.name}.json"


def _load_state(cfg: Config) -> dict:
    p = _state_path(cfg)
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _save_state(cfg: Config, patch: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state(cfg)
    state.update(patch)
    _state_path(cfg).write_text(json.dumps(state, indent=2), encoding="utf-8")


def backup_name(cfg: Config) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{cfg.name}-{stamp}.tar.gz"


# ---------------------------------------------------------------- backup

def run_backup(cfg: Config, keep_staging: bool = False) -> dict:
    root = Path(cfg.expand(cfg.staging_dir))
    staging = Path(tempfile.mkdtemp(dir=root.mkdir(parents=True, exist_ok=True) or root,
                                   prefix="stage-"))
    payload_dir = staging / cfg.name
    result: dict = {"name": cfg.name, "started": datetime.now(timezone.utc).isoformat(
        timespec="seconds"), "steps": []}

    try:
        _log(f"mengumpulkan sumber ke {payload_dir}")
        summary = sources.collect(cfg.sources, payload_dir)
        result["sources"] = summary

        _log("membuat manifest + hash")
        manifest = archive.make_manifest(payload_dir, {"job": cfg.name})
        result["total_files"] = manifest["total_files"]
        result["total_bytes"] = manifest["total_bytes"]

        archive_path = staging / backup_name(cfg)
        _log(f"mengemas arsip ({manifest['total_files']} file)")
        archive.pack(payload_dir, archive_path)

        artifact = archive_path
        if cfg.crypto.enabled:
            _log("mengenkripsi (gpg)")
            artifact = crypto.encrypt(archive_path, cfg.crypto)
            archive_path.unlink(missing_ok=True)

        size = artifact.stat().st_size
        result["artifact"] = artifact.name
        result["size"] = size
        _log(f"arsip siap: {artifact.name} ({size:,} byte)")

        _log("verifikasi lokal (uji-restore sampel)")
        local_report = verify_mod.verify(artifact, cfg.crypto, cfg.verify.sample,
                                         workdir=staging / "verify-local")
        result["verify_local"] = local_report
        if not local_report["ok"]:
            raise RuntimeError("verifikasi lokal gagal: " + "; ".join(local_report["errors"]))
        _log("verifikasi lokal LULUS")

        result["targets"] = []
        for target in cfg.targets:
            _log(f"unggah ke {target.kind}:{target.path}")
            ref = store.push(artifact, target)
            entry = {"target": f"{target.kind}:{target.path}", "ref": ref}

            removed = store.prune(target)
            entry["pruned"] = removed
            if removed:
                _log(f"  rotasi: {len(removed)} backup lama dihapus")

            if cfg.verify.auto:
                with tempfile.TemporaryDirectory() as td:
                    got = store.fetch(target, artifact.name, Path(td))
                    rep = verify_mod.verify(got, cfg.crypto, cfg.verify.sample,
                                            workdir=Path(td) / "v")
                    entry["verify_remote"] = rep
                    if not rep["ok"]:
                        raise RuntimeError(
                            f"verifikasi salinan remote gagal di {target.path}: "
                            + "; ".join(rep["errors"]))
                    _log(f"  verifikasi salinan remote LULUS ({target.path})")
            result["targets"].append(entry)

        result["ok"] = True
        _save_state(cfg, {
            "last_backup": {"at": result["started"], "artifact": artifact.name,
                            "size": size, "files": manifest["total_files"]},
            "last_verify": {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "ok": True,
                            "checked": len(local_report.get("checked", []))},
        })
        _log("selesai: backup sukses ✅")
        return result

    except Exception as exc:                       # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        result["ok"] = False
        result["error"] = detail
        _log(f"GAGAL: {detail}")
        sent = notify.notify_failure(cfg.notify, f"backupkit gagal: {cfg.name}", detail)
        result["notified"] = sent
        _save_state(cfg, {"last_error": {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "detail": detail, "notified": sent}})
        raise
    finally:
        if not keep_staging:
            shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------- lain-lain

def latest_backup(cfg: Config, target_index: int = 0) -> str | None:
    entries = store.list_backups(cfg.targets[target_index])
    return entries[0]["name"] if entries else None


def run_verify(cfg: Config, name: str | None = None, target_index: int = 0) -> dict:
    name = name or latest_backup(cfg, target_index)
    if not name:
        raise RuntimeError("tidak ada backup di target")
    with tempfile.TemporaryDirectory(prefix="backupkit-fetch-") as td:
        got = store.fetch(cfg.targets[target_index], name, Path(td))
        report = verify_mod.verify(got, cfg.crypto, cfg.verify.sample, workdir=Path(td) / "v")
    _save_state(cfg, {"last_verify": {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ok": report["ok"], "checked": len(report.get("checked", [])),
        "artifact": name}})
    return report


def run_restore(cfg: Config, name: str, dest: Path, target_index: int = 0) -> Path:
    dest = Path(dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="backupkit-restore-") as td:
        got = store.fetch(cfg.targets[target_index], name, Path(td))
        plain = got
        if got.suffix == ".gpg":
            plain = crypto.decrypt(got, cfg.crypto, Path(td) / "payload.tar.gz")
        archive.extract(plain, dest)
    return dest


def run_list(cfg: Config) -> list[dict]:
    out = []
    for i, target in enumerate(cfg.targets):
        for e in store.list_backups(target):
            out.append({"target": f"{target.kind}:{target.path}", "index": i, **e})
    return out


def run_status(cfg: Config) -> dict:
    state = _load_state(cfg)
    entries = store.list_backups(cfg.targets[0]) if cfg.targets else []
    latest = entries[0] if entries else None
    age_h = None
    if latest:
        age_h = round((datetime.now(timezone.utc) - latest["mtime"]).total_seconds() / 3600, 1)
    return {
        "job": cfg.name,
        "state": state,
        "latest": {"name": latest["name"], "size": latest["size"],
                   "age_hours": age_h} if latest else None,
        "count": len(entries),
    }
