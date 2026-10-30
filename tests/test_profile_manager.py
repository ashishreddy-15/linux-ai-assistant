"""Unit tests for config/profile_manager.py."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from config.profile_manager import (
    DEFAULT_SKILL_LEVEL,
    DEFAULT_PROVIDER,
    DEFAULT_GROQ_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OLLAMA_MODEL,
    VALID_SKILL_LEVELS,
    detect_cpu_architecture,
    detect_os_release,
    detect_primary_package_manager,
    detect_secondary_helpers,
    detect_static_system_profile,
    detect_total_ram,
    get_dynamic_session_info,
    get_or_create_profile,
    load_profile,
    save_profile,
    update_provider_settings,
    update_skill_level,
)


class TestProfileManager(unittest.TestCase):
    def test_detect_os_release(self):
        info = detect_os_release()
        self.assertIsInstance(info, dict)
        self.assertIn("name", info)
        self.assertIn("version", info)
        self.assertIn("id", info)
        self.assertIn("pretty_name", info)

    def test_detect_primary_package_manager(self):
        pm = detect_primary_package_manager()
        if pm is not None:
            self.assertIn(pm, ("pacman", "dnf", "apt", "zypper"))

    def test_detect_secondary_helpers(self):
        helpers = detect_secondary_helpers()
        self.assertIsInstance(helpers, list)
        for h in helpers:
            self.assertIn(h, ("flatpak", "snap", "yay", "paru"))

    def test_detect_cpu_architecture(self):
        arch = detect_cpu_architecture()
        self.assertIsInstance(arch, str)
        self.assertTrue(len(arch) > 0)

    def test_detect_total_ram(self):
        ram = detect_total_ram()
        self.assertIsInstance(ram, dict)
        self.assertGreater(ram["total_ram_bytes"], 0)
        self.assertGreater(ram["total_ram_gb"], 0)

    def test_detect_static_system_profile(self):
        profile = detect_static_system_profile()
        self.assertIsInstance(profile, dict)
        self.assertEqual(profile["skill_level"], DEFAULT_SKILL_LEVEL)
        self.assertIn("os_name", profile)
        self.assertIn("os_version", profile)
        self.assertIn("primary_package_manager", profile)
        self.assertIn("secondary_helpers", profile)
        self.assertIn("cpu_architecture", profile)
        self.assertIn("total_ram_gb", profile)

    def test_save_and_load_profile(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "system_profile.json"
            dummy = {
                "os_name": "TestOS",
                "os_version": "1.0",
                "os_id": "testos",
                "primary_package_manager": "apt",
                "secondary_helpers": ["snap"],
                "cpu_architecture": "x86_64",
                "total_ram_bytes": 8589934592,
                "total_ram_gb": 8.0,
                "skill_level": "Intermediate",
            }
            save_profile(dummy, path=target)
            self.assertTrue(target.exists())

            loaded = load_profile(path=target)
            self.assertEqual(loaded, dummy)

    def test_get_or_create_profile(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "subdir" / "system_profile.json"
            self.assertFalse(target.exists())

            # Initial creation
            profile1 = get_or_create_profile(path=target)
            self.assertTrue(target.exists())
            self.assertEqual(profile1["skill_level"], "Intermediate")

            # Modify skill_level on disk
            profile1["skill_level"] = "Advanced"
            save_profile(profile1, path=target)

            # Retrieval should return existing profile, not regenerate
            profile2 = get_or_create_profile(path=target)
            self.assertEqual(profile2["skill_level"], "Advanced")

            # Force refresh should reset
            refreshed = get_or_create_profile(path=target, force_refresh=True)
            self.assertEqual(refreshed["skill_level"], "Intermediate")

    def test_update_skill_level(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "system_profile.json"
            get_or_create_profile(path=target)

            updated = update_skill_level("Beginner", path=target)
            self.assertEqual(updated["skill_level"], "Beginner")

            updated = update_skill_level("Advanced", path=target)
            self.assertEqual(updated["skill_level"], "Advanced")

            with self.assertRaises(ValueError):
                update_skill_level("InvalidLevel", path=target)

    def test_update_provider_settings(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "system_profile.json"
            get_or_create_profile(path=target)

            updated = update_provider_settings("groq", "qwen-2.5-coder-32b", path=target)
            self.assertEqual(updated["active_provider"], "groq")
            self.assertEqual(updated["model_name"], "qwen-2.5-coder-32b")

            updated = update_provider_settings("gemini", path=target)
            self.assertEqual(updated["active_provider"], "gemini")
            self.assertEqual(updated["model_name"], DEFAULT_GEMINI_MODEL)

            updated = update_provider_settings("ollama", path=target)
            self.assertEqual(updated["active_provider"], "ollama")
            self.assertEqual(updated["model_name"], DEFAULT_OLLAMA_MODEL)

            with self.assertRaises(ValueError):
                update_provider_settings("invalid_provider", path=target)

    def test_get_dynamic_session_info(self):
        dyn = get_dynamic_session_info()
        self.assertIsInstance(dyn, dict)
        self.assertIn("xdg_session_type", dyn)
        self.assertIn("xdg_current_desktop", dyn)
        self.assertIn("home_disk_total_bytes", dyn)
        self.assertIn("home_disk_free_gb", dyn)


if __name__ == "__main__":
    unittest.main()
