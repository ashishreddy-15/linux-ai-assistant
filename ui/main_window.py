"""Main application window for the Linux AI Assistant.

Implements a split-pane layout:
  - Left pane: Interactive chat stream, dynamic response updates, embedded
    HITL ApprovalCard, and prompt input box.
  - Right pane: Real-time terminal log viewer streaming ANSI-stripped output
    from background PTY execution.
  - Header: Live skill switcher, status badge, log visibility toggle, and
    settings modal access.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

UI_DIR = Path(__file__).resolve().parent
TERMINAL_ICON_PATH = str(UI_DIR / "terminal_icon.svg")
SETTINGS_ICON_PATH = str(UI_DIR / "settings_icon.svg")

from agent.orchestrator import AgentOrchestrator, OrchestratorState
from agent.prompts import build_system_prompt
from config.keyring_manager import get_api_key
from config.profile_manager import (
    DEFAULT_SKILL_LEVEL,
    VALID_SKILL_LEVELS,
    get_dynamic_session_info,
    get_or_create_profile,
    update_skill_level,
)
from ui.approval_card import ApprovalCard
from ui.onboarding_modal import OnboardingModal

# Regex pattern for stripping terminal ANSI escape codes
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove terminal ANSI escape codes from streamed text."""
    return ANSI_ESCAPE_RE.sub("", text)


class ChatBubble(QFrame):
    """Visual bubble container for a single chat turn (user or assistant)."""

    def __init__(
        self,
        role: str,
        text: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._role = role

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Header with role identifier
        self._header = QLabel("You" if role == "user" else "Linux Assistant", self)
        self._header.setObjectName("mutedLabel")
        font = self._header.font()
        font.setBold(True)
        font.setPointSize(11)
        self._header.setFont(font)
        layout.addWidget(self._header)

        # Content text
        self._content = QLabel(text, self)
        self._content.setWordWrap(True)
        self._content.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._content.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._content)

        # Visual styling
        if role == "user":
            self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)
            layout.setContentsMargins(14, 10, 14, 10)
            self._header.setStyleSheet("color: #888888; font-size: 11px; font-weight: 600;")
            self._content.setStyleSheet("color: #ededed; font-size: 13.5px;")
            self.setStyleSheet(
                "ChatBubble {"
                "  background-color: #161616;"
                "  border: 1px solid #262626;"
                "  border-radius: 12px;"
                "}"
            )
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            layout.setContentsMargins(0, 6, 0, 8)
            self._header.setStyleSheet("color: #888888; font-size: 11px; font-weight: 600;")
            self._content.setStyleSheet(
                "color: #ededed; font-size: 13.5px; background: transparent;"
            )
            self.setStyleSheet(
                "ChatBubble {"
                "  background-color: transparent;"
                "  border: none;"
                "}"
            )

    def sizeHint(self) -> QSize:
        if self._role == "user":
            text = self._content.text()
            if not text:
                return super().sizeHint()
            fm = self._content.fontMetrics()
            lines = text.split("\n")
            longest_line = max((fm.horizontalAdvance(line) for line in lines), default=0)
            header_w = self._header.fontMetrics().horizontalAdvance(self._header.text())
            content_w = max(longest_line, header_w)

            parent = self.parentWidget()
            parent_w = parent.width() if parent and parent.width() > 0 else 780
            max_bubble_width = max(560, int(parent_w * 0.75))
            bubble_w = min(max_bubble_width, content_w + 28)

            bounding = fm.boundingRect(
                QRect(0, 0, bubble_w - 28, 10000),
                Qt.TextFlag.TextWordWrap,
                text,
            )
            bubble_h = bounding.height() + self._header.sizeHint().height() + 4 + 20
            return QSize(bubble_w, max(bubble_h, 38))
        return super().sizeHint()

    def set_text(self, text: str) -> None:
        """Update bubble content in place (for streaming tokens)."""
        self._content.setText(text)
        self.updateGeometry()

    def append_text(self, chunk: str) -> None:
        """Append chunk to bubble content."""
        self._content.setText(self._content.text() + chunk)
        self.updateGeometry()


