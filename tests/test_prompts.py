"""Tests for agent/prompts.py – build_system_prompt."""

from __future__ import annotations

import unittest

from agent.prompts import build_system_prompt, VALID_SKILL_LEVELS

# Reusable fixtures
_PROFILE = {
    "os_name": "Arch Linux",
    "os_version": "rolling",
    "os_id": "arch",
    "os_pretty_name": "Arch Linux",
    "primary_package_manager": "pacman",
    "secondary_helpers": ["flatpak", "yay"],
    "cpu_architecture": "x86_64",
    "total_ram_bytes": 16_000_000_000,
    "total_ram_gb": 14.9,
    "skill_level": "Intermediate",
}

_SESSION = {
    "xdg_session_type": "wayland",
    "xdg_current_desktop": "Hyprland",
    "home_disk_total_bytes": 240_000_000_000,
    "home_disk_used_bytes": 50_000_000_000,
    "home_disk_free_bytes": 190_000_000_000,
    "home_disk_free_gb": 177.0,
    "home_disk_total_gb": 223.5,
}


class TestBuildSystemPrompt(unittest.TestCase):

    def _build(self, skill_level: str) -> str:
        return build_system_prompt(_PROFILE, _SESSION, skill_level)

    # --- Environment injection ---

    def test_contains_os_name(self):
        prompt = self._build("Intermediate")
        self.assertIn("Arch Linux", prompt)

    def test_contains_primary_package_manager(self):
        prompt = self._build("Intermediate")
        self.assertIn("pacman", prompt)

    def test_contains_secondary_helpers(self):
        prompt = self._build("Intermediate")
        self.assertIn("flatpak", prompt)
        self.assertIn("yay", prompt)

    def test_contains_cpu_arch(self):
        prompt = self._build("Intermediate")
        self.assertIn("x86_64", prompt)

    def test_contains_desktop_session(self):
        prompt = self._build("Intermediate")
        self.assertIn("Hyprland", prompt)
        self.assertIn("wayland", prompt)

    def test_contains_disk_info(self):
        prompt = self._build("Intermediate")
        self.assertIn("177.0", prompt)

    def test_contains_ram(self):
        prompt = self._build("Intermediate")
        self.assertIn("14.9", prompt)

    # --- Invariants always present ---

    def test_hitl_rule_always_present(self):
        for level in VALID_SKILL_LEVELS:
            prompt = self._build(level)
            self.assertIn("Human-in-the-Loop", prompt, f"HITL missing for {level}")
            self.assertIn("approve", prompt.lower(), f"'approve' missing for {level}")

    def test_rootless_preference_always_present(self):
        for level in VALID_SKILL_LEVELS:
            prompt = self._build(level)
            self.assertIn("~/.local", prompt, f"rootless path missing for {level}")

    def test_root_elevation_rule_always_present(self):
        for level in VALID_SKILL_LEVELS:
            prompt = self._build(level)
            self.assertIn("pkexec", prompt, f"pkexec rule missing for {level}")
            self.assertIn("root elevation", prompt.lower(), f"root elevation missing for {level}")

    def test_propose_shell_command_tool_mentioned(self):
        for level in VALID_SKILL_LEVELS:
            prompt = self._build(level)
            self.assertIn("propose_shell_command", prompt)

    def test_inspect_manual_tool_mentioned(self):
        for level in VALID_SKILL_LEVELS:
            prompt = self._build(level)
            self.assertIn("inspect_manual", prompt)

    def test_freedesktop_path_resolution_present(self):
        for level in VALID_SKILL_LEVELS:
            prompt = self._build(level)
            self.assertIn("FreeDesktop Path Resolution", prompt)
            self.assertIn("absolute", prompt.lower())

    def test_five_domains_strategy_present(self):
        for level in VALID_SKILL_LEVELS:
            prompt = self._build(level)
            self.assertIn("5-Domain Execution Strategy", prompt)
            self.assertIn("Software Installation", prompt)
            self.assertIn("Hardware & Drivers", prompt)
            self.assertIn("Configuration & Settings", prompt)
            self.assertIn("Network & Connectivity", prompt)
            self.assertIn("Diagnostics & General Troubleshooting", prompt)
            self.assertIn("lspci", prompt)
            self.assertIn("ip link", prompt)
            self.assertIn("systemctl --failed", prompt)
            self.assertIn("journalctl -n 50", prompt)

    def test_no_postman_hardcoding(self):
        for level in VALID_SKILL_LEVELS:
            prompt = self._build(level)
            self.assertNotIn("postman", prompt.lower())

    # --- Persona differences ---

    def test_beginner_no_jargon_instruction(self):
        prompt = self._build("Beginner")
        self.assertIn("Beginner", prompt)
        # Beginner persona should mention plain English or outcome
        self.assertIn("plain", prompt.lower())

    def test_intermediate_educational_instruction(self):
        prompt = self._build("Intermediate")
        self.assertIn("Intermediate", prompt)
        self.assertIn("explain", prompt.lower())

    def test_advanced_terse_instruction(self):
        prompt = self._build("Advanced")
        self.assertIn("Advanced", prompt)
        self.assertIn("terse", prompt.lower())

    # --- Fallback for unknown skill level ---

    def test_unknown_skill_level_falls_back_to_intermediate(self):
        prompt = build_system_prompt(_PROFILE, _SESSION, "Unknown")
        self.assertIn("Intermediate", prompt)

    # --- Returns a non-empty string ---

    def test_returns_string(self):
        prompt = self._build("Intermediate")
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 200)


if __name__ == "__main__":
    unittest.main()
