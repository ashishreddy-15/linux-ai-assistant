"""Minimal ANSI terminal-output parser for the GTK output view.

Turns raw PTY output into a stream of tokens the text view can render:

  ("text", str, style)  – printable text with a style dict ({"fg": name, "bold": bool})
  ("cr",)               – a lone carriage return: the current line should be redrawn
                          (progress bars from dnf, flatpak, pip, curl use this)

Only SGR colour/bold codes are honoured. Every other escape sequence (cursor
movement, OSC window titles, bracketed paste, …) is dropped so it never shows
up as garbage. The parser keeps state between chunks because a PTY read can
split an escape sequence in half.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple, Union

# Colour names map to CSS-free GTK text tags created by the view.
_FG_CODES: Dict[int, str] = {
    30: "black", 31: "red", 32: "green", 33: "yellow",
    34: "blue", 35: "magenta", 36: "cyan", 37: "white",
    90: "bright-black", 91: "red", 92: "green", 93: "yellow",
    94: "blue", 95: "magenta", 96: "cyan", 97: "white",
}

# CSI ... final-byte, OSC ... BEL/ST, and two-byte ESC sequences.
_ESCAPE_RE = re.compile(
    r"\x1b\[(?P<csi>[0-9;?]*)(?P<final>[@-~])"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)

Style = Dict[str, object]
Token = Union[Tuple[str, str, Style], Tuple[str]]


class AnsiParser:
    """Stateful, chunk-safe ANSI parser."""

    def __init__(self) -> None:
        self._fg: str | None = None
        self._bold = False
        self._pending = ""

    def _style(self) -> Style:
        return {"fg": self._fg, "bold": self._bold}

    def _apply_sgr(self, params: str) -> None:
        codes = [int(p) for p in params.split(";") if p.isdigit()] or [0]
        for code in codes:
            if code == 0:
                self._fg, self._bold = None, False
            elif code == 1:
                self._bold = True
            elif code == 22:
                self._bold = False
            elif code == 39:
                self._fg = None
            elif code in _FG_CODES:
                self._fg = _FG_CODES[code]

    def _emit_text(self, text: str, out: List[Token]) -> None:
        # Normalise CRLF, then split on lone CRs so the view can redraw lines.
        text = text.replace("\r\n", "\n")
        parts = text.split("\r")
        for i, part in enumerate(parts):
            if i > 0:
                out.append(("cr",))
            cleaned = "".join(ch for ch in part if ch == "\n" or ch == "\t" or ch >= " ")
            if cleaned:
                out.append(("text", cleaned, self._style()))

    def feed(self, chunk: str) -> List[Token]:
        """Parse a chunk of output and return render tokens."""
        data = self._pending + chunk
        self._pending = ""

        # Hold back a trailing, possibly incomplete escape sequence.
        last_esc = data.rfind("\x1b")
        if last_esc != -1 and not _ESCAPE_RE.match(data, last_esc):
            if len(data) - last_esc < 64:
                self._pending = data[last_esc:]
                data = data[:last_esc]

        out: List[Token] = []
        pos = 0
        for match in _ESCAPE_RE.finditer(data):
            if match.start() > pos:
                self._emit_text(data[pos:match.start()], out)
            if match.group("final") == "m":
                self._apply_sgr(match.group("csi") or "")
            pos = match.end()
        if pos < len(data):
            self._emit_text(data[pos:], out)
        return out
