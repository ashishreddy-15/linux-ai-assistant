"""HITL Agent Orchestrator for the Linux AI Assistant.

AgentOrchestrator(QObject) is the central state machine that:

  1. Accepts user chat messages via the ``send_message`` slot.
  2. Maintains a multi-turn conversation history for the Gemini model.
  3. Calls GeminiAgentClient.generate() on a worker QThread.
  4. When the model proposes a MUTATING tool call (propose_shell_command or
     create_desktop_entry), it PAUSES and emits ``approval_requested`` for
     the UI to display an approval card.
  5. When the model calls a READONLY tool (read_file_snippet), it executes the
     tool automatically, injects the result back into the conversation, and
     continues the generation loop.
  6. On user approval, it executes the approved command via CommandRunner on a
     dedicated QThread, streams output, and feeds the result back into the
     conversation for the next model turn.
  7. On user rejection, it synthesises a tool-rejection message and asks the
     model to try a different approach.

Signal/Slot contract (UI ↔ Orchestrator):
  Slots called BY the UI:
    send_message(user_text: str)
    handle_user_approval(command: str)
    handle_user_rejection(reason: str)
    abort()

  Signals emitted TO the UI:
    response_chunk(str)         – incremental text while a streaming model generates
    response_ready(str)         – full model text turn complete
    approval_requested(dict)    – pausing for HITL; dict has full spec
    command_output(str)         – live PTY output chunk
    command_finished(int, str)  – (exit_code, tail_log) after execution
    error_occurred(str)         – unrecoverable error message
    state_changed(str)          – one of IDLE / THINKING / AWAITING_APPROVAL /
                                   EXECUTING / ERROR

Threading model:
  The orchestrator lives on the GUI thread but does all network I/O and
  subprocess execution on dedicated QThreads, communicating back via queued
  signals.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from enum import Enum
from typing import Any, Deque, Dict, List, Optional

from google.genai import types

from agent.events import Signal, Slot
from agent.llm_client import BaseLLMClient, GeminiAgentClient
from agent.package_search import execute_search_packages
from agent.tools import (
    MUTATING_TOOLS,
    READONLY_TOOLS,
    execute_inspect_manual,
    execute_read_file_snippet,
    normalize_tool_args,
)
from executor.pty_runner import CommandRunner, is_high_risk

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State enumeration
# ---------------------------------------------------------------------------

class OrchestratorState(str, Enum):
    IDLE = "IDLE"
    THINKING = "THINKING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class AgentOrchestrator:
    """HITL state machine coordinating LLM calls and PTY command execution.

    Parameters
    ----------
    llm_client:
        A fully configured GeminiAgentClient instance.
    parent:
        Optional parent object (kept for backwards compatibility).
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        parent: Any = None,
    ) -> None:
        # --- Signals ---
        self.response_chunk: Signal = Signal(str)
        self.response_ready: Signal = Signal(str)
        self.approval_requested: Signal = Signal(dict)
        self.command_output: Signal = Signal(str)
        self.command_finished: Signal = Signal(int, str)
        self.error_occurred: Signal = Signal(str)
        self.state_changed: Signal = Signal(str)
        # Read-only lookups run without approval; this tells the UI what the
        # assistant looked at (e.g. "read ~/.bashrc") so nothing is hidden.
        self.tool_activity: Signal = Signal(str)

        self._client = llm_client
        self._history: List[types.Content] = []
        self._state = OrchestratorState.IDLE

        # Pending tool call kept while AWAITING_APPROVAL
        self._pending_tool_call: Optional[types.FunctionCall] = None
        self._executing_tool_call: Optional[types.FunctionCall] = None

        # A model turn may contain several tool calls. They are handled one at
        # a time (each change still needs approval) and every call gets an
        # answer before the model is asked to continue.
        self._batch: Deque[types.FunctionCall] = deque()
        self._batch_results: List[types.Part] = []

        # Worker threads (kept alive while running)
        self._llm_thread: Optional[threading.Thread] = None
        self._cmd_thread: Optional[threading.Thread] = None
        self._cmd_runner: Optional[CommandRunner] = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> OrchestratorState:
        return self._state

    @property
    def history(self) -> List[types.Content]:
        """Read-only snapshot of the conversation history."""
        return list(self._history)

    def clear_history(self) -> None:
        """Reset the conversation to an empty state."""
        self._history.clear()
        self._batch.clear()
        self._batch_results = []
        self._set_state(OrchestratorState.IDLE)

    def update_system_prompt(self, system_prompt: str) -> None:
        """Update system prompt on the active LLM client."""
        self._client.update_system_prompt(system_prompt)

    def set_client(self, client: BaseLLMClient) -> None:
        """Replace the active LLM client (e.g. after updating API key or provider)."""
        self._client = client

    # ------------------------------------------------------------------
    # UI-facing slots
    # ------------------------------------------------------------------

    @Slot(str)
    def send_message(self, user_text: str) -> None:
        """Accept a new user message and trigger an LLM response."""
        if self._state not in (OrchestratorState.IDLE, OrchestratorState.ERROR):
            logger.warning("send_message called in state %s – ignoring", self._state)
            return

        # Append user turn to history
        self._history.append(GeminiAgentClient.make_user_turn(user_text))
        self._call_llm()

    @Slot(str)
    def handle_user_approval(self, command: str) -> None:
        """Called by the UI when the user approves (possibly edited) a command.

        Parameters
        ----------
        command:
            The shell command to execute. May differ from the originally
            proposed command if the user edited it.
        """
        if self._state != OrchestratorState.AWAITING_APPROVAL:
            logger.warning("handle_user_approval called outside AWAITING_APPROVAL")
            return
        if self._pending_tool_call is None:
            self._emit_error("Internal error: no pending tool call on approval.")
            return

        tool_call = self._pending_tool_call
        self._pending_tool_call = None

        if tool_call.name == "create_desktop_entry":
            self._execute_create_desktop_entry(tool_call)
        else:
            self._execute_command(command, tool_call)

    @Slot(str)
    def handle_user_rejection(self, reason: str) -> None:
        """Called by the UI when the user rejects a proposed command.

        Injects a rejection message into the conversation so the model can
        try a different approach.
        """
        if self._state != OrchestratorState.AWAITING_APPROVAL:
            logger.warning("handle_user_rejection called outside AWAITING_APPROVAL")
            return
        if self._pending_tool_call is None:
            self._emit_error("Internal error: no pending tool call on rejection.")
            return

        tool_call = self._pending_tool_call
        self._pending_tool_call = None

        rejection_msg = (
            f"The user rejected the proposed command. Reason: {reason or 'No reason given'}. "
            "Please suggest an alternative approach or ask the user for clarification."
        )
        self._add_result(tool_call, f"REJECTED: {rejection_msg}")
        self._skip_remaining("the user rejected an earlier step in this batch")
        self._continue()

    @Slot()
    def abort(self) -> None:
        """Request the currently running command (if any) to be stopped."""
        if self._cmd_runner is not None:
            self._cmd_runner.request_stop()

    # ------------------------------------------------------------------
    # Internal: LLM call
    # ------------------------------------------------------------------

    def _call_llm(self) -> None:
        """Kick off a background LLM call with the current history."""
        self._set_state(OrchestratorState.THINKING)

        def _worker() -> None:
            try:
                resp = self._client.generate(
                    list(self._history), on_chunk=self.response_chunk.emit
                )
                self._on_llm_response(resp)
            except Exception as exc:
                logger.exception("LLM call failed")
                self._on_llm_error(str(exc))

        thread = threading.Thread(target=_worker, daemon=True)
        self._llm_thread = thread
        thread.start()

    @Slot(object)
    def _on_llm_response(self, response: types.GenerateContentResponse) -> None:
        """Handle the raw model response on the GUI thread (via queued signal)."""
        # Append model content to history
        if response.candidates:
            model_content = response.candidates[0].content
            if model_content:
                self._history.append(model_content)

        # Extract text and function calls
        text = GeminiAgentClient.extract_text(response)
        function_calls = GeminiAgentClient.extract_function_calls(response)

        # Emit text if present
        if text:
            # Streaming clients already sent response_chunk while generating;
            # response_ready always carries the final, complete text.
            self.response_ready.emit(text)

        if not function_calls:
            # Pure text response – we're done this turn
            self._set_state(OrchestratorState.IDLE)
            return

        # Answer every tool call in this turn, in order.
        self._batch = deque(function_calls)
        self._batch_results = []
        self._process_next()

    @Slot(str)
    def _on_llm_error(self, error_msg: str) -> None:
        self._emit_error(f"LLM error: {error_msg}")

    # ------------------------------------------------------------------
    # Internal: tool handling
    # ------------------------------------------------------------------

    def _handle_readonly_tool(self, fc: types.FunctionCall) -> None:
        """Execute a read-only tool immediately and continue the LLM loop."""
        args: Dict[str, Any] = normalize_tool_args(fc.name, fc.args)

        if fc.name == "read_file_snippet":
            file_path = args.get("file_path", "")
            max_lines = int(args.get("max_lines", 100))
            self.tool_activity.emit(f"read {file_path}")
            result = execute_read_file_snippet(file_path, max_lines)
        elif fc.name == "inspect_manual":
            topic = args.get("command_or_topic", "")
            self.tool_activity.emit(f"looked up the manual for {topic}")
            result = execute_inspect_manual(topic)
        elif fc.name == "search_packages":
            query = args.get("query", "")
            self.tool_activity.emit(f"searched packages for “{query}”")
            result = execute_search_packages(query)
        else:
            result = f"ERROR: Unhandled readonly tool '{fc.name}'."

        self._add_result(fc, result)

    def _handle_mutating_tool(self, fc: types.FunctionCall) -> None:
        """Pause execution and emit approval_requested for the UI."""
        args: Dict[str, Any] = normalize_tool_args(fc.name, fc.args)
        tool_name = fc.name

        if tool_name == "propose_shell_command":
            command = args.get("command", "")
            explanation = args.get("explanation", "")
            requires_root = bool(args.get("requires_root", False))
            high_risk = is_high_risk(command)

            payload: Dict[str, Any] = {
                "tool_name": tool_name,
                "command": command,
                "explanation": explanation,
                "requires_root": requires_root,
                "high_risk": high_risk,
                "title": args.get("title") or "",
                "effects": list(args.get("effects") or []),
            }

        elif tool_name == "create_desktop_entry":
            from agent.tools import render_desktop_entry

            app_name = args.get("app_name", "") or "this app"
            payload = {
                "tool_name": tool_name,
                "title": f"Add {app_name} to your app menu",
                "explanation": args.get("comment", ""),
                "effects": [
                    "Creates a launcher in ~/.local/share/applications",
                    f"Lets you open {app_name} from your app menu",
                ],
                "command": render_desktop_entry(
                    app_name=args.get("app_name", ""),
                    exec_path=args.get("exec_path", ""),
                    icon_path=args.get("icon_path", ""),
                    categories=args.get("categories", ""),
                    comment=args.get("comment", ""),
                ),
                "requires_root": False,
                "app_name": args.get("app_name", ""),
                "exec_path": args.get("exec_path", ""),
                "icon_path": args.get("icon_path", ""),
                "categories": args.get("categories", ""),
                "comment": args.get("comment", ""),
                "high_risk": False,
            }
        else:
            payload = {"tool_name": tool_name, "raw_args": args}

        normalized_fc = types.FunctionCall(name=fc.name, args=args, id=fc.id)
        self._pending_tool_call = normalized_fc
        self._set_state(OrchestratorState.AWAITING_APPROVAL)
        self.approval_requested.emit(payload)

    # ------------------------------------------------------------------
    # Internal: command execution
    # ------------------------------------------------------------------

    def _execute_command(self, command: str, tool_call: types.FunctionCall) -> None:
        """Run an approved shell command via CommandRunner on a worker thread."""
        self._set_state(OrchestratorState.EXECUTING)
        self._executing_tool_call = tool_call

        runner = CommandRunner(command=command)

        # Wire PTY signals → orchestrator signals (forwarded to UI)
        runner.output_received.connect(self.command_output.emit)
        runner.command_finished.connect(self._on_command_finished_slot)

        # Store refs to allow abort()
        self._cmd_runner = runner

        thread = threading.Thread(target=runner.start_execution, daemon=True)
        self._cmd_thread = thread
        thread.start()

    @Slot(int, str)
    def _on_command_finished_slot(self, exit_code: int, tail: str) -> None:
        """Slot invoked on the GUI thread when the background runner finishes."""
        tool_call = self._executing_tool_call
        self._executing_tool_call = None
        self._on_command_finished(exit_code, tail, tool_call)

    def _execute_create_desktop_entry(self, tool_call: types.FunctionCall) -> None:
        """Execute creation of a .desktop launcher, trigger database update, and report back."""
        self._set_state(OrchestratorState.EXECUTING)
        args: Dict[str, Any] = normalize_tool_args(tool_call.name, tool_call.args)
        app_name = args.get("app_name", "")
        exec_path = args.get("exec_path", "")
        icon_path = args.get("icon_path", "")
        categories = args.get("categories", "")
        comment = args.get("comment", "")

        from agent.tools import create_desktop_entry

        try:
            result_msg = create_desktop_entry(
                app_name=app_name,
                exec_path=exec_path,
                icon_path=icon_path,
                categories=categories,
                comment=comment,
            )
            exit_code = 0
            self.command_output.emit(result_msg + "\n")
        except Exception as exc:
            result_msg = f"Failed to create desktop entry: {exc}"
            exit_code = 1
            self.command_output.emit(result_msg + "\n")

        self.command_finished.emit(exit_code, result_msg)
        self._add_result(tool_call, result_msg)
        if exit_code != 0:
            self._skip_remaining("an earlier step in this batch failed")
        self._continue()

    @Slot(int, str)
    def _on_command_finished(
        self,
        exit_code: int,
        tail_log: str,
        tool_call: types.FunctionCall,
    ) -> None:
        """Feed execution results back into the LLM conversation."""
        self.command_finished.emit(exit_code, tail_log)
        self._cmd_runner = None
        self._cmd_thread = None

        if exit_code != 0:
            result_text = (
                f"Command failed with exit code {exit_code}. Terminal output: {tail_log}\n\n"
                "Please acknowledge this failure to the user, diagnose the error based on the terminal output, "
                "and propose a corrective tool call to resolve it."
            )
        else:
            result_text = (
                f"Command execution SUCCESS.\n"
                f"Last output lines:\n{tail_log}"
            )

        self._add_result(tool_call, result_text)
        if exit_code != 0:
            why = "the user stopped an earlier step" if exit_code == -1 else "an earlier step in this batch failed"
            self._skip_remaining(why)
        self._continue()

    # ------------------------------------------------------------------
    # Internal: tool-call batches
    # ------------------------------------------------------------------

    def _process_next(self) -> None:
        """Handle queued tool calls until one needs approval or all are answered."""
        while self._batch:
            fc = self._batch.popleft()
            logger.debug("Model requested tool: %s", fc.name)
            if fc.name in READONLY_TOOLS:
                self._handle_readonly_tool(fc)
            elif fc.name in MUTATING_TOOLS:
                self._handle_mutating_tool(fc)
                return  # paused for approval; resumes via _continue()
            else:
                logger.warning("Unknown tool name: %s", fc.name)
                self._add_result(fc, f"ERROR: Unknown tool '{fc.name}'.")
        self._flush_results()

    def _add_result(self, fc: types.FunctionCall, result: str) -> None:
        self._batch_results.append(
            types.Part(
                function_response=types.FunctionResponse(
                    id=fc.id or "", name=fc.name, response={"output": result}
                )
            )
        )

    def _skip_remaining(self, why: str) -> None:
        """Answer the rest of the batch without running it."""
        while self._batch:
            fc = self._batch.popleft()
            self._add_result(
                fc,
                f"SKIPPED: not run because {why}. Explain this to the user and ask before trying it again.",
            )

    def _continue(self) -> None:
        """Resume the batch after a step finished, or hand results to the model."""
        if self._batch:
            # More calls queued: keep the UI locked and process them off the UI thread.
            self._set_state(OrchestratorState.THINKING)
            threading.Thread(target=self._process_next, daemon=True).start()
        else:
            self._set_state(OrchestratorState.IDLE)
            self._flush_results()

    def _flush_results(self) -> None:
        """Send every answer from this batch back to the model in one turn."""
        if not self._batch_results:
            self._set_state(OrchestratorState.IDLE)
            return
        parts, self._batch_results = self._batch_results, []
        self._history.append(types.Content(role="user", parts=parts))
        self._call_llm()

    # ------------------------------------------------------------------
    # Internal: state management
    # ------------------------------------------------------------------

    def _set_state(self, new_state: OrchestratorState) -> None:
        if self._state != new_state:
            logger.debug("State: %s → %s", self._state, new_state)
            self._state = new_state
            self.state_changed.emit(new_state.value)

    def _emit_error(self, message: str) -> None:
        logger.error(message)
        self._set_state(OrchestratorState.ERROR)
        self.error_occurred.emit(message)
