"""End-to-end integration tests for multi-step workflows and self-healing.

Simulates:
  1. Successful multi-step tarball installation workflow:
     propose shell command -> user approval -> command execution ->
     propose create_desktop_entry -> user approval -> desktop database updated ->
     final confirmation.
  2. Self-healing failure recovery workflow:
     propose build command -> execution fails (exit code != 0) ->
     structured error fed back to LLM -> agent diagnoses and proposes corrective command ->
     user approval -> successful resolution.
  3. User rejection recovery workflow:
     propose command -> user rejects with reason -> rejection fed back to LLM ->
     agent proposes alternative rootless approach.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from google.genai import types

from agent.llm_client import GeminiAgentClient
from agent.orchestrator import AgentOrchestrator, OrchestratorState


def _make_text_response(text: str) -> types.GenerateContentResponse:
    """Helper to create a mock GenerateContentResponse containing text."""
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


def _make_tool_call_response(
    tool_name: str, args: dict, call_id: str = "call-1"
) -> types.GenerateContentResponse:
    """Helper to create a mock GenerateContentResponse containing a FunctionCall."""
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


class TestIntegrationWorkflow(unittest.TestCase):
    """End-to-end integration tests driving AgentOrchestrator through multi-turn scenarios."""

    def setUp(self):
        self.mock_client = MagicMock(spec=GeminiAgentClient)
        self.orchestrator = AgentOrchestrator(self.mock_client)

        # Wire _call_llm to evaluate client.generate synchronously for deterministic tests
        def sync_call_llm():
            resp = self.mock_client.generate(self.orchestrator.history)
            self.orchestrator._on_llm_response(resp)

        self.orchestrator._call_llm = sync_call_llm

    def test_multistep_tarball_install_workflow(self):
        """Simulate a complete tarball installation and desktop launcher creation."""
        # Recorded signals
        approval_requests = []
        final_responses = []

        self.orchestrator.approval_requested.connect(approval_requests.append)
        self.orchestrator.response_ready.connect(final_responses.append)

        # Turn 1: Model proposes unpacking tarball to user-space
        resp_turn1 = _make_tool_call_response(
            "propose_shell_command",
            {
                "command": "mkdir -p ~/.local/opt/postman && tar -xzf postman.tar.gz -C ~/.local/opt/postman --strip-components=1",
                "explanation": "Extract Postman archive into ~/.local/opt/postman",
                "requires_root": False,
            },
            call_id="call-tarball",
        )

        # Turn 2: Model proposes creating desktop launcher
        resp_turn2 = _make_tool_call_response(
            "create_desktop_entry",
            {
                "app_name": "Postman",
                "exec_path": "~/.local/opt/postman/Postman",
                "icon_path": "~/.local/opt/postman/icon.png",
                "categories": "Development;",
                "comment": "API Development Environment",
            },
            call_id="call-desktop",
        )

        # Turn 3: Model returns final confirmation
        resp_turn3 = _make_text_response(
            "Postman has been successfully installed and registered in your application launcher."
        )

        self.mock_client.generate.side_effect = [resp_turn1, resp_turn2, resp_turn3]

        # 1. User initiates request
        self.orchestrator.send_message("Install Postman from postman.tar.gz")

        # Verify state paused at AWAITING_APPROVAL
        self.assertEqual(self.orchestrator.state, OrchestratorState.AWAITING_APPROVAL)
        self.assertEqual(len(approval_requests), 1)
        self.assertEqual(approval_requests[0]["tool_name"], "propose_shell_command")
        self.assertIn("~/.local/opt/postman", approval_requests[0]["command"])

        # 2. User approves command -> simulate command execution success
        pending_call = self.orchestrator._pending_tool_call
        self.orchestrator._pending_tool_call = None
        self.orchestrator._on_command_finished(
            exit_code=0,
            tail_log="x Postman/\nx Postman/Postman\nArchive extracted.",
            tool_call=pending_call,
        )

        # Verify state paused again for the second mutating tool (create_desktop_entry)
        self.assertEqual(self.orchestrator.state, OrchestratorState.AWAITING_APPROVAL)
        self.assertEqual(len(approval_requests), 2)
        self.assertEqual(approval_requests[1]["tool_name"], "create_desktop_entry")
        self.assertEqual(approval_requests[1]["app_name"], "Postman")

        # 3. User approves desktop entry creation
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("agent.tools.Path.home", return_value=Path(tmp_dir)):
                with patch("shutil.which", return_value="/usr/bin/update-desktop-database"):
                    with patch("subprocess.run") as mock_db_update:
                        mock_db_update.return_value.returncode = 0
                        self.orchestrator.handle_user_approval("")

        # Verify state transitioned back to IDLE with final response emitted
        self.assertEqual(self.orchestrator.state, OrchestratorState.IDLE)
        self.assertEqual(len(final_responses), 1)
        self.assertIn("Postman has been successfully installed", final_responses[0])

        # Verify full history trajectory (User prompt -> Tool 1 call -> Tool 1 result -> Tool 2 call -> Tool 2 result -> Final answer)
        history = self.orchestrator.history
        self.assertEqual(len(history), 6)
        self.assertEqual(history[0].role, "user")
        self.assertEqual(history[1].role, "model")
        self.assertEqual(history[2].role, "user")  # Tool 1 result
        self.assertEqual(history[3].role, "model")
        self.assertEqual(history[4].role, "user")  # Tool 2 result
        self.assertEqual(history[5].role, "model")

    def test_self_healing_recovery_workflow(self):
        """Simulate command failure, stderr routing, and agent self-healing via corrective tool call."""
        approval_requests = []
        final_responses = []

        self.orchestrator.approval_requested.connect(approval_requests.append)
        self.orchestrator.response_ready.connect(final_responses.append)

        # Turn 1: Model proposes initial build command
        resp_turn1 = _make_tool_call_response(
            "propose_shell_command",
            {
                "command": "make -C /lib/modules/6.12.1-arch1/build M=$PWD",
                "explanation": "Build kernel module",
                "requires_root": False,
            },
            call_id="call-build-1",
        )

        # Turn 2: After failure, model diagnoses and proposes corrective package install
        resp_turn2 = _make_tool_call_response(
            "propose_shell_command",
            {
                "command": "pacman -S linux-headers",
                "explanation": "Kernel build headers are missing. Installing linux-headers package.",
                "requires_root": True,
            },
            call_id="call-headers-fix",
        )

        # Turn 3: Final confirmation after corrective step
        resp_turn3 = _make_text_response(
            "Kernel headers installed. Ready to compile."
        )

        self.mock_client.generate.side_effect = [resp_turn1, resp_turn2, resp_turn3]

        # 1. User starts task
        self.orchestrator.send_message("Build my kernel module")
        self.assertEqual(self.orchestrator.state, OrchestratorState.AWAITING_APPROVAL)
        self.assertEqual(len(approval_requests), 1)

        # 2. User approves -> Command fails with exit code 2
        pending_call = self.orchestrator._pending_tool_call
        self.orchestrator._pending_tool_call = None
        error_output = "make: *** /lib/modules/6.12.1-arch1/build: No such file or directory. Stop."
        self.orchestrator._on_command_finished(
            exit_code=2,
            tail_log=error_output,
            tool_call=pending_call,
        )

        # Verify the failure was formatted with stderr and corrective prompt in history
        tool_result_content = self.orchestrator.history[2]
        result_text = tool_result_content.parts[0].function_response.response["output"]
        self.assertIn("Command failed with exit code 2", result_text)
        self.assertIn("Terminal output: make: ***", result_text)
        self.assertIn("propose a corrective tool call", result_text)

        # 3. Model diagnoses error and proposes corrective tool call
        self.assertEqual(self.orchestrator.state, OrchestratorState.AWAITING_APPROVAL)
        self.assertEqual(len(approval_requests), 2)
        corrective_payload = approval_requests[1]
        self.assertEqual(corrective_payload["command"], "pacman -S linux-headers")
        self.assertTrue(corrective_payload["requires_root"])

        # 4. User approves corrective action -> succeeds
        pending_call2 = self.orchestrator._pending_tool_call
        self.orchestrator._pending_tool_call = None
        self.orchestrator._on_command_finished(
            exit_code=0,
            tail_log="resolving dependencies...\ninstalled linux-headers (6.12.1-arch1)",
            tool_call=pending_call2,
        )

        # Final state IDLE with success message
        self.assertEqual(self.orchestrator.state, OrchestratorState.IDLE)
        self.assertEqual(len(final_responses), 1)
        self.assertIn("Kernel headers installed", final_responses[0])

    def test_user_rejection_recovery_workflow(self):
        """Simulate user rejecting a proposed command and the agent recovering with a safe alternative."""
        approval_requests = []
        final_responses = []

        self.orchestrator.approval_requested.connect(approval_requests.append)
        self.orchestrator.response_ready.connect(final_responses.append)

        # Turn 1: Model proposes a root installation command
        resp_turn1 = _make_tool_call_response(
            "propose_shell_command",
            {
                "command": "sudo pacman -S nodejs npm",
                "explanation": "Install Node.js system-wide",
                "requires_root": True,
            },
            call_id="call-sudo-node",
        )

        # Turn 2: After user rejection, model proposes rootless NVM alternative
        resp_turn2 = _make_tool_call_response(
            "propose_shell_command",
            {
                "command": "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash",
                "explanation": "Install NVM in user home without sudo",
                "requires_root": False,
            },
            call_id="call-nvm-rootless",
        )

        self.mock_client.generate.side_effect = [resp_turn1, resp_turn2]

        self.orchestrator.send_message("Install nodejs")
        self.assertEqual(self.orchestrator.state, OrchestratorState.AWAITING_APPROVAL)

        # User rejects with reason
        self.orchestrator.handle_user_rejection("Please do not use sudo, I want a rootless install.")

        # Verify rejection was injected into history
        rejection_content = self.orchestrator.history[2]
        output_msg = rejection_content.parts[0].function_response.response["output"]
        self.assertIn("REJECTED: The user rejected the proposed command", output_msg)
        self.assertIn("rootless install", output_msg)

        # Verify agent proposed alternative tool call without sudo
        self.assertEqual(self.orchestrator.state, OrchestratorState.AWAITING_APPROVAL)
        self.assertEqual(len(approval_requests), 2)
        alt_payload = approval_requests[1]
        self.assertFalse(alt_payload["requires_root"])
        self.assertIn("nvm", alt_payload["command"])

    def test_root_command_sudo_prompt_fast_fail_and_pkexec_recovery(self):
        """Simulate a command failing fast on interactive sudo prompt and recovering with pkexec."""
        approval_requests = []
        final_responses = []

        self.orchestrator.approval_requested.connect(approval_requests.append)
        self.orchestrator.response_ready.connect(final_responses.append)

        # Turn 1: Model proposes raw sudo command
        resp_turn1 = _make_tool_call_response(
            "propose_shell_command",
            {
                "command": "sudo pacman -S ripgrep",
                "explanation": "Install ripgrep via system package manager",
                "requires_root": True,
            },
            call_id="call-sudo-rg",
        )

        # Turn 2: After failure, model recovers by proposing pkexec command
        resp_turn2 = _make_tool_call_response(
            "propose_shell_command",
            {
                "command": "pkexec pacman -S ripgrep",
                "explanation": "Install ripgrep with graphical authentication via pkexec",
                "requires_root": True,
            },
            call_id="call-pkexec-rg",
        )

        # Turn 3: Final confirmation
        resp_turn3 = _make_text_response("ripgrep was installed successfully using pkexec.")

        self.mock_client.generate.side_effect = [resp_turn1, resp_turn2, resp_turn3]

        self.orchestrator.send_message("Install ripgrep")
        self.assertEqual(self.orchestrator.state, OrchestratorState.AWAITING_APPROVAL)

        # User approves raw sudo command, but command execution encounters sudo prompt
        # Simulate CommandRunner fast failing on sudo prompt
        with patch.object(self.orchestrator, "_execute_command") as mock_exec:
            def simulate_auth_fail(cmd, tool_call):
                self.orchestrator._on_command_finished(
                    1,
                    "Authentication required: Please use pkexec or run with an askpass agent enabled.",
                    tool_call,
                )
            mock_exec.side_effect = simulate_auth_fail
            self.orchestrator.handle_user_approval(approval_requests[0]["command"])

        # Check that error was fed into history
        err_content = self.orchestrator.history[2]
        output_msg = err_content.parts[0].function_response.response["output"]
        self.assertIn("Command failed with exit code 1", output_msg)
        self.assertIn("Authentication required: Please use pkexec", output_msg)

        # Verify model recovered with pkexec command
        self.assertEqual(self.orchestrator.state, OrchestratorState.AWAITING_APPROVAL)
        self.assertEqual(len(approval_requests), 2)
        self.assertEqual(approval_requests[1]["command"], "pkexec pacman -S ripgrep")

        # Approve pkexec command and simulate success
        with patch.object(self.orchestrator, "_execute_command") as mock_exec_pkexec:
            def simulate_pkexec_success(cmd, tool_call):
                self.orchestrator._on_command_finished(
                    0,
                    "resolving dependencies...\ninstalling ripgrep (14.1.0-1)...\nSuccess.",
                    tool_call,
                )
            mock_exec_pkexec.side_effect = simulate_pkexec_success
            self.orchestrator.handle_user_approval(approval_requests[1]["command"])

        # Final response
        self.assertEqual(self.orchestrator.state, OrchestratorState.IDLE)
        self.assertEqual(len(final_responses), 1)
        self.assertIn("installed successfully using pkexec", final_responses[0])


if __name__ == "__main__":
    unittest.main()
