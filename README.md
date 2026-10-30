# Linux AI Assistant

A lightweight, native Linux desktop assistant built with **Python** and **PySide6 (Qt6)**. It pairs modern LLM reasoning with a strict **Human-in-the-Loop (HITL)** approval safety architecture, dynamic system environment inspection, and a live streaming pseudo-terminal (PTY) drawer.

Designed specifically for Linux power users and beginners alike, it manages system configuration, software installations, tarball extraction, `.desktop` launcher creation, and system diagnostics without requiring elevated privileges whenever possible.

---

## Key Highlights

- **Strict Human-in-the-Loop (HITL) Invariant**: The assistant **never** executes mutating shell commands or modifies system files without explicit user review and approval via interactive approval cards.
- **Pure Native Qt6 Performance**: Zero Electron, zero webviews, zero heavy browser runtimes. Runs on a fast, resource-efficient PySide6 interface.
- **Obsidian / Raycast Dark Aesthetic**: Crafted with an ultra-clean neutral dark theme (`#080808` canvas, `#121212` surfaces, `#10b981` emerald accents, vector SVG controls) and a centered reading column.
- **Multi-Provider Engine**:
  - **Groq** (via `openai` SDK): Ultra-fast inference with models such as `llama-3.3-70b-versatile`, `qwen-2.5-coder-32b`, and `llama-3.1-8b-instant`.
  - **Google Gemini** (via `google-genai` SDK): Robust reasoning with exponential backoff and automatic retry on transient rate limits / 503s.
- **Dynamic Model Discovery**: Fetches active chat-capable models directly from provider APIs using the stored API key, with intelligent fallbacks.
- **Secure Key Storage**: API keys are securely stored and retrieved using the native Linux system keyring (`secret-service` / FreeDesktop Secret Service API).
- **Rootless Preference**: Defaults to user-space standards (`~/.local/opt`, `~/.local/bin`, `~/.local/share/applications`) so `sudo` is unnecessary for desktop applications.
- **Freeze-Proof Graphical Authentication**: Intercepts `sudo` prompts and routes root actions through `pkexec` or custom desktop askpass handlers, preventing terminal freeze on non-interactive `stdin`.
- **Self-Healing Error Recovery**: Captures command exit codes and `stderr` streams in real time; diagnoses failures and autonomously proposes corrective tool actions.

---

## Architecture & Skill Adaptation

The interface adapts dynamically based on the user's chosen expertise mode:

| Mode | Visual Presentation | Raw Command Preview | Terminal Output |
| :--- | :--- | :--- | :--- |
| **Beginner** | Jargon-free outcome explanation | Collapsible ("Show command ▸") | Collapsed by default |
| **Intermediate** | Educational summary with flag explanations | Read-only syntax-highlighted box | Open and visible |
| **Advanced** | Compact technical synopsis | Fully editable prompt before approving | Open and visible |

---

## User Interface

- **Unified Floating Command Bar**: Minimalist input bar inspired by Linear and Raycast with focus indicators, instant keyboard shortcuts (`Enter` to submit), and a contextual stop button.
- **Collapsible Terminal Drawer**: A vertical drawer containing real-time ANSI-stripped stdout/stderr logs. Auto-expands to 180px during command execution and collapses with a single click.
- **Starter Chips**: One-click prompt suggestions on empty chat states covering Software Setup, Hardware & Drivers, System Settings, Network & DNS, and System Health.
- **Interactive Approval Cards**: Highlighting command risk levels, parameter diffs, and irreversible command warnings.

---

## Directory Layout

```
linux-ai-assistant/
├── main.py                  # Application entry point & stylesheet loader
├── pyproject.toml           # Project dependencies & build metadata
├── AGENTS.md                # Invariant specifications & engineering rules
├── config/
│   ├── keyring_manager.py   # Secure key management via Linux system keyring
│   └── profile_manager.py   # Hardware, OS, package manager, and desktop inspection
├── agent/
│   ├── llm_client.py        # Multi-provider client adapter (Groq & Gemini) with retries
│   ├── orchestrator.py      # HITL state machine coordinating LLM and PTY execution
│   ├── prompts.py           # Domain system prompts tailored to skill levels
│   └── tools.py             # Declarations for shell proposals, desktop files & manual lookup
├── executor/
│   ├── askpass.py           # Graphical sudo / pkexec credential handler
│   └── pty_runner.py        # Pseudo-terminal runner streaming stdout/stderr
├── ui/
│   ├── main_window.py       # Centered chat stream & collapsible terminal drawer
│   ├── onboarding_modal.py  # First-run setup (system preview, provider, API key)
│   ├── approval_card.py     # Adaptive Approve / Edit / Reject card widget
│   ├── theme.qss            # Pure neutral dark stylesheet
│   └── *.svg                # Crisp vector icons (terminal, settings, chevron)
└── tests/                   # Complete test suite (200+ unit & integration tests)
```

---

## Installation & Setup

### 1. Prerequisites

- Linux (Arch, Fedora, Debian/Ubuntu, openSUSE, etc.)
- Python 3.11+
- System Keyring daemon (`gnome-keyring`, `ksecretsservice`, or `keepassxc`)

### 2. Clone the Repository

```bash
git clone git@github.com:ashishreddy-15/linux-ai-assistant.git
cd linux-ai-assistant
```

### 3. Create a Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install Dependencies

```bash
pip install -e .
```

Dependencies include:
- `PySide6>=6.5.0`
- `openai>=1.0.0`
- `google-genai>=0.1.0`
- `keyring>=24.0.0`

---

## Usage

Launch the assistant:

```bash
python3 main.py
```

### First Launch (Onboarding)
On first launch, the Onboarding modal will appear:
1. Review your detected system environment (OS, kernel, package manager, desktop session, RAM, disk space).
2. Choose your preferred **Skill Level** (`Beginner`, `Intermediate`, or `Advanced`).
3. Select your **AI Provider** (`Groq` or `Google Gemini`).
4. Enter your API Key and click **Test Key** (models will be dynamically fetched and populated in the dropdown).
5. Click **Save & Continue**.

### Command Line System Diagnostics
To run a standalone CLI inspection without launching the GUI:

```bash
python3 main.py --inspect
```

---

## Running the Test Suite

The project includes unit and integration tests covering the PTY runner, tool execution, keyring storage, multi-provider LLM adapters, orchestrator state transitions, and UI widgets:

```bash
.venv/bin/python3 -m unittest discover -s tests -v
```

---

## Security & Privacy

- **API Keys**: Stored in the encrypted Linux system keyring via FreeDesktop Secret Service API. Keys are never saved to plain-text configuration files or disk logs.
- **Zero Silent Mutations**: All mutating operations (`propose_shell_command`, `create_desktop_entry`) pause execution and require explicit approval.
- **Sandboxed Execution**: Inspect commands (`inspect_manual`, `read_file_snippet`) run with strict bounds and truncation to prevent token flooding or unauthorized writes.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
