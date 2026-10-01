"""Pembungkus GPG: enkripsi simetris (AES256) atau ke kunci publik."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .config import Crypto


class CryptoError(Exception):
    """Operasi GPG gagal."""


def available() -> bool:
    return shutil.which("gpg") is not None


def encrypt(src: Path, crypto: Crypto) -> Path:
    if not available():
        raise CryptoError("gpg tidak terpasang")
    out = src.with_suffix(src.suffix + ".gpg")
    cmd = ["gpg", "--batch", "--yes", "--quiet", "-o", str(out)]

    if crypto.recipient:
        cmd += ["--recipient", crypto.recipient, "--encrypt", str(src)]
    else:
        passfile = os.path.expanduser(crypto.passphrase_file)
        if not Path(passfile).is_file():
            raise CryptoError(f"passphrase file tidak ada: {passfile}")
        cmd += ["--passphrase-file", passfile, "--symmetric",
                "--cipher-algo", "AES256", str(src)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        raise CryptoError(f"enkripsi gagal: {proc.stderr.strip()[:300]}")
    return out


def decrypt(src: Path, crypto: Crypto, dest: Path) -> Path:
    if not available():
        raise CryptoError("gpg tidak terpasang")
    cmd = ["gpg", "--batch", "--yes", "--quiet", "-o", str(dest)]
    if crypto.recipient:
        cmd += ["--decrypt", str(src)]
    else:
        passfile = os.path.expanduser(crypto.passphrase_file)
        cmd += ["--passphrase-file", passfile, "--decrypt", str(src)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dest.is_file():
        raise CryptoError(f"dekripsi gagal: {proc.stderr.strip()[:300]}")
    return dest
