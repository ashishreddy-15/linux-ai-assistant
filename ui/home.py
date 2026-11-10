"""Home screen: a launcher-style prompt with keyboard-navigable suggestions."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gtk  # noqa: E402

from ui.widgets import clamp, glow_line, kbd, label, mono  # noqa: E402

# (icon, title, action, text)
#   action "send"    -> send text immediately
#   action "prefill" -> put text in the prompt for the user to finish
SUGGESTIONS = (
    ("package-x-generic-symbolic", "Install an app", "prefill", "Install "),
    ("network-wireless-symbolic", "Fix Wi‑Fi, sound or Bluetooth", "prefill", "Help me fix "),
    ("drive-harddisk-symbolic", "Free up disk space", "send",
     "Show me what is using the most space in my home folder and suggest safe ways to free some up."),
    ("software-update-available-symbolic", "Check for updates", "send",
     "Check whether my system has updates available and help me install them safely."),
    ("applications-system-symbolic", "Why is my computer slow?", "send",
     "My computer feels slow. Check what is using the most CPU and memory and explain it simply."),
)


class HomePage(Gtk.Box):
    def __init__(self, on_send: Callable[[str], None], on_setup_ai: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self._on_send = on_send

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        col.set_margin_top(72)
        col.set_margin_bottom(24)
        col.set_margin_start(24)
        col.set_margin_end(24)

        heads = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._status = mono("", ("la-muted", "la-small"))
        heads.append(self._status)
        heads.append(label("What do you want to do?", ("la-heading",)))
        col.append(heads)

        self._setup = Gtk.Button(label="Set up the AI to get started", halign=Gtk.Align.START, visible=False)
        self._setup.add_css_class("la-button")
        self._setup.add_css_class("suggested-action")
        self._setup.connect("clicked", lambda *_: on_setup_ai())
        col.append(self._setup)

        # Launcher-style prompt.
        prompt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box = Gtk.Box(spacing=12)
        box.add_css_class("la-input")
        box.append(mono("❯", ("la-glyph",), wrap=False))
        self._entry = Gtk.Entry(hexpand=True, placeholder_text="describe a task, e.g. install vlc")
        self._entry.update_property([Gtk.AccessibleProperty.LABEL], ["Describe a task"])
        self._entry.connect("activate", lambda *_: self._activate())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self._entry.add_controller(keys)
        box.append(self._entry)
        send = Gtk.Button(tooltip_text="Start", valign=Gtk.Align.CENTER)
        send.add_css_class("la-kbd-button")
        send.set_child(kbd("↵"))
        send.connect("clicked", lambda *_: self._activate())
        box.append(send)
        prompt.append(box)
        self._glow = glow_line()
        prompt.append(self._glow)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_: self._glow.add_css_class("on"))
        focus.connect("leave", lambda *_: self._glow.remove_css_class("on"))
        self._entry.add_controller(focus)
        col.append(prompt)

        # Suggestions.
        sugg = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        heading = mono("SUGGESTIONS", ("la-label",))
        heading.set_margin_start(10)
        sugg.append(heading)
        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._list.add_css_class("la-list")
        self._list.update_property([Gtk.AccessibleProperty.LABEL], ["Suggestions"])
        for icon, title, action, text in SUGGESTIONS:
            row = Gtk.ListBoxRow()
            inner = Gtk.Box(spacing=12)
            img = Gtk.Image.new_from_icon_name(icon)
            inner.append(img)
            title_lbl = label(title, wrap=False)
            title_lbl.set_hexpand(True)
            inner.append(title_lbl)
            inner.append(kbd("↵"))
            row.set_child(inner)
            row.action = (action, text)  # type: ignore[attr-defined]
            self._list.append(row)
        self._list.select_row(self._list.get_row_at_index(0))
        self._list.connect("row-activated", lambda _l, row: self._run(row))
        sugg.append(self._list)
        col.append(sugg)

        scroller.set_child(clamp(col, 600))
        self.append(scroller)

        hints = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER)
        hints.set_margin_bottom(14)
        for text in ("↑↓ choose", "↵ start", "ctrl , preferences"):
            hints.append(mono(text, ("la-hints",), wrap=False))
        self.append(hints)

    # ------------------------------------------------------------------

    def focus(self) -> None:
        self._entry.grab_focus()

    def refresh(self, status: str, ai_ready: bool) -> None:
        self._status.set_text(status)
        self._setup.set_visible(not ai_ready)

    def _on_key(self, _ctl: Gtk.EventControllerKey, keyval: int, _code: int, _state: Gdk.ModifierType) -> bool:
        if keyval not in (Gdk.KEY_Up, Gdk.KEY_Down):
            return False
        row = self._list.get_selected_row()
        index = row.get_index() if row else -1
        index += 1 if keyval == Gdk.KEY_Down else -1
        target = self._list.get_row_at_index(max(0, min(index, len(SUGGESTIONS) - 1)))
        if target is not None:
            self._list.select_row(target)
        return True

    def _activate(self) -> None:
        text = self._entry.get_text().strip()
        if text:
            self._entry.set_text("")
            self._on_send(text)
            return
        row = self._list.get_selected_row()
        if row is not None:
            self._run(row)

    def _run(self, row: Gtk.ListBoxRow) -> None:
        action, text = row.action  # type: ignore[attr-defined]
        if action == "send":
            self._on_send(text)
        else:
            self._entry.set_text(text)
            self._entry.grab_focus()
            self._entry.set_position(-1)
