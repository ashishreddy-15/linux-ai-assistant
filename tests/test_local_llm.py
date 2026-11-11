"""Tests for the native Ollama client (fake HTTP server, no real model)."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from google.genai import types

from agent.local_llm import (
    LocalModelError,
    OllamaAgentClient,
    compact_tools,
    history_to_ollama_messages,
    parse_text_tool_calls,
    trim_messages,
)


class TestParseTextToolCalls(unittest.TestCase):
    def test_tagged_call(self):
        text, calls = parse_text_tool_calls(
            'Sure.\n<tool_call>{"name": "inspect_manual", "arguments": {"command_or_topic": "ls"}}</tool_call>'
        )
        self.assertEqual(calls, [("inspect_manual", {"command_or_topic": "ls"})])
        self.assertEqual(text, "Sure.")

    def test_fenced_json_with_parameters_key(self):
        _, calls = parse_text_tool_calls(
            '```json\n{"name": "propose_shell_command", "parameters": {"command": "ls"}}\n```'
        )
        self.assertEqual(calls[0][0], "propose_shell_command")

    def test_bare_json_with_string_arguments(self):
        _, calls = parse_text_tool_calls('{"name": "read_file_snippet", "arguments": "{\\"file_path\\": \\"/etc/hosts\\"}"}')
        self.assertEqual(calls, [("read_file_snippet", {"file_path": "/etc/hosts"})])

    def test_unknown_tool_and_plain_json_ignored(self):
        for text in ('{"name": "rm_everything", "arguments": {}}', '{"answer": 42}', "Just words."):
            with self.subTest(text=text):
                self.assertEqual(parse_text_tool_calls(text), (text, []))


class TestHistoryConversion(unittest.TestCase):
    def test_tool_round_trip_and_capping(self):
        history = [
            types.Content(role="user", parts=[types.Part(text="hi")]),
            types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                name="inspect_manual", args={"command_or_topic": "ls"}, id="c1"))]),
            types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
                id="c1", name="inspect_manual", response={"output": "x" * 10_000}))]),
        ]
        msgs = history_to_ollama_messages(history, "SYS")
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "tool"])
        self.assertIsInstance(msgs[2]["tool_calls"][0]["function"]["arguments"], dict)
        self.assertEqual(msgs[3]["tool_name"], "inspect_manual")
        self.assertLess(len(msgs[3]["content"]), 3100)

    def test_trim_keeps_system_and_cuts_at_user_turns(self):
        msgs = [{"role": "system", "content": "S"}]
        for i in range(30):
            msgs += [
                {"role": "user", "content": f"q{i} " + "w" * 400},
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "x", "arguments": {}}}]},
                {"role": "tool", "tool_name": "x", "content": "r" * 400},
            ]
        trimmed = trim_messages(msgs, 1000)
        self.assertEqual(trimmed[0]["role"], "system")
        self.assertEqual(trimmed[1]["role"], "user")
        self.assertTrue(trimmed[-1]["content"].startswith("r"))
        self.assertLess(len(trimmed), len(msgs))

    def test_compact_tools_drop_aliases(self):
        tools = {t["function"]["name"]: t["function"] for t in compact_tools()}
        props = tools["inspect_manual"]["parameters"]["properties"]
        self.assertEqual(set(props), {"command_or_topic"})
        self.assertLess(len(json.dumps(compact_tools())), len(json.dumps(__import__("agent.tools").tools.OPENAI_TOOLS)))


class _FakeOllama(BaseHTTPRequestHandler):
    """Replies with the NDJSON lines in server.script; records request bodies."""

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        status, lines = self.server.script.pop(0)
        self.send_response(status)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for line in lines:
            self.wfile.write((json.dumps(line) + "\n").encode())

    def log_message(self, *args):
        pass


class TestOllamaClient(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
        self.server.script, self.server.requests = [], []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.server.server_port}"
        self.client = OllamaAgentClient(system_prompt="SYS", model="tiny", base_url=url)
        self.history = [types.Content(role="user", parts=[types.Part(text="install vlc")])]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_streams_text_and_sets_context(self):
        self.server.script.append((200, [
            {"message": {"content": "Hel"}}, {"message": {"content": "lo"}},
            {"message": {"content": ""}, "done": True, "eval_count": 2},
        ]))
        chunks = []
        resp = self.client.generate(self.history, on_chunk=chunks.append)
        self.assertEqual("".join(chunks), "Hello")
        self.assertEqual(resp.candidates[0].content.parts[0].text, "Hello")
        req = self.server.requests[0]
        self.assertEqual(req["options"]["num_ctx"], 8192)
        self.assertTrue(req["stream"])
        self.assertEqual(self.client.last_stats["eval_count"], 2)

    def test_native_tool_call(self):
        self.server.script.append((200, [{"message": {"content": "", "tool_calls": [
            {"function": {"name": "propose_shell_command", "arguments": {"command": "ls", "explanation": "x"}}}
        ]}, "done": True}]))
        resp = self.client.generate(self.history)
        fc = resp.candidates[0].content.parts[0].function_call
        self.assertEqual((fc.name, fc.args["command"], fc.id), ("propose_shell_command", "ls", "call_0"))

    def test_text_tool_call_recovered_and_not_streamed(self):
        call = '<tool_call>{"name": "inspect_manual", "arguments": {"command_or_topic": "ls"}}</tool_call>'
        self.server.script.append((200, [{"message": {"content": call}, "done": True}]))
        chunks = []
        resp = self.client.generate(self.history, on_chunk=chunks.append)
        self.assertEqual(chunks, [])
        self.assertEqual(resp.candidates[0].content.parts[0].function_call.name, "inspect_manual")

    def test_echo_question_becomes_text(self):
        self.server.script.append((200, [{"message": {"content": "", "tool_calls": [
            {"function": {"name": "propose_shell_command",
                          "arguments": {"command": 'echo "What should I fix?"', "explanation": "ask"}}}
        ]}, "done": True}]))
        resp = self.client.generate(self.history)
        parts = resp.candidates[0].content.parts
        self.assertEqual([p.text for p in parts], ["What should I fix?"])

    def test_real_echo_pipeline_is_kept(self):
        self.server.script.append((200, [{"message": {"content": "", "tool_calls": [
            {"function": {"name": "propose_shell_command",
                          "arguments": {"command": 'echo "x" >> ~/.bashrc', "explanation": "x"}}}
        ]}, "done": True}]))
        resp = self.client.generate(self.history)
        self.assertIsNotNone(resp.candidates[0].content.parts[0].function_call)

    def test_retries_without_think_for_models_that_cannot(self):
        self.server.script += [
            (400, [{"error": "\"tiny\" does not support thinking"}]),
            (200, [{"message": {"content": "ok"}, "done": True}]),
        ]
        self.client.generate(self.history)
        self.assertIn("think", self.server.requests[0])
        self.assertNotIn("think", self.server.requests[1])

    def test_missing_model_message(self):
        self.server.script.append((404, [{"error": "model \"tiny\" not found, try pulling it first"}]))
        with self.assertRaises(LocalModelError) as ctx:
            self.client.generate(self.history)
        self.assertIn("ollama pull tiny", str(ctx.exception))

    def test_server_down_message(self):
        client = OllamaAgentClient(system_prompt="", model="tiny", base_url="http://127.0.0.1:9")
        with self.assertRaises(LocalModelError) as ctx:
            client.generate(self.history)
        self.assertIn("Is it running", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class TestFileToolRouting(unittest.TestCase):
    def test_file_tools_only_when_chat_is_about_downloads(self):
        from google.genai import types

        from agent.local_llm import wants_file_tools

        def user(text):
            return types.Content(role="user", parts=[types.Part(text=text)])

        self.assertFalse(wants_file_tools([user("install vlc")]))
        self.assertFalse(wants_file_tools([user("what is using my memory?")]))
        for text in ("I downloaded the Antigravity tarball", "install foo.rpm", "remove the app you installed",
                     "get Proton VPN", "extract this .tar.gz"):
            self.assertTrue(wants_file_tools([user(text)]), text)
        call = types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="inspect_download", args={}))])
        self.assertTrue(wants_file_tools([user("go on"), call]))
