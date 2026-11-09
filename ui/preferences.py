"""Preferences dialog (libadwaita). Every change applies immediately."""

from __future__ import annotations

from typing import Callable, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from config.keyring_manager import get_api_key  # noqa: E402
from config.profile_manager import VALID_PROVIDERS  # noqa: E402
from ui.controller import (  # noqa: E402
    SKILL_LABELS,
    AppController,
    provider_display_name,
    provider_models,
)
from ui.widgets import run_in_thread  # noqa: E402

THEMES = (("system", "Follow system"), ("light", "Light"), ("dark", "Dark"))


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, controller: AppController, on_changed: Callable[[], None]) -> None:
        super().__init__(title="Preferences", search_enabled=False)
        self._controller = controller
        self._on_changed = on_changed
        self._loading = True
        self.add(self._general_page())
        self.add(self._ai_page())
        self.add(self._system_page())
        self._loading = False

    # ------------------------------------------------------------------

    def _general_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")

        exp = Adw.PreferencesGroup(
            title="Experience", description="Changes how much detail I show and whether you can edit commands."
        )
        levels = list(SKILL_LABELS)
        self._skill_row = Adw.ComboRow(
            title="How comfortable are you with Linux?",
            model=Gtk.StringList.new([SKILL_LABELS[l][0] for l in levels]),
        )
        self._skill_row.set_selected(levels.index(self._controller.skill_level()))
        self._skill_row.connect("notify::selected", lambda r, _p: self._set_skill(levels[r.get_selected()]))
        exp.add(self._skill_row)
        page.add(exp)

        look = Adw.PreferencesGroup(title="Appearance")
        keys = [t[0] for t in THEMES]
        theme_row = Adw.ComboRow(title="Style", model=Gtk.StringList.new([t[1] for t in THEMES]))
        current = self._controller.theme()
        theme_row.set_selected(keys.index(current) if current in keys else 0)
        theme_row.connect("notify::selected", lambda r, _p: self._set_theme(keys[r.get_selected()]))
        look.add(theme_row)
        page.add(look)
        return page

    def _ai_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="AI", icon_name="starred-symbolic")
        group = Adw.PreferencesGroup(
            title="AI service", description="Keys are stored in your system's password keyring."
        )
        self._providers: List[str] = list(VALID_PROVIDERS)
        self._provider_row = Adw.ComboRow(
            title="Provider",
            model=Gtk.StringList.new([provider_display_name(p) for p in self._providers]),
        )
        active = self._controller.active_provider()
        self._provider_row.set_selected(self._providers.index(active))
        self._provider_row.connect("notify::selected", lambda *_: self._provider_changed())
        group.add(self._provider_row)

        self._model_row = Adw.ComboRow(title="Model")
        self._model_row.connect("notify::selected", lambda *_: self._model_changed())
        group.add(self._model_row)

        self._key_row = Adw.PasswordEntryRow(title="API key", show_apply_button=True)
        self._key_row.connect("apply", lambda *_: self._apply_key())
        group.add(self._key_row)

        self._status_row = Adw.ActionRow(title="Status")
        group.add(self._status_row)
        page.add(group)
        self._sync_provider_rows(active, self._controller.profile().get("model_name"))
        return page

    def _system_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="System", icon_name="computer-symbolic")
        group = Adw.PreferencesGroup(
            title="This computer", description="Shared with the AI so it suggests commands that fit your system."
        )
        self._system_group = group
        self._system_rows: List[Adw.ActionRow] = []
        self._fill_system_rows()
        refresh = Gtk.Button(label="Refresh", valign=Gtk.Align.CENTER)
        refresh.add_css_class("flat")
        refresh.connect("clicked", lambda *_: self._refresh_system())
        group.set_header_suffix(refresh)
        page.add(group)
        return page

    # ------------------------------------------------------------------

    def _fill_system_rows(self) -> None:
        for row in self._system_rows:
            self._system_group.remove(row)
        self._system_rows = []
        profile = self._controller.profile()
        facts = self._controller.system_facts()
        rows = (
            ("Operating system", profile.get("os_pretty_name") or facts["os"]),
            ("Desktop", facts["desktop"]),
            ("Package manager", ", ".join(filter(None, [profile.get("primary_package_manager")] + list(profile.get("secondary_helpers") or []))) or "unknown"),
            ("Memory", f"{profile.get('total_ram_gb', 0):.0f} GB"),
            ("Free disk space", f"{facts['disk_free_gb']:.0f} GB"),
        )
        for title, value in rows:
            row = Adw.ActionRow(title=title, subtitle=str(value), subtitle_selectable=True)
            row.add_css_class("property")
            self._system_group.add(row)
            self._system_rows.append(row)

    def _refresh_system(self) -> None:
        self._controller.refresh_system_profile()
        self._fill_system_rows()
        self.add_toast(Adw.Toast(title="System info refreshed"))

    def _set_skill(self, level: str) -> None:
        if not self._loading:
            self._controller.set_skill_level(level)
            self._on_changed()

    def _set_theme(self, theme: str) -> None:
        if self._loading:
            return
        self._controller.set_theme(theme)
        apply_theme(theme)

    def _current_provider(self) -> str:
        return self._providers[self._provider_row.get_selected()]

    def _sync_provider_rows(self, provider: str, model: str | None = None) -> None:
        self._models = provider_models(provider)
        if model and model not in self._models:
            self._models.insert(0, model)
        was_loading, self._loading = self._loading, True
        self._model_row.set_model(Gtk.StringList.new(self._models))
        if model in self._models:
            self._model_row.set_selected(self._models.index(model))
        self._loading = was_loading
        is_local = provider == "ollama"
        self._key_row.set_visible(not is_local)
        self._key_row.set_text("")
        has_key = bool(get_api_key(provider))
        if is_local:
            self._status_row.set_subtitle("Runs on this computer. Make sure Ollama is running.")
        else:
            self._status_row.set_subtitle("Key saved" if has_key else "No key yet — paste one above and press ✓")

    def _provider_changed(self) -> None:
        if self._loading:
            return
        provider = self._current_provider()
        self._sync_provider_rows(provider)
        if provider == "ollama" or get_api_key(provider):
            self._controller.set_provider(provider, model_name=self._models[0] if self._models else None)
            self._on_changed()

    def _model_changed(self) -> None:
        if self._loading:
            return
        idx = self._model_row.get_selected()
        if 0 <= idx < len(self._models):
            self._controller.set_provider(self._current_provider(), model_name=self._models[idx])
            self._on_changed()

    def _apply_key(self) -> None:
        provider = self._current_provider()
        key = self._key_row.get_text().strip()
        if not key:
            return
        self._status_row.set_subtitle("Checking…")

        def done(result: object) -> None:
            if isinstance(result, Exception):
                self._status_row.set_subtitle(str(result))
                return
            ok, message, models = result  # type: ignore[misc]
            if not ok:
                self._status_row.set_subtitle(message)
                return
            idx = self._model_row.get_selected()
            model = self._models[idx] if 0 <= idx < len(self._models) else None
            self._controller.set_provider(provider, model_name=model, api_key=key)
            self._sync_provider_rows(provider, model)
            self.add_toast(Adw.Toast(title=f"{provider_display_name(provider)} key saved"))
            self._on_changed()

        run_in_thread(lambda: self._controller.test_key(provider, key), done)


def apply_theme(theme: str) -> None:
    scheme = {
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get(theme, Adw.ColorScheme.DEFAULT)
    Adw.StyleManager.get_default().set_color_scheme(scheme)
