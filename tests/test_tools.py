"""Tests for agent/tools.py – tool declarations, metadata, and helpers."""

from __future__ import annotations

import os
import tempfile
import unittest

from agent.tools import (
    AGENT_TOOLS,
    ALL_TOOLS,
    MUTATING_TOOLS,
    OPENAI_TOOLS,
    READONLY_TOOLS,
    execute_inspect_manual,
    execute_read_file_snippet,
    inspect_manual,
    normalize_tool_args,
    render_desktop_entry,
)
from google.genai import types


class TestToolDeclarations(unittest.TestCase):

    def _find_declaration(self, name: str):
        for fd in AGENT_TOOLS.function_declarations:
            if fd.name == name:
                return fd
        return None

    def test_agent_tools_is_tool_instance(self):
        self.assertIsInstance(AGENT_TOOLS, types.Tool)

    def test_tool_declarations_count(self):
        self.assertEqual(len(AGENT_TOOLS.function_declarations), 4)

    def test_all_tools_alias(self):
        self.assertIs(ALL_TOOLS, AGENT_TOOLS)

    def test_openai_tools_registered(self):
        self.assertEqual(len(OPENAI_TOOLS), 4)
        names = {t["function"]["name"] for t in OPENAI_TOOLS}
        self.assertEqual(
            names,
            {
                "propose_shell_command",
                "create_desktop_entry",
                "read_file_snippet",
                "inspect_manual",
            },
        )

    def test_propose_shell_command_exists(self):
        fd = self._find_declaration("propose_shell_command")
        self.assertIsNotNone(fd)

    def test_propose_shell_command_required_fields(self):
        fd = self._find_declaration("propose_shell_command")
        required = set(fd.parameters.required)
        self.assertIn("command", required)
        self.assertIn("explanation", required)
        self.assertIn("requires_root", required)

    def test_propose_shell_command_requires_root_is_boolean(self):
        fd = self._find_declaration("propose_shell_command")
        schema = fd.parameters.properties["requires_root"]
        self.assertEqual(schema.type, types.Type.BOOLEAN)

    def test_create_desktop_entry_exists(self):
        fd = self._find_declaration("create_desktop_entry")
        self.assertIsNotNone(fd)

    def test_create_desktop_entry_required_fields(self):
        fd = self._find_declaration("create_desktop_entry")
        required = set(fd.parameters.required)
        self.assertIn("app_name", required)
        self.assertIn("exec_path", required)
        self.assertIn("icon_path", required)
        self.assertIn("categories", required)
        self.assertIn("comment", required)

    def test_read_file_snippet_exists(self):
        fd = self._find_declaration("read_file_snippet")
        self.assertIsNotNone(fd)

    def test_read_file_snippet_file_path_required(self):
        fd = self._find_declaration("read_file_snippet")
        self.assertIn("file_path", fd.parameters.required)

    def test_inspect_manual_exists(self):
        fd = self._find_declaration("inspect_manual")
        self.assertIsNotNone(fd)

    def test_inspect_manual_required_fields(self):
        fd = self._find_declaration("inspect_manual")
        self.assertIn("command_or_topic", fd.parameters.required)

    def test_mutating_tools_set(self):
        self.assertIn("propose_shell_command", MUTATING_TOOLS)
        self.assertIn("create_desktop_entry", MUTATING_TOOLS)
        self.assertNotIn("read_file_snippet", MUTATING_TOOLS)
        self.assertNotIn("inspect_manual", MUTATING_TOOLS)

    def test_readonly_tools_set(self):
        self.assertIn("read_file_snippet", READONLY_TOOLS)
        self.assertIn("inspect_manual", READONLY_TOOLS)
        self.assertNotIn("propose_shell_command", READONLY_TOOLS)
        self.assertNotIn("create_desktop_entry", READONLY_TOOLS)


