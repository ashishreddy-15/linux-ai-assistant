"""Set up the local AI from inside the app: install, start, download a model.

Each step is an ordinary approval card. Approved commands run through the same
PTY runner as everything else, with live output in the card.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from agent import local_setup  # noqa: E402
from executor.pty_runner import CommandRunner  # noqa: E402
from ui.action_card import ActionCard  # noqa: E402
from ui.controller import AppController  # noqa: E402
from ui.widgets import clamp, label, mono, run_in_thread  # noqa: E402


def _idle(fn: Callable[..., None]) -> Callable[..., None]:
    return lambda *args: GLib.idle_add(lambda: (fn(*args), GLib.SOURCE_REMOVE)[1])


class LocalSetupView(Gtk.Box):
    """Checklist + the approval card for the next missing step.

    ``on_ready(model)`` is called once Ollama runs a model that uses tools.
    """

    def __init__(
        self,
        controller: AppController,
        on_ready: Callable[[str], None],
        on_cancel: Optional[Callable[[], None]] = None,
        cancel_label: str = "Use a cloud AI instead",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self._controller = controller
        self._on_ready = on_ready
        self._card: Optional[ActionCard] = None
        self._runner: Optional[CommandRunner] = None
        self._ready_model: Optional[str] = None

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        col.set_margin_top(40)
        col.set_margin_bottom(24)
        col.set_margin_start(24)
        col.set_margin_end(24)

        heads = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        heads.append(mono("local ai", ("la-muted", "la-small")))
        heads.append(label("Set up the AI on this computer", ("la-heading",)))
        heads.append(label(
            "A few steps, all inside your home folder. Once set up, the assistant works "
            "without internet and nothing you ask leaves this computer.",
            ("la-muted", "la-prose"),
        ))
        col.append(heads)

        self._checks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.append(self._checks)

        self._slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        col.append(self._slot)

        self._message = label("", ("la-prose",))
        self._message.set_visible(False)
        col.append(self._message)

        footer = Gtk.Box(spacing=8)
        footer.add_css_class("la-footer")
        if on_cancel is not None:
            cancel = Gtk.Button(label=cancel_label)
            cancel.add_css_class("la-button")
            cancel.add_css_class("flat")
            cancel.connect("clicked", lambda *_: on_cancel())
            footer.append(cancel)
        footer.append(Gtk.Box(hexpand=True))
        self._retry = Gtk.Button(label="Check again", visible=False)
        self._retry.add_css_class("la-button")
        self._retry.add_css_class("la-outline")
        self._retry.connect("clicked", lambda *_: self.refresh())
        footer.append(self._retry)
        self._done = Gtk.Button(label="Start", visible=False)
        self._done.add_css_class("la-button")
        self._done.add_css_class("suggested-action")
        self._done.connect("clicked", lambda *_: self._ready_model and self._on_ready(self._ready_model))
        footer.append(self._done)
        col.append(footer)

        scroller.set_child(clamp(col, 600))
        self.append(scroller)
        self.refresh()

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        self._retry.set_visible(False)
        self._say("checking…", muted=True)
        ram = float(self._controller.profile().get("total_ram_gb") or 0.0)
        run_in_thread(lambda: local_setup.detect(ram), self._show)

    def _say(self, text: str, muted: bool = False, error: bool = False) -> None:
        self._message.set_text(text)
        self._message.set_visible(bool(text))
        for css in ("la-muted", "la-error-title"):
            self._message.remove_css_class(css)
        if muted:
            self._message.add_css_class("la-muted")
        if error:
            self._message.add_css_class("la-error-title")

    def _render_checks(self, items: List[tuple[str, bool]], current: Optional[int]) -> None:
        while (child := self._checks.get_first_child()) is not None:
            self._checks.remove(child)
        for i, (text, ok) in enumerate(items):
            row = Gtk.Box(spacing=10)
            icon = Gtk.Image.new_from_icon_name("object-select-symbolic" if ok else "media-record-symbolic")
            icon.set_pixel_size(12 if ok else 8)
            icon.add_css_class("la-ok" if ok else "la-skip")
            icon.set_size_request(14, -1)
            row.append(icon)
            css = ("la-small",) + (() if ok or i == current else ("la-muted",))
            lbl = mono(text, css, wrap=False)
            if i == current:
                lbl.add_css_class("la-status")
                lbl.add_css_class("accent")
            row.append(lbl)
            self._checks.append(row)

    def _show(self, status: object) -> None:
        if isinstance(status, Exception):
            self._say(f"Couldn't check the local AI: {status}", error=True)
            self._retry.set_visible(True)
            return
        assert isinstance(status, local_setup.LocalStatus)
        items = local_setup.checklist(status)
        current = next((i for i, (_t, ok) in enumerate(items) if not ok), None)
        self._render_checks(items, current)
        self._clear_card()

        step = local_setup.next_step(status)
        if step is None:
            self._verify(local_setup.usable_model(status) or "")
            return
        self._say("")
        self._card = ActionCard(
            step,
            self._controller.skill_level(),
            on_approve=self._run,
            on_reject=self._rejected,
            on_stop=lambda: self._runner and self._runner.request_stop(),
        )
        self._slot.append(self._card)

    def _clear_card(self) -> None:
        while (child := self._slot.get_first_child()) is not None:
            self._slot.remove(child)
        self._card = None

    # ------------------------------------------------------------------

    def _run(self, command: str) -> None:
        card = self._card
        runner = CommandRunner(command=command)
        self._runner = runner
        if card is not None:
            runner.output_received.connect(_idle(card.append_output))
        runner.command_finished.connect(_idle(self._finished))
        threading.Thread(target=runner.start_execution, daemon=True).start()

    def _finished(self, exit_code: int, _tail: str) -> None:
        self._runner = None
        if self._card is not None:
            self._card.finish(exit_code)
        if exit_code == 0:
            # Give a freshly started server a moment before checking again.
            GLib.timeout_add(1500, lambda: (self.refresh(), GLib.SOURCE_REMOVE)[1])
        else:
            self._say("That step didn't finish. Open its details to see why, then try again.", error=True)
            self._retry.set_visible(True)

    def _rejected(self) -> None:
        self._say("Skipped. You can set up the local AI later from Preferences.", muted=True)
        self._retry.set_label("Show the step again")
        self._retry.set_visible(True)

    def _verify(self, model: str) -> None:
        self._say(f"checking that {model} can run commands…", muted=True)

        def done(result: object) -> None:
            if isinstance(result, Exception):
                ok, message = False, str(result)
            else:
                ok, message, _models = result  # type: ignore[misc]
            if ok:
                self._ready_model = model
                self._say(f"✓ {model} is ready. Everything runs on this computer.")
                self._done.set_visible(True)
                self._done.grab_focus()
            else:
                self._say(message, error=True)
                self._retry.set_label("Check again")
                self._retry.set_visible(True)

        run_in_thread(lambda: self._controller.check_local_model(model), done)


class LocalSetupDialog(Adw.Dialog):
    """LocalSetupView in a dialog, for Preferences and the home screen."""

    def __init__(self, controller: AppController, on_ready: Callable[[str], None]) -> None:
        super().__init__(title="Local AI", content_width=640, content_height=640)
        view = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        view.add_top_bar(Adw.HeaderBar())

        def ready(model: str) -> None:
            on_ready(model)
            self.close()

        view.set_content(LocalSetupView(controller, ready, on_cancel=self.close, cancel_label="Close"))
        self.set_child(view)
