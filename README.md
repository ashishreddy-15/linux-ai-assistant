# Linux AI Assistant

A lightweight, native Linux desktop assistant built with **Python** and **GTK4 / libadwaita**. It pairs modern LLM reasoning with a strict **Human-in-the-Loop (HITL)** approval safety architecture, dynamic system environment inspection, and live command output streamed into each action card.

Designed specifically for Linux power users and beginners alike, it manages system configuration, software installations, tarball extraction, `.desktop` launcher creation, and system diagnostics without requiring elevated privileges whenever possible.

---

## Key Highlights

- **Strict Human-in-the-Loop (HITL) Invariant**: The assistant **never** executes mutating shell commands or modifies system files without explicit user review and approval via interactive approval cards.
- **Lightweight Native Desktop Experience**: Zero Electron, zero embedded browser. A native **GTK4 + libadwaita** app drawn by the toolkit your desktop already has, so it starts fast and uses little memory.
- **Follows Your Theme**: Uses only libadwaita's colour variables, so it matches each user's light/dark setting, accent colour and custom GTK theme.
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

A quiet, native design that takes **all of its colours from your desktop theme**: light or dark, your GNOME accent colour, and any custom libadwaita theme (`~/.config/gtk-4.0/gtk.css`) are followed automatically.

- **Launcher-style home**: a one-line system summary (`fedora 45 · gnome · 212 GB free · groq`), a `❯` prompt, and keyboard-navigable suggestions (↑↓ to choose, ↵ to start).
- **Action cards**: every proposed change is a flat card with a plain-language title, a short “what will happen” list and a status word (*no password needed*, *needs your password* in the theme's warning colour, *high risk* in its error colour). Approving turns the card into a live progress view; finished steps collapse to a single `✓ … · done · 14s` row.
- **Extra safety for high-risk steps**: the run button stays locked until you tick “I understand this can't be undone”. If an expert edits a command into something destructive, the card switches to high risk before running it.
- **Adapts to skill level**: *New to Linux* hides commands behind “show command”; *I know the basics* shows them read-only with the output visible; *At home in a terminal* gets an editable command (Ctrl+Enter to run).
- **Two-step setup**: experience level, then a free cloud AI (Groq, with live key checking) or a private local one (Ollama).
- **Preferences dialog**: experience level, appearance (follow system / light / dark), AI provider/model/key (stored in the system keyring) and system info.

## Directory Layout

```
linux-ai-assistant/
├── main.py                     # Application entry point (CLI flags, launches the GTK app)
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
│   ├── app.py                  # Adw.Application: stylesheet, icons, light/dark sync
│   ├── window.py               # Main window; marshals orchestrator events onto the GTK loop
│   ├── home.py                 # Launcher-style prompt and suggestions
│   ├── chat.py                 # Conversation view and friendly error rows
│   ├── action_card.py          # Approval → running → result card
│   ├── onboarding.py           # First-run setup
│   ├── preferences.py          # Adw.PreferencesDialog
│   ├── controller.py           # GTK-free settings/provider logic (unit-tested)
│   ├── widgets.py              # Shared widgets: terminal view, Markdown view, key hints
│   ├── ansi.py / markup.py     # ANSI parser and escaped Markdown → Pango converter
│   ├── style.css               # GTK CSS: shape and type only, colours from the theme
│   └── icons/                  # App symbolic icon
└── tests/                      # Full test suite (227 unit & integration tests)
```

---

## Installation & Setup

### 1. Prerequisites

- Linux (Arch, Fedora, Debian/Ubuntu, openSUSE, etc.)
- Python 3.11+ (or let `uv` manage Python versions automatically)
- System GTK4 + libadwaita (1.7 or newer) with Python bindings (preinstalled on most current desktops):
  - **Fedora**: `sudo dnf install python3-gobject gtk4 libadwaita`
  - **Debian / Ubuntu**: `sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1`
  - **Arch Linux**: `sudo pacman -S python-gobject gtk4 libadwaita`
- The virtual environment's Python must be the same version as the system Python, because PyGObject is loaded from the system site-packages.
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
- `openai>=1.0.0`
- `google-genai>=0.1.0`
- `keyring>=24.0.0`

---

## Local AI (recommended)

The assistant is built to run on a **local model through [Ollama](https://ollama.com)**: free, private (nothing leaves your computer) and offline. Cloud providers remain available in Preferences.

### Set it up from the app

Choose **Private, on this computer** during setup (or Preferences → AI → Set up…). The app checks what is missing and proposes each step as an approval card, exactly like any other command:

1. **Install Ollama** into `~/.local/opt/ollama` with `scripts/install-ollama-user.sh` (official GitHub release, SHA-256 verified, CUDA libraries skipped without an NVIDIA GPU; no sudo).
2. **Start it**: a systemd *user* service (`~/.config/systemd/user/ollama.service`, only reachable from this computer, integrated GPU enabled), or a background process when there is no user systemd.
3. **Download a model** sized to your memory: `qwen3.5:2b` (≤ 8 GB), `qwen3.5:4b` (≤ 24 GB), `qwen3.5:9b` (more).

Finally it confirms the model really calls tools before switching the assistant to it.

### Install Ollama manually (without sudo)

```bash
# Download the official release, check it against the release's sha256sum.txt, then:
mkdir -p ~/.local/opt/ollama ~/.local/bin
tar --zstd -xf ollama-linux-amd64.tar.zst -C ~/.local/opt/ollama \
    --exclude='lib/ollama/cuda_v12*' --exclude='lib/ollama/cuda_v13*'   # skip CUDA if you have no NVIDIA GPU
ln -sfn ~/.local/opt/ollama/bin/ollama ~/.local/bin/ollama

# Start it. OLLAMA_IGPU_ENABLE=1 lets it use an integrated Intel/AMD GPU via Vulkan.
OLLAMA_IGPU_ENABLE=1 ollama serve &
ollama pull qwen3.5:4b
```

Then choose **Private, on this computer** during setup (or in Preferences → AI).

### Model choice

The default is **`qwen3.5:4b`** (3.3 GB): it uses tools reliably with thinking turned off. Avoid models that can't switch thinking off (for example the plain `qwen3:4b` tag, which writes its reasoning into every reply); the app checks that a model really calls tools before using it.

### What makes small models work here

- The native Ollama API is used so the context window can be raised to 8k tokens (Ollama's default silently cuts long conversations).
- A compact system prompt (~400 tokens) and trimmed tool schemas; old turns are dropped without separating a tool call from its result.
- Replies stream into the chat; tool calls that a model writes as text are recovered; a "question" proposed as an `echo` command is shown as a normal question.
- 8 CPU threads instead of Ollama's default (which can be 2 on hybrid laptop chips).

### Real package names, not guesses

Small models invent IDs (for example `org.postman.Postman` instead of Flathub's real `com.getpostman.Postman`). Before proposing an install the model calls the read-only **`search_packages`** tool, which looks up exact Flatpak IDs through Flathub's public search API and exact package names through `dnf`, `apt`, `pacman` or `zypper`. It also notices when Flathub isn't set up yet and gives the model the command to add it. The search term is sent to flathub.org (as `flatpak search` would do); nothing else leaves the computer.

### Choosing and managing local models

**Local models…** in the main menu (or `Ctrl+M`, or **Models…** in Preferences) opens a list of models you can try: Qwen 3.5 (2B, 4B, 9B), Phi-4 mini, Gemma 4, Granite 4.1 and Llama 3.2. Each row shows its size; **Download** shows live progress (and keeps going if you close the window), **Cancel** stops it and a later download resumes where it left off, **Use** makes the assistant use that model, **Run** loads it into memory ahead of time so the first answer is quick, **Stop** frees that memory, and the bin removes it from your disk. Only the Qwen models have been evaluated with this assistant's task set; the others are marked as not tested.

### Installing from a downloaded file

Some apps aren't in any store: an RPM from the vendor, a `.tar.gz` that needs unpacking and a menu entry, an AppImage, a cursor or icon theme. Say "install the Proton VPN rpm I downloaded", or use **Install a downloaded file…** on the home screen. The assistant inspects the file without running it (RPM signature and repository check, archive layout, safe paths, the real executable and icon), then shows one approval card with every step. Apps unpack into `~/.local/opt` and need no password; RPM/DEB installs use your package manager and ask for it. If the archive ships its own installer (`install.sh`, `.run`), you see the start of the script on the card before anything runs. Launcher-script apps, archives that lost their executable bits, bundled `.desktop` files (name and category), a missing-library warning and the Electron sandbox flag are handled too. If the file isn't there yet, the assistant can fetch it from a verified source (Proton's repository, Antigravity's download page, GitHub releases) or from a link you pasted: it never downloads a URL the AI made up. The card shows the domain and size, and GitHub's SHA-256 is checked. Source code is built too (CMake, Meson, Rust, Go, Autotools, plain Makefiles): the assistant installs any missing build tools, builds, and installs into `~/.local` without a password, and keeps a list of exactly which files were installed. If the build stops on a missing header or library, `find_package_for` looks up the package that provides it (Fedora/dnf); after three failed attempts it stops and explains. Building runs the project's own scripts, so the card warns you to build only code you trust. Installs are recorded, so "remove Antigravity" can undo them. The model only picks the tool; the commands come from code (`agent/installer.py`).

Read-only lookups run without approval, so the chat shows each one as a quiet line (`↳ searched packages for “postman”`, `↳ read ~/.bashrc`).

### Evaluating models

```bash
uv run python scripts/eval_local.py qwen3.5:4b [another-model …]
```

Runs 12 typical tasks through the app's real client and prompt, executing read-only lookups for real and checking (never running) the proposed commands, including exact app IDs.

### Measured speed (Core Ultra 5 125U, 16 GB, `qwen3.5:4b`)

| | Reading the prompt | Writing |
|---|---|---|
| CPU, Ollama default (2 threads) | — | ~4 tok/s |
| CPU, 8 threads | 36 tok/s | 10 tok/s |
| Integrated GPU (Vulkan) | 107 tok/s | 6.6 tok/s |

On the GPU a typical step takes **~25–30 s** for the first message of a conversation; follow-ups are faster because Ollama reuses what it already read (9 s → 2.4 s of prompt reading in a measured second turn).

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
uv run --group dev pytest

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
