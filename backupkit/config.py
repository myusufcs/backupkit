"""Baca & validasi konfigurasi (TOML)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Konfigurasi tidak valid."""


@dataclass
class Source:
    paths: list[str] = field(default_factory=list)
    sqlite: list[str] = field(default_factory=list)
    commands: dict[str, str] = field(default_factory=dict)
    exclude: list[str] = field(default_factory=list)


@dataclass
class Target:
    kind: str = "local"          # "local" | "rclone"
    path: str = ""               # folder lokal, atau remote rclone (mis. "Drive:backup")
    keep: int = 7                # simpan N backup terbaru (0 = tanpa rotasi)
    keep_days: int = 0           # atau: hapus yang lebih tua dari N hari


@dataclass
class Crypto:
    enabled: bool = False
    passphrase_file: str = ""    # enkripsi simetris AES256
    recipient: str = ""          # alternatif: kunci publik GPG


@dataclass
class Verify:
    sample: int = 3              # jumlah file yang diuji restore
    auto: bool = False           # jalankan verify otomatis selesai backup


@dataclass
class Notify:
    on_failure: str = ""         # perintah shell yang dijalankan bila backup gagal


@dataclass
class Config:
    name: str
    staging_dir: str
    sources: Source
    targets: list[Target]
    crypto: Crypto
    verify: Verify
    notify: Notify
    path: str = ""

    def expand(self, value: str) -> str:
        return os.path.expanduser(os.path.expandvars(value))


def _req(data: dict, key: str, where: str):
    if key not in data:
        raise ConfigError(f"bagian [{where}] wajib punya kunci {key!r}")
    return data[key]


def load(path: str | Path) -> Config:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ConfigError(f"config tidak ditemukan: {p}")
    with p.open("rb") as fh:
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"TOML tidak valid: {exc}") from exc

    job = data.get("backup", {})
    src = data.get("sources", {})
    cfg = Config(
        name=str(_req(job, "name", "backup")),
        staging_dir=str(job.get("staging_dir", "/tmp/backupkit")),
        sources=Source(
            paths=[str(x) for x in src.get("paths", [])],
            sqlite=[str(x) for x in src.get("sqlite", [])],
            commands={str(k): str(v) for k, v in (src.get("commands") or {}).items()},
            exclude=[str(x) for x in src.get("exclude", [])],
        ),
        targets=[],
        crypto=Crypto(**{k: v for k, v in (data.get("crypto") or {}).items()
                         if k in {"enabled", "passphrase_file", "recipient"}}),
        verify=Verify(**{k: v for k, v in (data.get("verify") or {}).items()
                         if k in {"sample", "auto"}}),
        notify=Notify(**{k: v for k, v in (data.get("notify") or {}).items()
                         if k in {"on_failure"}}),
        path=str(p),
    )

    for t in data.get("targets", []):
        cfg.targets.append(Target(
            kind=str(t.get("kind", "local")),
            path=str(_req(t, "path", "targets")),
            keep=int(t.get("keep", 7)),
            keep_days=int(t.get("keep_days", 0)),
        ))

    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if not cfg.targets:
        raise ConfigError("minimal satu [[targets]] harus didefinisikan")
    for t in cfg.targets:
        if t.kind not in {"local", "rclone"}:
            raise ConfigError(f"target kind tidak dikenal: {t.kind!r}")
        if not t.path:
            raise ConfigError("target tanpa path")
    if not (cfg.sources.paths or cfg.sources.sqlite or cfg.sources.commands):
        raise ConfigError("sources kosong — tidak ada yang dibackup")
    if cfg.crypto.enabled:
        if not cfg.crypto.passphrase_file and not cfg.crypto.recipient:
            raise ConfigError("crypto.enabled = true butuh passphrase_file atau recipient")
        if cfg.crypto.passphrase_file and not Path(
                os.path.expanduser(cfg.crypto.passphrase_file)).is_file():
            raise ConfigError(f"passphrase file tidak ada: {cfg.crypto.passphrase_file}")
    if cfg.verify.sample < 0:
        raise ConfigError("verify.sample tidak boleh negatif")
