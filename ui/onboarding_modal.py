"""Onboarding and configuration modal for the Linux AI Assistant.

Provides:
  - System preview (distribution, package managers, RAM, CPU arch, desktop session).
  - Skill level selection (Beginner, Intermediate, Advanced) with descriptions.
  - Multi-provider API selection:
    * Groq (Fast / Free tier recommended: llama-3.3-70b-versatile, qwen-2.5-coder-32b)
    * Google Gemini (gemini-3.8-flash)
  - API key input with show/hide toggle, live test verification, and secure
    persistence in the Linux system keyring.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QCommandLinkButton,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from urllib.parse import urlparse

from config.keyring_manager import (
    check_ollama_status,
    fetch_available_models,
    get_api_key,
    save_api_key,
    test_api_key,
    test_groq_api_key,
    test_provider_api_key,
    validate_key_format,
)
from config.profile_manager import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GROQ_MODEL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_PROVIDER,
    DEFAULT_SKILL_LEVEL,
    PROVIDER_MODELS,
    VALID_SKILL_LEVELS,
    get_dynamic_session_info,
    get_or_create_profile,
    update_provider_settings,
    update_skill_level,
)
from config.provider_registry import PROVIDER_CONFIGS

SKILL_DESCRIPTIONS = {
    "Beginner": (
        "Plain English explanations. Raw commands hidden behind a toggle. "
        "Log pane collapsed by default. Safe and outcome-focused."
    ),
    "Intermediate": (
        "Educational explanations describing paths and flags. Read-only command "
        "previews before approval. Terminal log pane visible."
    ),
    "Advanced": (
        "Compact technical summary. Editable command input box to tweak before "
        "approving. Full terminal log pane visible."
    ),
}

FREE_KEY_PROVIDERS: dict[str, dict[str, str]] = {
    p: {
        "title": cfg.get("guide_title", ""),
        "url": cfg.get("portal_url", ""),
        "steps": "\n".join(
            step if step[:2].rstrip(".").isdigit() else f"{i+1}. {step}"
            for i, step in enumerate(cfg.get("guide_steps", []))
        ),
    }
    for p, cfg in PROVIDER_CONFIGS.items()
}


_active_workers: set[_KeyTestWorker] = set()


class _KeyTestWorker(QThread):
    """Background worker to test an API key and fetch available models."""

    result_ready = Signal(bool, str, list)

    def __init__(
        self,
        provider: str,
        key: str,
        model: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider
        self._key = key
        self._model = model

    def run(self) -> None:
        valid, message = test_provider_api_key(self._provider, self._key, model=self._model)

        models: list[str] = []
        if valid:
            try:
                models = fetch_available_models(self._provider, self._key)
            except Exception:
                models = []

        self.result_ready.emit(valid, message, models)


class OnboardingModal(QDialog):
    """First-run / Settings onboarding dialog.

    Parameters
    ----------
    parent:
        Optional parent widget.
    profile:
        Optional system profile dictionary. Loaded from disk if omitted.
    """

    configuration_saved = Signal(str, str, str, str)  # (skill_level, api_key, provider, model)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Linux AI Assistant – Setup & Configuration")
        self.setModal(True)
        self.resize(600, 740)
        self.setStyleSheet("QDialog { background-color: #111216; border: none; }")

        self.setStyleSheet("background-color: #080808;")

        self._profile = profile or get_or_create_profile()
        self._dynamic = get_dynamic_session_info()
        self._test_worker: Optional[_KeyTestWorker] = None

        self._build_ui()
        self._load_existing_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # ── Header ─────────────────────────────────────────────────────────
        title = QLabel("Welcome to Linux AI Assistant")
        title.setObjectName("sectionHeader")
        font = title.font()
        font.setPointSize(15)
        font.setBold(True)
        title.setFont(font)
        title.setStyleSheet("color: #ededed; font-size: 15px; font-weight: 600;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Configure your skill preferences and AI model provider. "
            "All mutating actions run with strict Human-in-the-Loop approval."
        )
        subtitle.setObjectName("mutedLabel")
        subtitle.setStyleSheet("color: #9e9e9e; font-size: 12px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # ── System Overview Card ───────────────────────────────────────────
        sys_group = QFrame(self)
        sys_group.setObjectName("modalCard")
        sys_layout = QVBoxLayout(sys_group)
        sys_layout.setContentsMargins(14, 14, 14, 14)
        sys_layout.setSpacing(8)

        sys_header = QLabel("Detected System Environment")
        sys_header.setObjectName("sectionHeader")
        sys_layout.addWidget(sys_header)

        os_pretty = self._profile.get("os_pretty_name", "Linux")
        primary_pm = self._profile.get("primary_package_manager") or "None detected"
        helpers = ", ".join(self._profile.get("secondary_helpers", [])) or "None"
        cpu_arch = self._profile.get("cpu_architecture", "unknown")
        ram_gb = self._profile.get("total_ram_gb", 0.0)
        desktop = self._dynamic.get("xdg_current_desktop", "unknown")
        session = self._dynamic.get("xdg_session_type", "unknown")
        disk_free = self._dynamic.get("home_disk_free_gb", 0.0)

        grid = QGridLayout()
        grid.setSpacing(8)

        def _make_badge(key: str, val: str) -> QLabel:
            badge = QLabel(
                f"<span style='color: #737373;'>{key}:</span> <b style='color: #ededed;'>{val}</b>",
                sys_group,
            )
            badge.setTextFormat(Qt.TextFormat.RichText)
            badge.setStyleSheet(
                "background-color: #161616; border: 1px solid #262626; "
                "border-radius: 6px; padding: 4px 8px; font-size: 11px;"
            )
            return badge

        pm_val = f"{primary_pm} ({helpers})" if helpers and helpers != "None" else primary_pm

        grid.addWidget(_make_badge("OS", f"{os_pretty} ({cpu_arch})"), 0, 0)
        grid.addWidget(_make_badge("Package Manager", pm_val), 0, 1)
        grid.addWidget(_make_badge("Desktop", f"{desktop} ({session})"), 1, 0)
        grid.addWidget(
            _make_badge("Hardware", f"{ram_gb:.1f} GB RAM | {disk_free:.1f} GB free"),
            1,
            1,
        )

        sys_layout.addLayout(grid)
        layout.addWidget(sys_group)

        # ── Skill Level Section ─────────────────────────────────────────────
        skill_group = QFrame(self)
        skill_group.setObjectName("modalCard")
        skill_layout = QVBoxLayout(skill_group)
        skill_layout.setContentsMargins(14, 14, 14, 14)
        skill_layout.setSpacing(8)

        skill_header = QLabel("Skill Level & Adaptation")
        skill_header.setObjectName("sectionHeader")
        skill_layout.addWidget(skill_header)

        self._skill_combo = QComboBox(self)
        for level in VALID_SKILL_LEVELS:
            self._skill_combo.addItem(level)
        self._skill_combo.currentTextChanged.connect(self._on_skill_changed)
        skill_layout.addWidget(self._skill_combo)

        self._skill_desc = QLabel()
        self._skill_desc.setObjectName("mutedLabel")
        self._skill_desc.setWordWrap(True)
        skill_layout.addWidget(self._skill_desc)

        layout.addWidget(skill_group)

        # ── Provider & API Key Section ─────────────────────────────────────
        key_group = QFrame(self)
        key_group.setObjectName("modalCard")
        key_layout = QVBoxLayout(key_group)
        key_layout.setContentsMargins(14, 14, 14, 14)
        key_layout.setSpacing(8)

        key_header = QLabel("LLM Provider & Model")
        key_header.setObjectName("sectionHeader")
        key_layout.addWidget(key_header)

        # Provider Selector row
        prov_row = QHBoxLayout()
        prov_row.setSpacing(10)
        prov_label = QLabel("Provider:")
        prov_label.setFixedWidth(70)
        prov_row.addWidget(prov_label)

        self._provider_combo = QComboBox(self)
        for prov_id, conf in PROVIDER_CONFIGS.items():
            self._provider_combo.addItem(conf["display_name"], prov_id)
        self._provider_combo.addItem("Ollama (Local / Offline)", "ollama")
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        prov_row.addWidget(self._provider_combo, 1)
        key_layout.addLayout(prov_row)

        # ── Free API Key Helper Card (below Provider selection dropdown) ──
        self._helper_card = QFrame(self)
        self._helper_card.setObjectName("freeKeyHelperCard")
        self._helper_card.setStyleSheet(
            "QFrame#freeKeyHelperCard {"
            "  background: #14161b;"
            "  border: 1px solid #232630;"
            "  border-radius: 8px;"
            "  padding: 12px;"
            "}"
        )
        self.helper_card = self._helper_card
        helper_layout = QVBoxLayout(self._helper_card)
        helper_layout.setContentsMargins(0, 0, 0, 0)
        helper_layout.setSpacing(6)

        helper_header = QHBoxLayout()
        helper_header.setSpacing(8)

        self._helper_title = QLabel("Free API Key Helper", self._helper_card)
        self._helper_title.setStyleSheet(
            "color: #ededed; font-size: 12px; font-weight: 600; background: transparent; border: none; padding: 0;"
        )
        self.helper_title = self._helper_title
        helper_header.addWidget(self._helper_title)
        helper_header.addStretch()

        self._helper_link_btn = QCommandLinkButton("Get Free Key ↗", self._helper_card)
        self._helper_link_btn.setObjectName("helperLinkButton")
        self._helper_link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._helper_link_btn.setStyleSheet(
            "QCommandLinkButton {"
            "  color: #818cf8;"
            "  font-size: 11.5px;"
            "  font-weight: 500;"
            "  text-decoration: underline;"
            "  border: none;"
            "  background: transparent;"
            "  padding: 0px;"
            "  margin: 0px;"
            "}"
            "QCommandLinkButton:hover {"
            "  color: #a5b4fc;"
            "}"
        )
        self._helper_link_btn.clicked.connect(self._open_provider_key_url)
        self.helper_link_btn = self._helper_link_btn
        helper_header.addWidget(self._helper_link_btn)

        helper_layout.addLayout(helper_header)

        self._helper_steps = QLabel(self._helper_card)
        self._helper_steps.setObjectName("helperStepsLabel")
        self._helper_steps.setWordWrap(True)
        self._helper_steps.setStyleSheet(
            "color: #9e9e9e; font-size: 11.5px; line-height: 1.4; background: transparent; border: none; padding: 0;"
        )
        self.helper_steps = self._helper_steps
        helper_layout.addWidget(self._helper_steps)

        self._helper_url_label = QLabel(self._helper_card)
        self._helper_url_label.setObjectName("helperUrlLabel")
        self._helper_url_label.setTextFormat(Qt.TextFormat.RichText)
        self._helper_url_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._helper_url_label.linkActivated.connect(lambda u: QDesktopServices.openUrl(QUrl(u)))
        self._helper_url_label.setStyleSheet(
            "color: #818cf8; font-size: 11px; text-decoration: underline; background: transparent; border: none; padding: 0;"
        )
        self.helper_url_label = self._helper_url_label
        helper_layout.addWidget(self._helper_url_label)

        key_layout.addWidget(self._helper_card)

        # Model Selector row
        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        model_label = QLabel("Model:")
        model_label.setFixedWidth(70)
        model_row.addWidget(model_label)

        self.model_dropdown = QComboBox(self)
        self.model_dropdown.setEditable(True)
        # Aliases for compatibility
        self.model_input = self.model_dropdown
        self.model_field = self.model_dropdown
        self._model_combo = self.model_dropdown
        model_row.addWidget(self.model_dropdown, 1)

        self._fetch_models_btn = QPushButton("Fetch Models", self)
        self._fetch_models_btn.setObjectName("headerGhostButton")
        self._fetch_models_btn.setToolTip("Fetch available models directly from provider API")
        self._fetch_models_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fetch_models_btn.clicked.connect(self._on_fetch_models)
        model_row.addWidget(self._fetch_models_btn)

        key_layout.addLayout(model_row)

        # Link Hint row
        self._key_hint = QLabel()
        self._key_hint.setObjectName("mutedLabel")
        self._key_hint.setWordWrap(True)
        self._key_hint.setTextFormat(Qt.TextFormat.RichText)
        self._key_hint.setOpenExternalLinks(True)
        key_layout.addWidget(self._key_hint)

        # Key Input row with show/hide toggle & format badge
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self._key_input = QLineEdit(self)
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.textChanged.connect(self._on_key_text_changed)
        input_row.addWidget(self._key_input, 1)

        self._key_badge = QLabel(self)
        self._key_badge.setObjectName("keyValidationBadge")
        self._key_badge.setVisible(False)
        self.key_badge = self._key_badge
        self._format_badge = self._key_badge
        self._validation_badge = self._key_badge
        input_row.addWidget(self._key_badge)

        self._toggle_btn = QToolButton(self)
        self._toggle_btn.setObjectName("headerGhostButton")
        self._toggle_btn.setText("👁 Show")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle_echo_mode)
        input_row.addWidget(self._toggle_btn)

        key_layout.addLayout(input_row)

        # Test button & verification status label
        test_row = QHBoxLayout()
        test_row.setSpacing(8)

        self._test_btn = QPushButton("Test Key", self)
        self._test_btn.setObjectName("headerGhostButton")
        self._test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_btn.clicked.connect(self._on_test_key)
        test_row.addWidget(self._test_btn)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        test_row.addWidget(self._status_label, 1)

        key_layout.addLayout(test_row)
        layout.addWidget(key_group)

        layout.addStretch()

        # ── Action Buttons ──────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.setObjectName("modalCancelButton")
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save && Continue", self)
        self._save_btn.setObjectName("modalSaveButton")
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._save_settings)
        btn_row.addWidget(self._save_btn)

        layout.addLayout(btn_row)

    def _current_provider(self) -> str:
        data = self._provider_combo.currentData()
        return str(data) if data else "groq"

    def _load_existing_values(self) -> None:
        """Load profile skill level, provider, and any pre-existing keyring key."""
        saved_skill = self._profile.get("skill_level", DEFAULT_SKILL_LEVEL)
        idx = self._skill_combo.findText(saved_skill)
        if idx >= 0:
            self._skill_combo.setCurrentIndex(idx)
        self._on_skill_changed(saved_skill)

        saved_provider = self._profile.get("active_provider", DEFAULT_PROVIDER)
        p_idx = self._provider_combo.findData(saved_provider)
        if p_idx < 0:
            p_idx = 0
        self._provider_combo.setCurrentIndex(p_idx)
        self._on_provider_changed(p_idx)

        # Restore saved model if present in combo
        saved_model = self._profile.get("model_name")
        if saved_model:
            m_idx = self.model_dropdown.findText(saved_model)
            if m_idx >= 0:
                self.model_dropdown.setCurrentIndex(m_idx)
            else:
                self.model_dropdown.setEditText(saved_model)

    def _on_models_fetched(self, models: list[str]) -> None:
        """Update model dropdown with fetched models, preferring 'llama-3.1-8b-instant' if present."""
        self.model_dropdown.clear()
        self.model_dropdown.addItems(models)

        if not models:
            return

        # Prefer 'llama-3.1-8b-instant' if present
        target_idx = self.model_dropdown.findText("llama-3.1-8b-instant")
        if target_idx >= 0:
            self.model_dropdown.setCurrentIndex(target_idx)
        else:
            # Fall back to first chat-capable model
            preferred_order = [
                "llama-3.3-70b-versatile",
                "gemini-2.5-flash",
                "gemini-2.0-flash",
                "gemini-3.8-flash",
                "qwen-2.5-coder-32b",
                "qwen2.5-coder:7b",
                "llama3.2:3b",
                "mistral:7b",
            ]
            chosen = -1
            for pref in preferred_order:
                idx = self.model_dropdown.findText(pref)
                if idx >= 0:
                    chosen = idx
                    break
            if chosen >= 0:
                self.model_dropdown.setCurrentIndex(chosen)
            else:
                self.model_dropdown.setCurrentIndex(0)

    def _populate_models(self, models: list[str]) -> None:
        """Alias for _on_models_fetched."""
        self._on_models_fetched(models)

    @Slot()
    def _open_provider_key_url(self, url: str = "") -> None:
        target_url = url or getattr(self, "_current_helper_url", "")
        if not target_url:
            info = FREE_KEY_PROVIDERS.get(self._current_provider())
            if info:
                target_url = info.get("url", "")
        if target_url:
            QDesktopServices.openUrl(QUrl(target_url))

    def _update_helper_box(self, provider: str) -> None:
        cfg = PROVIDER_CONFIGS.get(provider)
        if cfg:
            self._current_helper_url = cfg.get("portal_url", "")
            self._helper_title.setText(cfg.get("guide_title", f"{cfg['display_name']} Key"))
            netloc = urlparse(cfg["portal_url"]).netloc or cfg["portal_url"]
            self._helper_link_btn.setText(f"↗ Get Free Key at {netloc}")
            self._helper_link_btn.setToolTip(cfg["portal_url"])

            steps = cfg.get("guide_steps", [])
            steps_text = "\n".join(
                step if step[:2].rstrip(".").isdigit() else f"{i+1}. {step}"
                for i, step in enumerate(steps)
            )
            self._helper_steps.setText(steps_text)
            if hasattr(self, "_helper_url_label"):
                self._helper_url_label.setText(
                    f"<a href='{cfg['portal_url']}' style='color: #818cf8; text-decoration: underline;'>{cfg['portal_url']}</a>"
                )
            self._helper_card.setVisible(True)
        else:
            self._current_helper_url = ""
            self._helper_card.setVisible(False)

    @Slot(str)
    def _on_key_text_changed(self, text: str) -> None:
        self._validate_key_format()

    def _validate_key_format(self) -> None:
        provider = self._current_provider()
        key = self._key_input.text().strip()

        if not key or provider == "ollama":
            self._key_badge.setVisible(False)
            self._key_badge.setText("")
            return

        # Autofill / auto-detect provider if key prefix uniquely identifies provider
        for p_name, conf in PROVIDER_CONFIGS.items():
            prefix = conf.get("key_prefix")
            if prefix and key.startswith(prefix) and provider != p_name:
                idx = self._provider_combo.findData(p_name)
                if idx >= 0:
                    self._auto_switching_provider = True
                    try:
                        self._provider_combo.setCurrentIndex(idx)
                        self._key_input.setText(key)
                    finally:
                        self._auto_switching_provider = False
                    self._validate_key_format()
                    return

        # Check format badge display for active provider
        if validate_key_format(provider, key):
            self._key_badge.setText("✓ Valid Key Format")
            self._key_badge.setStyleSheet(
                "color: #10b981; background-color: rgba(16, 185, 129, 0.15); "
                "border: 1px solid rgba(16, 185, 129, 0.35); border-radius: 4px; "
                "padding: 2px 8px; font-size: 11px; font-weight: 600;"
            )
            self._key_badge.setVisible(True)
        else:
            self._key_badge.setVisible(False)
            self._key_badge.setText("")

    @Slot(int)
    def _on_provider_changed(self, index: int) -> None:
        provider = self._current_provider()
        cfg = PROVIDER_CONFIGS.get(provider)
        if cfg:
            models = list(cfg.get("fallback_models", []))
            self._on_models_fetched(models)
            default_m = cfg.get("default_model")
            if default_m:
                m_idx = self.model_dropdown.findText(default_m)
                if m_idx >= 0:
                    self.model_dropdown.setCurrentIndex(m_idx)
                else:
                    self.model_dropdown.setEditText(default_m)
        else:
            models = PROVIDER_MODELS.get(provider, [])
            self._on_models_fetched(models)

        self._update_helper_box(provider)

        if provider == "ollama":
            self._key_hint.setText("Running locally on http://127.0.0.1:11434")
            self._key_input.setVisible(False)
            self._key_input.setEnabled(False)
            self._toggle_btn.setVisible(False)
            self._test_btn.setText("Test Connection")
            self._status_label.setText("")
            self._validate_key_format()
            # Automatically populate model dropdown with locally pulled models
            self._on_fetch_models()
        elif cfg:
            self._key_input.setVisible(True)
            self._key_input.setEnabled(True)
            self._toggle_btn.setVisible(True)
            self._test_btn.setText("Test Key")
            portal_url = cfg.get("portal_url", "")
            self._key_hint.setText(
                f"Stored securely in your system keyring. Get a free API key at "
                f"<a href='{portal_url}' style='color: #818cf8; text-decoration: none;'>{portal_url}</a>."
            )
            prefix = cfg.get("key_prefix")
            prefix_hint = f" ({prefix}...)" if prefix else ""
            self._key_input.setPlaceholderText(f"Paste your {cfg['display_name']} key{prefix_hint}")
            if not getattr(self, "_auto_switching_provider", False):
                existing_key = get_api_key(provider)
                if existing_key:
                    self._key_input.setText(existing_key)
                    self._status_label.setText(f"Key loaded from keyring for {provider.capitalize()}.")
                    self._status_label.setStyleSheet("color: #8b8d98; font-weight: 500;")
                else:
                    self._key_input.clear()
                    self._status_label.setText("")
            self._validate_key_format()

    @Slot(str)
    def _on_skill_changed(self, skill: str) -> None:
        desc = SKILL_DESCRIPTIONS.get(skill, "")
        self._skill_desc.setText(desc)

    @Slot()
    def _toggle_echo_mode(self) -> None:
        if self._key_input.echoMode() == QLineEdit.EchoMode.Password:
            self._key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self._toggle_btn.setText("Hide")
        else:
            self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self._toggle_btn.setText("👁 Show")

    @Slot()
    def _on_fetch_models(self) -> None:
        key = self._key_input.text().strip()
        provider = self._current_provider()

        if provider != "ollama" and not key:
            self._status_label.setText("⚠ Please enter an API key to fetch models.")
            self._status_label.setStyleSheet("color: #f59e0b; font-weight: 500;")
            return

        self._fetch_models_btn.setEnabled(False)
        self._test_btn.setEnabled(False)
        if provider == "ollama":
            self._status_label.setText("Fetching local models from Ollama...")
        else:
            self._status_label.setText(f"Fetching models from {provider.capitalize()}...")
        self._status_label.setStyleSheet("color: #818cf8; font-weight: 500;")

        self._test_worker = _KeyTestWorker(
            provider=provider, key=key or "ollama", model="", parent=None
        )
        _active_workers.add(self._test_worker)
        self._test_worker.result_ready.connect(self._on_test_result)
        self._test_worker.finished.connect(
            lambda w=self._test_worker: (
                _active_workers.discard(w),
                self._fetch_models_btn.setEnabled(True),
                self._test_btn.setEnabled(True),
            )
        )
        self._test_worker.start()

    @Slot()
    def _on_test_key(self) -> None:
        key = self._key_input.text().strip()
        provider = self._current_provider()
        model = self.model_dropdown.currentText().strip()

        if provider != "ollama" and not key:
            self._status_label.setText("⚠ Please enter an API key to test.")
            self._status_label.setStyleSheet("color: #f59e0b; font-weight: 500;")
            return

        self._test_btn.setEnabled(False)
        self._fetch_models_btn.setEnabled(False)
        if provider == "ollama":
            self._status_label.setText("Testing connection to Ollama daemon...")
        else:
            self._status_label.setText(f"Testing key & fetching models for {provider.capitalize()}...")
        self._status_label.setStyleSheet("color: #818cf8; font-weight: 500;")

        self._test_worker = _KeyTestWorker(
            provider=provider, key=key or "ollama", model=model, parent=None
        )
        _active_workers.add(self._test_worker)
        self._test_worker.result_ready.connect(self._on_test_result)
        self._test_worker.finished.connect(
            lambda w=self._test_worker: (
                _active_workers.discard(w),
                self._test_btn.setEnabled(True),
                self._fetch_models_btn.setEnabled(True),
            )
        )
        self._test_worker.start()

    @Slot(bool, str, list)
    def _on_test_result(self, valid: bool, message: str, models: list[str]) -> None:
        provider = self._current_provider()
        if valid:
            count_str = f" Retrieved {len(models)} models." if models else ""
            if provider == "ollama":
                self._status_label.setText(f"✓ {message}.{count_str}")
            else:
                self._status_label.setText(f"✓ Key verified successfully.{count_str}")
            self._status_label.setStyleSheet("color: #10b981; font-weight: 600;")
            if models:
                self._on_models_fetched(models)
        else:
            if provider == "ollama":
                self._status_label.setText(
                    f"⚠ {message}. Run: systemctl start ollama or ollama serve"
                )
                self._status_label.setStyleSheet("color: #f59e0b; font-weight: 500;")
            else:
                clean_msg = message.split("\n")[0]
                if len(clean_msg) > 90:
                    clean_msg = clean_msg[:87] + "..."
                self._status_label.setText(f"✗ Failed: {clean_msg}")
                self._status_label.setStyleSheet("color: #ef4444; font-weight: 500;")

    def _save_settings(self) -> None:
        """Save settings using the model dropdown text."""
        key = self._key_input.text().strip()
        skill = self._skill_combo.currentText()
        provider = self._current_provider()
        model = self.model_dropdown.currentText().strip()

        if key and provider != "ollama":
            save_api_key(key, provider=provider)

        try:
            update_provider_settings(provider, model_name=model)
            update_skill_level(skill)
        except OSError:
            pass
        effective_key = key or ("ollama" if provider == "ollama" else "")
        self.configuration_saved.emit(skill, effective_key, provider, model)
        self.accept()

    @Slot()
    def _on_save(self) -> None:
        self._save_settings()
