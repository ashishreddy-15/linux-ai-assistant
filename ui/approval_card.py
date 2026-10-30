"""Approval card widget for the Linux AI Assistant.

Displays a HITL approval request from the orchestrator with layout adapted to
the user's skill level.

Layout variants:
  Beginner     – Outcome summary only; bash command hidden behind a
                 collapsible "Show command ▸" toggle; [Approve] / [Reject].
  Intermediate – Plain summary + read-only monospace command box + [Approve]
                 / [Reject].
  Advanced     – Technical summary + *editable* QPlainTextEdit pre-filled with
                 the command (user can tweak before approving) + [Approve] /
                 [Reject].

High-risk payload (payload["high_risk"] is True):
  An amber warning badge is rendered above the summary.
  Card border switches to the #approvalCardHighRisk QSS selector.

Signals:
  approved(str)  – emitted with the (possibly edited) command string.
  rejected(str)  – emitted with an empty reason string (user pressed Reject).

Usage:
  card = ApprovalCard(skill_level="Intermediate", parent=self)
  card.approved.connect(orchestrator.handle_user_approval)
  card.rejected.connect(orchestrator.handle_user_rejection)
  card.load_payload(payload_dict)   # call when approval_requested fires
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

VALID_SKILL_LEVELS = ("Beginner", "Intermediate", "Advanced")


class _CollapsibleSection(QWidget):
    """A widget that wraps a child widget behind a toggle button.

    Used in Beginner mode to hide the raw command behind a
    "Show command ▸ / ▾" toggle.
    """

    def __init__(
        self,
        toggle_label_collapsed: str = "Show command ▸",
        toggle_label_expanded: str = "Hide command ▾",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._label_collapsed = toggle_label_collapsed
        self._label_expanded = toggle_label_expanded

        self._toggle = QToolButton(self)
        self._toggle.setObjectName("collapseToggle")
        self._toggle.setText(self._label_collapsed)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(False)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        self._content: Optional[QWidget] = None
        self._content_placeholder = QWidget(self)
        self._content_placeholder.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._toggle)
        layout.addWidget(self._content_placeholder)

        self._toggle.clicked.connect(self._on_toggle)

    def set_content(self, widget: QWidget) -> None:
        """Set the collapsible child widget."""
        if self._content is not None:
            self.layout().removeWidget(self._content)
            self._content.setParent(None)

        # Replace the placeholder
        self.layout().removeWidget(self._content_placeholder)
        self._content_placeholder.setParent(None)

        self._content = widget
        self._content.setVisible(self._toggle.isChecked())
        self.layout().addWidget(self._content)

    @Slot()
    def _on_toggle(self, checked: bool) -> None:
        self._toggle.setText(
            self._label_expanded if checked else self._label_collapsed
        )
        if self._content is not None:
            self._content.setVisible(checked)


class ApprovalCard(QFrame):
    """HITL approval card widget.

    Parameters
    ----------
    skill_level:
        One of "Beginner", "Intermediate", "Advanced". Controls the layout
        variant rendered by ``load_payload``.
    parent:
        Optional parent widget.

    Signals
    -------
    approved(str):
        Emitted with the command string when the user presses Approve. In
        Advanced mode this may be an edited version of the original command.
    rejected(str):
        Emitted with an empty string (rejection reason currently not collected
        from the UI; the orchestrator handles the fixed message).
    """

    approved: Signal = Signal(str)
    rejected: Signal = Signal(str)

    def __init__(
        self,
        skill_level: str = "Intermediate",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        if skill_level not in VALID_SKILL_LEVELS:
            skill_level = "Intermediate"
        self._skill_level = skill_level

        self._current_command: str = ""

        self._build_skeleton()
        self.hide()  # hidden until load_payload() is called

    def set_skill_level(self, skill_level: str) -> None:
        """Update the active skill level for future or current payload renders."""
        if skill_level in VALID_SKILL_LEVELS:
            self._skill_level = skill_level

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @Slot(dict)
    def load_payload(self, payload: Dict[str, Any]) -> None:
        """Populate the card from an orchestrator approval_requested payload.

        Expected keys (from orchestrator):
          tool_name (str), command (str), explanation (str),
          requires_root (bool), high_risk (bool)

          For create_desktop_entry: app_name, exec_path, icon_path,
          categories, comment, high_risk.
        """
        self._current_command = ""
        tool_name = payload.get("tool_name", "propose_shell_command")
        high_risk = bool(payload.get("high_risk", False))

        # Choose danger vs. warning badge and card border
        if high_risk:
            rm_rf = "rm -rf /" in payload.get("command", "")
            self.setObjectName("approvalCardDanger" if rm_rf else "approvalCardHighRisk")
        else:
            self.setObjectName("approvalCard")
        # Re-apply style after objectName change
        self.style().unpolish(self)
        self.style().polish(self)

        # Resolve human-readable summary
        if tool_name == "propose_shell_command":
            self._current_command = payload.get("command", "")
            explanation = payload.get("explanation", "")
            requires_root = bool(payload.get("requires_root", False))
            summary = self._build_shell_summary(
                explanation, requires_root, high_risk
            )
            command_text = self._current_command
        elif tool_name == "create_desktop_entry":
            app_name = payload.get("app_name", "")
            exec_path = payload.get("exec_path", "")
            icon_path = payload.get("icon_path", "")
            categories = payload.get("categories", "")
            comment = payload.get("comment", "")
            summary, command_text = self._build_desktop_summary(
                app_name, exec_path, icon_path, categories, comment
            )
            self._current_command = command_text
        else:
            summary = f"Unknown action: {tool_name}"
            command_text = str(payload)
            self._current_command = command_text

        self._populate_layout(summary, command_text, high_risk)
        self.show()

    def current_command(self) -> str:
        """Return the current command string (possibly edited by the user)."""
        if self._skill_level == "Advanced" and self._cmd_edit is not None:
            return self._cmd_edit.toPlainText().strip()
        return self._current_command

    # ------------------------------------------------------------------
    # Layout construction
    # ------------------------------------------------------------------

    def _build_skeleton(self) -> None:
        """Create the permanent outer layout and button row."""
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("approvalCard")
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        # -- Badge row (warning / danger) --
        self._badge_row = QHBoxLayout()
        self._badge_row.setContentsMargins(0, 0, 0, 0)
        self._badge_row.setSpacing(6)
        self._warning_badge = QLabel()
        self._warning_badge.setObjectName("warningBadge")
        self._warning_badge.setVisible(False)
        self._badge_row.addWidget(self._warning_badge)
        self._badge_row.addStretch()
        outer.addLayout(self._badge_row)

        # -- Dynamic content area --
        self._content_area = QVBoxLayout()
        self._content_area.setContentsMargins(0, 0, 0, 0)
        self._content_area.setSpacing(8)
        outer.addLayout(self._content_area)

        # -- Separator line --
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Plain)
        outer.addWidget(sep)

        # -- Button row --
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch()

        self._reject_btn = QPushButton("Reject")
        self._reject_btn.setObjectName("rejectButton")
        self._reject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reject_btn.setMinimumWidth(80)

        self._approve_btn = QPushButton("✓  Approve")
        self._approve_btn.setObjectName("approveButton")
        self._approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._approve_btn.setMinimumWidth(110)

        btn_row.addWidget(self._reject_btn)
        btn_row.addWidget(self._approve_btn)
        outer.addLayout(btn_row)

        self._approve_btn.clicked.connect(self._on_approve)
        self._reject_btn.clicked.connect(self._on_reject)

        # Internal ref to editable command box (Advanced mode)
        self._cmd_edit: Optional[QPlainTextEdit] = None

    def _clear_content_area(self) -> None:
        """Remove all widgets from the dynamic content area."""
        while self._content_area.count():
            item = self._content_area.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                # recursively clear nested layouts
                self._clear_layout(item.layout())
        self._cmd_edit = None

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _populate_layout(
        self, summary: str, command_text: str, high_risk: bool
    ) -> None:
        """Rebuild dynamic content area for the current payload."""
        self._clear_content_area()

        # --- Warning badge ---
        if high_risk:
            rm_rf = "rm -rf /" in command_text
            if rm_rf:
                self._warning_badge.setObjectName("dangerBadge")
                self._warning_badge.setText("⚠  DESTRUCTIVE – IRREVERSIBLE")
            else:
                self._warning_badge.setObjectName("warningBadge")
                self._warning_badge.setText("⚠  High-risk operation")
            self._warning_badge.style().unpolish(self._warning_badge)
            self._warning_badge.style().polish(self._warning_badge)
            self._warning_badge.setVisible(True)
        else:
            self._warning_badge.setVisible(False)

        # --- Skill-level layout ---
        if self._skill_level == "Beginner":
            self._build_beginner_layout(summary, command_text)
        elif self._skill_level == "Advanced":
            self._build_advanced_layout(summary, command_text)
        else:
            self._build_intermediate_layout(summary, command_text)

    # ── Beginner ────────────────────────────────────────────────────────

    def _build_beginner_layout(self, summary: str, command_text: str) -> None:
        """Outcome-focused: summary text + hidden collapsible command."""
        summary_label = QLabel(summary)
        summary_label.setWordWrap(True)
        summary_label.setTextFormat(Qt.TextFormat.PlainText)
        self._content_area.addWidget(summary_label)

        if command_text:
            collapsible = _CollapsibleSection(
                toggle_label_collapsed="Show command ▸",
                toggle_label_expanded="Hide command ▾",
            )
            cmd_box = self._make_command_box(command_text, editable=False)
            collapsible.set_content(cmd_box)
            self._content_area.addWidget(collapsible)

    # ── Intermediate ────────────────────────────────────────────────────

    def _build_intermediate_layout(self, summary: str, command_text: str) -> None:
        """Summary + always-visible read-only monospace command box."""
        summary_label = QLabel(summary)
        summary_label.setWordWrap(True)
        summary_label.setTextFormat(Qt.TextFormat.PlainText)
        self._content_area.addWidget(summary_label)

        if command_text:
            cmd_label = QLabel("Command:")
            cmd_label.setObjectName("mutedLabel")
            self._content_area.addWidget(cmd_label)

            cmd_box = self._make_command_box(command_text, editable=False)
            self._content_area.addWidget(cmd_box)

    # ── Advanced ────────────────────────────────────────────────────────

    def _build_advanced_layout(self, summary: str, command_text: str) -> None:
        """Compact technical summary + editable command input."""
        # Terse summary header
        summary_label = QLabel(summary)
        summary_label.setWordWrap(True)
        summary_label.setTextFormat(Qt.TextFormat.PlainText)
        summary_label.setObjectName("mutedLabel")
        self._content_area.addWidget(summary_label)

        if command_text:
            edit_label = QLabel("Edit command before approving:")
            edit_label.setObjectName("mutedLabel")
            self._content_area.addWidget(edit_label)

            cmd_edit = self._make_command_box(command_text, editable=True)
            self._cmd_edit = cmd_edit
            self._content_area.addWidget(cmd_edit)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_command_box(text: str, editable: bool) -> QPlainTextEdit:
        """Create a styled monospace command display / edit box."""
        box = QPlainTextEdit()
        box.setPlainText(text)
        box.setReadOnly(not editable)
        box.setObjectName("commandBox" if editable else "commandBoxReadOnly")
        # Auto-size height to content (up to ~6 lines)
        font = QFont("JetBrains Mono, Fira Code, Cascadia Code, Consolas, monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        box.setFont(font)
        line_count = min(max(text.count("\n") + 1, 1), 6)
        box.setFixedHeight(line_count * 20 + 20)
        box.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        return box

    @staticmethod
    def _build_shell_summary(
        explanation: str, requires_root: bool, high_risk: bool
    ) -> str:
        """Compose the summary text shown above the command."""
        parts = [explanation] if explanation else ["A shell command has been proposed."]
        if requires_root:
            parts.append("⚠ This operation requires administrator (root) privileges.")
        return "\n".join(parts)

    @staticmethod
    def _build_desktop_summary(
        app_name: str,
        exec_path: str,
        icon_path: str,
        categories: str,
        comment: str,
    ) -> tuple[str, str]:
        """Compose summary and command preview for a desktop entry proposal."""
        summary = (
            f"Create a desktop launcher for {app_name or 'the application'}.\n"
            f"{comment}"
        )
        lines = [
            "[Desktop Entry]",
            "Version=1.0",
            "Type=Application",
            f"Name={app_name}",
            f"Comment={comment}",
            f"Exec={exec_path}",
        ]
        if icon_path:
            lines.append(f"Icon={icon_path}")
        lines += [f"Categories={categories}", "Terminal=false"]
        command_preview = "\n".join(lines)
        return summary, command_preview

    # ------------------------------------------------------------------
    # Button slots
    # ------------------------------------------------------------------

    @Slot()
    def _on_approve(self) -> None:
        cmd = self.current_command()
        self.hide()
        self.approved.emit(cmd)

    @Slot()
    def _on_reject(self) -> None:
        self.hide()
        self.rejected.emit("")
