"""Tests for agent/orchestrator.py – state transitions using mocks.

All LLM calls and PTY execution are mocked so these tests run offline with
no API keys and no shell access.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

from PySide6.QtWidgets import QApplication
import sys

# Ensure a QApplication exists for Signal/Slot and widget machinery across test runner
_app = QApplication.instance() or QApplication(sys.argv)

from google.genai import types

from agent.orchestrator import AgentOrchestrator, OrchestratorState
from agent.llm_client import GeminiAgentClient


# ---------------------------------------------------------------------------
# Helpers to build mock Gemini response objects
# ---------------------------------------------------------------------------

def _make_text_response(text: str) -> types.GenerateContentResponse:
    """Build a mock GenerateContentResponse containing only text."""
    part = MagicMock(spec=types.Part)
    part.text = text
    part.function_call = None

    content = MagicMock(spec=types.Content)
    content.role = "model"
    content.parts = [part]

    candidate = MagicMock()
    candidate.content = content
    candidate.finish_reason = "STOP"

    resp = MagicMock(spec=types.GenerateContentResponse)
    resp.candidates = [candidate]
    return resp


def _make_function_call_response(
    tool_name: str,
    args: dict,
    call_id: str = "call-001",
) -> types.GenerateContentResponse:
    """Build a mock GenerateContentResponse containing a FunctionCall."""
    fc = MagicMock(spec=types.FunctionCall)
    fc.name = tool_name
    fc.args = args
    fc.id = call_id

    part = MagicMock(spec=types.Part)
    part.text = ""
    part.function_call = fc

    content = MagicMock(spec=types.Content)
    content.role = "model"
    content.parts = [part]

    candidate = MagicMock()
    candidate.content = content
    candidate.finish_reason = "STOP"

    resp = MagicMock(spec=types.GenerateContentResponse)
    resp.candidates = [candidate]
    return resp


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------

class TestOrchestratorStateMachine(unittest.TestCase):

    def setUp(self):
        self.mock_client = MagicMock(spec=GeminiAgentClient)
        self.orchestrator = AgentOrchestrator(self.mock_client)

    # --- Initial state ---

    def test_initial_state_is_idle(self):
        self.assertEqual(self.orchestrator.state, OrchestratorState.IDLE)

    def test_initial_history_is_empty(self):
        self.assertEqual(self.orchestrator.history, [])

    # --- History management ---

    def test_clear_history(self):
        self.orchestrator._history.append(MagicMock())
        self.orchestrator.clear_history()
        self.assertEqual(self.orchestrator.history, [])
        self.assertEqual(self.orchestrator.state, OrchestratorState.IDLE)

    # --- send_message blocked in non-IDLE states ---

    def test_send_message_ignored_when_thinking(self):
        self.orchestrator._state = OrchestratorState.THINKING
        initial_history_len = len(self.orchestrator._history)
        self.orchestrator.send_message("hello")
        # History should not have grown since we're in THINKING state
        self.assertEqual(len(self.orchestrator._history), initial_history_len)

    def test_send_message_ignored_when_awaiting_approval(self):
        self.orchestrator._state = OrchestratorState.AWAITING_APPROVAL
        initial_history_len = len(self.orchestrator._history)
        self.orchestrator.send_message("hello")
        self.assertEqual(len(self.orchestrator._history), initial_history_len)

    # --- Text response handling ---

    def test_on_llm_response_text_appends_to_history(self):
        text_resp = _make_text_response("Hello, world!")
        self.orchestrator._on_llm_response(text_resp)
        # Model content should be in history
        self.assertTrue(len(self.orchestrator._history) >= 1)

    def test_on_llm_response_text_emits_response_ready(self):
        received = []
        self.orchestrator.response_ready.connect(received.append)
        text_resp = _make_text_response("Hello!")
        self.orchestrator._on_llm_response(text_resp)
        self.assertEqual(received, ["Hello!"])

    def test_on_llm_response_text_sets_state_idle(self):
        self.orchestrator._state = OrchestratorState.THINKING
        text_resp = _make_text_response("Done.")
        self.orchestrator._on_llm_response(text_resp)
        self.assertEqual(self.orchestrator.state, OrchestratorState.IDLE)

    # --- Mutating tool call → AWAITING_APPROVAL ---

    def test_propose_shell_command_emits_approval_requested(self):
        approval_payloads = []
        self.orchestrator.approval_requested.connect(approval_payloads.append)

        fc_resp = _make_function_call_response(
            "propose_shell_command",
            {
                "command": "pacman -S neovim",
                "explanation": "Install neovim",
                "requires_root": True,
            },
        )
        self.orchestrator._on_llm_response(fc_resp)

        self.assertEqual(len(approval_payloads), 1)
        payload = approval_payloads[0]
        self.assertEqual(payload["tool_name"], "propose_shell_command")
        self.assertEqual(payload["command"], "pacman -S neovim")
        self.assertFalse(payload["high_risk"])

    def test_propose_shell_command_sets_awaiting_approval_state(self):
        states = []
        self.orchestrator.state_changed.connect(states.append)

        fc_resp = _make_function_call_response(
            "propose_shell_command",
            {"command": "ls -la", "explanation": "List files", "requires_root": False},
        )
        self.orchestrator._on_llm_response(fc_resp)

        self.assertIn(OrchestratorState.AWAITING_APPROVAL.value, states)
        self.assertEqual(self.orchestrator.state, OrchestratorState.AWAITING_APPROVAL)

    def test_high_risk_command_flagged_in_payload(self):
        approval_payloads = []
        self.orchestrator.approval_requested.connect(approval_payloads.append)

        fc_resp = _make_function_call_response(
            "propose_shell_command",
            {
                "command": "rm -rf /",
                "explanation": "Dangerous!",
                "requires_root": True,
            },
        )
        self.orchestrator._on_llm_response(fc_resp)

        self.assertTrue(approval_payloads[0]["high_risk"])

    def test_create_desktop_entry_emits_approval_requested(self):
        approval_payloads = []
        self.orchestrator.approval_requested.connect(approval_payloads.append)

        fc_resp = _make_function_call_response(
            "create_desktop_entry",
            {
                "app_name": "Postman",
                "exec_path": "~/.local/opt/postman/Postman",
                "icon_path": "",
                "categories": "Development;",
                "comment": "API client",
            },
        )
        self.orchestrator._on_llm_response(fc_resp)

        self.assertEqual(len(approval_payloads), 1)
        self.assertEqual(approval_payloads[0]["tool_name"], "create_desktop_entry")
        self.assertEqual(approval_payloads[0]["app_name"], "Postman")
        self.assertFalse(approval_payloads[0]["high_risk"])

    # --- handle_user_rejection ---

    def test_rejection_appends_tool_result_to_history(self):
        # Simulate being in AWAITING_APPROVAL with a pending tool call
        mock_fc = MagicMock(spec=types.FunctionCall)
        mock_fc.name = "propose_shell_command"
        mock_fc.id = "call-abc"
        self.orchestrator._pending_tool_call = mock_fc
        self.orchestrator._state = OrchestratorState.AWAITING_APPROVAL

        # Patch _call_llm to prevent thread creation in tests
        initial_len = len(self.orchestrator._history)
        with patch.object(self.orchestrator, '_call_llm'):
            self.orchestrator.handle_user_rejection("Too risky")

        # A tool result content should have been appended
        self.assertGreater(len(self.orchestrator._history), initial_len)

    def test_rejection_clears_pending_tool_call(self):
        mock_fc = MagicMock(spec=types.FunctionCall)
        mock_fc.name = "propose_shell_command"
        mock_fc.id = "call-xyz"
        self.orchestrator._pending_tool_call = mock_fc
        self.orchestrator._state = OrchestratorState.AWAITING_APPROVAL

        with patch.object(self.orchestrator, '_call_llm'):
            self.orchestrator.handle_user_rejection("No thanks")

        self.assertIsNone(self.orchestrator._pending_tool_call)

    # --- handle_user_approval ignored outside AWAITING_APPROVAL ---

    def test_approval_ignored_when_not_awaiting(self):
        # Should not raise or crash
        self.orchestrator._state = OrchestratorState.IDLE
        self.orchestrator.handle_user_approval("some command")  # no-op

    # --- handle_user_rejection ignored outside AWAITING_APPROVAL ---

    def test_rejection_ignored_when_not_awaiting(self):
        self.orchestrator._state = OrchestratorState.IDLE
        self.orchestrator.handle_user_rejection("some reason")  # no-op

    # --- Readonly tool: read_file_snippet auto-executed ---

    def test_read_file_snippet_does_not_emit_approval_requested(self):
        approval_payloads = []
        self.orchestrator.approval_requested.connect(approval_payloads.append)

        # Patch _call_llm to prevent QThread creation during test
        with patch.object(self.orchestrator, '_call_llm'):
            fc_resp = _make_function_call_response(
                "read_file_snippet",
                {"file_path": "/etc/os-release", "max_lines": 5},
            )
            self.orchestrator._on_llm_response(fc_resp)

        # No approval requested for readonly tools
        self.assertEqual(approval_payloads, [])

    def test_inspect_manual_does_not_emit_approval_requested(self):
        approval_payloads = []
        self.orchestrator.approval_requested.connect(approval_payloads.append)

        with patch.object(self.orchestrator, '_call_llm'):
            fc_resp = _make_function_call_response(
                "inspect_manual",
                {"command_or_topic": "nmcli"},
            )
            self.orchestrator._on_llm_response(fc_resp)

        # No approval requested for readonly tools
        self.assertEqual(approval_payloads, [])
        # History should contain the tool result
        self.assertGreater(len(self.orchestrator.history), 0)

    # --- LLM error ---

    def test_llm_error_sets_error_state(self):
        errors = []
        self.orchestrator.error_occurred.connect(errors.append)
        self.orchestrator._on_llm_error("Connection refused")
        self.assertEqual(self.orchestrator.state, OrchestratorState.ERROR)
        self.assertTrue(any("Connection refused" in e for e in errors))

    # --- State transitions ---

    def test_state_changed_signal_emitted(self):
        states = []
        self.orchestrator.state_changed.connect(states.append)
        self.orchestrator._set_state(OrchestratorState.THINKING)
        self.orchestrator._set_state(OrchestratorState.IDLE)
        self.assertIn("THINKING", states)
        self.assertIn("IDLE", states)

    def test_duplicate_state_does_not_emit_signal(self):
        states = []
        self.orchestrator.state_changed.connect(states.append)
        # Already IDLE, set again
        self.orchestrator._set_state(OrchestratorState.IDLE)
        self.assertEqual(states, [])


class TestOrchestratorHistoryBuilding(unittest.TestCase):
    """Verify the history entries added by orchestrator actions."""

    def setUp(self):
        self.mock_client = MagicMock(spec=GeminiAgentClient)
        self.orchestrator = AgentOrchestrator(self.mock_client)

    def test_send_message_adds_user_content(self):
        # We need to prevent the _call_llm from actually spinning up a thread
        with patch.object(self.orchestrator, '_call_llm'):
            self.orchestrator.send_message("Install neovim")
        self.assertEqual(len(self.orchestrator._history), 1)
        self.assertEqual(self.orchestrator._history[0].role, "user")

    def test_on_command_finished_adds_tool_result(self):
        mock_fc = MagicMock(spec=types.FunctionCall)
        mock_fc.name = "propose_shell_command"
        mock_fc.id = "call-999"

        self.mock_client.generate.return_value = _make_text_response("Done.")

        with patch.object(self.orchestrator, '_call_llm'):
            self.orchestrator._on_command_finished(0, "output tail", mock_fc)

        # History should include the tool result
        self.assertEqual(len(self.orchestrator._history), 1)

    def test_on_command_finished_failure_formats_stderr_and_corrective_instruction(self):
        mock_fc = MagicMock(spec=types.FunctionCall)
        mock_fc.name = "propose_shell_command"
        mock_fc.id = "call-err"

        with patch.object(self.orchestrator, '_call_llm'):
            self.orchestrator._on_command_finished(1, "error: permission denied", mock_fc)

        self.assertEqual(len(self.orchestrator._history), 1)
        part = self.orchestrator._history[0].parts[0]
        output_text = part.function_response.response["output"]
        self.assertIn("Command failed with exit code 1", output_text)
        self.assertIn("Terminal output: error: permission denied", output_text)
        self.assertIn("propose a corrective tool call", output_text)

    def test_approval_on_create_desktop_entry_delegates_and_executes(self):
        mock_fc = MagicMock(spec=types.FunctionCall)
        mock_fc.name = "create_desktop_entry"
        mock_fc.id = "call-desktop"
        mock_fc.args = {
            "app_name": "TestApp",
            "exec_path": "/bin/testapp",
            "icon_path": "",
            "categories": "Utility;",
            "comment": "A test application",
        }
        self.orchestrator._pending_tool_call = mock_fc
        self.orchestrator._state = OrchestratorState.AWAITING_APPROVAL

        with patch("agent.tools.create_desktop_entry", return_value="Desktop entry created successfully") as mock_create:
            with patch.object(self.orchestrator, '_call_llm'):
                self.orchestrator.handle_user_approval("")
                mock_create.assert_called_once()
                self.assertEqual(len(self.orchestrator._history), 1)
                self.assertIn("Desktop entry created", self.orchestrator._history[0].parts[0].function_response.response["output"])


if __name__ == "__main__":
    unittest.main()
