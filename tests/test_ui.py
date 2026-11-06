"""Unit tests for UI assets (HTML, CSS, JavaScript, and Webview Bridge)."""

from __future__ import annotations

import unittest
from pathlib import Path

from ui.bridge import AssistantBridge


class TestUIAssets(unittest.TestCase):
    """Validate that the web UI bundle contains all required beginner-friendly components."""

    @classmethod
    def setUpClass(cls):
        ui_dir = Path(__file__).resolve().parent.parent / "ui"
        cls.html_path = ui_dir / "index.html"
        cls.css_path = ui_dir / "app.css"
        cls.js_path = ui_dir / "app.js"
        cls.html_content = cls.html_path.read_text(encoding="utf-8")
        cls.css_content = cls.css_path.read_text(encoding="utf-8")
        cls.js_content = cls.js_path.read_text(encoding="utf-8")

    def test_html_file_exists(self):
        self.assertTrue(self.html_path.is_file())

    def test_css_file_exists(self):
        self.assertTrue(self.css_path.is_file())

    def test_js_file_exists(self):
        self.assertTrue(self.js_path.is_file())

    def test_html_contains_brand_and_badges(self):
        self.assertIn("Linux AI Assistant", self.html_content)
        self.assertIn('id="badge-system"', self.html_content)
        self.assertIn('id="badge-provider"', self.html_content)
        self.assertIn('id="btn-settings"', self.html_content)

    def test_html_contains_starter_chips(self):
        self.assertIn('id="starter-panel"', self.html_content)
        self.assertIn("📦 Software Setup", self.html_content)
        self.assertIn("⚡ Hardware & Drivers", self.html_content)
        self.assertIn("⚙️ System Settings", self.html_content)
        self.assertIn("🌐 Network & DNS", self.html_content)
        self.assertIn("🩺 System Health", self.html_content)

    def test_html_contains_command_bar(self):
        self.assertIn('id="prompt-input"', self.html_content)
        self.assertIn('id="btn-send"', self.html_content)
        self.assertIn('id="btn-stop"', self.html_content)

    def test_html_contains_terminal_drawer(self):
        self.assertIn('id="terminal-drawer"', self.html_content)
        self.assertIn('id="terminal-content"', self.html_content)
        self.assertIn('id="btn-toggle-terminal"', self.html_content)
        self.assertIn('id="btn-clear-terminal"', self.html_content)

    def test_html_contains_settings_modal_fields(self):
        self.assertIn('id="settings-modal"', self.html_content)
        self.assertIn('id="btn-refresh-system"', self.html_content)
        self.assertIn('id="select-skill"', self.html_content)
        self.assertIn('id="select-theme"', self.html_content)
        self.assertIn('id="select-provider"', self.html_content)
        self.assertIn('id="input-api-key"', self.html_content)
        self.assertIn('id="btn-test-key"', self.html_content)
        self.assertIn('id="select-model"', self.html_content)
        self.assertIn('id="btn-save-settings"', self.html_content)

    def test_html_contains_theme_toggle_button(self):
        self.assertIn('id="btn-theme-toggle"', self.html_content)
        self.assertIn('id="theme-icon-sun"', self.html_content)
        self.assertIn('id="theme-icon-moon"', self.html_content)

    def test_html_skill_levels_present(self):
        self.assertIn('value="Beginner"', self.html_content)
        self.assertIn('value="Intermediate"', self.html_content)
        self.assertIn('value="Advanced"', self.html_content)

    def test_html_all_providers_present(self):
        for prov in ["groq", "gemini", "openrouter", "cerebras", "mistral", "ollama"]:
            self.assertIn(f'value="{prov}"', self.html_content)

    def test_css_tokens_and_classes(self):
        self.assertIn("--bg-canvas: #09090b", self.css_content)
        self.assertIn('[data-theme="light"]', self.css_content)
        self.assertIn("--bg-canvas: #f8fafc", self.css_content)
        self.assertIn("--emerald-main: #10b981", self.css_content)
        self.assertIn(".approval-card", self.css_content)
        self.assertIn(".terminal-drawer", self.css_content)
        self.assertIn(".risk-badge", self.css_content)
        self.assertIn(".modal-overlay", self.css_content)

    def test_js_event_handlers_and_bridge_calls(self):
        self.assertIn("window.__onAssistantEvent", self.js_content)
        self.assertIn("response_chunk", self.js_content)
        self.assertIn("response_ready", self.js_content)
        self.assertIn("approval_requested", self.js_content)
        self.assertIn("command_output", self.js_content)
        self.assertIn("command_finished", self.js_content)
        self.assertIn("applyTheme", self.js_content)
        self.assertIn("toggleTheme", self.js_content)
        self.assertIn("window.pywebview.api.submit_prompt", self.js_content)
        self.assertIn("window.pywebview.api.approve_command", self.js_content)
        self.assertIn("window.pywebview.api.reject_command", self.js_content)
        self.assertIn("window.pywebview.api.save_settings", self.js_content)
        self.assertIn("window.pywebview.api.set_theme", self.js_content)
        self.assertIn("window.pywebview.api.refresh_system_profile", self.js_content)

    def test_js_ansi_and_markdown_helpers(self):
        self.assertIn("function ansiToHtml", self.js_content)
        self.assertIn("function formatMarkdown", self.js_content)


if __name__ == "__main__":
    unittest.main()
