"""Tests for the toolkit-independent UI logic (no display required)."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from agent.orchestrator import AgentOrchestrator
from agent.tools import normalize_tool_args, render_desktop_entry
from ui.ansi import AnsiParser
from ui.controller import AppController, greeting
from ui.markup import markdown_to_blocks


class TestAnsiParser(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(AnsiParser().feed("hello\n"), [("text", "hello\n", {"fg": None, "bold": False})])

    def test_colour_and_reset(self):
        tokens = AnsiParser().feed("a\x1b[31mred\x1b[0mb")
        self.assertEqual([t[1] for t in tokens], ["a", "red", "b"])
        self.assertEqual(tokens[1][2]["fg"], "red")
        self.assertIsNone(tokens[2][2]["fg"])

    def test_escape_split_across_chunks(self):
        p = AnsiParser()
        first = p.feed("x\x1b[3")
        second = p.feed("2mgreen")
        self.assertEqual([t[1] for t in first], ["x"])
        self.assertEqual(second[0][1], "green")
        self.assertEqual(second[0][2]["fg"], "green")

    def test_carriage_return_emits_redraw(self):
        tokens = AnsiParser().feed("10%\r40%")
        self.assertEqual(tokens, [
            ("text", "10%", {"fg": None, "bold": False}),
            ("cr",),
            ("text", "40%", {"fg": None, "bold": False}),
        ])

    def test_crlf_is_a_newline(self):
        tokens = AnsiParser().feed("a\r\nb")
        self.assertNotIn(("cr",), tokens)

    def test_non_sgr_sequences_and_controls_dropped(self):
        tokens = AnsiParser().feed("\x1b]0;title\x07\x1b[2Kok\x07\x08")
        self.assertEqual("".join(t[1] for t in tokens if t[0] == "text"), "ok")


class TestMarkup(unittest.TestCase):
    def test_model_markup_is_escaped(self):
        blocks = markdown_to_blocks('<span foreground="red">x</span> & <b>')
        self.assertEqual(blocks[0][0], "text")
        self.assertNotIn("<span", blocks[0][1])
        self.assertIn("&lt;span", blocks[0][1])
        self.assertIn("&amp;", blocks[0][1])

    def test_inline_formatting(self):
        markup = markdown_to_blocks("Run **this** with `a<b`")[0][1]
        self.assertIn("<b>this</b>", markup)
        self.assertIn("<tt>a&lt;b</tt>", markup)

    def test_code_fence_is_raw_block(self):
        blocks = markdown_to_blocks("Before\n```bash\necho <hi>\n```\nAfter")
        self.assertEqual([b[0] for b in blocks], ["text", "code", "text"])
        self.assertEqual(blocks[1][1], "echo <hi>")

    def test_only_http_links_become_anchors(self):
        markup = markdown_to_blocks("[ok](https://x.org) [bad](javascript:alert(1))")[0][1]
        self.assertIn('<a href="https://x.org">ok</a>', markup)
        self.assertNotIn("javascript:alert(1)\">", markup)

    def test_bullets(self):
        self.assertIn("•  item", markdown_to_blocks("- item")[0][1])


class TestController(unittest.TestCase):
    def setUp(self):
        self.orch = AgentOrchestrator(MagicMock())
        self.ctl = AppController(self.orch)

    def test_greeting_by_time(self):
        self.assertEqual(greeting(datetime(2026, 1, 1, 9), "Ana"), "Good morning, Ana")
        self.assertEqual(greeting(datetime(2026, 1, 1, 14), "Ana"), "Good afternoon, Ana")
        self.assertEqual(greeting(datetime(2026, 1, 1, 22), ""), "Good evening")

    def test_needs_onboarding(self):
        with patch("ui.controller.load_profile", return_value={"active_provider": "groq"}), \
             patch("ui.controller.get_api_key", return_value=None):
            self.assertTrue(self.ctl.needs_onboarding())
        with patch("ui.controller.load_profile", return_value={"active_provider": "groq", "onboarding_complete": True}), \
             patch("ui.controller.get_api_key", return_value=None):
            self.assertFalse(self.ctl.needs_onboarding())
        with patch("ui.controller.load_profile", return_value={"active_provider": "ollama"}):
            self.assertFalse(self.ctl.needs_onboarding())

    def test_test_key_rejects_bad_format_without_network(self):
        with patch("ui.controller.test_provider_api_key") as net:
            ok, message, models = self.ctl.test_key("groq", "not-a-key")
        self.assertFalse(ok)
        self.assertIn("gsk_", message)
        net.assert_not_called()

    def test_local_model_checks(self):
        with patch("agent.local_llm.server_version", return_value=None):
            ok, msg, models = self.ctl.test_key("ollama", "")
        self.assertFalse(ok)
        self.assertIn("isn't running", msg)

        with patch("agent.local_llm.server_version", return_value="0.40"), \
             patch("agent.local_llm.installed_models", return_value=[]):
            ok, msg, _ = self.ctl.test_key("ollama", "")
        self.assertFalse(ok)
        self.assertIn("ollama pull", msg)

        with patch("agent.local_llm.server_version", return_value="0.40"), \
             patch("agent.local_llm.installed_models", return_value=["llama3.2:3b", "qwen3.5:4b"]), \
             patch("agent.local_llm.model_supports_tools", return_value=True), \
             patch("agent.local_llm.probe_tool_calling", return_value=(True, "ok")):
            ok, msg, models = self.ctl.test_key("ollama", "")
        self.assertTrue(ok)
        self.assertEqual(models[0], "qwen3.5:4b")  # recommended model ranked first
        self.assertIn("qwen3.5:4b", msg)

    def test_set_skill_level_updates_prompt(self):
        with patch("ui.controller.load_profile", return_value={"active_provider": "groq"}), \
             patch("ui.controller.save_profile") as save, \
             patch("ui.controller.build_system_prompt", return_value="PROMPT"):
            self.orch.update_system_prompt = MagicMock()
            self.ctl.set_skill_level("Advanced")
        self.assertEqual(save.call_args[0][0]["skill_level"], "Advanced")
        self.orch.update_system_prompt.assert_called_once_with("PROMPT")

    def test_status_line(self):
        profile = {"os_id": "fedora", "os_version": "45 (Sway)", "active_provider": "groq"}
        session = {"xdg_current_desktop": "ubuntu:GNOME", "home_disk_free_gb": 211.6}
        with patch("ui.controller.load_profile", return_value=profile), \
             patch("ui.controller.get_dynamic_session_info", return_value=session), \
             patch("ui.controller.get_api_key", return_value="gsk_x"):
            self.assertEqual(self.ctl.status_line(), "fedora 45 · gnome · 212 GB free · groq")
        with patch("ui.controller.load_profile", return_value=profile), \
             patch("ui.controller.get_dynamic_session_info", return_value=session), \
             patch("ui.controller.get_api_key", return_value=None):
            self.assertNotIn("groq", self.ctl.status_line())

    def test_local_provider_gets_compact_prompt(self):
        with patch("ui.controller.load_profile", return_value={"active_provider": "ollama"}), \
             patch("ui.controller.build_compact_system_prompt", return_value="SHORT") as compact, \
             patch("ui.controller.build_system_prompt", return_value="LONG"):
            self.assertEqual(self.ctl.system_prompt(), "SHORT")
        compact.assert_called_once()

    def test_invalid_skill_level_rejected(self):
        with self.assertRaises(ValueError):
            self.ctl.set_skill_level("Wizard")


class TestPrimaryLabel(unittest.TestCase):
    def test_labels(self):
        from ui.card_text import primary_label_for

        for title, want in (("Install VLC media player", "Install"), ("Set up Flathub", "Set up"),
                            ("Check free disk space", "Check"), ("Wifi status", "Run"), ("", "Run")):
            with self.subTest(title=title):
                self.assertEqual(primary_label_for(title), want)


class TestApprovalPayload(unittest.TestCase):
    def test_title_and_effects_normalised(self):
        args = normalize_tool_args("propose_shell_command", {
            "command": "ls", "explanation": "x", "requires_root": False,
            "title": "  List files ", "effects": "- one\n- two\n",
        })
        self.assertEqual(args["title"], "List files")
        self.assertEqual(args["effects"], ["one", "two"])

    def test_payload_carries_title_and_effects(self):
        from google.genai import types

        orch = AgentOrchestrator(MagicMock())
        seen = []
        orch.approval_requested.connect(seen.append)
        orch._handle_mutating_tool(types.FunctionCall(name="propose_shell_command", args={
            "command": "flatpak install --user -y flathub org.videolan.VLC",
            "explanation": "x", "requires_root": False,
            "title": "Install VLC", "effects": ["Adds VLC to your app menu"],
        }))
        self.assertEqual(seen[0]["title"], "Install VLC")
        self.assertEqual(seen[0]["effects"], ["Adds VLC to your app menu"])

    def test_desktop_entry_payload_has_preview(self):
        from google.genai import types

        orch = AgentOrchestrator(MagicMock())
        seen = []
        orch.approval_requested.connect(seen.append)
        orch._handle_mutating_tool(types.FunctionCall(name="create_desktop_entry", args={
            "app_name": "Postman", "exec_path": "/opt/Postman/Postman",
            "icon_path": "", "categories": "Development;", "comment": "API client",
        }))
        self.assertIn("Exec=/opt/Postman/Postman", seen[0]["command"])
        self.assertEqual(seen[0]["title"], "Add Postman to your app menu")

    def test_desktop_entry_newlines_cannot_inject_keys(self):
        text = render_desktop_entry("App\nExec=/bin/evil", "/opt/a b/run", "", "Utility;", "hi\nX=1")
        lines = text.splitlines()
        self.assertEqual(sum(1 for line in lines if line.startswith("Exec=")), 1)
        self.assertIn('Exec="/opt/a b/run"', lines)
        self.assertFalse(any(line.startswith("X=") for line in lines))


if __name__ == "__main__":
    unittest.main()
