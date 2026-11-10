"""Tests for in-app local AI setup (agent/local_setup.py + the install script)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from agent import local_setup as ls
from executor.pty_runner import is_high_risk

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-ollama-user.sh"


class TestNextStep(unittest.TestCase):
    def status(self, **kw):
        base = dict(binary="/home/u/.local/bin/ollama", version="0.40", models=["qwen3.5:4b"],
                    user_systemd=True, ram_gb=16.0)
        base.update(kw)
        return ls.LocalStatus(**base)

    def test_order_install_start_pull_ready(self):
        self.assertEqual(ls.next_step(self.status(binary=None, version=None, models=[]))["title"],
                         "Install Ollama, the local AI engine")
        self.assertIn("Start", ls.next_step(self.status(version=None, models=[]))["title"])
        self.assertIn("Download", ls.next_step(self.status(models=[]))["title"])
        self.assertIsNone(ls.next_step(self.status()))

    def test_model_by_memory(self):
        self.assertEqual(ls.recommend_model(8)[0], "qwen3.5:2b")
        self.assertEqual(ls.recommend_model(15.1)[0], "qwen3.5:4b")
        self.assertEqual(ls.recommend_model(32)[0], "qwen3.5:9b")
        self.assertEqual(ls.recommend_model(0)[0], "qwen3.5:4b")  # unknown: assume 16 GB

    def test_pull_uses_recommended_model(self):
        step = ls.next_step(self.status(models=[], ram_gb=64))
        self.assertTrue(step["command"].endswith("pull qwen3.5:9b"))

    def test_service_enables_integrated_gpu_only_without_nvidia(self):
        unit = ls.service_unit("/x/ollama", self.status(integrated_gpu=True))
        self.assertIn("OLLAMA_IGPU_ENABLE=1", unit)
        self.assertIn("ExecStart=/x/ollama serve", unit)
        self.assertNotIn("IGPU", ls.service_unit("/x/ollama", self.status(integrated_gpu=True, nvidia=True)))

    def test_start_without_systemd_runs_detached(self):
        step = ls.start_step(self.status(version=None, user_systemd=False))
        self.assertTrue(step["command"].startswith("setsid -f env"))

    def test_steps_are_safe_rootless_cards(self):
        st = self.status(binary=None, version=None, models=[])
        for step in (ls.install_step(), ls.start_step(st), ls.pull_step(st)):
            with self.subTest(step=step["title"]):
                self.assertFalse(step["requires_root"])
                self.assertNotIn("sudo", step["command"])
                self.assertFalse(is_high_risk(step["command"]))
                self.assertTrue(step["effects"])

    def test_usable_model_prefers_known_models(self):
        self.assertEqual(ls.usable_model(self.status(models=["llama3.2:3b", "qwen3.5:4b"])), "qwen3.5:4b")
        self.assertEqual(ls.usable_model(self.status(models=["llama3.2:3b"])), "llama3.2:3b")


@unittest.skipUnless(shutil.which("zstd") and shutil.which("bash"), "needs bash and zstd")
class TestInstallScript(unittest.TestCase):
    """Runs the real installer against a tiny fake release (no network)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        rel = self.tmp / "release"
        (rel / "pkg" / "bin").mkdir(parents=True)
        (rel / "pkg" / "lib" / "ollama" / "cuda_v12").mkdir(parents=True)
        fake = rel / "pkg" / "bin" / "ollama"
        fake.write_text("#!/bin/sh\necho 'ollama version is 9.9.9'\n")
        fake.chmod(0o755)
        (rel / "pkg" / "lib" / "ollama" / "cuda_v12" / "big.so").write_text("x")
        tar_path = rel / "ollama-linux-amd64.tar"
        with tarfile.open(tar_path, "w") as tar:
            for item in ("bin", "lib"):
                tar.add(rel / "pkg" / item, arcname=item)
        subprocess.run(["zstd", "-q", "--rm", str(tar_path)], check=True)
        self.asset = rel / "ollama-linux-amd64.tar.zst"
        digest = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        (rel / "sha256sum.txt").write_text(f"{digest}  ./ollama-linux-amd64.tar.zst\n")
        self.release = rel

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self):
        env = dict(os.environ, OLLAMA_DOWNLOAD_BASE=f"file://{self.release}",
                   INSTALL_PREFIX=str(self.tmp / "prefix"), KEEP_CUDA="0")
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)

    def test_installs_into_prefix_without_cuda(self):
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stderr)
        link = self.tmp / "prefix" / "bin" / "ollama"
        self.assertTrue(link.is_symlink())
        self.assertIn("9.9.9", subprocess.run([str(link)], capture_output=True, text=True).stdout)
        has_nvidia = Path("/proc/driver/nvidia/version").exists()
        cuda = self.tmp / "prefix" / "opt" / "ollama" / "lib" / "ollama" / "cuda_v12"
        self.assertEqual(cuda.exists(), has_nvidia)

    def test_rejects_tampered_download(self):
        self.asset.write_bytes(self.asset.read_bytes() + b"tampered")
        res = self.run_script()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("checksum", res.stdout + res.stderr)
        self.assertFalse((self.tmp / "prefix" / "bin" / "ollama").exists())


if __name__ == "__main__":
    unittest.main()
