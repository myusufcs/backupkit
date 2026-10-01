"""CLI backupkit.

    python3 -m backupkit -c job.toml backup
    python3 -m backupkit -c job.toml verify [nama]
    python3 -m backupkit -c job.toml restore <nama> --to /tmp/restore
    python3 -m backupkit -c job.toml list
    python3 -m backupkit -c job.toml status
    python3 -m backupkit -c job.toml prune
    python3 -m backupkit -c job.toml run --sample            # buat file contoh
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, config, runner, store
from .verify import format_report

EXAMPLE = """\
# backupkit — contoh konfigurasi
[backup]
name = "contoh"
staging_dir = "/tmp/backupkit"

[sources]
paths = ["~/Documents/penting"]
sqlite = []                       # mis. ["~/app/instance/app.sqlite3"]
exclude = ["*.tmp", "node_modules", ".cache"]

[sources.commands]                # keluaran perintah ikut dibackup
# db_dump = "mysqldump -u root app"

[crypto]
enabled = false                   # true = wajib isi passphrase_file / recipient
passphrase_file = "~/.config/backupkit/pass"
# recipient = "nama@email.com"

[verify]
sample = 3                        # jumlah file yang diuji-restore
auto = true                       # verifikasi salinan remote setelah unggah

[notify]
# on_failure = "openclaw message send --channel whatsapp --target +62xxx --message \\"$BACKUPKIT_SUBJECT\\""

[[targets]]
kind = "local"
path = "~/backups/lokal"
keep = 7                          # simpan 7 terbaru
# keep_days = 30

# [[targets]]
# kind = "rclone"
# path = "Drive:backupkit"
# keep = 10
"""


def _load(args):
    try:
        return config.load(args.config)
    except config.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def cmd_init(args) -> int:
    p = Path(args.config).expanduser()
    if p.exists():
        print(f"sudah ada: {p}", file=sys.stderr)
        return 1
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(EXAMPLE, encoding="utf-8")
    print(f"contoh config ditulis ke {p}")
    return 0


def cmd_backup(args) -> int:
    cfg = _load(args)
    try:
        res = runner.run_backup(cfg, keep_staging=args.keep_staging)
    except Exception as exc:                       # noqa: BLE001
        print(f"backup GAGAL: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    return 0


def cmd_verify(args) -> int:
    cfg = _load(args)
    try:
        report = runner.run_verify(cfg, args.name, args.target)
    except Exception as exc:                       # noqa: BLE001
        print(f"verifikasi GAGAL: {exc}", file=sys.stderr)
        return 1
    print(format_report(report))
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


def cmd_restore(args) -> int:
    cfg = _load(args)
    name = args.name or runner.latest_backup(cfg, args.target)
    if not name:
        print("tidak ada backup untuk dipulihkan", file=sys.stderr)
        return 1
    try:
        dest = runner.run_restore(cfg, name, Path(args.to), args.target)
    except Exception as exc:                       # noqa: BLE001
        print(f"restore GAGAL: {exc}", file=sys.stderr)
        return 1
    print(f"dipulihkan: {name} -> {dest}")
    return 0


def cmd_list(args) -> int:
    cfg = _load(args)
    rows = runner.run_list(cfg)
    if not rows:
        print("(belum ada backup)")
        return 0
    for r in rows:
        print(f"  {r['mtime'].isoformat(timespec='seconds')}  {r['size']:>12,}  "
              f"{r['name']}  [{r['target']}]")
    return 0


def cmd_status(args) -> int:
    cfg = _load(args)
    st = runner.run_status(cfg)
    latest = st["latest"]
    print(f"job            : {st['job']}")
    if latest:
        print(f"backup terbaru : {latest['name']} ({latest['size']:,} byte, "
              f"{latest['age_hours']} jam lalu)")
        print(f"jumlah backup  : {st['count']}")
    else:
        print("backup terbaru : (belum ada)")
    lv = st["state"].get("last_verify")
    if lv:
        print(f"verifikasi     : {'LULUS ✅' if lv['ok'] else 'GAGAL ❌'} "
              f"({lv.get('checked', 0)} file diuji, {lv['at']})")
    le = st["state"].get("last_error")
    if le:
        print(f"error terakhir : {le['detail'][:120]} ({le['at']})")
    return 0


def cmd_prune(args) -> int:
    cfg = _load(args)
    total = 0
    for t in cfg.targets:
        removed = store.prune(t)
        total += len(removed)
        print(f"{t.kind}:{t.path} -> {len(removed)} dihapus")
    print(f"total {total} backup lama dihapus")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="backupkit",
        description="Backup berlapis + verifikasi pemulihan (uji-restore sha256)")
    ap.add_argument("-c", "--config", default="backupkit.toml", help="file konfigurasi TOML")
    ap.add_argument("--version", action="version", version=f"backupkit {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="tulis file contoh konfigurasi")

    b = sub.add_parser("backup", help="jalankan backup")
    b.add_argument("--keep-staging", action="store_true", help="jangan hapus folder staging")
    b.add_argument("--json", action="store_true")

    v = sub.add_parser("verify", help="verifikasi + uji-restore sampel")
    v.add_argument("name", nargs="?", help="nama file backup (default: terbaru)")
    v.add_argument("--target", type=int, default=0, help="indeks target (default 0)")
    v.add_argument("--json", action="store_true")

    r = sub.add_parser("restore", help="pulihkan backup ke folder")
    r.add_argument("name", nargs="?", help="nama file backup (default: terbaru)")
    r.add_argument("--to", required=True, help="folder tujuan")
    r.add_argument("--target", type=int, default=0)

    sub.add_parser("list", help="daftar backup")
    sub.add_parser("status", help="ringkasan status job")
    sub.add_parser("prune", help="terapkan retensi sekarang")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return {
        "init": cmd_init,
        "backup": cmd_backup,
        "verify": cmd_verify,
        "restore": cmd_restore,
        "list": cmd_list,
        "status": cmd_status,
        "prune": cmd_prune,
    }[args.cmd](args)
