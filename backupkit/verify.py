"""Verifikasi: baca manifest, lalu uji-restore sampel file dan bandingkan sha256.

Inilah pembeda backupkit: bukan sekadar "file backup ada", tapi bukti bahwa
isinya benar-benar bisa dipulihkan dan tidak berubah.
"""
from __future__ import annotations

import random
import shutil
import tempfile
from pathlib import Path

from . import archive, crypto
from .config import Crypto


class VerifyError(Exception):
    """Verifikasi gagal di level struktur."""


def verify(backup: Path, crypto_cfg: Crypto | None = None,
           sample: int = 3, workdir: Path | None = None) -> dict:
    """Verifikasi satu file backup (.tar.gz atau .tar.gz.gpg)."""
    backup = Path(backup)
    if not backup.is_file():
        raise VerifyError(f"backup tidak ada: {backup}")

    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="backupkit-verify-"))
    tmp.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "backup": str(backup),
        "size": backup.stat().st_size,
        "encrypted": backup.suffix == ".gpg",
        "ok": False,
        "checked": [],
        "errors": [],
    }

    try:
        plain = backup
        if backup.suffix == ".gpg":
            if not crypto_cfg or not (crypto_cfg.passphrase_file or crypto_cfg.recipient):
                raise VerifyError("backup terenkripsi tapi konfigurasi crypto tidak ada")
            plain = crypto.decrypt(backup, crypto_cfg, tmp / "payload.tar.gz")
            report["decrypted_bytes"] = plain.stat().st_size

        if not archive.is_valid_tar(plain):
            raise VerifyError("isi bukan arsip tar yang valid")

        manifest = archive.read_manifest(plain)
        files = manifest.get("files", [])
        report["created_at"] = manifest.get("created_at")
        report["hostname"] = manifest.get("hostname")
        report["total_files"] = manifest.get("total_files", len(files))
        report["total_bytes"] = manifest.get("total_bytes", 0)

        member_names = {m.lstrip("./") for m in archive.members(plain)}
        missing = [f["path"] for f in files if f["path"] not in member_names]
        if missing:
            report["errors"].append(
                f"{len(missing)} file di manifest tidak ada di arsip "
                f"(contoh: {missing[:3]})")

        picks = _pick(files, sample)
        out_dir = tmp / "restore-test"
        for item in picks:
            member = next((m for m in member_names if m == item["path"]), item["path"])
            try:
                archive.extract(plain, out_dir, f"./{member}" if not member.startswith("./") else member)
                restored = out_dir / member
                if not restored.is_file():
                    report["errors"].append(f"{item['path']}: tidak muncul setelah ekstrak")
                    continue
                actual = archive.sha256_file(restored)
                if actual != item["sha256"]:
                    report["errors"].append(
                        f"{item['path']}: sha256 beda (manifest {item['sha256'][:12]}…, "
                        f"hasil {actual[:12]}…)")
                else:
                    report["checked"].append({
                        "path": item["path"], "size": item["size"],
                        "sha256": actual, "match": True})
            except Exception as exc:       # noqa: BLE001
                report["errors"].append(f"{item['path']}: {type(exc).__name__}: {exc}")

        report["ok"] = not report["errors"]
        return report
    finally:
        if workdir is None:
            shutil.rmtree(tmp, ignore_errors=True)


def _pick(files: list[dict], sample: int) -> list[dict]:
    if sample <= 0 or not files:
        return []
    if len(files) <= sample:
        return list(files)
    return random.sample(files, sample)


def format_report(report: dict) -> str:
    lines = [f"verifikasi : {report['backup']}"]
    lines.append(f"dibuat     : {report.get('created_at', '?')} oleh {report.get('hostname', '?')}")
    lines.append(f"isi        : {report.get('total_files', 0)} file, "
                 f"{report.get('total_bytes', 0):,} byte")
    lines.append(f"terenkripsi: {'ya' if report.get('encrypted') else 'tidak'}")
    lines.append(f"diuji      : {len(report.get('checked', []))} file")
    for c in report.get("checked", []):
        lines.append(f"  OK   {c['path']} ({c['size']:,} byte) sha256={c['sha256'][:16]}…")
    for e in report.get("errors", []):
        lines.append(f"  GAGAL {e}")
    lines.append("HASIL      : " + ("LULUS ✅" if report["ok"] else "TIDAK LULUS ❌"))
    return "\n".join(lines)