class TestExecuteReadFileSnippet(unittest.TestCase):

    def test_reads_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            fname = f.name
        try:
            result = execute_read_file_snippet(fname, max_lines=10)
            self.assertIn("line1", result)
            self.assertIn("line3", result)
        finally:
            os.unlink(fname)

    def test_respects_max_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for i in range(50):
                f.write(f"line{i}\n")
            fname = f.name
        try:
            result = execute_read_file_snippet(fname, max_lines=5)
            self.assertIn("line0", result)
            self.assertNotIn("line49", result)
            self.assertIn("omitted", result)
        finally:
            os.unlink(fname)

    def test_nonexistent_file_returns_error(self):
        result = execute_read_file_snippet("/does/not/exist/file.txt")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("not found", result)

    def test_directory_returns_error(self):
        result = execute_read_file_snippet("/tmp")
        self.assertTrue(result.startswith("ERROR:"))

    def test_tilde_expansion(self):
        """~-prefixed paths should be expanded and not cause a crash."""
        result = execute_read_file_snippet("~/.bashrc", max_lines=5)
        # Either reads successfully or returns an error (file may not exist)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


class TestRenderDesktopEntry(unittest.TestCase):

    def test_basic_render(self):
        content = render_desktop_entry(
            app_name="Postman",
            exec_path="/home/user/.local/opt/postman/Postman",
            icon_path="/home/user/.local/opt/postman/app/resources/app/assets/icon.png",
            categories="Development;Network;",
            comment="API development environment",
        )
        self.assertIn("[Desktop Entry]", content)
        self.assertIn("Name=Postman", content)
        self.assertIn("Exec=/home/user/.local/opt/postman/Postman", content)
        self.assertIn("Icon=", content)
        self.assertIn("Categories=Development;Network;", content)
        self.assertIn("Comment=API development environment", content)
        self.assertIn("Type=Application", content)

    def test_no_icon_omitted(self):
        content = render_desktop_entry(
            app_name="MyApp",
            exec_path="/home/user/.local/bin/myapp",
            icon_path="",
            categories="Utility;",
            comment="A utility",
        )
        self.assertNotIn("Icon=", content)

    def test_output_is_string(self):
        content = render_desktop_entry("A", "/usr/bin/a", "", "Utility;", "App A")
        self.assertIsInstance(content, str)
        self.assertTrue(content.endswith("\n"))

    def test_tilde_path_resolution(self):
        content = render_desktop_entry(
            app_name="Postman",
            exec_path="~/.local/opt/postman/Postman",
            icon_path="~/.local/opt/postman/icon.png",
            categories="Development;",
            comment="API tool",
        )
        self.assertNotIn("Exec=~", content)
        self.assertNotIn("Icon=~", content)
        expected_exec = os.path.abspath(os.path.expanduser("~/.local/opt/postman/Postman"))
        expected_icon = os.path.abspath(os.path.expanduser("~/.local/opt/postman/icon.png"))
        self.assertIn(f"Exec={expected_exec}", content)
        self.assertIn(f"Icon={expected_icon}", content)


