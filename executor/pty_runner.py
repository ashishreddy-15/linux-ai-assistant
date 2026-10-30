"""PTY-based command runner for Linux AI Assistant.

Runs shell commands inside a pseudo-terminal so that interactive programs
(e.g. pacman, apt, pip) believe they are attached to a real TTY.  All I/O
is streamed to the Qt GUI via Signals from a background QThread – the GUI
thread is never blocked.

Public API
----------
CommandRunner(QObject)
    - Signals: output_received(str), command_finished(int, str)
    - Slot:    run(cmd: str)

is_high_risk(cmd: str) -> bool
    Returns True when a command matches known destructive patterns.
"""

from __future__ import annotations

import logging
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Deque

from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)

AUTH_REQUIRED_MESSAGE = (
    "Authentication required: Please use pkexec or run with an askpass agent enabled."
)
_SUDO_PROMPT_PATTERN = re.compile(r"\[sudo\]\s+password\s+for\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------

_HIGH_RISK_PATTERNS: list[re.Pattern[str]] = [
    # Wipe filesystem root
    re.compile(r"rm\s+.*-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/(?:\s|$)"),
    re.compile(r"rm\s+.*-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/(?:\s|$)"),
    # mkfs of any kind
    re.compile(r"\bmkfs(?:\.\w+)?\b"),
    # dd reading from a raw device and writing (potential wipe)
    re.compile(r"\bdd\b.*\bif="),
    # Overwriting fstab
    re.compile(r">\s*/etc/fstab"),
    re.compile(r"\bchmod\s+.*\s+/(?:\s|$)"),
    re.compile(r"\bchown\s+.*\s+/(?:\s|$)"),
    # shred on block devices
    re.compile(r"\bshred\b.*/dev/"),
    # wipefs
    re.compile(r"\bwipefs\b"),
    # fdisk / parted / gdisk on raw devices
    re.compile(r"\b(?:fdisk|parted|gdisk|sgdisk)\b.*/dev/"),
]


def is_high_risk(cmd: str) -> bool:
    """Return True if *cmd* matches any known destructive command pattern.

    Designed to be conservative (may produce false positives for valid but
    unusual commands), never false negatives on the listed patterns.
    """
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(cmd):
            return True
    return False


# ---------------------------------------------------------------------------
# Authentication & Askpass Helpers
# ---------------------------------------------------------------------------


_SUDO_CMD_PATTERN = re.compile(r"(?<![\[\w])\bsudo\b(?!\s+(?:-[a-zA-Z]*A|-A\b))")


def prepare_command(cmd: str) -> str:
    """Prepare a command for non-interactive execution.

    If pkexec is not used and the command contains sudo, automatically
    convert sudo to sudo -A (askpass flag) so that an askpass helper is invoked.
    """
    if not re.search(r"\bpkexec\b", cmd):
        return _SUDO_CMD_PATTERN.sub("sudo -A", cmd)
    return cmd


def detect_askpass() -> str | None:
    """Detect an existing desktop askpass agent on the system.

    Checks SUDO_ASKPASS env var, desktop agents (kssh-askpass, ssh-askpass),
    and standard system paths.
    """
    env_askpass = os.environ.get("SUDO_ASKPASS")
    if env_askpass and os.path.isfile(env_askpass) and os.access(env_askpass, os.X_OK):
        return env_askpass

    for agent in ("kssh-askpass", "ssh-askpass"):
        found = shutil.which(agent)
        if found:
            return found

    for path in (
        "/usr/lib/ssh/x11-ssh-askpass",
        "/usr/lib/openssh/gnome-ssh-askpass",
        "/usr/libexec/openssh/gnome-ssh-askpass",
        "/usr/lib/seahorse/ssh-askpass",
    ):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return None


def ensure_askpass_script() -> str:
    """Ensure a fallback askpass script pointing to a lightweight QInputDialog helper."""
    helper_dir = Path.home() / ".config" / "linux-helper"
    helper_dir.mkdir(parents=True, exist_ok=True)
    askpass_sh = helper_dir / "askpass.sh"

    executor_dir = Path(__file__).resolve().parent
    askpass_py = executor_dir / "askpass.py"

    content = f"""#!/bin/sh
exec "{sys.executable}" "{askpass_py}" "$@"
"""
    if not askpass_sh.exists() or askpass_sh.read_text(encoding="utf-8") != content:
        askpass_sh.write_text(content, encoding="utf-8")
        askpass_sh.chmod(0o755)

    return str(askpass_sh)


def get_askpass_agent() -> str:
    """Return an askpass executable path: detected desktop agent or fallback QInputDialog script."""
    agent = detect_askpass()
    if agent:
        return agent
    return ensure_askpass_script()


# ---------------------------------------------------------------------------
# PTY runner
# ---------------------------------------------------------------------------

_TAIL_LINES = 20       # lines retained for the command_finished summary
_READ_SIZE = 4096      # bytes per pty read
_POLL_TIMEOUT = 0.1    # seconds to wait in select() per iteration


class CommandRunner(QObject):
    """Execute a shell command inside a PTY and stream output to Qt signals.

    Intended usage
    --------------
    Create the runner, optionally move it to a QThread, then call ``run()``.

        thread = QThread()
        runner = CommandRunner()
        runner.moveToThread(thread)
        thread.started.connect(lambda: runner.run("echo hello"))
        runner.command_finished.connect(on_done)
        runner.output_received.connect(on_line)
        thread.start()

    Signals
    -------
    output_received(str)
        Emitted for every chunk of output decoded from the PTY master fd.
        Chunks are raw strings (may contain partial lines or ANSI escapes).
    command_finished(int, str)
        Emitted once after the child process exits.
        Args: (exit_code, last_20_lines_of_output)
    """

    output_received: Signal = Signal(str)
    command_finished: Signal = Signal(int, str)

    def __init__(
        self,
        command: str = "",
        timeout: float | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._command: str = command
        self._timeout: float | None = timeout
        self._stop_requested: bool = False

    def set_command(self, cmd: str, timeout: float | None = None) -> None:
        """Set or update the command to be executed."""
        self._command = cmd
        self._timeout = timeout

    @Slot()
    def start_execution(self) -> None:
        """Slot to start execution on the worker thread where this object lives."""
        self.run(self._command, timeout=self._timeout)

    @Slot(str)
    def run(self, cmd: str, timeout: float | None = None) -> None:
        """Execute *cmd* in a PTY child process, streaming output via signals.

        This method blocks the *calling thread* (should be a worker QThread).
        It must never be called from the GUI thread.
        """
        self._stop_requested = False
        tail: Deque[str] = deque(maxlen=_TAIL_LINES)
        line_buf: str = ""
        prompt_buf: str = ""
        start_time = time.monotonic()

        # Prepare command (convert sudo -> sudo -A if pkexec is not present)
        prepared_cmd = prepare_command(cmd)

        # Set up child environment with SUDO_ASKPASS
        child_env = os.environ.copy()
        if "SUDO_ASKPASS" not in child_env or not child_env["SUDO_ASKPASS"]:
            try:
                child_env["SUDO_ASKPASS"] = get_askpass_agent()
            except Exception as exc:
                logger.warning("Could not setup askpass agent: %s", exc)

        # Open a PTY pair: master_fd is readable by us; slave_fd is the TTY
        # that the child process inherits as stdin/stdout/stderr.
        master_fd, slave_fd = os.openpty()

        try:
            proc = subprocess.Popen(
                prepared_cmd,
                shell=True,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,  # child gets its own process group / session
                env=child_env,
            )
            # Close the slave side in the parent – only the child needs it.
            os.close(slave_fd)
            slave_fd = -1

            exit_code: int = 0

            while True:
                if self._stop_requested:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    exit_code = -1
                    break

                if timeout is not None and (time.monotonic() - start_time) > timeout:
                    timeout_msg = f"\nCommand timed out after {timeout} seconds.\n"
                    self.output_received.emit(timeout_msg)
                    tail.append(f"Command timed out after {timeout} seconds.")
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    exit_code = 124
                    break

                # poll for output with a short timeout so we can check stop flag
                try:
                    readable, _, _ = select.select([master_fd], [], [], _POLL_TIMEOUT)
                except (ValueError, OSError):
                    # master_fd was closed (child exited)
                    break

                if readable:
                    try:
                        chunk = os.read(master_fd, _READ_SIZE)
                    except OSError:
                        # EIO when slave end closes (normal on child exit)
                        break

                    if not chunk:
                        break

                    text = chunk.decode("utf-8", errors="replace")
                    self.output_received.emit(text)

                    # Accumulate lines for the tail buffer
                    line_buf += text
                    while "\n" in line_buf:
                        line, line_buf = line_buf.split("\n", 1)
                        tail.append(line)

                    # Detect if sudo is waiting for password on stdin without interactive input
                    prompt_buf = (prompt_buf + text)[-256:]
                    if _SUDO_PROMPT_PATTERN.search(prompt_buf):
                        fail_msg = f"\n{AUTH_REQUIRED_MESSAGE}\n"
                        self.output_received.emit(fail_msg)
                        tail.append(AUTH_REQUIRED_MESSAGE)
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            proc.wait(timeout=0.5)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            proc.wait()
                        exit_code = 1
                        break

                # Check if child finished (non-blocking)
                ret = proc.poll()
                if ret is not None:
                    # Drain any remaining output before declaring done
                    try:
                        while True:
                            r, _, _ = select.select([master_fd], [], [], 0)
                            if not r:
                                break
                            chunk = os.read(master_fd, _READ_SIZE)
                            if not chunk:
                                break
                            text = chunk.decode("utf-8", errors="replace")
                            self.output_received.emit(text)
                            line_buf += text
                            while "\n" in line_buf:
                                line, line_buf = line_buf.split("\n", 1)
                                tail.append(line)
                    except OSError:
                        pass
                    exit_code = ret
                    break

            # Flush any partial line remaining
            if line_buf.strip():
                tail.append(line_buf)

            summary = "\n".join(tail)
            self.command_finished.emit(exit_code, summary)

        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
            if slave_fd != -1:
                try:
                    os.close(slave_fd)
                except OSError:
                    pass

    @Slot()
    def request_stop(self) -> None:
        """Request that the currently running command be terminated gracefully."""
        self._stop_requested = True
