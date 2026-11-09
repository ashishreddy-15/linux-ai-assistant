"""Small reusable GTK widgets for the Linux Assistant UI."""

from __future__ import annotations

import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from ui.ansi import AnsiParser  # noqa: E402
from ui.markup import markdown_to_blocks  # noqa: E402

APP_ICON = "la-prompt-symbolic"


def label(
    text: str = "",
    css: tuple[str, ...] = (),
    xalign: float = 0.0,
    wrap: bool = True,
    selectable: bool = False,
) -> Gtk.Label:
    """A plain-text label (never interpreted as markup)."""
    lbl = Gtk.Label(label=text, xalign=xalign, wrap=wrap, selectable=selectable)
    lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for c in css:
        lbl.add_css_class(c)
    return lbl


def mono(text: str, css: tuple[str, ...] = (), **kwargs: object) -> Gtk.Label:
    """A plain-text label in the system monospace font."""
    return label(text, ("monospace",) + css, **kwargs)  # type: ignore[arg-type]


def kbd(text: str) -> Gtk.Label:
    """A small keyboard-hint chip, e.g. ↵ or esc."""
    hint = mono(text, ("la-kbd",), wrap=False)
    hint.set_valign(Gtk.Align.CENTER)
    return hint


def glow_line(danger: bool = False) -> Gtk.Box:
    """The 1px accent line that fades at both ends (toggle with the 'on' class)."""
    line = Gtk.Box()
    line.add_css_class("la-glowline")
    if danger:
        line.add_css_class("danger")
    return line


def clamp(child: Gtk.Widget, width: int) -> Adw.Clamp:
    c = Adw.Clamp(maximum_size=width, tightening_threshold=width)
    c.set_child(child)
    return c


def status_line(text: str, kind: str = "", dot: str | None = None) -> Gtk.Box:
    """A coloured dot plus a mono status word (kind: '', accent, warning, error)."""
    box = Gtk.Box(spacing=7, valign=Gtk.Align.CENTER)
    if dot is not None:
        d = Gtk.Box(valign=Gtk.Align.CENTER)
        d.add_css_class("la-dot")
        if dot:
            d.add_css_class(dot)
        box.append(d)
    lbl = mono(text, ("la-status",) + ((kind,) if kind else ()), wrap=False)
    box.append(lbl)
    box.label = lbl  # type: ignore[attr-defined]
    return box


class TerminalView(Gtk.ScrolledWindow):
    """Read-only, colourised command output (a GtkTextView — never HTML)."""

    _COLORS = {
        "black": "#8b8a99", "red": "#e66161", "green": "#57c48a", "yellow": "#d6a520",
        "blue": "#62a0ea", "magenta": "#c061cb", "cyan": "#33b5c4", "white": "#c0bfbc",
        "bright-black": "#9a9996",
    }
    MAX_CHARS = 200_000

    def __init__(self, height: int = 150) -> None:
        super().__init__(min_content_height=height, max_content_height=height * 2)
        self.set_propagate_natural_height(True)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.add_css_class("la-term-frame")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._parser = AnsiParser()
        self._view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        self._view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._view.add_css_class("la-terminal")
        for side in ("top", "bottom"):
            getattr(self._view, f"set_{side}_margin")(10)
        for side in ("left", "right"):
            getattr(self._view, f"set_{side}_margin")(12)
        self._buf = self._view.get_buffer()
        table = self._buf.get_tag_table()
        for name, color in self._COLORS.items():
            table.add(Gtk.TextTag(name=f"fg-{name}", foreground=color))
        table.add(Gtk.TextTag(name="bold", weight=Pango.Weight.BOLD))
        table.add(Gtk.TextTag(name="prompt", weight=Pango.Weight.BOLD))
        self.set_child(self._view)

    def append_prompt(self, command: str) -> None:
        self._buf.insert_with_tags_by_name(self._buf.get_end_iter(), "$ ", "prompt")
        self._buf.insert(self._buf.get_end_iter(), command.strip() + "\n")

    def append(self, chunk: str) -> None:
        for token in self._parser.feed(chunk):
            if token[0] == "cr":
                # Redraw the current line (progress bars).
                end = self._buf.get_end_iter()
                start = end.copy()
                start.set_line_offset(0)
                self._buf.delete(start, end)
                continue
            _, text, style = token
            tags = []
            if style.get("fg"):
                tags.append(f"fg-{style['fg']}")
            if style.get("bold"):
                tags.append("bold")
            self._buf.insert_with_tags_by_name(self._buf.get_end_iter(), text, *tags)
        self._trim()
        self._scroll_to_end()

    def text(self) -> str:
        return self._buf.get_text(self._buf.get_start_iter(), self._buf.get_end_iter(), False)

    def _trim(self) -> None:
        excess = self._buf.get_char_count() - self.MAX_CHARS
        if excess > 0:
            self._buf.delete(self._buf.get_start_iter(), self._buf.get_iter_at_offset(excess))

    def _scroll_to_end(self) -> None:
        def _do() -> bool:
            adj = self.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_do)


class MarkdownView(Gtk.Box):
    """Renders model Markdown as escaped Pango labels plus code blocks."""

    def __init__(self, text: str, css: tuple[str, ...] = ("la-prose",)) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for kind, content in markdown_to_blocks(text):
            if kind == "code":
                self.append(mono(content, ("la-code",), selectable=True))
            else:
                lbl = Gtk.Label(xalign=0, wrap=True, selectable=True)
                lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                lbl.set_markup(content)  # content is pre-escaped by markdown_to_blocks
                for c in css:
                    lbl.add_css_class(c)
                lbl.connect("activate-link", _open_link)
                self.append(lbl)


def _open_link(lbl: Gtk.Label, uri: str) -> bool:
    if uri.startswith(("https://", "http://")):
        open_uri(lbl, uri)
    return True


def open_uri(widget: Gtk.Widget, uri: str) -> None:
    root = widget.get_root()
    Gtk.UriLauncher(uri=uri).launch(root if isinstance(root, Gtk.Window) else None, None, None, None)


def run_in_thread(work: Callable[[], object], done: Callable[[object], None]) -> None:
    """Run blocking work off the main loop and deliver the result on it."""

    def _worker() -> None:
        try:
            result = work()
        except Exception as exc:  # delivered to the UI as a value
            result = exc
        GLib.idle_add(lambda: (done(result), GLib.SOURCE_REMOVE)[1])

    threading.Thread(target=_worker, daemon=True).start()
