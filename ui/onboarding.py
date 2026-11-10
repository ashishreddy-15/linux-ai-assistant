"""First-run setup in two steps: experience level, then connect an AI."""

from __future__ import annotations

from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from config.provider_registry import PROVIDER_CONFIGS  # noqa: E402
from ui.controller import SKILL_LABELS, AppController  # noqa: E402
from ui.local_setup import LocalSetupView  # noqa: E402
from ui.widgets import clamp, glow_line, label, mono, open_uri, run_in_thread  # noqa: E402


class Onboarding(Gtk.Box):
    def __init__(self, controller: AppController, on_finished: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self._controller = controller
        self._on_finished = on_finished
        self._skill = controller.skill_level()
        self._provider = "ollama"
        self._key_ok = False
        self._local_model: Optional[str] = None

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self._stack.add_named(self._skill_page(), "skill")
        self._stack.add_named(self._connect_page(), "connect")
        self._local_page: Optional[Gtk.Widget] = None
        self.append(self._stack)

    # ------------------------------------------------------------------

    def _page(self, step: int, title: str, subtitle: str) -> tuple[Gtk.Widget, Gtk.Box, Gtk.Box]:
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        col.set_margin_top(56)
        col.set_margin_start(24)
        col.set_margin_end(24)
        col.set_margin_bottom(24)

        heads = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        heads.append(mono(f"setup · {step} of 2", ("la-muted", "la-small")))
        heads.append(label(title, ("la-heading",)))
        heads.append(label(subtitle, ("la-muted", "la-prose")))
        col.append(heads)

        options = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.append(options)

        footer = Gtk.Box(spacing=8)
        footer.add_css_class("la-footer")
        footer.set_margin_top(4)
        col.append(footer)
        scroller.set_child(clamp(col, 520))
        return scroller, options, footer

    def _option(self, title: str, subtitle: str, group: Optional[Gtk.CheckButton], tag: str = "") -> Gtk.CheckButton:
        btn = Gtk.CheckButton()
        btn.add_css_class("la-option")
        if group is not None:
            btn.set_group(group)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin_start=6)
        head = Gtk.Box(spacing=8)
        t = label(title, ("la-option-title",), wrap=False)
        t.set_hexpand(True)
        head.append(t)
        if tag:
            head.append(mono(tag, ("la-muted", "la-small"), wrap=False))
        texts.append(head)
        texts.append(label(subtitle, ("la-muted", "la-small", "la-prose")))
        btn.set_child(texts)
        btn.update_property([Gtk.AccessibleProperty.LABEL], [f"{title}. {subtitle}"])
        return btn

    @staticmethod
    def _button(text: str, css: str) -> Gtk.Button:
        btn = Gtk.Button(label=text)
        btn.add_css_class("la-button")
        btn.add_css_class(css)
        return btn

    def _skill_page(self) -> Gtk.Widget:
        page, options, footer = self._page(
            1, "How comfortable are you with Linux?",
            "This only changes how much detail you see. Every change still needs your OK.",
        )
        first: Optional[Gtk.CheckButton] = None
        for level, (title, subtitle) in SKILL_LABELS.items():
            btn = self._option(title, subtitle, first)
            first = first or btn
            btn.set_active(level == self._skill)
            btn.connect("toggled", lambda b, lv=level: b.get_active() and self._set_skill(lv))
            options.append(btn)

        footer.append(Gtk.Box(hexpand=True))
        cont = self._button("Continue", "suggested-action")
        cont.connect("clicked", lambda *_: self._stack.set_visible_child_name("connect"))
        footer.append(cont)
        return page

    def _connect_page(self) -> Gtk.Widget:
        page, options, footer = self._page(
            2, "Choose the AI behind the assistant",
            "Both are free. The local AI keeps everything on this computer.",
        )
        local = self._option(
            "Private, on this computer",
            "Works offline and nothing leaves your computer. The app sets it up for you: "
            "a one-time download of about 5 GB.",
            None, tag="ollama · recommended",
        )
        local.set_active(True)
        options.append(local)
        self._local_status = mono("", ("la-key-status", "la-small"))
        self._local_status.set_visible(False)
        self._local_status.set_margin_start(36)
        options.append(self._local_status)
        self._setup_btn = self._button("Set up the local AI", "la-outline")
        self._setup_btn.set_halign(Gtk.Align.START)
        self._setup_btn.set_margin_start(36)
        self._setup_btn.set_visible(False)
        self._setup_btn.connect("clicked", lambda *_: self._open_local_setup())
        options.append(self._setup_btn)

        cloud = self._option(
            "Free cloud AI", "Fast on any computer. Needs a free account and an internet connection.",
            local, tag="groq",
        )
        options.append(cloud)

        self._key_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._key_box.set_margin_start(36)
        self._key_box.set_margin_end(12)
        self._key_box.set_margin_bottom(10)
        get_key = Gtk.LinkButton(
            uri=str(PROVIDER_CONFIGS["groq"].get("portal_url", "https://console.groq.com/keys")),
            label="Get a free key ↗", halign=Gtk.Align.START,
        )
        get_key.connect("activate-link", lambda b: (open_uri(b, b.get_uri()), True)[1])
        self._key_box.append(get_key)

        field = Gtk.Box(spacing=10)
        field.add_css_class("la-input")
        field.add_css_class("compact")
        self._key_entry = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True, placeholder_text="paste your key (gsk_…)")
        self._key_entry.add_css_class("monospace")
        self._key_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Groq API key"])
        self._key_entry.connect("changed", lambda *_: self._reset_key_status())
        self._key_entry.connect("activate", lambda *_: self._check())
        field.append(self._key_entry)
        self._check_btn = self._button("Check", "flat")
        self._check_btn.connect("clicked", lambda *_: self._check())
        field.append(self._check_btn)
        self._key_box.append(field)
        glow = glow_line()
        self._key_glow = glow
        self._key_box.append(glow)
        self._status = mono("", ("la-key-status", "la-small"))
        self._status.set_visible(False)
        self._key_box.append(self._status)
        options.append(self._key_box)
        self._key_box.set_visible(False)

        cloud.connect("toggled", lambda b: b.get_active() and self._set_provider("groq"))
        local.connect("toggled", lambda b: b.get_active() and self._set_provider("ollama"))

        later = self._button("Set up later", "flat")
        later.connect("clicked", lambda *_: self._finish(save_provider=False))
        footer.append(later)
        footer.append(Gtk.Box(hexpand=True))
        back = self._button("Back", "la-outline")
        back.connect("clicked", lambda *_: self._stack.set_visible_child_name("skill"))
        footer.append(back)
        self._done_btn = self._button("Start", "suggested-action")
        self._done_btn.set_sensitive(False)
        self._done_btn.connect("clicked", lambda *_: self._finish(save_provider=True))
        footer.append(self._done_btn)
        GLib.idle_add(lambda: (self._check(), GLib.SOURCE_REMOVE)[1])
        return page

    def _open_local_setup(self) -> None:
        if self._local_page is not None:
            self._stack.remove(self._local_page)
        self._local_page = LocalSetupView(
            self._controller,
            on_ready=self._local_ready,
            on_cancel=lambda: self._stack.set_visible_child_name("connect"),
            cancel_label="Back",
        )
        self._stack.add_named(self._local_page, "local")
        self._stack.set_visible_child_name("local")

    def _local_ready(self, model: str) -> None:
        self._controller.set_provider("ollama", model_name=model)
        self._controller.complete_onboarding()
        self._on_finished()

    # ------------------------------------------------------------------

    def _set_skill(self, level: str) -> None:
        self._skill = level
        self._controller.set_skill_level(level)

    def _set_provider(self, provider: str) -> None:
        self._provider = provider
        self._setup_btn.set_visible(False)
        self._key_box.set_visible(provider == "groq")
        self._local_status.set_visible(provider == "ollama")
        self._reset_key_status()
        if provider == "ollama":
            self._check()

    def _reset_key_status(self) -> None:
        self._key_ok = False
        self._local_model: Optional[str] = None
        self._done_btn.set_sensitive(False)
        self._key_glow.remove_css_class("on")
        for lbl in (self._status, self._local_status):
            lbl.set_text("")
            lbl.set_visible(False)
            lbl.remove_css_class("ok")
            lbl.remove_css_class("error")
            lbl.remove_css_class("la-muted")

    def _check(self) -> None:
        provider = self._provider
        key = self._key_entry.get_text().strip()
        if provider == "groq" and not key:
            return
        target = self._status if provider == "groq" else self._local_status
        self._check_btn.set_sensitive(False)
        target.set_text("checking…")
        target.set_visible(True)

        def done(result: object) -> None:
            self._check_btn.set_sensitive(True)
            if provider != self._provider:
                return
            if isinstance(result, Exception):
                ok, message = False, str(result)
            else:
                ok, message, models = result  # type: ignore[misc]
                if provider == "ollama" and ok and models:
                    self._local_model = models[0]
            if provider == "ollama":
                self._setup_btn.set_visible(not ok)
                if not ok:
                    message = "Not set up yet. The app can do it for you."
            self._key_ok = ok
            target.set_text(("✓ " if ok else "") + message.lower() if ok else message)
            neutral = provider == "ollama" and not ok  # "not set up yet" isn't an error
            target.add_css_class("ok" if ok else ("la-muted" if neutral else "error"))
            if ok and provider == "groq":
                self._key_glow.add_css_class("on")
            self._done_btn.set_sensitive(ok)

        run_in_thread(lambda: self._controller.test_key(provider, key), done)

    def _finish(self, save_provider: bool) -> None:
        if save_provider and self._key_ok:
            if self._provider == "ollama":
                self._controller.set_provider("ollama", model_name=self._local_model)
            else:
                self._controller.set_provider(self._provider, api_key=self._key_entry.get_text().strip())
        self._controller.complete_onboarding()
        self._on_finished()
