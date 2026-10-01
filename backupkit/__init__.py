"""backupkit — backup berlapis dengan verifikasi pemulihan.

Filosofi: backup yang tidak pernah diuji restore = bukan backup, cuma harapan.
Karena itu setiap backup menghasilkan manifest (daftar file + sha256) dan perintah
`verify` benar-benar mengekstrak sampel lalu membandingkan hash-nya.

Zero dependency: hanya standard library Python 3.11+ (tomllib) + tool sistem
(tar/gzip, opsional: gpg, rclone, sqlite3).
"""

__version__ = "0.1.0"
