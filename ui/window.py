"""Main application window."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from agent.orchestrator import AgentOrchestrator  # noqa: E402
from ui.action_card import ActionCard  # noqa: E402
from ui.chat import ChatView  # noqa: E402
from ui.controller import AppController  # noqa: E402
from ui.home import HomePage  # noqa: E402
from ui.local_setup import LocalSetupDialog  # noqa: E402
from ui.onboarding import Onboarding  # noqa: E402
from ui.preferences import PreferencesDialog  # noqa: E402
from ui.widgets import APP_ICON, clamp, glow_line, label, mono  # noqa: E402

_PLACEHOLDERS = {
    "IDLE": "what's next?",
    "ERROR": "try again, or ask something else",
    "THINKING": "thinking…",
    "AWAITING_APPROVAL": "choose an option on the card above",
    "EXECUTING": "working…",
}


def on_main_thread(fn: Callable[..., Any]) -> Callable[..., None]:
    """Wrap a callback so it always runs on the GTK main loop."""

    def _wrapper(*args: Any) -> None:
        GLib.idle_add(lambda: (fn(*args), GLib.SOURCE_REMOVE)[1])

    return _wrapper


class MainWindow(Adw.ApplicationWindow):
    def __init__(
        self,
        app: Adw.Application,
        orchestrator: AgentOrchestrator,
        controller: AppController,
        force_onboarding: bool = False,
    ) -> None:
        super().__init__(application=app, title="Linux Assistant", default_width=960, default_height=720)
        self.set_size_request(380, 480)
        self._orch = orchestrator
        self._controller = controller
        self._card: Optional[ActionCard] = None
        self._state = orchestrator.state.value

        self._toasts = Adw.ToastOverlay()
        self._root = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._toasts.set_child(self._root)
        self.set_content(self._toasts)

        self._root.add_named(self._build_main(), "main")
        self._install_actions()
        self._connect_orchestrator()
        self._refresh_home()

        if force_onboarding or controller.needs_onboarding():
            self._show_onboarding()
        else:
            self._root.set_visible_child_name("main")
            self._home.focus()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_main(self) -> Gtk.Widget:
        view = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        self._header = Adw.HeaderBar()
        self._header.add_css_class("la-header")
        self._title = label("Linux Assistant", ("la-title",), wrap=False)
        self._title.set_ellipsize(3)
        self._title.set_max_width_chars(48)
        self._header.set_title_widget(self._title)

        new_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New conversation (Ctrl+N)")
        new_btn.set_action_name("win.new-chat")
        self._header.pack_start(new_btn)
        menu = Gio.Menu()
        menu.append("New conversation", "win.new-chat")
        menu.append("Preferences", "win.preferences")
        menu.append("About Linux Assistant", "win.about")
        self._header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu"))
        view.add_top_bar(self._header)

        self._pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self._home = HomePage(self._send, self._setup_ai)
        chat_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._chat = ChatView(self._open_preferences)
        chat_page.append(self._chat)
        chat_page.append(self._build_composer())
        self._pages.add_named(self._home, "home")
        self._pages.add_named(chat_page, "chat")
        view.set_content(self._pages)
        return view

    def _build_composer(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box = Gtk.Box(spacing=12)
        box.add_css_class("la-input")
        box.add_css_class("compact")
        self._glyph = mono("❯", ("la-glyph",), wrap=False)
        box.append(self._glyph)
        self._entry = Gtk.Entry(hexpand=True, placeholder_text=_PLACEHOLDERS["IDLE"])
        self._entry.update_property([Gtk.AccessibleProperty.LABEL], ["Message"])
        self._entry.connect("activate", lambda *_: self._send_from_entry())
        box.append(self._entry)
        outer.append(box)
        self._composer_glow = glow_line()
        outer.append(self._composer_glow)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_: self._composer_glow.add_css_class("on"))
        focus.connect("leave", lambda *_: self._composer_glow.remove_css_class("on"))
        self._entry.add_controller(focus)

        wrap = clamp(outer, 640)
        wrap.set_margin_start(24)
        wrap.set_margin_end(24)
        wrap.set_margin_top(10)
        wrap.set_margin_bottom(18)
        self._composer_box = box
        self._sync_composer()
        return wrap

    def _install_actions(self) -> None:
        for name, handler in (
            ("new-chat", self._new_chat),
            ("preferences", self._open_preferences),
            ("about", self._about),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, h=handler: h())
            self.add_action(action)
        app = self.get_application()
        app.set_accels_for_action("win.new-chat", ["<Control>n"])
        app.set_accels_for_action("win.preferences", ["<Control>comma"])

    def _connect_orchestrator(self) -> None:
        o = self._orch
        o.response_chunk.connect(on_main_thread(self._chat.stream))
        o.response_ready.connect(on_main_thread(self._on_response))
        o.state_changed.connect(on_main_thread(self._on_state))
        o.approval_requested.connect(on_main_thread(self._on_approval))
        o.command_output.connect(on_main_thread(self._on_output))
        o.command_finished.connect(on_main_thread(self._on_finished))
        o.error_occurred.connect(on_main_thread(self._chat.add_error))
        o.tool_activity.connect(on_main_thread(self._chat.add_activity))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _show_onboarding(self) -> None:
        old = self._root.get_child_by_name("onboarding")
        if old is not None:
            self._root.remove(old)
        wrapper = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        header = Adw.HeaderBar(show_title=False)
        header.add_css_class("la-header")
        wrapper.add_top_bar(header)
        wrapper.set_content(Onboarding(self._controller, self._finish_onboarding))
        self._root.add_named(wrapper, "onboarding")
        self._root.set_visible_child_name("onboarding")

    def _finish_onboarding(self) -> None:
        self._controller.rebuild_client()
        self._refresh_home()
        self._root.set_visible_child_name("main")
        self._home.focus()

    def _refresh_home(self) -> None:
        self._home.refresh(self._controller.status_line(), self._controller.has_usable_ai())

    def _new_chat(self) -> None:
        if self._state not in ("IDLE", "ERROR"):
            self._toasts.add_toast(Adw.Toast(title="Wait for the current step to finish first"))
            return
        self._orch.clear_history()
        self._chat.clear()
        self._card = None
        self._title.set_text("Linux Assistant")
        self._header.remove_css_class("divided")
        self._pages.set_visible_child_name("home")
        self._refresh_home()
        self._home.focus()

    def _open_preferences(self) -> None:
        PreferencesDialog(self._controller, self._refresh_home, self._open_local_setup).present(self)

    def _setup_ai(self) -> None:
        if self._controller.active_provider() == "ollama":
            self._open_local_setup()
        else:
            self._open_preferences()

    def _open_local_setup(self) -> None:
        def ready(model: str) -> None:
            self._controller.set_provider("ollama", model_name=model)
            self._refresh_home()
            self._toasts.add_toast(Adw.Toast(title=f"Local AI ready: {model}"))

        LocalSetupDialog(self._controller, ready).present(self)

    def _about(self) -> None:
        Adw.AboutDialog(
            application_name="Linux Assistant",
            application_icon=APP_ICON,
            version="0.2.0",
            comments="A friendly assistant that explains every step and asks before changing anything.",
            license_type=Gtk.License.MIT_X11,
        ).present(self)

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def _send_from_entry(self) -> None:
        text = self._entry.get_text().strip()
        if text and self._can_send():
            self._entry.set_text("")
            self._send(text)

    def _send(self, text: str) -> None:
        if not self._can_send():
            return
        if self._pages.get_visible_child_name() == "home":
            self._pages.set_visible_child_name("chat")
            self._header.add_css_class("divided")
            self._title.set_text(text if len(text) <= 48 else text[:46] + "…")
        self._chat.add_user(text)
        self._orch.send_message(text)

    def _can_send(self) -> bool:
        return self._state in ("IDLE", "ERROR")

    def _sync_composer(self) -> None:
        can = self._can_send()
        self._entry.set_sensitive(can)
        self._glyph.set_sensitive(can)
        self._entry.set_placeholder_text(_PLACEHOLDERS.get(self._state, _PLACEHOLDERS["IDLE"]))

    # ------------------------------------------------------------------
    # Orchestrator events (main thread)
    # ------------------------------------------------------------------

    def _on_state(self, state: str) -> None:
        self._state = state
        if state != "THINKING":
            self._chat.end_stream()
        self._chat.set_typing(state == "THINKING")
        self._sync_composer()
        if state in ("IDLE", "ERROR") and self._pages.get_visible_child_name() == "chat":
            self._entry.grab_focus()

    def _on_response(self, text: str) -> None:
        self._chat.add_assistant(text)

    def _on_approval(self, payload: Dict[str, Any]) -> None:
        card = ActionCard(
            payload,
            self._controller.skill_level(),
            on_approve=self._orch.handle_user_approval,
            on_reject=lambda: self._orch.handle_user_rejection("The user chose not to do this."),
            on_stop=self._orch.abort,
        )
        self._card = card
        self._chat.add_card(card)

    def _on_output(self, chunk: str) -> None:
        if self._card is not None:
            self._card.append_output(chunk)

    def _on_finished(self, exit_code: int, _summary: str) -> None:
        if self._card is not None:
            self._card.finish(exit_code)
            self._card = None
        self._refresh_home()
