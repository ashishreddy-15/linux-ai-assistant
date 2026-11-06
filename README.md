# Linux AI Assistant

A lightweight, native Linux desktop assistant built with **Python** and **pywebview (WebKitGTK)**. It pairs modern LLM reasoning with a strict **Human-in-the-Loop (HITL)** approval safety architecture, dynamic system environment inspection, and a live streaming pseudo-terminal (PTY) drawer.

Designed specifically for Linux power users and beginners alike, it manages system configuration, software installations, tarball extraction, `.desktop` launcher creation, and system diagnostics without requiring elevated privileges whenever possible.

---

## Key Highlights

- **Strict Human-in-the-Loop (HITL) Invariant**: The assistant **never** executes mutating shell commands or modifies system files without explicit user review and approval via interactive approval cards.
- **Lightweight Native Desktop Experience**: Zero Electron, zero heavy Qt framework downloads. Uses the Linux system's native **WebKitGTK 4.1** runtime via `pywebview`.
- **Obsidian / Raycast Dark Aesthetic**: Crafted with an ultra-clean neutral dark theme (`#09090b` canvas, `#121215` surfaces, `#10b981` emerald accents) and a centered reading column.
- **Comprehensive Multi-Provider Engine**:
  - **Groq Cloud** (Ultra-Fast Free Tier): High-speed inference using `llama-3.3-70b-versatile`, `qwen-2.5-32b`, and `llama-3.1-8b-instant`.
  - **Google Gemini** (AI Studio Free Tier): Robust reasoning with `gemini-2.5-flash` and `gemini-1.5-flash` with exponential backoff on transient errors.
  - **OpenRouter** (Free Open-Weight Models): Access to free models including `meta-llama/llama-3.3-70b-instruct:free`, `qwen/qwen-2.5-coder-32b-instruct:free`, and `mistralai/mistral-small-24b-instruct-2501:free`.
  - **Cerebras Inference** (Ultra-Fast Free Llama 3.3): Instant inference with `llama-3.3-70b` and `llama3.1-8b`.
  - **Mistral AI** (La Plateforme Free Experiment): Access to `mistral-small-latest`, `codestral-latest`, and `mistral-large-latest`.
  - **Ollama** (Local / Offline Private Execution): Complete offline privacy powered by local models such as `qwen2.5-coder:7b`, `llama3.2:3b`, and `mistral:7b` via `http://127.0.0.1:11434`.
- **Integrated Free API Key Helper**: Built-in interactive guidance card with direct portal links (`[ ↗ Get Free Key at <domain> ]`), numbered registration steps, live key format validation badges (`✓ Valid Key Format`), and provider auto-detection on paste.
- **Dynamic Model Discovery**: Fetches active chat-capable models dynamically from provider APIs or local Ollama instances, with fallback models when offline.
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
- **Quick-Start Guidance Card**: Modern Raycast-styled surface card below the Provider dropdown guiding users through free key registration, live credential testing, and local daemon status checks.

---

## Directory Layout

```
linux-ai-assistant/
├── main.py                     # Application entry point & pywebview window loader
├── pyproject.toml              # Project dependencies & build metadata
├── AGENTS.md                   # Invariant specifications & engineering rules
├── config/
│   ├── provider_registry.py    # Centralized registry for all cloud & local providers
│   ├── keyring_manager.py      # Secure key management via Linux system keyring
│   └── profile_manager.py      # Hardware, OS, package manager, and desktop inspection
├── agent/
│   ├── events.py               # Pure-Python Signal/Slot event dispatcher
│   ├── llm_client.py           # Unified client architecture (OpenAIAgentClient & GeminiAgentClient)
│   ├── orchestrator.py         # HITL state machine coordinating LLM and PTY execution
│   ├── prompts.py              # Domain system prompts tailored to skill levels
│   └── tools.py                # Declarations for shell proposals, desktop files & manual lookup
├── executor/
│   ├── askpass.py              # Graphical sudo / pkexec credential handler (zenity / kdialog)
│   └── pty_runner.py           # Pseudo-terminal runner streaming stdout/stderr
├── ui/
│   ├── bridge.py               # Python ↔ JavaScript bidirectional API bridge
│   ├── index.html              # Desktop client window layout with chat & terminal drawer
│   ├── app.css                 # Clean neutral dark stylesheet
│   └── app.js                  # Frontend event dispatch, ANSI parser & markdown renderer
└── tests/                      # Full test suite (210+ unit & integration tests)
```

