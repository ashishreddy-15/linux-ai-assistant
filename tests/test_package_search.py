"""Tests for the read-only search_packages tool (network and package managers faked)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from google.genai import types

from agent import package_search as ps
from agent.orchestrator import AgentOrchestrator
from agent.tools import READONLY_TOOLS, normalize_tool_args

POSTMAN = [("com.getpostman.Postman", "Postman: API Development Environment (9.5k installs/month)")]


class TestSearchPackages(unittest.TestCase):
    def test_rejects_option_like_or_odd_queries(self):
        for bad in ("--rm -rf", "", "a;b", "$(id)", "x" * 60):
            with self.subTest(bad=bad):
                self.assertTrue(ps.execute_search_packages(bad).startswith("ERROR"))

    def test_flathub_ids_and_setup_hint(self):
        with patch.object(ps, "flatpak_state", return_value=(True, False, set())), \
             patch.object(ps, "search_flathub", return_value=POSTMAN), \
             patch.object(ps, "detect_package_manager", return_value=None):
            out = ps.execute_search_packages("postman")
        self.assertIn("com.getpostman.Postman", out)
        self.assertIn("flatpak remote-add --user --if-not-exists flathub", out)
        self.assertIn("Never guess", out)

    def test_marks_installed_and_no_hint_when_flathub_ready(self):
        with patch.object(ps, "flatpak_state", return_value=(True, True, {"com.getpostman.Postman"})), \
             patch.object(ps, "search_flathub", return_value=POSTMAN), \
             patch.object(ps, "detect_package_manager", return_value=None):
            out = ps.execute_search_packages("postman")
        self.assertIn("[already installed]", out)
        self.assertNotIn("remote-add", out)

    def test_offline_flathub_is_reported(self):
        with patch.object(ps, "flatpak_state", return_value=(True, True, set())), \
             patch.object(ps, "search_flathub", return_value=None), \
             patch.object(ps, "detect_package_manager", return_value=None):
            self.assertIn("could not be reached", ps.execute_search_packages("vlc"))

    def test_dnf_results_exact_match_first(self):
        text = "pcp-htop|PCP process viewer\nhtop|Interactive process viewer\nhtop|Interactive process viewer\n"
        with patch.object(ps, "_run", return_value=text) as run:
            hits = ps.search_native("dnf", "htop")
        self.assertEqual(hits[0], ("htop", "Interactive process viewer"))
        self.assertEqual(len(hits), 2)  # duplicates removed
        self.assertIn("*htop*", run.call_args[0][0])

    def test_apt_and_pacman_parsing(self):
        with patch.object(ps, "_run", return_value="htop - interactive processes viewer\nbtop - resource monitor\n"):
            self.assertEqual(ps.search_native("apt", "htop")[0], ("htop", "interactive processes viewer"))
        with patch.object(ps, "_run", return_value="extra/htop 3.4-1\n    Interactive process viewer\n"):
            self.assertEqual(ps.search_native("pacman", "htop"), [("htop", "Interactive process viewer")])

    def test_long_flathub_summary_is_shortened(self):
        resp = MagicMock()
        resp.read.return_value = (
            b'{"hits": [{"app_id": "x.y.Z", "name": "Z", "summary": "' + b"w" * 200 + b'"}]}'
        )
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        with patch.object(ps.urllib.request, "urlopen", return_value=resp):
            hits = ps.search_flathub("z")
        self.assertLessEqual(len(hits[0][1]), 80)


class TestRootForPackageManagers(unittest.TestCase):
    def test_adds_sudo_where_needed(self):
        from agent.tools import ensure_root_for_package_managers as fix

        cases = {
            "dnf install -y htop": "sudo dnf install -y htop",
            "apt-get -y upgrade": "sudo apt-get -y upgrade",
            "pacman -Syu": "sudo pacman -Syu",
            "dnf check-update && dnf upgrade -y": "dnf check-update && sudo dnf upgrade -y",
            "sudo dnf install htop": "sudo dnf install htop",
            "dnf search htop": "dnf search htop",
            "flatpak install --user -y flathub org.videolan.VLC": "flatpak install --user -y flathub org.videolan.VLC",
            "echo dnf install x": "echo dnf install x",
        }
        for before, after in cases.items():
            with self.subTest(before=before):
                self.assertEqual(fix(before)[0], after)

    def test_normalize_marks_requires_root(self):
        args = normalize_tool_args("propose_shell_command", {"command": "dnf install -y htop", "explanation": "x"})
        self.assertEqual(args["command"], "sudo dnf install -y htop")
        self.assertTrue(args["requires_root"])


class TestToolWiring(unittest.TestCase):
    def test_registered_as_readonly_with_aliases(self):
        self.assertIn("search_packages", READONLY_TOOLS)
        self.assertEqual(normalize_tool_args("search_packages", {"name": " vlc "}), {"query": "vlc"})

    def test_orchestrator_runs_it_without_approval_and_reports_activity(self):
        orch = AgentOrchestrator(MagicMock())
        orch._call_llm = MagicMock()
        activity, approvals = [], []
        orch.tool_activity.connect(activity.append)
        orch.approval_requested.connect(approvals.append)
        resp = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
            role="model", parts=[types.Part(function_call=types.FunctionCall(
                name="search_packages", id="c1", args={"query": "postman"}))]))])
        with patch("agent.orchestrator.execute_search_packages", return_value="RESULTS") as search:
            orch._on_llm_response(resp)
        search.assert_called_once_with("postman")
        self.assertEqual(approvals, [])
        self.assertEqual(activity, ["searched packages for “postman”"])
        self.assertEqual(orch.history[-1].parts[0].function_response.response["output"], "RESULTS")


if __name__ == "__main__":
    unittest.main()
