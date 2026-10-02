# backupkit

**Backup berlapis dengan verifikasi pemulihan** — bukan sekadar "file backup-nya ada".

> Backup yang tidak pernah diuji restore itu bukan backup, cuma harapan.
> `backupkit` menutup celah itu: setiap backup punya **manifest** (daftar file + sha256),
> dan perintah `verify` **benar-benar mengekstrak sampel file** lalu membandingkan hash-nya —
> termasuk memverifikasi ulang **salinan yang sudah diunggah ke target**.

[![CI](https://github.com/myusufcs/backupkit/actions/workflows/ci.yml/badge.svg)](https://github.com/myusufcs/backupkit/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Zero deps](https://img.shields.io/badge/dependencies-none-success)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-17%20passing-brightgreen)

---

## Masalah yang dipecahkan

Backup rutin biasanya "sukses" di log, tapi baru ketahuan rusak saat benar-benar dibutuhkan —
file terpotong, arsip korup, enkripsi salah passphrase, atau backup tidak pernah terunggah.
`backupkit` menganggap backup belum selesai sampai **isinya terbukti bisa dipulihkan**.

## Cara kerjanya

```
  sumber                kemas                amankan            simpan            buktikan
┌──────────┐      ┌──────────────┐      ┌───────────┐      ┌──────────┐      ┌──────────────┐
│ folder   │      │ manifest     │      │ gpg       │      │ lokal /  │      │ uji-restore  │
│ file     │─────▶│ (sha256 per  │─────▶│ AES-256   │─────▶│ rclone   │─────▶│ sampel file  │
│ sqlite   │      │  file)       │      │ (opsional)│      │ (rotasi) │      │ + sha256     │
│ command  │      └──────────────┘      └───────────┘      └──────────┘      └──────────────┘
└──────────┘            │                                        │                  │
                        └── ikut masuk ke dalam arsip ───────────┘         bandingkan hash
```

1. **Kumpulkan** — folder/file, snapshot konsisten SQLite (`.backup`), dan keluaran perintah (`mysqldump`, `pg_dump`, …).
2. **Manifest** — setiap file di-hash sha256 → daftar bukti, ikut dibungkus ke dalam arsip.
3. **Kemas** — `tar.gz`.
4. **Amankan** — enkripsi simetris GPG AES-256 (atau ke kunci publik).
5. **Simpan** — ke folder lokal dan/atau remote rclone (Drive, S3, B2, …) + rotasi otomatis.
6. **Buktikan** — uji-restore sampel di lokal **dan** dari salinan yang sudah diunggah.
7. **Lapor** — kalau ada satu langkah gagal, hook `on_failure` dipanggil (WhatsApp/Telegram/email).

## Instalasi

Tanpa dependensi Python — hanya standard library 3.11+ dan tool sistem `tar`/`gzip`.
Opsional: `gpg` (enkripsi), `rclone` (target remote), `sqlite3` (snapshot DB).

```bash
git clone https://github.com/myusufcs/backupkit
cd backupkit
python3 -m backupkit init -c job.toml     # tulis contoh konfigurasi
```

## Pemakaian

```bash
python3 -m backupkit -c job.toml backup            # backup + verifikasi + unggah + rotasi
python3 -m backupkit -c job.toml verify            # uji-restore backup terbaru
python3 -m backupkit -c job.toml verify <nama>     # uji backup tertentu
python3 -m backupkit -c job.toml restore <nama> --to /tmp/pulih
python3 -m backupkit -c job.toml list
python3 -m backupkit -c job.toml status
python3 -m backupkit -c job.toml prune
```

Semua perintah mengembalikan **exit code ≠ 0 saat gagal**, jadi langsung cocok dipasang di systemd/cron.

## Konfigurasi (`job.toml`)

```toml
[backup]
name = "server-web"
staging_dir = "/tmp/backupkit"

[sources]
paths   = ["/etc/nginx", "/var/www"]
sqlite  = ["/srv/app/instance/app.sqlite3"]
exclude = ["*.tmp", "node_modules", ".cache"]

[sources.commands]                    # keluaran perintah ikut dibackup
db_dump = "mysqldump --single-transaction -u root app"

[crypto]
enabled = true
passphrase_file = "~/.config/backupkit/pass"

[verify]
sample = 5          # jumlah file yang diuji-restore
auto   = true       # verifikasi juga salinan yang sudah diunggah

[notify]
on_failure = "openclaw message send --channel whatsapp --target +628xxx --message \"$BACKUPKIT_SUBJECT\""

[[targets]]
kind = "local"
path = "/mnt/backup-eksternal"
keep = 7            # simpan 7 backup terbaru

[[targets]]
kind = "rclone"
path = "Drive:backupkit"
keep_days = 30      # hapus yang lebih tua dari 30 hari
```

## Contoh keluaran nyata

```
$ python3 -m backupkit -c job.toml backup
[00:04:12] mengumpulkan sumber ke /tmp/bk-demo/staging/…/demo
[00:04:12] membuat manifest + hash
[00:04:12] mengemas arsip (12 file)
[00:04:12] mengenkripsi (gpg)
[00:04:12] arsip siap: demo-20261002-000412.tar.gz.gpg (11,832 byte)
[00:04:13] verifikasi lokal LULUS
[00:04:13] unggah ke local:/tmp/bk-demo/dest
[00:04:13]   verifikasi salinan remote LULUS (/tmp/bk-demo/dest)
[00:04:13] selesai: backup sukses ✅

$ python3 -m backupkit -c job.toml verify
verifikasi : /tmp/backupkit-fetch-…/demo-20261002-000412.tar.gz.gpg
dibuat     : 2026-10-01T17:04:12+00:00 oleh parkee
isi        : 12 file, 36,529 byte
terenkripsi: ya
diuji      : 3 file
  OK   backupkit/verify.py (4,588 byte) sha256=662e039c8ca36353…
  OK   backupkit/archive.py (3,353 byte) sha256=507720fca7ac2ed4…
  OK   backupkit/__init__.py (443 byte) sha256=d79abbc75f499aa5…
HASIL      : LULUS ✅
```

## Dijalankan otomatis (systemd)

```ini
# ~/.config/systemd/user/backupkit.service
[Unit]
Description=backupkit harian

[Service]
Type=oneshot
Environment=BACKUPKIT_STATE_DIR=%h/.local/state/backupkit
ExecStart=%h/projects/backupkit/.venv/bin/python -m backupkit -c %h/backupkit/job.toml backup
Nice=10
IOSchedulingClass=idle
```

```ini
# ~/.config/systemd/user/backupkit.timer
[Unit]
Description=Jalankan backupkit tiap hari

[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=900
Persistent=true

[Install]
WantedBy=timers.target
```

`Persistent=true` membuat backup yang terlewat (laptop mati) langsung dikejar saat boot berikutnya.

## Status & uji

```bash
python3 -m unittest discover -s tests -v     # 17 test
```

Cakupan: validasi konfigurasi, pembuatan manifest + hash, round-trip arsip,
**verifikasi positif & negatif** (arsip yang diubah harus GAGAL), rotasi retensi,
snapshot SQLite, enkripsi GPG, serta alur backup→verify→**restore** end-to-end.

## Batasan yang jujur

- Snapshot SQLite memakai `sqlite3 .backup`. Kalau `sqlite3` tidak ada, file disalin mentah
  (bisa tidak konsisten) dan ditandai `fallback: true` di ringkasan.
- Backup **full**, bukan incremental — kesederhanaan lebih diutamakan daripada ukuran.
- Verifikasi menguji **sampel** (default 3 file), bukan seluruh isi. Naikkan `verify.sample`
  atau jalankan `verify` manual untuk pemeriksaan menyeluruh.
- Rotasi hanya menghapus backup milik job ini (berdasarkan pola nama), bukan file lain di folder.

## Lisensi

MIT — lihat [LICENSE](LICENSE). Copyright (c) 2026 M Yusuf Chairul Saleh.
