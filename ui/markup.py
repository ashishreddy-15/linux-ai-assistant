"""Convert the small Markdown subset LLMs produce into Pango markup blocks.

Everything is escaped *before* any formatting is applied, so model output can
never inject markup of its own. Code fences become separate "code" blocks
(rendered as monospace, selectable widgets) and carry raw, unescaped text
because they are shown with ``set_text``.
"""

from __future__ import annotations

import html
import re
from typing import List, Tuple

Block = Tuple[str, str]  # ("text", pango_markup) | ("code", raw_text)

_FENCE_RE = re.compile(r"```[^\n`]*\n(.*?)(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC_RE = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")


def escape(text: str) -> str:
    """Escape text for Pango markup."""
    return html.escape(text, quote=True)


def _inline(line: str) -> str:
    """Escape one line and apply inline formatting."""
    # Pull inline code out first so its contents are not formatted.
    codes: List[str] = []

    def _stash(m: re.Match[str]) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    line = _INLINE_CODE_RE.sub(_stash, line)
    out = escape(line)
    out = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', out)
    out = _BOLD_RE.sub(r"<b>\1</b>", out)
    out = _ITALIC_RE.sub(r"<i>\1</i>", out)
    return re.sub(
        r"\x00(\d+)\x00",
        lambda m: f"<tt>{escape(codes[int(m.group(1))])}</tt>",
        out,
    )


def _format_text(text: str) -> str:
    lines = []
    for raw in text.split("\n"):
        heading = _HEADING_RE.match(raw)
        bullet = _BULLET_RE.match(raw)
        numbered = _NUMBERED_RE.match(raw)
        if heading:
            lines.append(f"<b>{_inline(heading.group(2))}</b>")
        elif bullet:
            indent = "    " * (len(bullet.group(1)) // 2)
            lines.append(f"{indent}•  {_inline(bullet.group(2))}")
        elif numbered:
            indent = "    " * (len(numbered.group(1)) // 2)
            lines.append(f"{indent}{numbered.group(2)}.  {_inline(numbered.group(3))}")
        else:
            lines.append(_inline(raw))
    return "\n".join(lines).strip("\n")


def markdown_to_blocks(text: str) -> List[Block]:
    """Split Markdown into text and code blocks with safe Pango markup."""
    blocks: List[Block] = []
    pos = 0
    for match in _FENCE_RE.finditer(text or ""):
        before = text[pos:match.start()].strip("\n")
        if before.strip():
            blocks.append(("text", _format_text(before)))
        code = match.group(1).rstrip("\n")
        if code.strip():
            blocks.append(("code", code))
        pos = match.end()
    rest = (text or "")[pos:].strip("\n")
    if rest.strip():
        blocks.append(("text", _format_text(rest)))
    return blocks
