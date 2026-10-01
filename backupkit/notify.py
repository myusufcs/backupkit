"""Notifikasi kegagalan: jalankan perintah shell yang ditentukan user."""
from __future__ import annotations

import subprocess

from .config import Notify


def notify_failure(cfg_notify: Notify, subject: str, detail: str = "") -> bool:
    """Jalankan `on_failure` dengan env BACKUPKIT_SUBJECT / BACKUPKIT_DETAIL."""
    if not cfg_notify.on_failure:
        return False
    env = {
        "BACKUPKIT_SUBJECT": subject,
        "BACKUPKIT_DETAIL": detail[:2000],
    }
    import os
    merged = {**os.environ, **env}
    try:
        proc = subprocess.run(cfg_notify.on_failure, shell=True, env=merged,
                              capture_output=True, text=True, timeout=60)
        return proc.returncode == 0
    except Exception:                      # noqa: BLE001
        return False
