"""Unit tests for UI components (theme, approval card, onboarding, and main window)."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QLabel

# Ensure QApplication is initialized for widget tests
_app = QApplication.instance() or QApplication(sys.argv)

from agent.orchestrator import AgentOrchestrator, OrchestratorState
from ui.approval_card import ApprovalCard, _CollapsibleSection
from ui.main_window import ChatBubble, MainWindow, StarterChipsWidget, strip_ansi
from ui.onboarding_modal import FREE_KEY_PROVIDERS, OnboardingModal, SKILL_DESCRIPTIONS


class TestUIHelpers(unittest.TestCase):
    def test_strip_ansi(self):
        raw = "\x1b[31;1mError:\x1b[0m \x1b[32mpackage not found\x1b[0m\r\n"
        clean = strip_ansi(raw)
        self.assertEqual(clean, "Error: package not found\r\n")

    def test_strip_ansi_clean_string(self):
        plain = "Normal text without escapes"
        self.assertEqual(strip_ansi(plain), plain)


class TestApprovalCard(unittest.TestCase):
    def test_beginner_layout_has_collapsible(self):
        card = ApprovalCard(skill_level="Beginner")
        payload = {
            "tool_name": "propose_shell_command",
            "command": "pacman -S git",
            "explanation": "Install git version control.",
            "requires_root": True,
            "high_risk": False,
        }
        card.load_payload(payload)
        self.assertTrue(card.isVisible())

        # Check for collapsible section
        collapsibles = card.findChildren(_CollapsibleSection)
        self.assertEqual(len(collapsibles), 1)
        self.assertFalse(collapsibles[0]._toggle.isChecked())

        # Toggle it
        collapsibles[0]._toggle.click()
        self.assertTrue(collapsibles[0]._toggle.isChecked())

    def test_intermediate_layout_read_only(self):
        card = ApprovalCard(skill_level="Intermediate")
        payload = {
            "tool_name": "propose_shell_command",
            "command": "echo test",
            "explanation": "Print test",
            "requires_root": False,
            "high_risk": False,
        }
        card.load_payload(payload)
        # Should not have collapsible section
        collapsibles = card.findChildren(_CollapsibleSection)
        self.assertEqual(len(collapsibles), 0)

        cmd = card.current_command()
        self.assertEqual(cmd, "echo test")

    def test_advanced_layout_editable(self):
        card = ApprovalCard(skill_level="Advanced")
        payload = {
            "tool_name": "propose_shell_command",
            "command": "pacman -S original",
            "explanation": "Technical info",
            "requires_root": False,
            "high_risk": False,
        }
        card.load_payload(payload)
        self.assertIsNotNone(card._cmd_edit)
        self.assertFalse(card._cmd_edit.isReadOnly())

        # Edit command
        card._cmd_edit.setPlainText("pacman -S modified")
        self.assertEqual(card.current_command(), "pacman -S modified")

    def test_high_risk_badge(self):
        card = ApprovalCard(skill_level="Intermediate")
        payload = {
            "tool_name": "propose_shell_command",
            "command": "rm -rf /",
            "explanation": "Root deletion",
            "requires_root": True,
            "high_risk": True,
        }
        card.load_payload(payload)
        self.assertTrue(card._warning_badge.isVisible())
        self.assertIn("DESTRUCTIVE", card._warning_badge.text())

    def test_desktop_entry_payload(self):
        card = ApprovalCard(skill_level="Intermediate")
        payload = {
            "tool_name": "create_desktop_entry",
            "app_name": "Postman",
            "exec_path": "/home/user/.local/opt/Postman",
            "icon_path": "/home/user/.local/opt/icon.png",
            "categories": "Development;",
            "comment": "API test client",
            "high_risk": False,
        }
        card.load_payload(payload)
        self.assertIn("[Desktop Entry]", card.current_command())
        self.assertIn("Postman", card.current_command())

    def test_signals_emitted(self):
        card = ApprovalCard(skill_level="Intermediate")
        card.load_payload({
            "tool_name": "propose_shell_command",
            "command": "uptime",
            "explanation": "Show uptime",
            "requires_root": False,
            "high_risk": False,
        })
        approved_results = []
        rejected_results = []
        card.approved.connect(approved_results.append)
        card.rejected.connect(rejected_results.append)

        card._approve_btn.click()
        self.assertEqual(approved_results, ["uptime"])

        # Reset and test reject
        card.load_payload({
            "tool_name": "propose_shell_command",
            "command": "uptime",
            "explanation": "Show uptime",
            "requires_root": False,
            "high_risk": False,
        })
        card._reject_btn.click()
        self.assertEqual(len(rejected_results), 1)


class TestOnboardingModal(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        from ui.onboarding_modal import _active_workers

        for w in list(_active_workers):
            w.wait(3000)
    def test_modal_initialization(self):
        modal = OnboardingModal()
        self.assertEqual(modal.windowTitle(), "Linux AI Assistant – Setup & Configuration")
        self.assertGreater(modal._skill_combo.count(), 0)
        self.assertIn("Beginner", SKILL_DESCRIPTIONS)
        self.assertGreater(modal._provider_combo.count(), 0)
        self.assertGreater(modal._model_combo.count(), 0)

    def test_provider_switching_updates_models_and_placeholder(self):
        modal = OnboardingModal()

        # Select Groq
        modal._provider_combo.setCurrentIndex(0)
        self.assertEqual(modal._current_provider(), "groq")
        self.assertIn("llama-3.3-70b-versatile", [modal._model_combo.itemText(i) for i in range(modal._model_combo.count())])
        self.assertIn("gsk_", modal._key_input.placeholderText())

        # Select Gemini
        idx_gemini = modal._provider_combo.findData("gemini")
        modal._provider_combo.setCurrentIndex(idx_gemini)
        self.assertEqual(modal._current_provider(), "gemini")
        self.assertIn("gemini-2.5-flash", [modal._model_combo.itemText(i) for i in range(modal._model_combo.count())])
        self.assertIn("AIzaSy", modal._key_input.placeholderText())

    def test_model_combo_editable_and_custom_entry(self):
        modal = OnboardingModal()
        self.assertIsInstance(modal.model_dropdown, QComboBox)
        self.assertTrue(modal.model_dropdown.isEditable())
        self.assertIs(modal.model_input, modal.model_dropdown)
        self.assertIs(modal.model_field, modal.model_dropdown)
        modal.model_dropdown.setEditText("custom-fine-tuned-model-v1")
        self.assertEqual(modal.model_dropdown.currentText(), "custom-fine-tuned-model-v1")

    def test_on_test_result_populates_dynamic_models(self):
        modal = OnboardingModal()
        self.assertIsNotNone(modal._fetch_models_btn)

        # Trigger dynamic population
        modal._on_test_result(True, "verified", ["live-model-alpha", "live-model-beta"])
        items = [modal.model_dropdown.itemText(i) for i in range(modal.model_dropdown.count())]
        self.assertEqual(items, ["live-model-alpha", "live-model-beta"])
        self.assertIn("Retrieved 2 models", modal._status_label.text())

    def test_on_models_fetched_prefers_llama_3_1_8b_instant(self):
        modal = OnboardingModal()
        test_models = ["custom-model", "llama-3.1-8b-instant", "qwen-2.5-coder-32b"]
        modal._on_models_fetched(test_models)
        self.assertEqual(modal.model_dropdown.currentText(), "llama-3.1-8b-instant")

    def test_on_models_fetched_fallback_first(self):
        modal = OnboardingModal()
        test_models = ["alpha-model-v1", "beta-model-v2"]
        modal._on_models_fetched(test_models)
        self.assertEqual(modal.model_dropdown.currentText(), "alpha-model-v1")

    def test_save_settings_reads_model_dropdown(self):
        modal = OnboardingModal()
        modal.model_dropdown.setEditText("llama-3.1-8b-instant")
        saved_values = []
        modal.configuration_saved.connect(lambda s, k, p, m: saved_values.append((s, k, p, m)))
        modal._save_settings()
        self.assertEqual(len(saved_values), 1)
        self.assertEqual(saved_values[0][3], "llama-3.1-8b-instant")

    def test_onboarding_modal_ollama_selection_and_ui_state(self):
        with patch("config.keyring_manager.check_ollama_status", return_value=(True, "Ollama is running locally")):
            modal = OnboardingModal()
            idx = modal._provider_combo.findData("ollama")
            self.assertGreaterEqual(idx, 0)
            modal._provider_combo.setCurrentIndex(idx)

            # API Key input should be hidden and disabled
            self.assertFalse(modal._key_input.isVisible())
            self.assertFalse(modal._key_input.isEnabled())
            self.assertFalse(modal._toggle_btn.isVisible())

            # Test button should say "Test Connection"
            self.assertEqual(modal._test_btn.text(), "Test Connection")
            self.assertIn("http://127.0.0.1:11434", modal._key_hint.text())
            if modal._test_worker:
                modal._test_worker.wait(1000)

    def test_onboarding_modal_ollama_test_connection_warning(self):
        with patch("config.keyring_manager.check_ollama_status", return_value=(False, "Could not connect to Ollama daemon on localhost:11434")):
            modal = OnboardingModal()
            idx = modal._provider_combo.findData("ollama")
            modal._provider_combo.setCurrentIndex(idx)
            if modal._test_worker:
                modal._test_worker.wait(1000)

            # Simulate daemon not running
            modal._on_test_result(
                False, "Could not connect to Ollama daemon on localhost:11434", []
            )
            self.assertIn("Could not connect to Ollama daemon on localhost:11434", modal._status_label.text())
            self.assertIn("Run: systemctl start ollama or ollama serve", modal._status_label.text())

    def test_onboarding_modal_ollama_test_connection_success(self):
        with patch("config.keyring_manager.check_ollama_status", return_value=(True, "Ollama is running locally")):
            modal = OnboardingModal()
            idx = modal._provider_combo.findData("ollama")
            modal._provider_combo.setCurrentIndex(idx)
            if modal._test_worker:
                modal._test_worker.wait(1000)

            # Simulate daemon running with local models
            modal._on_test_result(
                True, "Ollama is running locally", ["qwen2.5-coder:7b", "llama3.2:3b"]
            )
            self.assertIn("Ollama is running locally", modal._status_label.text())
            items = [modal.model_dropdown.itemText(i) for i in range(modal.model_dropdown.count())]
            self.assertEqual(items, ["qwen2.5-coder:7b", "llama3.2:3b"])

        # Simulate daemon running with local models
        modal._on_test_result(
            True, "Ollama is running locally", ["qwen2.5-coder:7b", "llama3.2:3b"]
        )
        self.assertIn("Ollama is running locally", modal._status_label.text())
        items = [modal.model_dropdown.itemText(i) for i in range(modal.model_dropdown.count())]
        self.assertEqual(items, ["qwen2.5-coder:7b", "llama3.2:3b"])

    def test_modal_surface_cards_and_button_text(self):
        modal = OnboardingModal()
        # Verify Save button uses escaped ampersand so Qt renders 'Save & Continue' without mnemonic underscore
        self.assertEqual(modal._save_btn.text(), "Save && Continue")
        self.assertEqual(modal._save_btn.objectName(), "modalSaveButton")
        self.assertEqual(modal._cancel_btn.objectName(), "modalCancelButton")

    def test_modal_system_overview_metadata_badges(self):
        modal = OnboardingModal()
        # Check that environment metadata is displayed via modern badge pills rather than raw bullet strings
        badge_labels = [lbl.text() for lbl in modal.findChildren(QLabel) if "span style" in lbl.text()]
        self.assertGreaterEqual(len(badge_labels), 4)
        combined = " ".join(badge_labels)
        self.assertIn("OS:", combined)
        self.assertIn("Package Manager:", combined)
        self.assertIn("Desktop:", combined)
        self.assertIn("Hardware:", combined)
        self.assertNotIn("•", combined)

    def test_free_key_providers_dictionary(self):
        self.assertIn("gemini", FREE_KEY_PROVIDERS)
        self.assertIn("groq", FREE_KEY_PROVIDERS)
        self.assertIn("openrouter", FREE_KEY_PROVIDERS)
        self.assertIn("cerebras", FREE_KEY_PROVIDERS)
        self.assertIn("mistral", FREE_KEY_PROVIDERS)

        self.assertEqual(FREE_KEY_PROVIDERS["gemini"]["url"], "https://aistudio.google.com/apikey")
        self.assertIn("Google AI Studio", FREE_KEY_PROVIDERS["gemini"]["title"])
        self.assertIn("Create API Key", FREE_KEY_PROVIDERS["gemini"]["steps"])

        self.assertEqual(FREE_KEY_PROVIDERS["groq"]["url"], "https://console.groq.com/keys")
        self.assertIn("Groq Cloud", FREE_KEY_PROVIDERS["groq"]["title"])
        self.assertIn("gsk_", FREE_KEY_PROVIDERS["groq"]["steps"])

        self.assertEqual(FREE_KEY_PROVIDERS["openrouter"]["url"], "https://openrouter.ai/keys")
        self.assertIn("OpenRouter", FREE_KEY_PROVIDERS["openrouter"]["title"])
        self.assertIn(":free", FREE_KEY_PROVIDERS["openrouter"]["steps"])

        self.assertEqual(FREE_KEY_PROVIDERS["cerebras"]["url"], "https://cloud.cerebras.ai/")
        self.assertIn("Cerebras", FREE_KEY_PROVIDERS["cerebras"]["title"])

        self.assertEqual(FREE_KEY_PROVIDERS["mistral"]["url"], "https://console.mistral.ai/api-keys/")
        self.assertIn("Mistral", FREE_KEY_PROVIDERS["mistral"]["title"])

    def test_free_key_helper_ui_card_and_open_url(self):
        modal = OnboardingModal()
        modal.show()
        _app.processEvents()
        self.assertIsNotNone(modal._helper_card)
        self.assertIn("background: #14161b", modal._helper_card.styleSheet())
        self.assertIn("border: 1px solid #232630", modal._helper_card.styleSheet())

        # Test all 5 providers update guidance card text and portal URL
        providers_expected = [
            ("groq", "Groq Cloud API Key (Free)", "https://console.groq.com/keys", "gsk_"),
            ("gemini", "Google AI Studio API Key (Free)", "https://aistudio.google.com/apikey", "AIzaSy"),
            ("openrouter", "OpenRouter Free Key", "https://openrouter.ai/keys", "sk-or-v1-"),
            ("cerebras", "Cerebras Cloud API Key (Free)", "https://cloud.cerebras.ai/", "csk-"),
            ("mistral", "Mistral La Plateforme Key (Free Experiment)", "https://console.mistral.ai/api-keys/", "API Keys"),
        ]

        for prov_id, expected_title, expected_url, step_fragment in providers_expected:
            with self.subTest(provider=prov_id):
                idx = modal._provider_combo.findData(prov_id)
                self.assertGreaterEqual(idx, 0, f"Provider {prov_id} should exist in combo")
                modal._provider_combo.setCurrentIndex(idx)
                _app.processEvents()
                self.assertFalse(modal._helper_card.isHidden())
                self.assertTrue(modal._helper_card.isVisible())
                self.assertIn(expected_title, modal._helper_title.text())
                self.assertIn(step_fragment, modal._helper_steps.text())

                with patch("PySide6.QtGui.QDesktopServices.openUrl") as mock_open:
                    modal._helper_link_btn.click()
                    mock_open.assert_called_once()
                    called_url = mock_open.call_args[0][0].toString()
                    self.assertEqual(called_url, expected_url)

        # Test Ollama hides the helper card
        idx_ollama = modal._provider_combo.findData("ollama")
        modal._provider_combo.setCurrentIndex(idx_ollama)
        _app.processEvents()
        self.assertTrue(modal._helper_card.isHidden())
        self.assertFalse(modal._helper_card.isVisible())

    def test_key_format_validation_and_autofill(self):
        modal = OnboardingModal()
        modal.show()
        _app.processEvents()
        badge = modal._key_badge

        # Groq key validation
        idx_groq = modal._provider_combo.findData("groq")
        modal._provider_combo.setCurrentIndex(idx_groq)

        modal._key_input.setText("gsk_valid_groq_key_1234567890")
        _app.processEvents()
        self.assertFalse(badge.isHidden())
        self.assertTrue(badge.isVisible())
        self.assertIn("✓ Valid Key Format", badge.text())
        self.assertIn("10b981", badge.styleSheet())

        # Invalid key hides badge
        modal._key_input.setText("random_invalid_key")
        _app.processEvents()
        self.assertTrue(badge.isHidden())
        self.assertFalse(badge.isVisible())

        # Empty key hides badge
        modal._key_input.setText("")
        _app.processEvents()
        self.assertTrue(badge.isHidden())
        self.assertFalse(badge.isVisible())

        # Gemini key validation
        idx_gemini = modal._provider_combo.findData("gemini")
        modal._provider_combo.setCurrentIndex(idx_gemini)

        modal._key_input.setText("AIzaSyValidGeminiKey678901234567")
        _app.processEvents()
        self.assertFalse(badge.isHidden())
        self.assertTrue(badge.isVisible())
        self.assertIn("✓ Valid Key Format", badge.text())
        self.assertIn("10b981", badge.styleSheet())

        # Autofill / auto-detect: pasting gsk_ while Gemini is active switches to Groq
        modal._provider_combo.setCurrentIndex(idx_gemini)
        self.assertEqual(modal._current_provider(), "gemini")
        modal._key_input.setText("gsk_autofill_groq_switch_12345")
        _app.processEvents()
        self.assertEqual(modal._current_provider(), "groq")
        self.assertFalse(badge.isHidden())
        self.assertTrue(badge.isVisible())

        # Autofill / auto-detect: pasting AIzaSy while Groq is active switches to Gemini
        modal._provider_combo.setCurrentIndex(idx_groq)
        self.assertEqual(modal._current_provider(), "groq")
        modal._key_input.setText("AIzaSy_autofill_gemini_switch_1234")
        _app.processEvents()
        self.assertEqual(modal._current_provider(), "gemini")
        self.assertFalse(badge.isHidden())
        self.assertTrue(badge.isVisible())


class TestMainWindow(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.orchestrator = AgentOrchestrator(self.mock_client)
        self.window = MainWindow(self.orchestrator)

    def test_window_created(self):
        self.assertEqual(self.window.windowTitle(), "Linux AI Assistant")
        self.assertIsNotNone(self.window._splitter)
        self.assertEqual(self.window._splitter.orientation(), Qt.Orientation.Vertical)

    def test_chat_centered_column_and_bubble_styles(self):
        # Verify user and assistant bubbles
        user_bubble = ChatBubble("user", "Hello world")
        self.assertIn("#161616", user_bubble.styleSheet())
        self.assertIn("border-radius: 12px", user_bubble.styleSheet())

        asst_bubble = ChatBubble("assistant", "Response text")
        self.assertIn("background-color: transparent", asst_bubble.styleSheet())
        self.assertIn("border: none", asst_bubble.styleSheet())

    def test_vertical_splitter_drawer_auto_expand(self):
        # Drawer should auto-expand on EXECUTING
        self.orchestrator._set_state(OrchestratorState.EXECUTING)
        self.assertFalse(self.window._log_pane.isHidden())
        self.assertTrue(self.window._log_toggle_btn.isChecked())

        # Drawer can be collapsed with a single call
        self.window._set_log_pane_visible(False)
        self.assertTrue(self.window._log_pane.isHidden())
        self.assertFalse(self.window._log_toggle_btn.isChecked())

    def test_skill_switch_updates_log_pane(self):
        # Switch to Beginner -> log pane should hide
        self.window._skill_combo.setCurrentText("Beginner")
        self.assertFalse(self.window._log_toggle_btn.isChecked())

        # Switch to Intermediate -> log pane should show
        self.window._skill_combo.setCurrentText("Intermediate")
        self.assertTrue(self.window._log_toggle_btn.isChecked())

    def test_state_change_updates_badge(self):
        self.orchestrator._set_state(OrchestratorState.THINKING)
        self.assertEqual(self.window._status_badge.text(), "THINKING")
        self.assertFalse(self.window._thinking_bubble.isHidden())
        self.assertFalse(self.window._send_btn.isEnabled())

        self.orchestrator._set_state(OrchestratorState.IDLE)
        self.assertEqual(self.window._status_badge.text(), "Ready")
        self.assertTrue(self.window._thinking_bubble.isHidden())
        self.assertTrue(self.window._send_btn.isEnabled())

    def test_status_bar_displays_provider(self):
        status_msg = self.window.statusBar().currentMessage()
        self.assertIn("Provider:", status_msg)

    def test_command_output_appended(self):
        self.window._log_view.clear()
        self.orchestrator.command_output.emit("line 1\n")
        self.orchestrator.command_output.emit("\x1b[32mline 2\x1b[0m\n")
        log_text = self.window._log_view.toPlainText()
        self.assertIn("line 1", log_text)
        self.assertIn("line 2", log_text)
        self.assertNotIn("\x1b[32m", log_text)

    def test_unified_command_bar_layout_and_interactions(self):
        # Placeholder & object names
        self.assertEqual(
            self.window.input_field.placeholderText(),
            "Ask a question or describe an action...",
        )
        self.assertEqual(self.window.input_field.objectName(), "commandBarInput")
        self.assertEqual(self.window._send_btn.text(), "↵")
        self.assertEqual(self.window._send_btn.objectName(), "commandBarSendBtn")

        # Stop button initial visibility and execution state visibility
        self.assertTrue(self.window._abort_btn.isHidden())
        self.orchestrator._set_state(OrchestratorState.EXECUTING)
        self.assertFalse(self.window._abort_btn.isHidden())
        self.assertTrue(self.window._abort_btn.isEnabled())

        self.orchestrator._set_state(OrchestratorState.IDLE)
        self.assertTrue(self.window._abort_btn.isHidden())

        # Test sending message via send button click
        self.window.input_field.setText("System check")
        with patch.object(self.orchestrator, "send_message") as mock_send:
            self.window._send_btn.click()
            mock_send.assert_called_once_with("System check")

    def test_status_dot_and_label_states(self):
        # Check initial IDLE
        self.assertEqual(self.window._status_badge.text(), "Ready")
        self.assertIn("#10b981", self.window._status_dot.styleSheet())

        # Check THINKING
        self.orchestrator._set_state(OrchestratorState.THINKING)
        self.assertEqual(self.window._status_badge.text(), "THINKING")
        self.assertIn("#818cf8", self.window._status_dot.styleSheet())

        # Check AWAITING_APPROVAL
        self.orchestrator._set_state(OrchestratorState.AWAITING_APPROVAL)
        self.assertEqual(self.window._status_badge.text(), "AWAITING_APPROVAL")
        self.assertIn("#f59e0b", self.window._status_dot.styleSheet())

        # Check ERROR
        self.orchestrator._set_state(OrchestratorState.ERROR)
        self.assertEqual(self.window._status_badge.text(), "ERROR")
        self.assertIn("#ef4444", self.window._status_dot.styleSheet())

    def test_starter_chips_initial_visibility(self):
        self.assertIsNotNone(self.window.starter_chips)
        self.assertFalse(self.window.starter_chips.isHidden())
        self.assertEqual(len(self.window.starter_chips.buttons), 5)

    def test_starter_chips_click_populates_input(self):
        # Click first chip (Software Setup)
        self.window.starter_chips.buttons[0].click()
        self.assertEqual(
            self.window.input_field.text(),
            "Help me install an application or configure a local package.",
        )

        # Click second chip (Hardware & Drivers)
        self.window.starter_chips.buttons[1].click()
        self.assertEqual(
            self.window.input_field.text(),
            "Check my hardware details and verify if all drivers and kernel modules are loaded.",
        )

    def test_starter_chips_hide_after_message_sent(self):
        self.assertFalse(self.window.starter_chips.isHidden())
        self.window.input_field.setText("Show network status")
        with patch.object(self.orchestrator, "send_message"):
            self.window._on_send_message()
        self.assertTrue(self.window.starter_chips.isHidden())

    def test_starter_chips_restore_visibility_on_reset(self):
        self.window.input_field.setText("Diagnose system")
        with patch.object(self.orchestrator, "send_message"):
            self.window._on_send_message()
        self.assertTrue(self.window.starter_chips.isHidden())

        # Reset / clear conversation
        self.window.reset_conversation()
        self.assertFalse(self.window.starter_chips.isHidden())

    def test_chat_column_geometry_does_not_collapse_when_chips_hide(self):
        self.window.resize(1150, 750)
        self.window.show()
        _app.processEvents()

        col = self.window._chat_scroll.parentWidget()
        self.assertEqual(col.width(), 1150)

        # Hide starter chips (simulating user sending a message)
        self.window.starter_chips.hide()
        _app.processEvents()
        self.assertEqual(col.width(), 1150)

    def test_chat_column_scalable_at_various_window_sizes(self):
        self.window.show()
        _app.processEvents()
        col = self.window._chat_scroll.parentWidget()

        for w in (850, 940, 1150, 1400):
            self.window.resize(w, 800)
            _app.processEvents()
            self.assertEqual(col.width(), w)

        # Verify command bar input scales to full width after message is sent
        self.window.resize(940, 1024)
        _app.processEvents()
        self.window.input_field.setText("test")
        with patch.object(self.orchestrator, "send_message"):
            self.window._on_send_message()
        _app.processEvents()
        self.assertEqual(col.width(), 940)
        self.assertEqual(self.window.input_field.parentWidget().width(), 940 - 32)

    def test_chat_bubble_assistant_expands_to_full_column_width(self):
        self.window.resize(1150, 750)
        self.window.show()
        _app.processEvents()

        asst_bubble = self.window._chat_layout.itemAt(0).widget()
        self.assertGreaterEqual(asst_bubble.width(), 700)

    def test_chat_bubble_user_sizing_and_alignment(self):
        self.window.resize(1150, 750)
        self.window.show()
        _app.processEvents()

        user_bubble = self.window._append_message("user", "test")
        _app.processEvents()
        self.assertLess(user_bubble.width(), 100)
        self.assertGreater(user_bubble.x(), 600)


class TestStarterChipsWidget(unittest.TestCase):
    def test_widget_instantiation_and_chips(self):
        widget = StarterChipsWidget()
        self.assertEqual(len(widget.buttons), 5)
        self.assertEqual(len(widget.CHIPS), 5)

        labels = [btn.text() for btn in widget.buttons]
        self.assertIn("📦 Software Setup", labels)
        self.assertIn("🔧 Hardware & Drivers", labels)
        self.assertIn("⚙️ System Settings", labels)
        self.assertIn("🌐 Network & DNS", labels)
        self.assertIn("🔍 System Health", labels)

    def test_chip_clicked_signal(self):
        widget = StarterChipsWidget()
        received: list[str] = []
        widget.chip_clicked.connect(received.append)

        widget.buttons[0].click()
        self.assertEqual(len(received), 1)
        self.assertEqual(
            received[0],
            "Help me install an application or configure a local package.",
        )

        widget.buttons[4].click()
        self.assertEqual(len(received), 2)
        self.assertEqual(
            received[1],
            "Check for failed systemd services and scan recent system logs for errors.",
        )


if __name__ == "__main__":
    unittest.main()