class ThinkingBubble(QFrame):
    """Visual animated indicator displayed while orchestrator state is THINKING."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 6)
        layout.setSpacing(8)

        self._label = QLabel("⚡ Analyzing system and planning actions...", self)
        font = self._label.font()
        font.setBold(True)
        font.setPointSize(11)
        self._label.setFont(font)
        self._label.setStyleSheet("color: #a3a3a3; font-size: 11px; font-weight: 600;")
        layout.addWidget(self._label)

        self.setStyleSheet(
            "ThinkingBubble {"
            "  background-color: transparent;"
            "  border: none;"
            "}"
        )

        self._dots = 0
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._animate_dots)

    def start_animation(self) -> None:
        self._dots = 0
        self._animate_dots()
        self._timer.start()

    def stop_animation(self) -> None:
        self._timer.stop()

    def _animate_dots(self) -> None:
        self._dots = (self._dots % 4) + 1
        dots_str = "." * self._dots
        self._label.setText(f"⚡ Analyzing system and planning actions{dots_str}")


class _ChipButton(QPushButton):
    """Chip button that escapes mnemonics while preserving logical text."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        escaped = text.replace("&", "&&")
        super().__init__(escaped, parent)
        self._raw_text = text

    def text(self) -> str:
        return self._raw_text


