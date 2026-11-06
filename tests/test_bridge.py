"""Unit tests for ui/bridge.py – AssistantBridge."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from agent.orchestrator import AgentOrchestrator, OrchestratorState
from ui.bridge import AssistantBridge


class TestAssistantBridge(unittest.TestCase):
    """Validate that AssistantBridge correctly connects frontend actions to orchestrator."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.generate.return_value = MagicMock()
        self.orchestrator = AgentOrchestrator(self.mock_client)
        self.mock_window = MagicMock()
        self.bridge = AssistantBridge(self.orchestrator, self.mock_window)

    def test_get_initial_state_structure(self):
        with patch("ui.bridge.load_profile", return_value={"os_name": "Fedora", "skill_level": "Beginner"}), \
             patch("ui.bridge.get_dynamic_session_info", return_value={"xdg_current_desktop": "niri"}), \
             patch("ui.bridge.get_api_key", return_value="gsk_test1234567890"):
            state = self.bridge.get_initial_state()
            self.assertIn("profile", state)
            self.assertIn("session", state)
            self.assertIn("providers", state)
            self.assertEqual(state["skill_level"], "Beginner")
            self.assertTrue(state["has_active_key"])
            self.assertIn("...", state["masked_key"])

    def test_submit_prompt_empty(self):
        self.assertFalse(self.bridge.submit_prompt(""))
        self.assertFalse(self.bridge.submit_prompt("   "))

    def test_submit_prompt_delegates_to_orchestrator(self):
        with patch.object(self.orchestrator, "send_message") as mock_send:
            res = self.bridge.submit_prompt("Install curl")
            self.assertTrue(res)
            mock_send.assert_called_once_with("Install curl")

    def test_approve_command_delegates(self):
        with patch.object(self.orchestrator, "handle_user_approval") as mock_app:
            self.bridge.approve_command("dnf install -y curl")
            mock_app.assert_called_once_with("dnf install -y curl")

    def test_reject_command_delegates(self):
        with patch.object(self.orchestrator, "handle_user_rejection") as mock_rej:
            self.bridge.reject_command("Unsafe")
            mock_rej.assert_called_once_with("Unsafe")

    def test_abort_execution_delegates(self):
        with patch.object(self.orchestrator, "abort") as mock_abort:
            self.bridge.abort_execution()
            mock_abort.assert_called_once()

    def test_clear_chat_delegates(self):
        with patch.object(self.orchestrator, "clear_history") as mock_clear:
            self.bridge.clear_chat()
            mock_clear.assert_called_once()

    def test_dispatch_evaluates_js(self):
        self.bridge._dispatch("test_event", {"data": 123})
        self.mock_window.evaluate_js.assert_called_once()
        args, _ = self.mock_window.evaluate_js.call_args
        self.assertIn("test_event", args[0])
        self.assertIn("123", args[0])

    def test_open_external_url(self):
        with patch("webbrowser.open", return_value=True) as mock_open:
            res = self.bridge.open_external_url("https://groq.com")
            self.assertTrue(res)
            mock_open.assert_called_once_with("https://groq.com")

    def test_check_ollama(self):
        with patch("ui.bridge.check_ollama_status", return_value=True), \
             patch("ui.bridge.fetch_available_models", return_value=["qwen2.5:7b"]):
            res = self.bridge.check_ollama()
            self.assertTrue(res["running"])
            self.assertEqual(res["models"], ["qwen2.5:7b"])

    def test_test_key_invalid_format(self):
        res = self.bridge.test_key("groq", "invalid_short_key")
        self.assertFalse(res["valid"])
        self.assertFalse(res["format_valid"])

    def test_save_settings_updates_profile_and_client(self):
        with patch("ui.bridge.update_skill_level") as mock_skill, \
             patch("ui.bridge.update_provider_settings") as mock_prov, \
             patch("ui.bridge.save_api_key") as mock_save_key, \
             patch("ui.bridge.create_llm_client") as mock_create_client:
            res = self.bridge.save_settings(
                skill_level="Advanced",
                provider="groq",
                model_name="llama-3.3-70b-versatile",
                api_key="gsk_valid_key_123456",
            )
            self.assertEqual(res["status"], "success")
            mock_skill.assert_called_once_with("Advanced")
            mock_prov.assert_called_once_with("groq", model_name="llama-3.3-70b-versatile")
            mock_save_key.assert_called_once_with("gsk_valid_key_123456", provider="groq")
            mock_create_client.assert_called_once()

    def test_refresh_system_profile(self):
        with patch("ui.bridge.get_or_create_profile", return_value={"os_name": "Fedora", "os_version": "45"}), \
             patch("ui.bridge.get_dynamic_session_info", return_value={"xdg_current_desktop": "niri"}), \
             patch.object(self.orchestrator, "update_system_prompt") as mock_update_prompt:
            res = self.bridge.refresh_system_profile()
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["profile"]["os_version"], "45")
            mock_update_prompt.assert_called_once()

    def test_set_theme_persists_and_returns_status(self):
        with patch("ui.bridge.update_theme") as mock_update_theme:
            res = self.bridge.set_theme("light")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["theme"], "light")
            mock_update_theme.assert_called_once_with("light")


if __name__ == "__main__":
    unittest.main()