class TestCreateDesktopEntry(unittest.TestCase):

    def test_creates_desktop_file_and_updates_database(self):
        from unittest.mock import patch
        from agent.tools import create_desktop_entry
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("shutil.which", return_value="/usr/bin/update-desktop-database"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value.returncode = 0
                    msg = create_desktop_entry(
                        app_name="Postman",
                        exec_path="~/.local/opt/postman/Postman",
                        icon_path="~/.local/opt/postman/icon.png",
                        categories="Development;",
                        comment="API tool",
                        target_dir=tmp_dir,
                    )

                    target_file = Path(tmp_dir) / "postman.desktop"
                    self.assertTrue(target_file.exists())
                    content = target_file.read_text(encoding="utf-8")
                    self.assertIn("Name=Postman", content)
                    expected_exec = os.path.abspath(os.path.expanduser("~/.local/opt/postman/Postman"))
                    self.assertIn(f"Exec={expected_exec}", content)
                    self.assertNotIn("Exec=~", content)
                    self.assertIn("and desktop database updated", msg)

                    mock_run.assert_called_once_with(
                        ["/usr/bin/update-desktop-database", str(Path(tmp_dir))],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

    def test_handles_missing_update_desktop_database(self):
        from unittest.mock import patch
        from agent.tools import create_desktop_entry
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("shutil.which", return_value=None):
                msg = create_desktop_entry(
                    app_name="Custom App",
                    exec_path="/usr/bin/custom",
                    target_dir=tmp_dir,
                )
                target_file = Path(tmp_dir) / "custom-app.desktop"
                self.assertTrue(target_file.exists())
                self.assertIn("Desktop entry created successfully", msg)
                self.assertIn("update-desktop-database not found", msg)


class TestExecuteInspectManual(unittest.TestCase):

    def test_inspect_manual_alias(self):
        self.assertIs(inspect_manual, execute_inspect_manual)

    def test_empty_topic_returns_error(self):
        result = execute_inspect_manual("   ")
        self.assertIn("ERROR: No command or topic specified.", result)

    def test_nonexistent_command_returns_message(self):
        result = execute_inspect_manual("nonexistent_utility_xyz_987")
        self.assertIn("No manual entry or --help documentation available", result)

    def test_help_fallback_for_ls(self):
        result = execute_inspect_manual("ls")
        # Should return usage or options for ls
        self.assertTrue(len(result) > 20)
        self.assertTrue("ls" in result.lower() or "usage" in result.lower())

    def test_truncation_to_120_lines(self):
        from unittest.mock import MagicMock, patch

        fake_output = "\n".join([f"line {i}" for i in range(150)])
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = fake_output

        with patch("shutil.which", return_value="/usr/bin/man"):
            with patch("subprocess.run", return_value=mock_proc):
                result = execute_inspect_manual("dummycmd")
                lines = result.splitlines()
                # 120 lines + empty line + truncation notice
                self.assertTrue(any("more lines truncated" in line for line in lines))
                self.assertEqual(lines[0], "line 0")
                self.assertEqual(lines[119], "line 119")

    def test_timeout_handling_man(self):
        import subprocess
        from unittest.mock import patch

        with patch("shutil.which", return_value="/usr/bin/man"):
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="man", timeout=5),
            ):
                result = execute_inspect_manual("slow_cmd")
                self.assertIn("ERROR: Inspecting manual for 'slow_cmd' timed out after 5s.", result)

    def test_timeout_handling_help_fallback(self):
        import subprocess
        from unittest.mock import patch

        def fake_which(cmd):
            return f"/usr/bin/{cmd}" if cmd == "slow_helper" else None

        with patch("shutil.which", side_effect=fake_which):
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="slow_helper --help", timeout=5),
            ):
                result = execute_inspect_manual("slow_helper")
                self.assertIn("ERROR: Help command for 'slow_helper' timed out after 5s.", result)


class TestNormalizeToolArgs(unittest.TestCase):
    """Verify tool argument normalization and alias resolution."""

    def test_inspect_manual_with_canonical_arg(self):
        res = normalize_tool_args("inspect_manual", {"command_or_topic": "dnf"})
        self.assertEqual(res["command_or_topic"], "dnf")

    def test_inspect_manual_with_command_alias(self):
        res = normalize_tool_args("inspect_manual", {"command": "systemctl"})
        self.assertEqual(res["command_or_topic"], "systemctl")

    def test_inspect_manual_with_topic_alias(self):
        res = normalize_tool_args("inspect_manual", {"topic": "nmcli"})
        self.assertEqual(res["command_or_topic"], "nmcli")

    def test_inspect_manual_empty_defaults_to_bash(self):
        res = normalize_tool_args("inspect_manual", {})
        self.assertEqual(res["command_or_topic"], "bash")

    def test_read_file_snippet_with_canonical_arg(self):
        res = normalize_tool_args("read_file_snippet", {"file_path": "/etc/os-release", "max_lines": 50})
        self.assertEqual(res["file_path"], "/etc/os-release")
        self.assertEqual(res["max_lines"], 50)

    def test_read_file_snippet_with_path_alias_and_default_max_lines(self):
        res = normalize_tool_args("read_file_snippet", {"path": "/etc/fstab"})
        self.assertEqual(res["file_path"], "/etc/fstab")
        self.assertEqual(res["max_lines"], 100)

    def test_propose_shell_command_aliases_and_defaults(self):
        res = normalize_tool_args(
            "propose_shell_command",
            {"cmd": "dnf check-update", "desc": "Check updates", "root": True},
        )
        self.assertEqual(res["command"], "dnf check-update")
        self.assertEqual(res["explanation"], "Check updates")
        self.assertTrue(res["requires_root"])

    def test_create_desktop_entry_field_defaults(self):
        res = normalize_tool_args("create_desktop_entry", {"name": "TestApp", "exec": "/bin/test"})
        self.assertEqual(res["app_name"], "TestApp")
        self.assertEqual(res["exec_path"], "/bin/test")
        self.assertEqual(res["icon_path"], "")
        self.assertEqual(res["categories"], "")
        self.assertEqual(res["comment"], "")


if __name__ == "__main__":
    unittest.main()
