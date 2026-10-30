"""Lightweight graphical askpass helper using PySide6 QInputDialog."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def prompt_via_pyside(prompt: str) -> bool:
    """Prompt for password using PySide6 QInputDialog."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit

        app = QApplication.instance()
        if app is None:
            app = QApplication([sys.argv[0]])

        clean_prompt = prompt.strip() if prompt else "Password:"
        if not clean_prompt:
            clean_prompt = "Password:"

        password, ok = QInputDialog.getText(
            None,
            "Authentication Required",
            clean_prompt,
            QLineEdit.EchoMode.Password,
        )
        if ok and password:
            sys.stdout.write(password + "\n")
            sys.stdout.flush()
            return True
    except Exception:
        pass
    return False


def prompt_via_zenity(prompt: str) -> bool:
    """Prompt for password using zenity if available."""
    zenity = shutil.which("zenity")
    if not zenity:
        return False
    try:
        clean_prompt = prompt.strip() or "Authentication Required"
        res = subprocess.run(
            [zenity, "--password", f"--title={clean_prompt}"],
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            sys.stdout.write(res.stdout)
            sys.stdout.flush()
            return True
    except Exception:
        pass
    return False


def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Password:"
    # Try PySide6 first, then zenity fallback
    if prompt_via_pyside(prompt):
        sys.exit(0)
    if prompt_via_zenity(prompt):
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
