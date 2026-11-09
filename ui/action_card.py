"""The approval / progress / result card for a proposed action.

One card walks through the whole lifecycle of a mutating tool call:

    proposed ──approve──▶ running ──▶ done (collapses to one row)
        │                         └─▶ failed (stays open) | stopped
        └────reject───▶ skipped (one row)

Everything the model supplied (title, explanation, effects, command) is shown
with plain-text labels or the escaping Markdown renderer — never raw markup.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gdk, Gtk  # noqa: E402

from executor.pty_runner import is_high_risk  # noqa: E402
from ui.widgets import MarkdownView, TerminalView, glow_line, label, mono, status_line  # noqa: E402

_ROOT_RE = re.compile(r"(^|[\s;&|(])(sudo|pkexec|doas|su)\b")

_RISK_STATUS = {
    "safe": ("no password needed", "", ""),
    "root": ("needs your password", "warning", "warning"),
    "high": ("high risk", "error", "error"),
}


def _risk(payload: Dict[str, Any]) -> str:
    if payload.get("high_risk"):
        return "high"
    if payload.get("requires_root") or _ROOT_RE.search(payload.get("command", "")):
        return "root"
    return "safe"


def _default_title(payload: Dict[str, Any]) -> str:
    if payload.get("title"):
        return str(payload["title"])
    cmd = str(payload.get("command", "")).strip().split("\n")[0]
    return "Run a command" if not cmd else f"Run “{cmd[:40]}{'…' if len(cmd) > 40 else ''}”"


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class ActionCard(Gtk.Box):
    def __init__(
        self,
        payload: Dict[str, Any],
        skill_level: str,
        on_approve: Callable[[str], None],
        on_reject: Callable[[], None],
        on_stop: Callable[[], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("la-card")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Proposed action"])

        self._payload = payload
        self._skill = skill_level
        self._on_approve = on_approve
        self._on_reject = on_reject
        self._on_stop = on_stop
        self._risk = _risk(payload)
        self._started_at = 0.0
        self._timer_id: Optional[int] = None
        self._pulse_id: Optional[int] = None
        self._command_edit: Optional[Gtk.TextView] = None
        self.is_desktop_entry = payload.get("tool_name") == "create_desktop_entry"

        self._build_top()
        self._build_header()
        self._build_body()
        self._build_footer()
        self._apply_risk()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_top(self) -> None:
        self._glow = glow_line()
        self._glow.add_css_class("on")
        self.append(self._glow)
        self._progress = Gtk.ProgressBar(visible=False, pulse_step=0.08)
        self._progress.add_css_class("la-progress")
        self.append(self._progress)

    def _build_header(self) -> None:
        self._header = Gtk.Box(spacing=10)
        self._header.set_margin_top(13)
        self._header.set_margin_start(16)
        self._header.set_margin_end(16)

        self._state_icon = Gtk.Image(visible=False, pixel_size=14, valign=Gtk.Align.CENTER)
        self._header.append(self._state_icon)
        self._title = label(_default_title(self._payload), ("la-card-title",))
        self._title.set_hexpand(True)
        self._header.append(self._title)

        self._status = status_line("", dot="")
        self._header.append(self._status)

        self._toggle = Gtk.Button(icon_name="pan-end-symbolic", visible=False, valign=Gtk.Align.CENTER)
        self._toggle.add_css_class("flat")
        self._toggle.add_css_class("circular")
        self._toggle.set_tooltip_text("Show details")
        self._toggle.connect("clicked", lambda *_: self._toggle_details())
        self._header.append(self._toggle)
        self.append(self._header)

    def _build_body(self) -> None:
        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._body.set_margin_top(10)
        self._body.set_margin_start(16)
        self._body.set_margin_end(16)
        self._body.set_margin_bottom(14)

        self._intro: List[Gtk.Widget] = []
        explanation = str(self._payload.get("explanation") or "").strip()
        if explanation and self._skill != "Advanced":
            self._intro.append(MarkdownView(explanation, ("la-prose", "la-muted")))
        effects: List[str] = list(self._payload.get("effects") or [])
        if effects and self._skill != "Advanced":
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for effect in effects:
                row = Gtk.Box(spacing=10)
                row.append(mono("–", ("la-bullet",), wrap=False))
                row.append(label(effect, ("la-effect",)))
                box.append(row)
            self._intro.append(box)
        for widget in self._intro:
            self._body.append(widget)

        command = str(self._payload.get("command") or "")
        what = "launcher file" if self.is_desktop_entry else "command"
        if self._skill == "Beginner":
            exp = Gtk.Expander(label=f"show {what}")
            exp.add_css_class("la-expander")
            exp.set_child(self._code(command))
            self._body.append(exp)
        elif self._skill == "Advanced" and not self.is_desktop_entry:
            self._command_edit = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            self._command_edit.add_css_class("la-command")
            self._command_edit.get_buffer().set_text(command)
            self._command_edit.update_property([Gtk.AccessibleProperty.LABEL], ["Command"])
            for side in ("top", "bottom"):
                getattr(self._command_edit, f"set_{side}_margin")(10)
            for side in ("left", "right"):
                getattr(self._command_edit, f"set_{side}_margin")(12)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._on_edit_key)
            self._command_edit.add_controller(keys)
            # A wrapping TextView needs a scrolled parent to get a sane height.
            frame = Gtk.ScrolledWindow(min_content_height=60, max_content_height=220)
            frame.set_propagate_natural_height(True)
            frame.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            frame.add_css_class("la-term-frame")
            frame.add_css_class("editable")
            frame.set_overflow(Gtk.Overflow.HIDDEN)
            frame.set_child(self._command_edit)
            self._body.append(frame)
        else:
            self._body.append(mono(what.upper(), ("la-label",)))
            self._body.append(self._code(command))

        self._terminal = TerminalView()
        if self._skill == "Beginner":
            self._output = Gtk.Expander(label="show output", visible=False)
            self._output.add_css_class("la-expander")
            self._output.set_child(self._terminal)
        else:
            self._output = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
            self._output.append(mono("OUTPUT", ("la-label",)))
            self._output.append(self._terminal)
        self._body.append(self._output)

        self._confirm = Gtk.CheckButton(label="I understand this can't be undone", visible=False)
        self._confirm.add_css_class("la-confirm")
        self._confirm.connect("toggled", lambda *_: self._sync_primary())
        self._body.append(self._confirm)
        self.append(self._body)

    def _code(self, text: str) -> Gtk.Label:
        return mono(text, ("la-code",), selectable=True)

    def _build_footer(self) -> None:
        self._footer = Gtk.Box(spacing=8)
        self._footer.add_css_class("la-footer")
        self._note = mono("", ("la-muted", "la-small"), wrap=False)
        self._note.set_hexpand(True)
        self._note.set_ellipsize(3)  # Pango.EllipsizeMode.END
        if self._command_edit is not None:
            self._note.set_text("ctrl+enter runs · edits are re-checked for risk")
        self._footer.append(self._note)

        self._reject_btn = self._button("Reject" if self._skill == "Advanced" else "Not now", "la-outline")
        self._reject_btn.connect("clicked", lambda *_: self._reject())
        self._footer.append(self._reject_btn)

        self._approve_btn = self._button(self._primary_label(), "suggested-action")
        self._approve_btn.connect("clicked", lambda *_: self._approve())
        self._footer.append(self._approve_btn)

        self._stop_btn = self._button("Stop", "la-outline")
        self._stop_btn.set_visible(False)
        self._stop_btn.connect("clicked", lambda *_: self._stop())
        self._footer.append(self._stop_btn)

        self._copy_btn = self._button("Copy output", "flat")
        self._copy_btn.set_visible(False)
        self._copy_btn.connect("clicked", self._copy_output)
        self._footer.append(self._copy_btn)
        self.append(self._footer)

    @staticmethod
    def _button(text: str, css: str) -> Gtk.Button:
        btn = Gtk.Button(label=text)
        btn.add_css_class("la-button")
        btn.add_css_class(css)
        return btn

    def _primary_label(self) -> str:
        if self.is_desktop_entry:
            return "Add to app menu"
        if self._skill == "Advanced":
            return "Run"
        first = str(self._payload.get("title") or "").split(" ")[0]
        return first if first.isalpha() and len(first) <= 12 else "Run"

    # ------------------------------------------------------------------
    # Risk presentation
    # ------------------------------------------------------------------

    def _apply_risk(self) -> None:
        text, kind, dot = _RISK_STATUS[self._risk]
        self._set_status(text, kind, dot)
        high = self._risk == "high"
        if high:
            self.add_css_class("danger")
            self._glow.add_css_class("danger")
            self._approve_btn.remove_css_class("suggested-action")
            self._approve_btn.add_css_class("destructive-action")
        self._confirm.set_visible(high)
        self._sync_primary()

    def _sync_primary(self) -> None:
        self._approve_btn.set_sensitive(self._risk != "high" or self._confirm.get_active())

    def _set_status(self, text: str, kind: str = "", dot: Optional[str] = None) -> None:
        lbl = self._status.label  # type: ignore[attr-defined]
        lbl.set_text(text)
        for css in ("accent", "warning", "error"):
            lbl.remove_css_class(css)
        if kind:
            lbl.add_css_class(kind)
        dot_widget = self._status.get_first_child()
        if dot_widget is not lbl:
            dot_widget.set_visible(dot is not None)
            for css in ("warning", "error"):
                dot_widget.remove_css_class(css)
            if dot:
                dot_widget.add_css_class(dot)

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------

    def _on_edit_key(self, _ctl: Gtk.EventControllerKey, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            self._approve()
            return True
        return False

    def _current_command(self) -> str:
        if self._command_edit is not None:
            buf = self._command_edit.get_buffer()
            edited = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
            return edited or str(self._payload.get("command", ""))
        return str(self._payload.get("command", ""))

    def _approve(self) -> None:
        if not self._approve_btn.get_sensitive() or not self._approve_btn.get_visible():
            return
        command = self._current_command()
        original = str(self._payload.get("command", ""))
        if command != original and self._risk != "high" and is_high_risk(command):
            # The user's edit turned this into something destructive: ask again.
            self._risk = "high"
            self._apply_risk()
            self._approve_btn.set_label("Run anyway")
            self._note.set_text("your edit is high risk · confirm to run it")
            return
        self.start_running(command)
        self._on_approve(command)

    def _reject(self) -> None:
        self._collapse("skipped", "", None, "go-next-symbolic", "la-skip")
        self._on_reject()

    def _stop(self) -> None:
        self._stop_btn.set_sensitive(False)
        self._stop_btn.set_label("Stopping…")
        self._on_stop()

    # ------------------------------------------------------------------
    # Lifecycle driven by the orchestrator
    # ------------------------------------------------------------------

    def start_running(self, command: str) -> None:
        if self._command_edit is not None:
            self._command_edit.set_editable(False)
        self._confirm.set_visible(False)
        self._glow.set_visible(False)
        self._progress.set_visible(True)
        self._pulse_id = GLib.timeout_add(90, self._pulse)
        self._reject_btn.set_visible(False)
        self._approve_btn.set_visible(False)
        self._stop_btn.set_visible(not self.is_desktop_entry)
        self._output.set_visible(True)
        if not self.is_desktop_entry:
            self._terminal.append_prompt(command)
        self._started_at = time.monotonic()
        self._set_status("running · 0:00", "accent", None)
        self._note.set_text("you can keep using your computer")
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)

    def append_output(self, chunk: str) -> None:
        self._terminal.append(chunk)

    def finish(self, exit_code: int) -> None:
        self._stop_timers()
        elapsed = _format_duration(time.monotonic() - self._started_at)
        self._stop_btn.set_visible(False)
        self._progress.set_visible(False)
        if exit_code == 0:
            self._collapse(f"done · {elapsed}", "", None, "object-select-symbolic", "la-ok", expandable=True)
        elif exit_code == -1:
            self._collapse("stopped", "warning", None, "process-stop-symbolic", "la-bad", expandable=True)
        else:
            self._state_icon.set_from_icon_name("dialog-warning-symbolic")
            self._state_icon.add_css_class("la-bad")
            self._state_icon.set_visible(True)
            self._set_status(f"failed · exit {exit_code}", "error", None)
            self._note.set_text("I'll look into what went wrong")
            if isinstance(self._output, Gtk.Expander):
                self._output.set_expanded(True)
            self._copy_btn.set_visible(True)

    # ------------------------------------------------------------------

    def _collapse(
        self, status: str, kind: str, dot: Optional[str], icon: str, icon_css: str, expandable: bool = False
    ) -> None:
        self._stop_timers()
        self.add_css_class("compact")
        self.remove_css_class("danger")
        self._glow.set_visible(False)
        self._progress.set_visible(False)
        self._state_icon.set_from_icon_name(icon)
        self._state_icon.add_css_class(icon_css)
        self._state_icon.set_visible(True)
        self._set_status(status, kind, dot)
        self._header.set_margin_bottom(12)
        self._header.set_margin_top(12)
        self._title.remove_css_class("la-card-title")
        self._body.set_visible(False)
        self._footer.set_visible(False)
        self._copy_btn.set_visible(expandable)
        self._note.set_text("")
        self._toggle.set_visible(expandable)
        for widget in self._intro:
            widget.set_visible(False)

    def _toggle_details(self) -> None:
        show = not self._body.get_visible()
        self._body.set_visible(show)
        self._footer.set_visible(show)
        self._header.set_margin_bottom(0 if show else 12)
        self._toggle.set_icon_name("pan-down-symbolic" if show else "pan-end-symbolic")
        self._toggle.set_tooltip_text("Hide details" if show else "Show details")
        if show and isinstance(self._output, Gtk.Expander):
            self._output.set_expanded(True)

    def _tick(self) -> bool:
        self._set_status(f"running · {_clock(time.monotonic() - self._started_at)}", "accent", None)
        return GLib.SOURCE_CONTINUE

    def _pulse(self) -> bool:
        self._progress.pulse()
        return GLib.SOURCE_CONTINUE

    def _stop_timers(self) -> None:
        for attr in ("_timer_id", "_pulse_id"):
            source = getattr(self, attr)
            if source is not None:
                GLib.source_remove(source)
                setattr(self, attr, None)

    def _copy_output(self, *_args: object) -> None:
        self.get_clipboard().set(self._terminal.text())
