"""Tool declarations for the Linux AI Assistant's Gemini agent.

Defines the three typed FunctionDeclaration objects that the LLM can call:
  - propose_shell_command   – proposes a shell command for HITL approval
  - create_desktop_entry    – proposes a .desktop launcher file
  - read_file_snippet       – reads lines from a local file (read-only)

Also exports AGENT_TOOLS (a single types.Tool bundling all declarations) and
MUTATING_TOOLS (the set of tool names that require HITL approval before
execution).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from google.genai import types

# ---------------------------------------------------------------------------
# Individual FunctionDeclarations
# ---------------------------------------------------------------------------

_propose_shell_command = types.FunctionDeclaration(
    name="propose_shell_command",
    description=(
        "Propose a shell command that will be shown to the user for approval "
        "before execution. Use this for any action that modifies system state: "
        "installing packages, configuring hardware, updating network settings, "
        "modifying dotfiles, writing configs, or restarting services. "
        "Do NOT use this for read-only information gathering — use read_file_snippet "
        "or inspect_manual instead."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "command": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The full shell command string to be executed, e.g. "
                    "'pacman -S --noconfirm neovim', 'systemctl restart NetworkManager', "
                    "or 'ip link show'."
                ),
            ),
            "explanation": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Human-readable explanation of what the command does and why "
                    "it was chosen. Tailor the vocabulary to the user's skill "
                    "level as described in your persona. For beginners, avoid "
                    "jargon. For advanced users, be concise and technical."
                ),
            ),
            "requires_root": types.Schema(
                type=types.Type.BOOLEAN,
                description=(
                    "Set to true only if the command requires elevated privileges "
                    "(pkexec / root). Prefer rootless alternatives whenever possible."
                ),
            ),
            "title": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Short outcome-focused title shown on the approval card, "
                    "e.g. 'Install VLC media player'. Max ~6 words, no command syntax."
                ),
            ),
            "effects": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description=(
                    "2-4 plain-language bullet points describing what will change "
                    "on the user's computer, e.g. 'Adds VLC to your app menu'."
                ),
            ),
        },
        required=["command", "explanation", "requires_root"],
    ),
)

_create_desktop_entry = types.FunctionDeclaration(
    name="create_desktop_entry",
    description=(
        "Propose creating a .desktop launcher file for an application so it "
        "appears in the system application menu. The file will be written to "
        "~/.local/share/applications/<app_name>.desktop. The user must approve "
        "before the file is created."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "app_name": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Display name of the application, e.g. 'My Application'. Used as "
                    "the desktop file Name field and as the filename stem."
                ),
            ),
            "exec_path": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Absolute path to the executable, e.g. "
                    "'/home/user/.local/opt/app/bin/start'."
                ),
            ),
            "icon_path": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Absolute path to the icon file, e.g. "
                    "'/home/user/.local/opt/app/icon.png'. Leave empty string if no icon is available."
                ),
            ),
            "categories": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Semicolon-separated XDG categories, e.g. "
                    "'Utility;Development;'. See "
                    "https://specifications.freedesktop.org/menu-spec/ for valid "
                    "values."
                ),
            ),
            "comment": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Short one-line description of the application shown as a "
                    "tooltip in launchers."
                ),
            ),
        },
        required=["app_name", "exec_path", "icon_path", "categories", "comment"],
    ),
)

_read_file_snippet = types.FunctionDeclaration(
    name="read_file_snippet",
    description=(
        "Read up to max_lines lines from a file on the user's local filesystem. "
        "Use this to inspect config files, logs, or scripts before suggesting "
        "edits. This is a safe, read-only operation — no user approval is "
        "required. Files that may hold secrets (SSH/GPG keys, keyrings, browser "
        "profiles, .env files, shell history) are blocked; never try to read them."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "file_path": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Absolute or ~-expanded path to the file, e.g. "
                    "'/etc/pacman.conf' or '~/.config/hypr/hyprland.conf'."
                ),
            ),
            "max_lines": types.Schema(
                type=types.Type.INTEGER,
                description=(
                    "Maximum number of lines to read from the beginning of the "
                    "file. Defaults to 100. Use a smaller value for large files."
                ),
            ),
        },
        required=["file_path"],
    ),
)

_inspect_manual = types.FunctionDeclaration(
    name="inspect_manual",
    description=(
        "Look up manual pages (man) or command help (--help) for a Linux command or topic. "
        "Use this safe, read-only research tool to discover command syntax, available flags, "
        "or configuration options before proposing shell commands. No user approval required."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "command_or_topic": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The command or topic to look up (e.g. 'nmcli', 'systemctl', "
                    "'ip-link', 'journalctl', 'pacman', 'tar')."
                ),
            ),
        },
        required=["command_or_topic"],
    ),
)

_search_packages = types.FunctionDeclaration(
    name="search_packages",
    description=(
        "Search for installable apps and packages by name. Returns exact Flatpak app IDs "
        "from Flathub and exact system package names. Read-only, no approval needed. "
        "ALWAYS call this before proposing an install, and use only IDs it returns."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="App or package name to search for, e.g. 'vlc', 'postman', 'htop'.",
            ),
        },
        required=["query"],
    ),
)


def _path_tool(name: str, description: str, prop: str, prop_desc: str) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=name, description=description,
        parameters=types.Schema(type=types.Type.OBJECT, properties={
            prop: types.Schema(type=types.Type.STRING, description=prop_desc)}, required=[prop]),
    )


_inspect_download = _path_tool(
    "inspect_download",
    "Look at a downloaded file (RPM, DEB, tarball, zip, AppImage) without running it and say what it is "
    "and how to install it. Read-only. Use when the user mentions a file or an app that must be installed from a download.",
    "name", "File name or part of it (e.g. 'protonvpn', 'antigravity'), or a full path.")
_install_from_file = _path_tool(
    "install_from_file",
    "Install a downloaded file. The app builds the exact steps and the user approves them. "
    "Call inspect_download first and pass the path it gave.",
    "path", "Full path of the file, as returned by inspect_download.")
_uninstall_app = _path_tool(
    "uninstall_app",
    "Remove an app that was installed from a downloaded file by this assistant. The user approves the steps.",
    "name", "App name.")

# ---------------------------------------------------------------------------
# Bundled tool list & metadata
# ---------------------------------------------------------------------------

#: Single Tool object containing all agent function declarations.
AGENT_TOOLS: types.Tool = types.Tool(
    function_declarations=[
        _propose_shell_command,
        _create_desktop_entry,
        _read_file_snippet,
        _inspect_manual,
        _search_packages,
        _inspect_download,
        _install_from_file,
        _uninstall_app,
    ]
)

#: Alias for AGENT_TOOLS providing a standard tool registry handle.
ALL_TOOLS: types.Tool = AGENT_TOOLS

#: Tool names that mutate system state and MUST pause for HITL approval.
MUTATING_TOOLS: frozenset[str] = frozenset({
    "propose_shell_command",
    "create_desktop_entry",
    "install_from_file",
    "uninstall_app",
})

#: Tool names that are safe to execute automatically without user approval.
READONLY_TOOLS: frozenset[str] = frozenset({
    "read_file_snippet",
    "inspect_manual",
    "search_packages",
    "inspect_download",
})

#: Standard OpenAI-formatted tool schemas for Groq / OpenAI-compatible endpoints
OPENAI_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "propose_shell_command",
            "description": (
                "Propose a shell command that will be shown to the user for approval "
                "before execution. Use this for any action that modifies system state: "
                "installing packages, configuring hardware, updating network settings, "
                "modifying dotfiles, writing configs, or restarting services. "
                "Do NOT use this for read-only information gathering — use read_file_snippet "
                "or inspect_manual instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "The full shell command string to be executed, e.g. "
                            "'pacman -S --noconfirm neovim' or 'systemctl restart NetworkManager'."
                        ),
                    },
                    "explanation": {
                        "type": "string",
                        "description": (
                            "Human-readable explanation of what the command does and why "
                            "it was chosen. Tailor the vocabulary to the user's skill level."
                        ),
                    },
                    "requires_root": {
                        "type": "boolean",
                        "description": (
                            "Set to true only if the command requires elevated privileges "
                            "(pkexec / root). Prefer rootless alternatives whenever possible."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "Short outcome-focused title shown on the approval card, "
                            "e.g. 'Install VLC media player'. Max ~6 words, no command syntax."
                        ),
                    },
                    "effects": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "2-4 plain-language bullet points describing what will change "
                            "on the user's computer, e.g. 'Adds VLC to your app menu'."
                        ),
                    },
                },
                "required": ["command", "explanation", "requires_root"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_desktop_entry",
            "description": (
                "Propose creating a .desktop launcher file for an application so it "
                "appears in the system application menu. The file will be written to "
                "~/.local/share/applications/<app_name>.desktop. The user must approve "
                "before the file is created."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Display name of the application.",
                    },
                    "exec_path": {
                        "type": "string",
                        "description": (
                            "Absolute path to the executable, e.g. "
                            "'/home/user/.local/opt/app/bin/start'."
                        ),
                    },
                    "icon_path": {
                        "type": "string",
                        "description": (
                            "Absolute path to the icon file. Leave empty string "
                            "if no icon is available."
                        ),
                    },
                    "categories": {
                        "type": "string",
                        "description": (
                            "Semicolon-separated XDG categories, e.g. 'Development;Network;'."
                        ),
                    },
                    "comment": {
                        "type": "string",
                        "description": "Short one-line description of the application.",
                    },
                },
                "required": ["app_name", "exec_path", "icon_path", "categories", "comment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_snippet",
            "description": (
                "Read up to max_lines lines from a file on the user's local filesystem. "
                "Safe, read-only operation — no user approval required. Files that may "
                "hold secrets (SSH/GPG keys, keyrings, browser profiles, .env files, "
                "shell history) are blocked; never try to read them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute or ~-expanded path to the file.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Alias for file_path.",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum number of lines to read. Defaults to 100.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_manual",
            "description": (
                "Look up manual pages (man) or command help (--help) for a Linux command or topic. "
                "Safe, read-only operation — no user approval required."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command_or_topic": {
                        "type": "string",
                        "description": (
                            "The command or topic to inspect (e.g. 'nmcli', 'systemctl', 'ip-link', 'dnf')."
                        ),
                    },
                    "command": {
                        "type": "string",
                        "description": "Alias for command_or_topic.",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Alias for command_or_topic.",
                    },
                },
                "required": ["command_or_topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_packages",
            "description": (
                "Search for installable apps and packages by name. Returns exact Flatpak app IDs "
                "from Flathub and exact system package names. Read-only, no approval needed. "
                "ALWAYS call this before proposing an install, and use only IDs it returns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "App or package name to search for, e.g. 'vlc', 'postman', 'htop'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def _openai_path_tool(decl: types.FunctionDeclaration, prop: str) -> Dict[str, Any]:
    return {"type": "function", "function": {
        "name": decl.name, "description": decl.description,
        "parameters": {"type": "object", "properties": {prop: {
            "type": "string", "description": decl.parameters.properties[prop].description}}, "required": [prop]}}}


OPENAI_TOOLS.extend([
    _openai_path_tool(_inspect_download, "name"),
    _openai_path_tool(_install_from_file, "path"),
    _openai_path_tool(_uninstall_app, "name"),
])


_PM_ROOT_RE = re.compile(
    r"^(?P<pm>dnf|dnf5|yum|apt|apt-get|zypper|pacman)\s+(?:-\S+\s+)*"
    r"(?P<sub>install|reinstall|remove|erase|purge|autoremove|upgrade|update|"
    r"distro-sync|dist-upgrade|full-upgrade|refresh|in|rm|up|dup|-S\S*|-R\S*|-U\S*)\b"
)


def ensure_root_for_package_managers(command: str) -> tuple[str, bool]:
    """Prefix sudo to system package-manager changes that lack it.

    Small models often write `dnf install -y htop`, which can only fail
    without root. Returns (command, changed). Each `&&`/`;` segment is checked;
    the user still sees the final command on the approval card.
    """
    changed = False
    parts = re.split(r"(\s*(?:&&|\|\||;)\s*)", command)
    for i in range(0, len(parts), 2):
        segment = parts[i]
        stripped = segment.lstrip()
        if _PM_ROOT_RE.match(stripped):
            parts[i] = segment[: len(segment) - len(stripped)] + "sudo " + stripped
            changed = True
    return "".join(parts), changed


def normalize_tool_args(name: str, raw_args: Any) -> Dict[str, Any]:
    """Normalize and alias tool call arguments to conform to canonical schemas.

    Ensures that aliases (e.g. 'command' or 'topic' for 'command_or_topic',
    'path' for 'file_path') are mapped and mandatory schema fields are populated
    with sensible defaults so that OpenAI/Groq tool-call schema validation succeeds.
    """
    args = dict(raw_args or {}) if isinstance(raw_args, dict) else {}
    if name == "inspect_manual":
        val = (
            args.get("command_or_topic")
            or args.get("command")
            or args.get("topic")
            or args.get("query")
            or args.get("name")
        )
        if not val and args:
            for v in args.values():
                if isinstance(v, str) and v.strip():
                    val = v.strip()
                    break
        args["command_or_topic"] = str(val or "bash")

    elif name == "read_file_snippet":
        val = (
            args.get("file_path")
            or args.get("path")
            or args.get("file")
            or args.get("filepath")
            or args.get("target")
        )
        args["file_path"] = str(val or "/dev/null")
        if "max_lines" not in args:
            args["max_lines"] = 100
        else:
            try:
                args["max_lines"] = int(args["max_lines"])
            except (ValueError, TypeError):
                args["max_lines"] = 100

    elif name == "propose_shell_command":
        if "command" not in args or not args["command"]:
            args["command"] = str(args.get("cmd") or args.get("shell_command") or "")
        if "explanation" not in args or not args["explanation"]:
            args["explanation"] = str(
                args.get("desc")
                or args.get("description")
                or args.get("reason")
                or args.get("summary")
                or "Proposed shell command"
            )
        if "requires_root" not in args:
            args["requires_root"] = bool(
                args.get("root") or args.get("sudo") or args.get("is_root") or False
            )
        else:
            args["requires_root"] = bool(args["requires_root"])
        args["command"], needs_root = ensure_root_for_package_managers(str(args["command"]))
        if needs_root:
            args["requires_root"] = True
        args["title"] = str(args.get("title") or "").strip()
        effects = args.get("effects") or []
        if isinstance(effects, str):
            effects = [line.strip(" -•\t") for line in effects.splitlines()]
        args["effects"] = [str(e).strip() for e in effects if str(e).strip()][:6]

    elif name == "search_packages":
        val = (
            args.get("query") or args.get("name") or args.get("package")
            or args.get("app") or args.get("term") or args.get("search")
        )
        args = {"query": str(val or "").strip()}

    elif name in ("inspect_download", "install_from_file", "uninstall_app"):
        key = "path" if name == "install_from_file" else "name"
        val = next((args.get(k) for k in (key, "file_path", "file", "path", "name", "app", "query") if args.get(k)), "")
        args = {key: str(val or "").strip()}

    elif name == "create_desktop_entry":
        args.setdefault("app_name", str(args.get("name") or ""))
        args.setdefault("exec_path", str(args.get("exec") or args.get("binary") or ""))
        args.setdefault("icon_path", str(args.get("icon") or ""))
        args.setdefault("categories", str(args.get("category") or ""))
        args.setdefault("comment", str(args.get("description") or ""))

    return args


# ---------------------------------------------------------------------------
# Tool execution helpers
# ---------------------------------------------------------------------------


# Files the model may never read without the user pasting them in: they can
# hold passwords, private keys, tokens or browsing data that would otherwise
# be sent to a cloud AI provider. Checked against the *resolved* path, so a
# symlink cannot be used to get around it.
_SENSITIVE_DIRS = (
    "~/.ssh", "~/.gnupg", "~/.pki", "~/.password-store", "~/.local/share/keyrings",
    "~/.local/share/kwalletd", "~/.aws", "~/.azure", "~/.config/gcloud", "~/.kube",
    "~/.docker", "~/.mozilla", "~/.thunderbird", "~/.config/google-chrome",
    "~/.config/chromium", "~/.config/BraveSoftware", "~/.config/microsoft-edge",
    "~/.config/vivaldi", "~/.config/opera", "~/.var/app", "~/.config/Signal",
    "~/.config/discord", "~/.config/Slack", "~/.local/share/TelegramDesktop",
    "~/.config/gh", "~/.config/hub", "~/.config/rclone", "~/.config/linux-helper",
    "/etc/ssl/private", "/root",
)
_SENSITIVE_FILES = (
    "~/.netrc", "~/.git-credentials", "~/.pgpass", "~/.my.cnf", "~/.npmrc", "~/.pypirc",
    "~/.bash_history", "~/.zsh_history", "~/.python_history", "~/.local/share/fish/fish_history",
    "/etc/shadow", "/etc/gshadow", "/etc/sudoers",
)
_SENSITIVE_NAME_RE = re.compile(
    r"(^id_(rsa|dsa|ecdsa|ed25519)(?!.*\.pub$))|(\.(pem|key|p12|pfx|kdbx|keystore|jks)$)"
    r"|(^\.env(\..*)?$)|(secret|credential|token|password)",
    re.IGNORECASE,
)
_READ_LIMIT_LINES = 400


def sensitive_path_reason(path: Path) -> str | None:
    """Return why reading *path* (already resolved) is blocked, or None if allowed."""
    resolved = str(path)
    for d in _SENSITIVE_DIRS:
        base = os.path.realpath(os.path.expanduser(d))
        if resolved == base or resolved.startswith(base + os.sep):
            return f"it is inside {d}, which can hold keys, passwords or browsing data"
    for f in _SENSITIVE_FILES:
        if resolved == os.path.realpath(os.path.expanduser(f)):
            return "it can contain passwords or command history"
    if resolved.startswith("/proc/") and resolved.endswith(("/environ", "/cmdline", "/mem")):
        return "process environments can contain secrets"
    if _SENSITIVE_NAME_RE.search(path.name):
        return "its name suggests it holds a key, password or token"
    return None


def execute_read_file_snippet(file_path: str, max_lines: int = 100) -> str:
    """Execute the read_file_snippet tool locally (no approval required).

    Returns up to *max_lines* lines, or a message prefixed with 'ERROR:' when
    the file is missing, not text, or blocked because it may hold secrets.
    """
    expanded = Path(os.path.expanduser(file_path))
    try:
        resolved = expanded.resolve(strict=True)
    except (OSError, RuntimeError):
        return f"ERROR: File not found: {file_path}"
    reason = sensitive_path_reason(resolved)
    if reason:
        return (
            f"ERROR: Reading {file_path} was blocked because {reason}. "
            "Do not try to read it another way. If you need part of it, ask the user to paste "
            "only the relevant, non-secret lines."
        )
    if not resolved.is_file():
        return f"ERROR: Path is not a regular file: {file_path}"
    limit = max(1, min(int(max_lines or 100), _READ_LIMIT_LINES))
    try:
        with resolved.open("rb") as fh:
            head = fh.read(8192)
            if b"\x00" in head:
                return f"ERROR: {file_path} looks like a binary file, not text."
            fh.seek(0)
            lines: List[str] = []
            more = False
            for raw in fh:
                if len(lines) >= limit:
                    more = True
                    break
                lines.append(raw.decode("utf-8", errors="replace").rstrip("\n"))
        return "\n".join(lines) + ("\n[… more lines omitted]" if more else "")
    except OSError as exc:
        return f"ERROR: Could not read file: {exc}"


_MANUAL_TOPIC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,63}$")


def execute_inspect_manual(command_or_topic: str) -> str:
    """Execute the inspect_manual tool locally (no approval required).

    Runs `man -P cat <topic>` or falls back to `<topic> --help` via subprocess (timeout=5s).
    Truncates output to the first 120 lines to keep token usage bounded.
    """
    topic = command_or_topic.strip()
    if not topic:
        return "ERROR: No command or topic specified."
    # One bare name only (e.g. 'nmcli', 'ip-link', 'systemd.unit'): never options
    # or extra arguments, which `man` or the program itself could act on.
    topic = topic.split()[0]
    if not _MANUAL_TOPIC_RE.match(topic):
        return f"ERROR: '{command_or_topic}' is not a valid command or manual page name."

    output = ""
    # 1. Try man -P cat <topic>
    man_path = shutil.which("man")
    if man_path:
        try:
            res = subprocess.run(
                [man_path, "-P", "cat", "--", topic],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                output = res.stdout
        except subprocess.TimeoutExpired:
            return f"ERROR: Inspecting manual for '{topic}' timed out after 5s."
        except Exception:
            pass

    # 2. Fallback to <topic> --help
    if not output.strip():
        parts = [topic]
        if parts:
            cmd = parts[0]
            if shutil.which(cmd):
                try:
                    res = subprocess.run(
                        [cmd, "--help"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    help_text = res.stdout.strip() or res.stderr.strip()
                    if help_text:
                        output = help_text
                except subprocess.TimeoutExpired:
                    return f"ERROR: Help command for '{topic}' timed out after 5s."
                except Exception:
                    pass

    if not output.strip():
        return f"No manual entry or --help documentation available for '{topic}'."

    lines = output.splitlines()
    if len(lines) > 120:
        return "\n".join(lines[:120]) + f"\n\n[… {len(lines) - 120} more lines truncated]"
    return "\n".join(lines)


# Public alias
inspect_manual = execute_inspect_manual


def render_desktop_entry(
    app_name: str,
    exec_path: str,
    icon_path: str,
    categories: str,
    comment: str,
) -> str:
    """Render the .desktop file content for a proposed desktop entry.

    Expands '~' and resolves paths to absolute FreeDesktop compliant paths.
    Does NOT write anything to disk — writing requires HITL approval and
    execution through create_desktop_entry().
    """
    def _one_line(value: str) -> str:
        # A newline in any field would let it inject extra keys (e.g. a second Exec=).
        return " ".join(str(value or "").split())

    app_name, categories, comment = _one_line(app_name), _one_line(categories), _one_line(comment)
    exec_path, icon_path = _one_line(exec_path), _one_line(icon_path)
    resolved_exec = os.path.abspath(os.path.expanduser(exec_path)) if exec_path else ""
    resolved_icon = os.path.abspath(os.path.expanduser(icon_path)) if icon_path else ""
    if " " in resolved_exec:
        resolved_exec = '"' + resolved_exec.replace('\\', '\\\\').replace('"', '\\"') + '"'

    lines = [
        "[Desktop Entry]",
        "Version=1.0",
        "Type=Application",
        f"Name={app_name}",
        f"Comment={comment}",
        f"Exec={resolved_exec}",
    ]
    if resolved_icon:
        lines.append(f"Icon={resolved_icon}")
    lines += [
        f"Categories={categories}",
        "Terminal=false",
        "StartupNotify=true",
    ]
    return "\n".join(lines) + "\n"


def create_desktop_entry(
    app_name: str,
    exec_path: str,
    icon_path: str = "",
    categories: str = "",
    comment: str = "",
    target_dir: str | Path | None = None,
) -> str:
    """Write .desktop file to ~/.local/share/applications/ and update desktop database.

    After writing the .desktop launcher, automatically executes
    `update-desktop-database ~/.local/share/applications` (if the binary exists)
    so new applications appear instantly in desktop application menus and
    launchers without requiring a reboot or logout.
    """
    import os
    import re
    import shutil
    import subprocess
    from pathlib import Path

    resolved_exec = os.path.abspath(os.path.expanduser(exec_path)) if exec_path else ""
    resolved_icon = os.path.abspath(os.path.expanduser(icon_path)) if icon_path else ""

    dest = (
        Path(os.path.expanduser(str(target_dir)))
        if target_dir
        else Path.home() / ".local" / "share" / "applications"
    )
    dest.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "", app_name.lower().replace(" ", "-")) or "app"
    desktop_file = dest / f"{safe_name}.desktop"

    content = render_desktop_entry(
        app_name=app_name,
        exec_path=resolved_exec,
        icon_path=resolved_icon,
        categories=categories,
        comment=comment,
    )
    desktop_file.write_text(content, encoding="utf-8")

    db_updated = False
    updater = shutil.which("update-desktop-database")
    if updater:
        try:
            res = subprocess.run(
                [updater, str(dest)],
                capture_output=True,
                text=True,
                check=False,
            )
            db_updated = (res.returncode == 0)
        except Exception:
            db_updated = False

    status_msg = f"Desktop entry created successfully at {desktop_file}"
    if db_updated:
        status_msg += " and desktop database updated."
    elif updater:
        status_msg += " (warning: update-desktop-database returned non-zero)."
    else:
        status_msg += " (update-desktop-database not found; changes will appear on next session)."
    return status_msg
