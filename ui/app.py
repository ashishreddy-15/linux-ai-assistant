"""GTK4 / libadwaita application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from agent.orchestrator import AgentOrchestrator  # noqa: E402
from ui.controller import AppController  # noqa: E402
from ui.preferences import apply_theme  # noqa: E402
from ui.window import MainWindow  # noqa: E402

APP_ID = "io.github.LinuxAssistant"
UI_DIR = Path(__file__).resolve().parent


class _PlaceholderClient:
    """Stands in until AppController.rebuild_client() installs the real one."""

    def generate(self, history):  # pragma: no cover - replaced immediately
        raise RuntimeError("AI is not configured yet.")

    def update_system_prompt(self, system_prompt: str) -> None:
        pass


class AssistantApplication(Adw.Application):
    def __init__(self, force_onboarding: bool = False) -> None:
        super().__init__(application_id=APP_ID)
        self._force_onboarding = force_onboarding
        self._window: MainWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(str(UI_DIR / "icons"))
        css = Gtk.CssProvider()
        css.load_from_path(str(UI_DIR / "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # GTK evaluates @media (prefers-color-scheme) per provider; libadwaita
        # only updates its own, so keep ours in sync with light/dark mode.
        style = Adw.StyleManager.get_default()

        def _sync_scheme(*_args: object) -> None:
            scheme = Gtk.InterfaceColorScheme.DARK if style.get_dark() else Gtk.InterfaceColorScheme.LIGHT
            css.set_property("prefers-color-scheme", scheme)

        style.connect("notify::dark", _sync_scheme)
        _sync_scheme()
        self._css = css

    def do_activate(self) -> None:
        if self._window is None:
            orchestrator = AgentOrchestrator(_PlaceholderClient())
            controller = AppController(orchestrator)
            controller.rebuild_client()
            apply_theme(controller.theme())
            self._window = MainWindow(self, orchestrator, controller, self._force_onboarding)
        self._window.present()


def run(force_onboarding: bool = False) -> int:
    app = AssistantApplication(force_onboarding=force_onboarding)
    return app.run([sys.argv[0]])
