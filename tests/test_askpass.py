"""Tests for executor/askpass.py – GUI askpass helper."""

from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import MagicMock, patch

from executor.askpass import main, prompt_via_pyside, prompt_via_zenity


class TestAskpass(unittest.TestCase):
    """Test askpass helper routines."""

    def test_prompt_via_pyside_success(self):
        with patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("secret_password", True)):
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                res = prompt_via_pyside("Enter password:")
                self.assertTrue(res)
                self.assertEqual(captured_stdout.getvalue(), "secret_password\n")

    def test_prompt_via_pyside_cancel(self):
        with patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("", False)):
            captured_stdout = io.StringIO()
            with patch("sys.stdout", captured_stdout):
                res = prompt_via_pyside("Enter password:")
                self.assertFalse(res)
                self.assertEqual(captured_stdout.getvalue(), "")

    def test_prompt_via_zenity_missing(self):
        with patch("shutil.which", return_value=None):
            self.assertFalse(prompt_via_zenity("Prompt:"))

    def test_prompt_via_zenity_success(self):
        with patch("shutil.which", return_value="/usr/bin/zenity"):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "zenity_pass\n"
            with patch("subprocess.run", return_value=mock_proc):
                captured_stdout = io.StringIO()
                with patch("sys.stdout", captured_stdout):
                    res = prompt_via_zenity("Prompt:")
                    self.assertTrue(res)
                    self.assertEqual(captured_stdout.getvalue(), "zenity_pass\n")

    def test_main_exits_0_on_success(self):
        with patch("executor.askpass.prompt_via_pyside", return_value=True):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)

    def test_main_exits_1_on_failure(self):
        with patch("executor.askpass.prompt_via_pyside", return_value=False), \
             patch("executor.askpass.prompt_via_zenity", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
