"""Models window: download, choose, run and stop local AI models.

One instance lives for the whole session (the main window reuses it), so a
download keeps going, and keeps showing progress, when the window is closed
and opened again. All network calls run on worker threads.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from agent import model_manager as mm  # noqa: E402
from agent.local_llm import server_version  # noqa: E402
from ui.controller import AppController  # noqa: E402
from ui.widgets import run_in_thread  # noqa: E402

POLL_SECONDS = 3


def _on_main(fn: Callable[..., None]) -> Callable[..., None]:
    return lambda *a: GLib.idle_add(lambda: (fn(*a), GLib.SOURCE_REMOVE)[1])


class ModelRow(Adw.ActionRow):
    """One model: its description on the left, the current action on the right."""

    def __init__(self, name: str, title: str, note: str, size_gb: float = 0.0) -> None:
        super().__init__(title=title, use_markup=False)  # model/registry text is never markup
        self.name = name
        self._note = note
        self._size_gb = size_gb
        self.set_subtitle(self._plain_subtitle())

        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER, visible=False)
        self.progress.set_size_request(120, -1)
        self.status = Gtk.Label(visible=False)
        self.status.add_css_class("success")
        self.status.add_css_class("caption")
        self.download_btn = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
        self.cancel_btn = Gtk.Button(label="Cancel", valign=Gtk.Align.CENTER, visible=False)
        self.use_btn = Gtk.Button(label="Use", valign=Gtk.Align.CENTER, visible=False)
        self.run_btn = Gtk.Button(label="Run", valign=Gtk.Align.CENTER, visible=False)
        self.remove_btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                     visible=False, tooltip_text="Remove from this computer")
        self.remove_btn.add_css_class("flat")
        self.download_btn.add_css_class("suggested-action")
        self.cancel_btn.add_css_class("flat")
        for w in (self.progress, self.status, self.download_btn, self.cancel_btn, self.use_btn, self.run_btn, self.remove_btn):
            box.append(w)
        self.add_suffix(box)

    def _plain_subtitle(self, extra: str = "") -> str:
        parts = []
        if self._size_gb:
            parts.append(f"{self._size_gb:.1f} GB")
        if self._note:
            parts.append(self._note)
        if extra:
            parts.append(extra)
        return " · ".join(parts)

    def show_state(self, *, installed: bool, downloading: bool, running: bool, active: bool,
                   busy: bool, server_up: bool, size_bytes: int = 0) -> None:
        self.progress.set_visible(downloading)
        self.cancel_btn.set_visible(downloading)
        self.download_btn.set_visible(not installed and not downloading)
        self.download_btn.set_sensitive(server_up)
        self.use_btn.set_visible(installed)
        self.use_btn.set_label("In use" if active else "Use")
        self.use_btn.set_sensitive(not active)
        for cls, on in (("suggested-action", not active), ):
            (self.use_btn.add_css_class if on else self.use_btn.remove_css_class)(cls)
        self.run_btn.set_visible(installed)
        self.run_btn.set_label("Working…" if busy else ("Stop" if running else "Run"))
        self.run_btn.set_sensitive(server_up and not busy)
        self.remove_btn.set_visible(installed and not downloading)
        self.remove_btn.set_sensitive(not busy)
        self.status.set_visible(running)
        self.status.set_text("● running")
        if installed and size_bytes and not self._size_gb:
            self.set_subtitle(f"{mm.format_bytes(size_bytes)} · installed on this computer")

    def show_progress(self, p: mm.PullProgress) -> None:
        frac = p.fraction
        if frac is None:
            self.progress.pulse()
            self.set_subtitle(self._plain_subtitle(p.status or "starting…"))
        else:
            self.progress.set_fraction(min(frac, 1.0))
            self.set_subtitle(f"{mm.format_bytes(p.completed)} of {mm.format_bytes(p.total)} · {frac * 100:.0f}%")

    def reset_subtitle(self) -> None:
        self.set_subtitle(self._plain_subtitle())


class ModelsDialog(Adw.Dialog):
    def __init__(self, controller: AppController, on_changed: Callable[[], None],
                 on_setup: Callable[[], None]) -> None:
        super().__init__(title="Local models", content_width=600, content_height=680)
        self._controller = controller
        self._on_changed = on_changed
        self._on_setup = on_setup
        self._rows: Dict[str, ModelRow] = {}
        self._extra_group_rows: List[ModelRow] = []
        self._installed: Dict[str, int] = {}
        self._running: List[str] = []
        self._server_up = True
        self._busy: set = set()
        self._downloads: Dict[str, mm.ModelPuller] = {}
        self._last_paint: Dict[str, float] = {}
        self._polling = False
        self._poll_source = 0

        self._toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self._banner = Adw.Banner(title="The local AI isn't running", button_label="Set up…", revealed=False)
        self._banner.connect("button-clicked", lambda *_: self._open_setup())
        view.add_top_bar(self._banner)

        page = Adw.PreferencesPage()
        self._group = Adw.PreferencesGroup(
            title="Models",
            description="Models run on this computer. Bigger ones are smarter but slower and need more memory. "
                        "“Run” loads a model ahead of time so the first answer is quick; “Stop” frees its memory.",
        )
        for m in mm.CATALOG:
            note = m.note + ("" if m.tested else " Not tested with this assistant yet.")
            self._add_row(ModelRow(m.name, m.label, note, m.size_gb))
        page.add(self._group)
        self._other = Adw.PreferencesGroup(title="Also on this computer")
        self._other.set_visible(False)
        page.add(self._other)
        view.set_content(page)
        self._toasts.set_child(view)
        self.set_child(self._toasts)
        self.connect("closed", lambda *_: self._stop_polling())

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def show_for(self, parent: Gtk.Widget) -> None:
        self._start_polling()
        self.present(parent)

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def _add_row(self, row: ModelRow, group: Optional[Adw.PreferencesGroup] = None) -> None:
        (group or self._group).add(row)
        self._rows[row.name] = row
        row.download_btn.connect("clicked", lambda *_: self._download(row.name))
        row.cancel_btn.connect("clicked", lambda *_: self._cancel(row.name))
        row.use_btn.connect("clicked", lambda *_: self._use(row.name))
        row.run_btn.connect("clicked", lambda *_: self._toggle_run(row.name))
        row.remove_btn.connect("clicked", lambda *_: self._confirm_remove(row.name))

    def _installed_name(self, name: str) -> Optional[str]:
        return next((n for n in self._installed if mm.same_model(n, name)), None)

    def _active_model(self) -> str:
        if self._controller.active_provider() != "ollama":
            return ""
        return str(self._controller.profile().get("model_name") or "")

    def _paint(self) -> None:
        # Models that are installed but not in the catalog.
        for name in self._installed:
            if not any(mm.same_model(name, k) for k in self._rows):
                row = ModelRow(name, name, "")
                self._add_row(row, self._other)
                self._other.set_visible(True)
        active = self._active_model()
        catalog = {m.name for m in mm.CATALOG}
        for name, row in self._rows.items():
            have = self._installed_name(name)
            if name not in catalog:  # an extra model that has since been removed
                row.set_visible(have is not None)
            running = any(mm.same_model(r, name) for r in self._running)
            row.show_state(
                installed=have is not None and name not in self._downloads,
                downloading=name in self._downloads,
                running=running,
                active=bool(active) and mm.same_model(active, name),
                busy=name in self._busy,
                server_up=self._server_up,
                size_bytes=self._installed.get(have or "", 0),
            )
        self._banner.set_revealed(not self._server_up)

    # ------------------------------------------------------------------
    # Polling the server
    # ------------------------------------------------------------------

    def _start_polling(self) -> None:
        if self._poll_source:
            return
        self._poll()
        self._poll_source = GLib.timeout_add_seconds(POLL_SECONDS, self._poll)

    def _stop_polling(self) -> None:
        if self._poll_source:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0

    def _poll(self) -> bool:
        if self._polling:
            return GLib.SOURCE_CONTINUE
        self._polling = True

        def work() -> object:
            if server_version() is None:
                return None
            return mm.installed_sizes(), mm.running_models()

        def done(result: object) -> None:
            self._polling = False
            if result is None or isinstance(result, Exception):
                self._server_up = False
            else:
                self._server_up = True
                self._installed, self._running = result  # type: ignore[misc]
            self._paint()

        run_in_thread(work, done)
        return GLib.SOURCE_CONTINUE

    def _refresh_now(self) -> None:
        self._polling = False
        self._poll()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _toast(self, text: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=text))

    def _open_setup(self) -> None:
        self.close()
        self._on_setup()

    def _download(self, name: str) -> None:
        if name in self._downloads:
            return
        puller = mm.ModelPuller(name)
        self._downloads[name] = puller
        row = self._rows[name]
        self._paint()
        row.show_progress(mm.PullProgress("starting…"))
        row.cancel_btn.grab_focus()

        def progress(p: mm.PullProgress) -> None:
            now = time.monotonic()
            if p.status != "success" and now - self._last_paint.get(name, 0) < 0.25:
                return  # keep the UI thread calm: ~4 updates a second
            self._last_paint[name] = now
            GLib.idle_add(lambda: (row.show_progress(p), GLib.SOURCE_REMOVE)[1])

        def work() -> None:
            puller.run(progress)

        def done(result: object) -> None:
            self._downloads.pop(name, None)
            row.reset_subtitle()
            if isinstance(result, mm.PullCancelled):
                self._toast("Download stopped. Press Download to continue where it left off.")
            elif isinstance(result, Exception):
                self._toast(f"Download failed: {result}")
            else:
                self._toast(f"{name} is ready")
            self._paint()
            (row.run_btn if self._installed_name(name) else row.download_btn).grab_focus()
            self._refresh_now()

        run_in_thread(work, done)

    def _cancel(self, name: str) -> None:
        puller = self._downloads.get(name)
        if puller is not None:
            puller.cancel()

    def _use(self, name: str) -> None:
        installed = self._installed_name(name) or name
        self._controller.set_provider("ollama", model_name=installed)
        self._on_changed()
        self._toast(f"The assistant now uses {installed}")
        self._paint()

    def _toggle_run(self, name: str) -> None:
        installed = self._installed_name(name) or name
        running = any(mm.same_model(r, name) for r in self._running)
        self._busy.add(name)
        self._paint()

        def done(result: object) -> None:
            self._busy.discard(name)
            if isinstance(result, Exception):
                self._toast(str(result))
            else:
                self._toast(f"{installed} stopped" if running else f"{installed} is running")
                # Update at once; the next poll confirms it.
                self._running = ([r for r in self._running if not mm.same_model(r, name)] if running
                                 else self._running + [installed])
            self._paint()
            self._rows[name].run_btn.grab_focus()  # the button was disabled meanwhile, which drops focus
            self._refresh_now()

        run_in_thread(lambda: mm.unload_model(installed) if running else mm.load_model(installed), done)

    def _confirm_remove(self, name: str) -> None:
        installed = self._installed_name(name) or name
        active = bool(self._active_model()) and mm.same_model(self._active_model(), name)
        body = (f"This frees about {mm.format_bytes(self._installed.get(installed, 0))} on your disk. "
                "You can download it again later.")
        if active:
            body += " The assistant is using this model right now, so pick another one afterwards."
        dialog = Adw.AlertDialog(heading=f"Remove {installed}?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def chosen(_d: Adw.AlertDialog, response: str) -> None:
            if response == "remove":
                self._remove(name, installed)

        dialog.connect("response", chosen)
        dialog.present(self)

    def _remove(self, name: str, installed: str) -> None:
        self._busy.add(name)
        self._paint()

        def done(result: object) -> None:
            self._busy.discard(name)
            self._toast(str(result) if isinstance(result, Exception) else f"{installed} removed")
            self._refresh_now()

        run_in_thread(lambda: mm.delete_model(installed), done)
