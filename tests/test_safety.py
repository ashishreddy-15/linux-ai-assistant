"""Tests for the review fixes: secret-file blocking, manual-topic validation,
and answering every tool call in a multi-call model turn."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from google.genai import types

from agent.orchestrator import AgentOrchestrator, OrchestratorState
from agent.tools import execute_inspect_manual, execute_read_file_snippet


class TestReadFileSnippetBlocking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._env = patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()

    def _write(self, rel: str, text: str = "secret-value\n") -> Path:
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_ordinary_file_is_readable(self):
        path = self._write("notes/todo.txt", "one\ntwo\nthree\n")
        self.assertEqual(execute_read_file_snippet(str(path), 2), "one\ntwo\n[… more lines omitted]")

    def test_ssh_key_blocked(self):
        path = self._write(".ssh/id_ed25519")
        result = execute_read_file_snippet(str(path))
        self.assertTrue(result.startswith("ERROR: Reading"))
        self.assertNotIn("secret-value", result)

    def test_tilde_path_blocked(self):
        self._write(".aws/credentials")
        self.assertIn("blocked", execute_read_file_snippet("~/.aws/credentials"))

    def test_symlink_to_secret_blocked(self):
        target = self._write(".gnupg/private.asc")
        link = self.home / "innocent.txt"
        link.symlink_to(target)
        self.assertIn("blocked", execute_read_file_snippet(str(link)))

    def test_env_and_named_secrets_blocked(self):
        for rel in ("project/.env", "project/.env.local", "project/server.pem", "api_token.txt"):
            with self.subTest(rel=rel):
                self.assertIn("blocked", execute_read_file_snippet(str(self._write(rel))))

    def test_public_key_allowed(self):
        path = self._write("keys/id_ed25519.pub", "ssh-ed25519 AAAA\n")
        # Outside ~/.ssh and ending in .pub: not a secret.
        self.assertEqual(execute_read_file_snippet(str(path)), "ssh-ed25519 AAAA")

    def test_binary_refused(self):
        path = self.home / "blob.bin"
        path.write_bytes(b"\x00\x01\x02")
        self.assertIn("binary", execute_read_file_snippet(str(path)))

    def test_line_limit_capped(self):
        path = self._write("big.log", "x\n" * 1000)
        self.assertEqual(execute_read_file_snippet(str(path), 10_000).count("\n"), 400)


class TestInspectManualValidation(unittest.TestCase):
    def test_option_injection_rejected(self):
        with patch("agent.tools.subprocess.run") as run:
            result = execute_inspect_manual("--pager=sh -c 'touch /tmp/pwned'")
        self.assertTrue(result.startswith("ERROR"))
        run.assert_not_called()

    def test_only_first_word_and_double_dash(self):
        with patch("agent.tools.shutil.which", return_value="/usr/bin/man"), \
             patch("agent.tools.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="MANUAL TEXT")
            execute_inspect_manual("rm -rf ~")
        args = run.call_args[0][0]
        self.assertEqual(args[-2:], ["--", "rm"])


def _fc(name: str, call_id: str, **args) -> types.FunctionCall:
    return types.FunctionCall(name=name, id=call_id, args=args)


def _response(*calls: types.FunctionCall) -> types.GenerateContentResponse:
    parts = [types.Part(function_call=c) for c in calls]
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


class TestToolCallBatches(unittest.TestCase):
    def setUp(self):
        self.orch = AgentOrchestrator(MagicMock())
        self.orch._call_llm = MagicMock()
        self.approvals = []
        self.orch.approval_requested.connect(self.approvals.append)

    def _cmd(self, call_id: str, command: str = "echo hi") -> types.FunctionCall:
        return _fc("propose_shell_command", call_id, command=command, explanation="x", requires_root=False)

    def _answers(self):
        last = self.orch.history[-1]
        return [(p.function_response.id, p.function_response.response["output"]) for p in last.parts]

    def test_readonly_calls_answered_together(self):
        with patch("agent.orchestrator.execute_inspect_manual", side_effect=["A", "B"]):
            self.orch._on_llm_response(_response(
                _fc("inspect_manual", "c1", command_or_topic="ls"),
                _fc("inspect_manual", "c2", command_or_topic="cp"),
            ))
        self.assertEqual(self._answers(), [("c1", "A"), ("c2", "B")])
        self.orch._call_llm.assert_called_once()

    def test_two_changes_need_two_approvals(self):
        self.orch._on_llm_response(_response(self._cmd("c1"), self._cmd("c2", "echo two")))
        self.assertEqual(len(self.approvals), 1)
        self.assertEqual(self.orch.state, OrchestratorState.AWAITING_APPROVAL)

        # First step finishes: the second is proposed, the model is not called yet.
        self.orch._pending_tool_call = None
        with patch("agent.orchestrator.threading.Thread") as thread:
            thread.side_effect = lambda target, daemon: MagicMock(start=target)
            self.orch._on_command_finished(0, "hi", self._cmd("c1"))
        self.assertEqual(len(self.approvals), 2)
        self.assertEqual(self.approvals[1]["command"], "echo two")
        self.orch._call_llm.assert_not_called()

        self.orch._pending_tool_call = None
        self.orch._on_command_finished(0, "two", self._cmd("c2", "echo two"))
        ids = [i for i, _ in self._answers()]
        self.assertEqual(ids, ["c1", "c2"])
        self.orch._call_llm.assert_called_once()

    def test_rejection_skips_the_rest(self):
        self.orch._on_llm_response(_response(self._cmd("c1"), self._cmd("c2")))
        self.orch.handle_user_rejection("no")
        answers = self._answers()
        self.assertEqual([i for i, _ in answers], ["c1", "c2"])
        self.assertTrue(answers[0][1].startswith("REJECTED"))
        self.assertTrue(answers[1][1].startswith("SKIPPED"))
        self.assertEqual(len(self.approvals), 1)

    def test_failure_skips_the_rest(self):
        self.orch._on_llm_response(_response(self._cmd("c1"), self._cmd("c2")))
        self.orch._pending_tool_call = None
        self.orch._on_command_finished(2, "boom", self._cmd("c1"))
        answers = self._answers()
        self.assertIn("failed", answers[0][1])
        self.assertTrue(answers[1][1].startswith("SKIPPED"))

    def test_every_call_id_answered_for_openai_providers(self):
        from agent.llm_client import OpenAIAgentClient

        self.orch._on_llm_response(_response(self._cmd("c1"), self._cmd("c2")))
        self.orch.handle_user_rejection("")
        client = OpenAIAgentClient.__new__(OpenAIAgentClient)
        client._system_prompt = ""
        messages = client._history_to_messages(self.orch.history)
        asked = {tc["id"] for m in messages if m.get("tool_calls") for tc in m["tool_calls"]}
        answered = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
        self.assertEqual(asked, answered)


if __name__ == "__main__":
    unittest.main()