---

## Installation & Setup

### 1. Prerequisites

- Linux (Arch, Fedora, Debian/Ubuntu, openSUSE, etc.)
- Python 3.11+ (or let `uv` manage Python versions automatically)
- System GTK & WebKitGTK runtime (already installed on most desktop distributions):
  - **Fedora**: `sudo dnf install python3-gobject webkit2gtk4.1`
  - **Debian / Ubuntu**: `sudo apt install python3-gi python3-gi-cairo gir1.2-webkit2-4.1`
  - **Arch Linux**: `sudo pacman -S python-gobject webkit2gtk-4.1`
- [`uv`](https://github.com/astral-sh/uv) (recommended) or Python `venv` + `pip`
- System Keyring daemon (`gnome-keyring`, `ksecretsservice`, or `keepassxc`)
- *(Optional)* [Ollama](https://ollama.com/) for fully local, offline inference

### 2. Clone the Repository

```bash
git clone git@github.com:ashishreddy-15/linux-ai-assistant.git
cd linux-ai-assistant
```

### 3. Environment & Dependency Setup

#### Using `uv` (Fast & Recommended)

Synchronize the virtual environment and install all dependencies in one command:

```bash
uv sync
```

#### Alternatively, Using Traditional `venv`

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Dependencies include:
- `pywebview>=6.2.1`
- `openai>=1.0.0`
- `google-genai>=0.1.0`
- `keyring>=24.0.0`

---

## Usage

Launch the assistant:

```bash
# Using uv:
uv run python main.py

# Or directly if .venv is activated:
python3 main.py
```

### First Launch (Onboarding & Configuration)
On first launch, or when clicking **Settings**:
1. Review your detected system environment (OS, kernel, package manager, desktop session, RAM, disk space).
2. Choose your preferred **Skill Level** (`Beginner`, `Intermediate`, or `Advanced`).
3. Select your **Provider**:
   - **Cloud Providers**: Groq, Google Gemini, OpenRouter, Cerebras, or Mistral.
     - View step-by-step instructions and click `[ ↗ Get Free Key at <domain> ]` to acquire an API key without documentation hunting.
     - Paste your API key; valid formats display an emerald `✓ Valid Key Format` badge.
     - Click **Test Key** to verify credentials and fetch available models dynamically.
   - **Local LLM**: Select **Ollama (Local / Offline)**.
     - Click **Test Connection** to verify your local daemon status (`http://127.0.0.1:11434`) and populate locally pulled models.
4. Click **Save & Continue**.

### Command Line System Diagnostics
To run a standalone CLI inspection without launching the GUI:

```bash
python3 main.py --inspect
```

---

## Running the Test Suite

The project includes extensive test coverage across the PTY runner, tool execution, keyring storage, multi-provider LLM adapters, orchestrator state transitions, and UI assets:

```bash
# Using uv:
uv run python -m unittest discover -s tests -v

# Or directly with an activated .venv:
python3 -m unittest discover -s tests -v
```

---

## Security & Privacy

- **Encrypted Key Storage**: API keys are stored in the encrypted Linux system keyring via FreeDesktop Secret Service API. Keys are never saved to plain-text configuration files or disk logs.
- **Local & Offline Option**: With Ollama support, you can run prompts and diagnostic workflows 100% locally on your machine with zero external network traffic.
- **Zero Silent Mutations**: All mutating operations (`propose_shell_command`, `create_desktop_entry`) pause execution and require explicit approval.
- **Sandboxed Inspection**: Inspect commands (`inspect_manual`, `read_file_snippet`) run with strict bounds and truncation to prevent token flooding or unauthorized writes.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
