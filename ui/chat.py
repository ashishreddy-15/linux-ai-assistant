"""Conversation view: user messages, assistant replies, action cards, errors."""

from __future__ import annotations

from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ui.widgets import MarkdownView, clamp, label, mono  # noqa: E402

_AUTH_HINTS = ("401", "403", "api key", "api_key", "unauthorized", "unconfigured", "permission denied")
_NETWORK_HINTS = ("connection", "timed out", "timeout", "name resolution", "unreachable")


def friendly_error(message: str) -> tuple[str, bool]:
    """Return (plain-language summary, offer_preferences)."""
    low = message.lower()
    if any(h in low for h in _AUTH_HINTS):
        return "The AI service didn't accept your key. You can fix it in Preferences.", True
    if any(h in low for h in _NETWORK_HINTS):
        return "I couldn't reach the AI service. Check your internet connection and try again.", False
    if "429" in low or "rate limit" in low or "resource_exhausted" in low:
        return "The AI service is busy or you've hit its free limit. Wait a minute and try again.", False
    return "Something went wrong while talking to the AI service.", False


class ChatView(Gtk.ScrolledWindow):
    def __init__(self, on_open_preferences: Callable[[], None]) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self._on_open_preferences = on_open_preferences
        self._column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._column.set_margin_top(28)
        self._column.set_margin_bottom(24)
        self._column.set_margin_start(24)
        self._column.set_margin_end(24)
        self._typing: Optional[Gtk.Widget] = None
        self._live: Optional[Gtk.Label] = None
        self.set_child(clamp(self._column, 688))  # 640 of content + margins

    def clear(self) -> None:
        while (child := self._column.get_first_child()) is not None:
            self._column.remove(child)
        self._typing = None
        self._live = None

    def add_user(self, text: str) -> None:
        bubble = Gtk.Box(halign=Gtk.Align.END)
        bubble.add_css_class("la-user")
        lbl = label(text, ("la-prose",), selectable=True)
        lbl.set_max_width_chars(52)
        bubble.append(lbl)
        self._append(bubble)

    def add_assistant(self, text: str) -> None:
        """Show a finished reply, replacing the streamed draft if there is one."""
        view = MarkdownView(text)
        if self._live is not None:
            self._column.insert_child_after(view, self._live)
            self._column.remove(self._live)
            self._live = None
            GLib.timeout_add(50, self._scroll_to_end)
        else:
            self._append(view)

    def stream(self, chunk: str) -> None:
        """Append streamed text to a plain draft label (formatted when done)."""
        if self._live is None:
            self.set_typing(False)
            self._live = label("", ("la-prose",), selectable=True)
            self._append(self._live)
        self._live.set_text(self._live.get_text() + chunk)
        GLib.timeout_add(30, self._scroll_to_end)

    def end_stream(self) -> None:
        """Drop an unfinished draft (e.g. the reply turned out to be a tool call)."""
        if self._live is not None:
            if not self._live.get_text().strip():
                self._column.remove(self._live)
            self._live = None

    def add_activity(self, text: str) -> None:
        """A quiet line for read-only lookups the assistant made on its own."""
        line = mono(f"↳ {text}", ("la-muted", "la-small"))
        line.set_margin_start(2)
        self._append(line)

    def add_card(self, card: Gtk.Widget) -> None:
        self._append(card)

    def add_error(self, message: str) -> None:
        summary, offer_prefs = friendly_error(message)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("la-error")
        box.append(label(summary, ("la-error-title",)))
        details = Gtk.Expander(label="details")
        details.add_css_class("la-expander")
        details.set_child(mono(message, ("la-code", "la-small"), selectable=True))
        box.append(details)
        if offer_prefs:
            btn = Gtk.Button(label="Open Preferences", halign=Gtk.Align.START)
            btn.add_css_class("la-button")
            btn.add_css_class("la-outline")
            btn.connect("clicked", lambda *_: self._on_open_preferences())
            box.append(btn)
        self._append(box)

    def set_typing(self, typing: bool) -> None:
        if typing and self._typing is None:
            dots = Gtk.Box(spacing=5, halign=Gtk.Align.START)
            for css in ("d1", "d2", "d3"):
                d = Gtk.Box(valign=Gtk.Align.CENTER)
                d.add_css_class("la-typing")
                d.add_css_class(css)
                dots.append(d)
            dots.update_property([Gtk.AccessibleProperty.LABEL], ["Assistant is thinking"])
            self._typing = dots
            self._append(dots)
        elif not typing and self._typing is not None:
            self._column.remove(self._typing)
            self._typing = None

    def _append(self, widget: Gtk.Widget) -> None:
        # Keep the typing indicator last.
        if self._typing is not None and widget is not self._typing:
            self._column.insert_child_after(widget, self._typing.get_prev_sibling())
        else:
            self._column.append(widget)
        GLib.timeout_add(50, self._scroll_to_end)

    def _scroll_to_end(self) -> bool:
        adj = self.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return GLib.SOURCE_REMOVE
