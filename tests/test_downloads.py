"""Tests for verified-source downloads (network replaced by fakes)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import downloads
from agent.installer import InstallError

AG_PAGE = (
    b'<a href="https://storage.googleapis.com/antigravity-public/antigravity-hub/2.31.0-1/linux-x64/Antigravity.tar.gz">'
    b'<a href="https://edgedl.me.gvt1.com/edgedl/release2/x/antigravity/stable/2.6.0-1/linux-x64/Antigravity%20IDE.tar.gz">'
    b'<a href="https://evil.example.com/linux-x64/Antigravity.tar.gz">'
    b'<a href="https://storage.googleapis.com/antigravity-public/antigravity-hub/2.31.0-1/linux-arm/Antigravity.tar.gz">'
)


class Base(unittest.TestCase):
    def setUp(self):
        self.dl = Path(tempfile.mkdtemp())
        for target, value in (("DOWNLOADS", self.dl), ("remote_size", lambda url: 1000), ("machine", lambda: "x86_64")):
            p = patch.object(downloads, target, value)
            p.start()
            self.addCleanup(p.stop)


class TestVendors(Base):
    def test_antigravity_needs_a_choice_and_ignores_foreign_hosts(self):
        with patch.object(downloads, "http_get", return_value=AG_PAGE):
            sources = downloads.antigravity_sources()
            self.assertEqual({s.label for s in sources}, {"Antigravity", "Antigravity IDE"})
            with self.assertRaises(InstallError) as ctx:
                downloads.resolve("antigravity", "dnf", [])
            self.assertIn("more than one", str(ctx.exception))
            src = downloads.resolve("antigravity ide", "dnf", [])
        self.assertEqual(src.filename, "Antigravity IDE.tar.gz")
        self.assertEqual(src.host, "edgedl.me.gvt1.com")

    def test_proton_picks_newest_for_this_fedora(self):
        listing = b'<a href="protonvpn-stable-release-1.0.3-1.noarch.rpm">x</a><a href="protonvpn-stable-release-1.0.10-1.noarch.rpm">'
        with patch.object(downloads, "os_release", return_value={"ID": "fedora", "VERSION_ID": "45"}), \
             patch.object(downloads, "http_get", return_value=listing) as get:
            src = downloads.resolve("Proton VPN", "dnf", [])
        self.assertIn("fedora-45-stable", get.call_args[0][0])
        self.assertEqual(src.filename, "protonvpn-stable-release-1.0.10-1.noarch.rpm")

    def test_proton_refuses_other_distros(self):
        with patch.object(downloads, "os_release", return_value={"ID": "arch"}), self.assertRaises(InstallError):
            downloads.resolve("protonvpn", "pacman", [])

    def test_github_filters_and_prefers_system_format(self):
        release = {"tag_name": "v1", "assets": [
            {"name": "App.dmg", "browser_download_url": "https://github.com/o/r/a.dmg", "size": 1},
            {"name": "app-arm64.tar.gz", "browser_download_url": "https://github.com/o/r/b", "size": 1},
            {"name": "app-x64.tar.gz", "browser_download_url": "https://github.com/o/r/c.tar.gz", "size": 5, "digest": "sha256:" + "a" * 64},
            {"name": "app-x86_64.rpm", "browser_download_url": "https://github.com/o/r/d.rpm", "size": 6, "digest": "sha256:" + "b" * 64},
        ]}
        with patch.object(downloads, "http_get", return_value=json.dumps(release).encode()):
            src = downloads.resolve("o/r", "dnf", [])
        self.assertEqual(src.filename, "app-x86_64.rpm")
        self.assertEqual(src.sha256, "b" * 64)

    def test_unknown_app_has_no_source(self):
        with self.assertRaises(InstallError):
            downloads.resolve("randomthing", "dnf", [])


class TestSafety(Base):
    def test_model_cannot_supply_a_url(self):
        with self.assertRaises(InstallError):
            downloads.resolve("https://evil.example.com/x.rpm", "dnf", [])
        with self.assertRaises(InstallError):
            downloads.resolve("https://evil.example.com/x.rpm", "dnf", ["https://other.example.com/y.rpm"])

    def test_user_pasted_link_is_allowed(self):
        url = "https://example.com/dl/tool-1.0.tar.gz"
        self.assertEqual(downloads.user_urls([f"please get {url}."]), [url])
        self.assertEqual(downloads.resolve(url, "dnf", [url]).filename, "tool-1.0.tar.gz")

    def test_plan_verifies_checksum_and_deletes_on_mismatch(self):
        data = b"hello"
        src = downloads.Source("Tool", "file:///unused", "tool.tar.gz", 5, hashlib.sha256(data).hexdigest())
        with patch.object(downloads, "resolve", return_value=src):
            plan = downloads.build_download("tool", "dnf", [])
        self.assertIn("Checks the SHA-256", " ".join(plan.effects))
        # Run the checksum part against a local file instead of curl.
        part = self.dl / "tool.tar.gz.part"
        script = "\n".join(l for l in plan.script.splitlines() if not l.startswith("curl"))
        part.write_bytes(data)
        self.assertEqual(subprocess.run(["bash", "-c", script], capture_output=True).returncode, 0)
        self.assertTrue((self.dl / "tool.tar.gz").exists())
        part.write_bytes(b"tampered")
        (self.dl / "tool.tar.gz").unlink()
        bad = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse(part.exists())

    def test_existing_file_is_not_overwritten(self):
        (self.dl / "tool.tar.gz").write_bytes(b"x")
        self.assertEqual(downloads._free_name("tool.tar.gz"), "tool-1.tar.gz")


if __name__ == "__main__":
    unittest.main()


class TestOrchestratorOffer(Base):
    def _orch(self):
        from unittest.mock import MagicMock

        from agent.orchestrator import AgentOrchestrator

        orch = AgentOrchestrator(MagicMock())
        seen = []
        orch.approval_requested.connect(seen.append)
        return orch, seen

    def test_missing_file_with_verified_source_becomes_a_download_card(self):
        from google.genai import types

        from agent import installer

        orch, seen = self._orch()
        src = downloads.Source("Proton VPN", "https://repo.protonvpn.com/x.rpm", "x.rpm", 7000)
        with patch.object(installer, "DOWNLOAD_DIRS", (self.dl,)), \
             patch.object(downloads, "resolve", return_value=src):
            orch._batch.append(types.FunctionCall(name="inspect_download", args={"name": "protonvpn"}, id="1"))
            orch._process_next()
        self.assertEqual(seen[0]["title"], "Download Proton VPN")
        self.assertIn("repo.protonvpn.com", seen[0]["effects"][0])
        self.assertEqual(orch._pending_tool_call.name, "inspect_download")  # history stays consistent

    def test_unknown_app_is_just_not_found(self):
        from agent import installer

        with patch.object(installer, "DOWNLOAD_DIRS", (self.dl,)):
            kind, text = downloads.inspect_or_offer("randomthing", "dnf", [])
        self.assertEqual(kind, "text")
        self.assertIn("couldn't find", text)

    def test_existing_file_is_inspected_not_downloaded(self):
        from agent import installer

        (self.dl / "tool.AppImage").write_bytes(b"\x7fELF" + b"\0" * 10)
        with patch.object(installer, "DOWNLOAD_DIRS", (self.dl,)):
            kind, text = downloads.inspect_or_offer("tool", "dnf", [])
        self.assertEqual(kind, "text")
        self.assertIn("AppImage", text)