class StarterChipsWidget(QFrame):
    """Interactive container of quick-action starter chips for empty chat states."""

    chip_clicked: Signal = Signal(str)

    CHIPS: list[tuple[str, str]] = [
        ("📦 Software Setup", "Help me install an application or configure a local package."),
        ("🔧 Hardware & Drivers", "Check my hardware details and verify if all drivers and kernel modules are loaded."),
        ("⚙️ System Settings", "Help me configure desktop settings, power profiles, or manage dotfiles."),
        ("🌐 Network & DNS", "Check my current network interfaces and help me configure DNS or firewall rules."),
        ("🔍 System Health", "Check for failed systemd services and scan recent system logs for errors."),
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("starterChipsFrame")
        self.setStyleSheet(
            "QFrame#starterChipsFrame {"
            "  background-color: transparent;"
            "  border: none;"
            "}"
            "QPushButton#starterChip {"
            "  background-color: #141414;"
            "  color: #9e9e9e;"
            "  border: 1px solid #242424;"
            "  border-radius: 14px;"
            "  padding: 4px 10px;"
            "  font-size: 11px;"
            "  font-weight: 500;"
            "  min-height: 24px;"
            "}"
            "QPushButton#starterChip:hover {"
            "  background-color: #1c1c1c;"
            "  border-color: #383838;"
            "  color: #ffffff;"
            "}"
            "QPushButton#starterChip:pressed {"
            "  background-color: #242424;"
            "  border-color: #444444;"
            "  color: #ffffff;"
            "}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 4)
        layout.setSpacing(6)

        title = QLabel("SUGGESTED")
        title.setObjectName("mutedLabel")
        font = title.font()
        font.setPointSize(11)
        font.setBold(True)
        title.setFont(font)
        title.setStyleSheet("color: #666666; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;")
        layout.addWidget(title)

        self.buttons: list[QPushButton] = []

        chips_row = QHBoxLayout()
        chips_row.setContentsMargins(0, 0, 0, 0)
        chips_row.setSpacing(6)
        for label, prompt in self.CHIPS:
            btn = _ChipButton(label, self)
            btn.setObjectName("starterChip")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(prompt)
            btn.clicked.connect(lambda _, p=prompt: self.chip_clicked.emit(p))
            chips_row.addWidget(btn)
            self.buttons.append(btn)
        chips_row.addStretch()
        layout.addLayout(chips_row)


class MainWindow(QMainWindow):
    """Main split-pane GUI window.

    Parameters
    ----------
    orchestrator:
        Active AgentOrchestrator driving LLM communication and execution.
    profile:
        Cached or inspected system profile dictionary.
    parent:
        Optional parent widget.
    """

    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        profile: Optional[Dict[str, Any]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._orchestrator = orchestrator
        self._profile = profile or get_or_create_profile()
        self._dynamic = get_dynamic_session_info()
        self._skill_level = self._profile.get("skill_level", DEFAULT_SKILL_LEVEL)

        self._active_assistant_bubble: Optional[ChatBubble] = None
        self._thinking_bubble: Optional[ThinkingBubble] = None

        self.setWindowTitle("Linux AI Assistant")
        self.resize(1150, 750)
        self.setMinimumSize(850, 550)

        self._build_ui()
        self._connect_orchestrator()
        self._apply_initial_skill_layout()
        self._post_welcome_message()

    # ------------------------------------------------------------------
    # UI Layout Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Slim Top Bar (44px) ─────────────────────────────────────────────
        top_bar_widget = QWidget(central)
        top_bar_widget.setFixedHeight(44)
        top_bar_widget.setStyleSheet(
            "background-color: #080808; border-bottom: 1px solid #1c1c1c;"
        )
        top_bar = QHBoxLayout(top_bar_widget)
        top_bar.setContentsMargins(16, 0, 16, 0)
        top_bar.setSpacing(10)

        app_title = QLabel("Linux AI Assistant", top_bar_widget)
        font = app_title.font()
        font.setPointSize(13)
        font.setBold(True)
        app_title.setFont(font)
        app_title.setStyleSheet("color: #ededed; font-size: 13px; font-weight: 600;")
        top_bar.addWidget(app_title)

        # Subtle Status indicator: 6px colored dot + "Ready" text
        status_box = QWidget(top_bar_widget)
        status_box_layout = QHBoxLayout(status_box)
        status_box_layout.setContentsMargins(4, 0, 0, 0)
        status_box_layout.setSpacing(6)

        self._status_dot = QFrame(status_box)
        self._status_dot.setFixedSize(6, 6)
        self._status_dot.setStyleSheet("background-color: #10b981; border-radius: 3px;")
        status_box_layout.addWidget(self._status_dot)

        self._status_badge = QLabel("Ready", status_box)
        self._status_badge.setObjectName("statusLabel")
        self._status_badge.setStyleSheet(
            "color: #888888; font-size: 11px; font-weight: 500; background: transparent; border: none; padding: 0;"
        )
        status_box_layout.addWidget(self._status_badge)

        top_bar.addWidget(status_box)
        top_bar.addStretch()

        # Condensed Environment badge in header (with full tooltip)
        self._env_badge = QLabel(top_bar_widget)
        self._env_badge.setStyleSheet(
            "color: #666666; font-size: 11px; padding: 3px 8px; background: transparent;"
        )
        top_bar.addWidget(self._env_badge)

        # Skill mode label & dropdown
        skill_lbl = QLabel("Mode:", top_bar_widget)
        skill_lbl.setObjectName("mutedLabel")
        skill_lbl.setStyleSheet("color: #737373; font-size: 11px; font-weight: 500; margin-right: 2px;")
        top_bar.addWidget(skill_lbl)

        self._skill_combo = QComboBox(top_bar_widget)
        self._skill_combo.setObjectName("headerCombo")
        self._skill_combo.setFixedHeight(28)
        for level in VALID_SKILL_LEVELS:
            self._skill_combo.addItem(level)
        idx = self._skill_combo.findText(self._skill_level)
        if idx >= 0:
            self._skill_combo.setCurrentIndex(idx)
        self._skill_combo.currentTextChanged.connect(self._on_skill_changed)
        top_bar.addWidget(self._skill_combo)

        # Log toggle button
        self._log_toggle_btn = QPushButton("Terminal Logs", top_bar_widget)
        self._log_toggle_btn.setObjectName("headerGhostButton")
        self._log_toggle_btn.setFixedHeight(28)
        self._log_toggle_btn.setIcon(QIcon(TERMINAL_ICON_PATH))
        self._log_toggle_btn.setIconSize(QSize(13, 13))
        self._log_toggle_btn.setCheckable(True)
        self._log_toggle_btn.setChecked(self._skill_level != "Beginner")
        self._log_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._log_toggle_btn.clicked.connect(self._toggle_log_pane)
        top_bar.addWidget(self._log_toggle_btn)

        # Settings button
        self._settings_btn = QPushButton("Settings", top_bar_widget)
        self._settings_btn.setObjectName("headerGhostButton")
        self._settings_btn.setFixedHeight(28)
        self._settings_btn.setIcon(QIcon(SETTINGS_ICON_PATH))
        self._settings_btn.setIconSize(QSize(13, 13))
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.clicked.connect(self._open_settings)
        top_bar.addWidget(self._settings_btn)

        root_layout.addWidget(top_bar_widget)

        # ── Splitter: Centered Chat Column (Top) & Terminal Log Drawer (Bottom) ─
        self._splitter = QSplitter(Qt.Orientation.Vertical, central)
        self._splitter.setChildrenCollapsible(True)
        self._splitter.setHandleWidth(1)

        # --- Scalable Chat Column ---
        chat_outer = QWidget(self._splitter)
        chat_outer_layout = QVBoxLayout(chat_outer)
        chat_outer_layout.setContentsMargins(0, 0, 0, 0)
        chat_outer_layout.setSpacing(0)

        chat_column = QWidget(chat_outer)
        chat_column.setMinimumWidth(320)
        chat_column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        chat_layout = QVBoxLayout(chat_column)
        chat_layout.setContentsMargins(16, 12, 16, 12)
        chat_layout.setSpacing(10)
        chat_outer_layout.addWidget(chat_column)

        # Scrollable chat messages container
        self._chat_scroll = QScrollArea(chat_column)
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._chat_container = QWidget()
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(0, 4, 0, 4)
        self._chat_layout.setSpacing(12)
        self._chat_layout.addStretch()  # pushes messages to bottom
        self._chat_scroll.setWidget(self._chat_container)

        chat_layout.addWidget(self._chat_scroll, 1)

        # Embedded Approval Card
        self._approval_card = ApprovalCard(
            skill_level=self._skill_level, parent=chat_column
        )
        self._approval_card.approved.connect(self._orchestrator.handle_user_approval)
        self._approval_card.rejected.connect(self._orchestrator.handle_user_rejection)
        chat_layout.addWidget(self._approval_card)

        # Starter Chips Widget (quick actions displayed above the prompt input)
        self._starter_chips = StarterChipsWidget(parent=chat_column)
        self._starter_chips.chip_clicked.connect(self._on_chip_clicked)
        self.starter_chips = self._starter_chips
        chat_layout.addWidget(self._starter_chips)

        # Unified Floating Command Bar
        input_container = QFrame(chat_column)
        input_container.setObjectName("commandBar")
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(10, 6, 8, 6)
        input_layout.setSpacing(8)

        self._input_field = QLineEdit(input_container)
        self.input_field = self._input_field
        self._input_field.setObjectName("commandBarInput")
        self._input_field.setPlaceholderText(
            "Ask a question or describe an action..."
        )
        self._input_field.returnPressed.connect(self._on_send_message)
        input_layout.addWidget(self._input_field, 1)

        # Focus filter to highlight commandBar container when input is focused
        class _FocusFilter(QObject):
            def __init__(self, target: QFrame, parent: Optional[QObject] = None) -> None:
                super().__init__(parent)
                self._target = target

            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Type.FocusIn:
                    self._target.setProperty("focused", True)
                    self._target.style().unpolish(self._target)
                    self._target.style().polish(self._target)
                elif ev.type() == QEvent.Type.FocusOut:
                    self._target.setProperty("focused", False)
                    self._target.style().unpolish(self._target)
                    self._target.style().polish(self._target)
                return False

        self._focus_filter = _FocusFilter(input_container, self)
        self._input_field.installEventFilter(self._focus_filter)

        # Stop / Abort button (unobtrusive, hidden when idle)
        self._abort_btn = QPushButton("■ Stop", input_container)
        self._abort_btn.setObjectName("commandBarStopBtn")
        self._abort_btn.setEnabled(False)
        self._abort_btn.setVisible(False)
        self._abort_btn.setToolTip("Stop currently running command")
        self._abort_btn.clicked.connect(self._orchestrator.abort)
        input_layout.addWidget(self._abort_btn)

        # Compact keyboard shortcut badge / send button on the right
        self._send_btn = QPushButton("↵", input_container)
        self._send_btn.setObjectName("commandBarSendBtn")
        self._send_btn.setToolTip("Send message (Enter)")
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.clicked.connect(self._on_send_message)
        input_layout.addWidget(self._send_btn)

        chat_layout.addWidget(input_container)

        self._splitter.addWidget(chat_outer)

        # --- Bottom Drawer: Terminal Log Pane ---
        self._log_pane = QWidget(self._splitter)
        self._log_pane.setStyleSheet("background-color: #080808; border-top: 1px solid #1c1c1c;")
        log_layout = QVBoxLayout(self._log_pane)
        log_layout.setContentsMargins(16, 8, 16, 8)
        log_layout.setSpacing(6)

        # Log drawer header
        log_header_row = QHBoxLayout()
        log_header_row.setContentsMargins(0, 0, 0, 0)
        log_header_row.setSpacing(8)

        log_title = QLabel("TERMINAL OUTPUT", self._log_pane)
        log_title.setStyleSheet("color: #666666; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;")
        log_header_row.addWidget(log_title)
        log_header_row.addStretch()

        self._clear_logs_btn = QPushButton("Clear", self._log_pane)
        self._clear_logs_btn.setObjectName("headerGhostButton")
        self._clear_logs_btn.clicked.connect(self._clear_logs)
        log_header_row.addWidget(self._clear_logs_btn)

        self._close_logs_btn = QPushButton("✕ Hide", self._log_pane)
        self._close_logs_btn.setObjectName("headerGhostButton")
        self._close_logs_btn.setToolTip("Collapse terminal log drawer")
        self._close_logs_btn.clicked.connect(lambda: self._set_log_pane_visible(False))
        log_header_row.addWidget(self._close_logs_btn)

        log_layout.addLayout(log_header_row)

        # Log text view
        self._log_view = QPlainTextEdit(self._log_pane)
        self._log_view.setReadOnly(True)
        self._log_view.setObjectName("commandBox")
        font = QFont("JetBrains Mono, Fira Code, Cascadia Code, Consolas, monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self._log_view.setFont(font)
        self._log_view.setPlaceholderText("Subprocess stdout & stderr will stream here...")
        log_layout.addWidget(self._log_view, 1)

        self._splitter.addWidget(self._log_pane)

        root_layout.addWidget(self._splitter, 1)

        # ── Status Bar (Compact minimal 20px footer) ────────────────────────
        status_bar = QStatusBar(self)
        self.setStatusBar(status_bar)
        self.statusBar().setFixedHeight(20)
        self.statusBar().setStyleSheet(
            "QStatusBar { background-color: #080808; color: #666666; font-size: 10px; border-top: 1px solid #181818; padding: 0 16px; }"
        )
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        """Update status bar text with active environment, provider, and model."""
        os_name = self._profile.get("os_pretty_name", "Linux")
        cpu = self._profile.get("cpu_architecture", "x86_64")
        pm = self._profile.get("primary_package_manager", "pacman")
        session = self._dynamic.get("xdg_session_type", "wayland")
        prov = self._profile.get("active_provider", "groq").capitalize()
        model = self._profile.get("model_name", "llama-3.3-70b-versatile")
        self.statusBar().showMessage(f"Provider: {prov} ({model})")
        if hasattr(self, "_env_badge"):
            self._env_badge.setText(f"{pm} • {session} • {prov}")
            self._env_badge.setToolTip(
                f"OS: {os_name} ({cpu})\nPackage Manager: {pm}\nSession: {session}\nLLM: {prov} ({model})"
            )

    # ------------------------------------------------------------------
    # Orchestrator Signal Connections
    # ------------------------------------------------------------------

    def _connect_orchestrator(self) -> None:
        self._orchestrator.response_chunk.connect(self._on_response_chunk)
        self._orchestrator.response_ready.connect(self._on_response_ready)
        self._orchestrator.approval_requested.connect(self._on_approval_requested)
        self._orchestrator.command_output.connect(self._on_command_output)
        self._orchestrator.command_finished.connect(self._on_command_finished)
        self._orchestrator.error_occurred.connect(self._on_error_occurred)
        self._orchestrator.state_changed.connect(self._on_state_changed)

    # ------------------------------------------------------------------
    # Chat Area Management
    # ------------------------------------------------------------------

    def _post_welcome_message(self) -> None:
        welcome_text = (
            "Hello! I am your native Linux desktop assistant.\n\n"
            "I can help you install packages, download and unpack applications into user-space "
            "(~/.local/opt), create desktop launchers, and diagnose issues.\n\n"
            "Every mutating action will be proposed via an Approval Card for your review before "
            "anything runs."
        )
        self._append_message("assistant", welcome_text)

    def _append_message(self, role: str, text: str = "") -> ChatBubble:
        bubble = ChatBubble(role=role, text=text, parent=self._chat_container)
        # Insert before the stretch item
        idx = max(0, self._chat_layout.count() - 1)
        if role == "user":
            self._chat_layout.insertWidget(idx, bubble, 0, Qt.AlignmentFlag.AlignRight)
        else:
            self._chat_layout.insertWidget(idx, bubble, 0)
        self._scroll_chat_to_bottom()
        return bubble

    def _scroll_chat_to_bottom(self) -> None:
        QTimer.singleShot(
            50,
            lambda: self._chat_scroll.verticalScrollBar().setValue(
                self._chat_scroll.verticalScrollBar().maximum()
            ),
        )

    # ------------------------------------------------------------------
    # Slots: User Interaction
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_chip_clicked(self, prompt: str) -> None:
        """Prefill the input field with the chip prompt and set focus."""
        self._input_field.setText(prompt)
        self._input_field.setFocus()

    @Slot()
    def _on_send_message(self) -> None:
        text = self._input_field.text().strip()
        if not text:
            return

        # Hide starter chips on the first message sent
        self._starter_chips.hide()

        self._input_field.clear()
        self._append_message("user", text)
        self._active_assistant_bubble = None

        self._input_field.setEnabled(False)
        self._send_btn.setEnabled(False)
        self._orchestrator.send_message(text)

    def reset_conversation(self) -> None:
        """Reset conversation messages and restore the starter chips widget."""
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._active_assistant_bubble = None
        self._post_welcome_message()
        self._starter_chips.show()

    def clear_chat(self) -> None:
        """Alias for reset_conversation."""
        self.reset_conversation()

    @Slot(str)
    def _on_skill_changed(self, skill: str) -> None:
        if skill not in VALID_SKILL_LEVELS:
            return
        self._skill_level = skill
        try:
            update_skill_level(skill)
        except OSError:
            pass
        self._approval_card.set_skill_level(skill)

        # Update system prompt in orchestrator client
        new_prompt = build_system_prompt(self._profile, self._dynamic, skill)
        self._orchestrator.update_system_prompt(new_prompt)

        # Adjust log pane visibility based on skill
        if skill == "Beginner":
            self._set_log_pane_visible(False)
        else:
            self._set_log_pane_visible(True)

    @Slot()
    def _toggle_log_pane(self) -> None:
        visible = self._log_toggle_btn.isChecked()
        self._set_log_pane_visible(visible)

    def _set_log_pane_visible(self, visible: bool) -> None:
        self._log_toggle_btn.setChecked(visible)
        if visible:
            self._log_pane.show()
            h = max(self.height(), 600)
            self._splitter.setSizes([h - 180, 180])
        else:
            self._log_pane.hide()
            h = max(self.height(), 600)
            self._splitter.setSizes([h, 0])

    def _apply_initial_skill_layout(self) -> None:
        if self._skill_level == "Beginner":
            self._set_log_pane_visible(False)
        else:
            self._set_log_pane_visible(True)

    def _show_thinking_indicator(self) -> None:
        """Display and animate the thinking indicator bubble."""
        if self._thinking_bubble is None:
            self._thinking_bubble = ThinkingBubble(self._chat_container)
            idx = max(0, self._chat_layout.count() - 1)
            self._chat_layout.insertWidget(idx, self._thinking_bubble, 0)
        self._thinking_bubble.show()
        self._thinking_bubble.start_animation()
        self._scroll_chat_to_bottom()

    def _hide_thinking_indicator(self) -> None:
        """Stop and hide the thinking indicator bubble."""
        if self._thinking_bubble is not None:
            self._thinking_bubble.stop_animation()
            self._thinking_bubble.hide()

    @Slot()
    def _clear_logs(self) -> None:
        self._log_view.clear()

    @Slot()
    def _open_settings(self) -> None:
        modal = OnboardingModal(parent=self, profile=self._profile)
        modal.configuration_saved.connect(self._on_settings_saved)
        modal.exec()

    @Slot(str, str)
    @Slot(str, str, str, str)
    def _on_settings_saved(
        self, skill: str, api_key: str, provider: str = "", model: str = ""
    ) -> None:
        self._skill_combo.setCurrentText(skill)
        if provider:
            self._profile["active_provider"] = provider
        if model:
            self._profile["model_name"] = model
        self._update_status_bar()

        # Re-initialize client if key changed or provider is ollama
        effective_key = api_key or ("ollama" if provider == "ollama" else "")
        if effective_key:
            from agent.llm_client import create_llm_client

            prompt = build_system_prompt(self._profile, self._dynamic, skill)
            target_prov = provider or self._profile.get("active_provider", "groq")
            target_model = model or self._profile.get("model_name")
            new_client = create_llm_client(
                provider=target_prov,
                api_key=effective_key,
                model_name=target_model,
                system_prompt=prompt,
            )
            self._orchestrator.set_client(new_client)

    # ------------------------------------------------------------------
    # Slots: Orchestrator Events
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_response_chunk(self, chunk: str) -> None:
        self._hide_thinking_indicator()
        if self._active_assistant_bubble is None:
            self._active_assistant_bubble = self._append_message("assistant", chunk)
        else:
            self._active_assistant_bubble.append_text(chunk)
        self._scroll_chat_to_bottom()

    @Slot(str)
    def _on_response_ready(self, full_text: str) -> None:
        self._hide_thinking_indicator()
        if self._active_assistant_bubble is None:
            self._active_assistant_bubble = self._append_message(
                "assistant", full_text
            )
        else:
            self._active_assistant_bubble.set_text(full_text)
        self._active_assistant_bubble = None
        self._scroll_chat_to_bottom()

    @Slot(dict)
    def _on_approval_requested(self, payload: Dict[str, Any]) -> None:
        self._hide_thinking_indicator()
        self._approval_card.load_payload(payload)
        self._scroll_chat_to_bottom()

    @Slot(str)
    def _on_command_output(self, raw_chunk: str) -> None:
        clean = strip_ansi(raw_chunk)
        self._log_view.moveCursor(QTextCursor.MoveOperation.End)
        self._log_view.insertPlainText(clean)
        self._log_view.moveCursor(QTextCursor.MoveOperation.End)

    @Slot(int, str)
    def _on_command_finished(self, exit_code: int, tail_summary: str) -> None:
        status = "COMPLETED SUCCESSFULLY (exit code 0)" if exit_code == 0 else f"FAILED (exit code {exit_code})"
        banner = f"\n─── [Execution {status}] ───\n\n"
        self._log_view.moveCursor(QTextCursor.MoveOperation.End)
        self._log_view.insertPlainText(banner)
        self._log_view.moveCursor(QTextCursor.MoveOperation.End)

    @Slot(str)
    def _on_error_occurred(self, err_msg: str) -> None:
        self._hide_thinking_indicator()
        self._append_message("assistant", f"⚠ An error occurred: {err_msg}")
        self._active_assistant_bubble = None
        self._input_field.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._abort_btn.setVisible(False)

    @Slot(str)
    def _on_state_changed(self, new_state: str) -> None:
        if new_state == OrchestratorState.IDLE.value:
            self._status_badge.setText("Ready")
            self._hide_thinking_indicator()
            self._status_dot.setStyleSheet(
                "background-color: #10b981; border-radius: 3px;"
            )
            self._status_badge.setStyleSheet(
                "color: #888888; font-size: 11px; font-weight: 500; background: transparent; border: none; padding: 0;"
            )
            self._input_field.setEnabled(True)
            self._send_btn.setEnabled(True)
            self._abort_btn.setEnabled(False)
            self._abort_btn.setVisible(False)

        elif new_state == OrchestratorState.THINKING.value:
            self._status_badge.setText(new_state)
            self._show_thinking_indicator()
            self._status_dot.setStyleSheet(
                "background-color: #818cf8; border-radius: 3px;"
            )
            self._status_badge.setStyleSheet(
                "color: #818cf8; font-size: 11px; font-weight: 500; background: transparent; border: none; padding: 0;"
            )
            self._input_field.setEnabled(False)
            self._send_btn.setEnabled(False)
            self._abort_btn.setEnabled(False)
            self._abort_btn.setVisible(False)

        elif new_state == OrchestratorState.AWAITING_APPROVAL.value:
            self._status_badge.setText(new_state)
            self._hide_thinking_indicator()
            self._status_dot.setStyleSheet(
                "background-color: #f59e0b; border-radius: 3px;"
            )
            self._status_badge.setStyleSheet(
                "color: #f59e0b; font-size: 11px; font-weight: 500; background: transparent; border: none; padding: 0;"
            )
            self._input_field.setEnabled(False)
            self._send_btn.setEnabled(False)
            self._abort_btn.setEnabled(False)
            self._abort_btn.setVisible(False)

        elif new_state == OrchestratorState.EXECUTING.value:
            self._status_badge.setText(new_state)
            self._hide_thinking_indicator()
            self._set_log_pane_visible(True)
            self._status_dot.setStyleSheet(
                "background-color: #10b981; border-radius: 3px;"
            )
            self._status_badge.setStyleSheet(
                "color: #10b981; font-size: 11px; font-weight: 500; background: transparent; border: none; padding: 0;"
            )
            self._input_field.setEnabled(False)
            self._send_btn.setEnabled(False)
            self._abort_btn.setEnabled(True)
            self._abort_btn.setVisible(True)

        elif new_state == OrchestratorState.ERROR.value:
            self._status_badge.setText(new_state)
            self._hide_thinking_indicator()
            self._status_dot.setStyleSheet(
                "background-color: #ef4444; border-radius: 3px;"
            )
            self._status_badge.setStyleSheet(
                "color: #ef4444; font-size: 11px; font-weight: 500; background: transparent; border: none; padding: 0;"
            )
            self._input_field.setEnabled(True)
            self._send_btn.setEnabled(True)
            self._abort_btn.setEnabled(False)
            self._abort_btn.setVisible(False)
