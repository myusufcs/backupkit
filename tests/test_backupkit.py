"""Uji backupkit: config, arsip, verifikasi, rotasi, dan alur backup end-to-end."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backupkit import archive, config, crypto, runner, sources, store, verify  # noqa: E402

HAS_GPG = shutil.which("gpg") is not None
HAS_SQLITE = shutil.which("sqlite3") is not None


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="backupkit-test-"))
        os.environ["BACKUPKIT_STATE_DIR"] = str(self.tmp / "state")
        runner.STATE_DIR = Path(os.environ["BACKUPKIT_STATE_DIR"])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("BACKUPKIT_STATE_DIR", None)

    def write(self, rel: str, content: str) -> Path:
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def toml(self, body: str) -> Path:
        p = self.tmp / "job.toml"
        p.write_text(body)
        return p


# ------------------------------------------------------------------ config

GOOD_CFG = """
[backup]
name = "uji"
staging_dir = "{tmp}/staging"

[sources]
paths = ["{tmp}/data"]

[[targets]]
kind = "local"
path = "{tmp}/dest"
keep = 2

[verify]
sample = 2
"""


class TestConfig(TempCase):
    def test_load_valid(self):
        self.write("data/a.txt", "halo")
        cfg = config.load(self.toml(GOOD_CFG.format(tmp=self.tmp)))
        self.assertEqual(cfg.name, "uji")
        self.assertEqual(len(cfg.targets), 1)
        self.assertEqual(cfg.targets[0].keep, 2)

    def test_tolak_target_kosong(self):
        body = GOOD_CFG.format(tmp=self.tmp).split("[[targets]]")[0]
        with self.assertRaises(config.ConfigError):
            config.load(self.toml(body))

    def test_tolak_sumber_kosong(self):
        body = GOOD_CFG.format(tmp=self.tmp).replace('paths = ["%s/data"]' % self.tmp, "paths = []")
        with self.assertRaises(config.ConfigError):
            config.load(self.toml(body))

    def test_tolak_crypto_tanpa_kunci(self):
        body = GOOD_CFG.format(tmp=self.tmp) + '\n[crypto]\nenabled = true\n'
        with self.assertRaises(config.ConfigError):
            config.load(self.toml(body))

    def test_tolak_kind_tidak_dikenal(self):
        body = GOOD_CFG.format(tmp=self.tmp).replace('kind = "local"', 'kind = "ftp"')
        with self.assertRaises(config.ConfigError):
            config.load(self.toml(body))


# ------------------------------------------------------------------ archive

class TestArchive(TempCase):
    def test_manifest_dan_pack_roundtrip(self):
        staging = self.tmp / "stage"
        (staging / "sub").mkdir(parents=True)
        (staging / "a.txt").write_text("aaa")
        (staging / "sub/b.txt").write_text("bbb")

        m = archive.make_manifest(staging, {"job": "t"})
        self.assertEqual(m["total_files"], 2)
        self.assertEqual(m["total_bytes"], 6)

        out = self.tmp / "out.tar.gz"
        archive.pack(staging, out)
        self.assertTrue(archive.is_valid_tar(out))

        back = archive.read_manifest(out)
        self.assertEqual({f["path"] for f in back["files"]}, {"a.txt", "sub/b.txt"})
        for f in back["files"]:
            self.assertEqual(len(f["sha256"]), 64)

    def test_sha256_konsisten(self):
        p = self.write("x.bin", "isi")
        self.assertEqual(archive.sha256_file(p), archive.sha256_file(p))
        self.assertEqual(len(archive.sha256_file(p)), 64)


# ------------------------------------------------------------------ verify

class TestVerify(TempCase):
    def _make(self) -> Path:
        staging = self.tmp / "stage"
        staging.mkdir()
        (staging / "satu.txt").write_text("satu")
        (staging / "dua.txt").write_text("dua")
        archive.make_manifest(staging, {"job": "t"})
        out = self.tmp / "b.tar.gz"
        archive.pack(staging, out)
        return out

    def test_verifikasi_lulus(self):
        rep = verify.verify(self._make(), None, sample=2, workdir=self.tmp / "v")
        self.assertTrue(rep["ok"], rep.get("errors"))
        self.assertEqual(len(rep["checked"]), 2)

    def test_verifikasi_gagal_kalau_isi_berubah(self):
        out = self._make()
        # tusuk arsip: ganti isi satu file langsung di dalam tar
        tampered = self.tmp / "tampered.tar.gz"
        stage2 = self.tmp / "stage2"
        archive.extract(out, stage2)
        (stage2 / "satu.txt").write_text("DIUBAH TANPA UPDATE MANIFEST")
        archive.pack(stage2, tampered)
        rep = verify.verify(tampered, None, sample=5, workdir=self.tmp / "v2")
        self.assertFalse(rep["ok"])
        self.assertTrue(any("sha256 beda" in e for e in rep["errors"]))


# ------------------------------------------------------------------ store

class TestStore(TempCase):
    def test_push_list_prune_lokal(self):
        src = self.write("bak.tar.gz", "x")
        target = config.Target(kind="local", path=str(self.tmp / "dest"), keep=2)
        store.push(src, target)

        for i in range(4):
            f = self.write(f"bak{i}.tar.gz", "y")
            store.push(f, target)
        self.assertEqual(len(store.list_backups(target)), 5)

        removed = store.prune(target)
        self.assertEqual(len(removed), 3)
        self.assertEqual(len(store.list_backups(target)), 2)


# ------------------------------------------------------------------ sources

class TestSources(TempCase):
    def test_salin_path_dan_perintah(self):
        self.write("data/isi.txt", "konten")
        s = config.Source(paths=[str(self.tmp / "data")],
                          commands={"echo_halo": "echo halo"})
        out = self.tmp / "collected"
        summary = sources.collect(s, out)
        self.assertTrue((out / "data" / "isi.txt").is_file())
        self.assertEqual((out / "commands" / "echo_halo.out").read_text().strip(), "halo")
        self.assertEqual(summary["command:echo_halo"]["type"], "command")

    def test_perintah_gagal_melempar(self):
        s = config.Source(commands={"boom": "exit 3"})
        with self.assertRaises(sources.SourceError):
            sources.collect(s, self.tmp / "c2")

    @unittest.skipUnless(HAS_SQLITE, "sqlite3 tidak terpasang")
    def test_snapshot_sqlite(self):
        db = self.tmp / "x.sqlite3"
        subprocess.run(["sqlite3", str(db), "create table t(a); insert into t values (1);"],
                       check=True)
        s = config.Source(sqlite=[str(db)])
        out = self.tmp / "c3"
        summary = sources.collect(s, out)
        snapshot = out / "sqlite" / "x.sqlite3.sqlite3"
        self.assertTrue(snapshot.is_file())
        self.assertFalse(summary[str(db)]["fallback"])


# ------------------------------------------------------------------ crypto

@unittest.skipUnless(HAS_GPG, "gpg tidak terpasang")
class TestCrypto(TempCase):
    def test_enkripsi_dekripsi(self):
        passfile = self.write("pass", "rahasia-sekali")
        cfg = config.Crypto(enabled=True, passphrase_file=str(passfile))
        src = self.write("plain.txt", "isi rahasia")
        enc = crypto.encrypt(src, cfg)
        self.assertTrue(enc.is_file())
        self.assertNotIn(b"isi rahasia", enc.read_bytes())
        dec = crypto.decrypt(enc, cfg, self.tmp / "out.txt")
        self.assertEqual(dec.read_text(), "isi rahasia")


# ------------------------------------------------------------------ end-to-end

class TestRunner(TempCase):
    def test_backup_end_to_end_lokal(self):
        self.write("data/satu.txt", "satu")
        self.write("data/dua.txt", "dua")
        cfg = config.load(self.toml(GOOD_CFG.format(tmp=self.tmp)))
        result = runner.run_backup(cfg)

        self.assertTrue(result["ok"])
        self.assertEqual(result["total_files"], 2)
        self.assertEqual(len(result["targets"]), 1)

        dest = self.tmp / "dest"
        artifacts = list(dest.glob("uji-*.tar.gz"))
        self.assertEqual(len(artifacts), 1)

        st = runner.run_status(cfg)
        self.assertIsNotNone(st["latest"])
        self.assertTrue(st["state"]["last_verify"]["ok"])

        # verifikasi ulang dari salinan di target
        rep = runner.run_verify(cfg)
        self.assertTrue(rep["ok"], rep.get("errors"))

        # restore nyata — sumber "data" disalin apa adanya ke dalam arsip
        out = runner.run_restore(cfg, artifacts[0].name, self.tmp / "restored")
        self.assertTrue((out / "data" / "satu.txt").is_file())
        self.assertEqual((out / "data" / "dua.txt").read_text(), "dua")

    def test_rotasi_dijalankan(self):
        self.write("data/f.txt", "x")
        cfg = config.load(self.toml(GOOD_CFG.format(tmp=self.tmp)))
        cfg.targets[0].keep = 1
        dest = self.tmp / "dest"
        dest.mkdir()
        for i in range(3):
            (dest / f"uji-lama{i}.tar.gz").write_text("lama")
        runner.run_backup(cfg)
        self.assertEqual(len(list(dest.glob("*.tar.gz"))), 1)

    def test_notifikasi_dipanggil_saat_gagal(self):
        marker = self.tmp / "notified.txt"
        body = GOOD_CFG.format(tmp=self.tmp).replace(
            '[verify]', f'[notify]\non_failure = "echo ok > {marker}"\n\n[verify]')
        body = body.replace('paths = ["%s/data"]' % self.tmp,
                            'paths = ["%s/tidak-ada"]' % self.tmp)
        cfg = config.load(self.toml(body))
        with self.assertRaises(Exception):
            runner.run_backup(cfg)
        self.assertTrue(marker.is_file(), "hook notifikasi tidak dijalankan")
        self.assertFalse(runner.run_status(cfg)["state"]["last_error"]["notified"] is None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
