"""Lightweight graphical askpass helper using zenity or kdialog."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


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


def prompt_via_kdialog(prompt: str) -> bool:
    """Prompt for password using kdialog if available."""
    kdialog = shutil.which("kdialog")
    if not kdialog:
        return False
    try:
        clean_prompt = prompt.strip() or "Authentication Required"
        res = subprocess.run(
            [kdialog, "--password", clean_prompt],
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


def prompt_via_pyside(prompt: str) -> bool:
    """Legacy compatibility stub."""
    return False


def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Password:"
    if prompt_via_zenity(prompt):
        sys.exit(0)
    if prompt_via_kdialog(prompt):
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
