"""GTK-free text helpers for action cards."""

from __future__ import annotations

_VERBS = {
    "install", "reinstall", "remove", "uninstall", "update", "upgrade", "download", "start",
    "restart", "stop", "enable", "disable", "add", "create", "delete", "erase", "fix", "check",
    "clean", "extract", "open", "run", "configure", "change", "copy", "move", "rename", "mount",
    "unmount", "format", "reset", "turn", "free", "back", "connect", "disconnect", "show", "list",
}
_PHRASES = ("set up", "turn on", "turn off", "back up", "clean up", "free up", "sign in", "log out")


def primary_label_for(title: str) -> str:
    """Short verb for the approve button, from an outcome title like
    "Install VLC media player" -> "Install"; "Set up Flathub" -> "Set up"."""
    low = title.strip().lower()
    for phrase in _PHRASES:
        if low.startswith(phrase + " ") or low == phrase:
            return phrase.capitalize()
    first = low.split(" ")[0] if low else ""
    return first.capitalize() if first in _VERBS else "Run"
