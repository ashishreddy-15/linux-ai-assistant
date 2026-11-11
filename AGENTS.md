# Project Architecture & Rules: Linux AI Assistant

## 1. Overview
A lightweight native Linux desktop GUI assistant built with Python and GTK4 / libadwaita (PyGObject). It assists users in installing packages, running diagnostics, and unpacking tarball applications (such as Postman) via an LLM with a strict Human-in-the-Loop (HITL) approval step and a live terminal log stream.

## 2. Tech Stack & Dependencies
- Language: Python 3.11+ (Managed with `uv` or standard venv)
- Package & Environment Manager: `uv` (recommended) or `pip`
- GUI Framework: GTK4 + libadwaita via system PyGObject (no embedded browser). Styling lives in `ui/style.css`; light/dark follows the system via `Adw.StyleManager`.
- LLM Providers: **Local first** — Ollama via its native `/api/chat` (`agent/local_llm.py`, default model `qwen3.5:4b`, 8k context, streaming, compact prompt). Cloud providers are optional: Groq and other OpenAI-compatible APIs (`openai` SDK) and Google GenAI (`google-genai`).
- Local-model rules: keep the compact prompt stable between turns (Ollama reuses its cache), never split a tool call from its result when trimming history, and verify a model really emits tool calls (`probe_tool_calling`) before relying on it.
- Secret Storage: Linux keyring via the Python `keyring` library.
- Threading & Concurrency: All LLM networking and subprocess executions run on background daemon threads communicating via thread-safe signal/callback dispatch. Never block the UI loop.

## 3. Directory Layout
linux-ai-helper/
├── AGENTS.md
├── pyproject.toml
├── main.py
├── scripts/
│   ├── install-ollama-user.sh  # No-sudo Ollama install used by in-app setup
│   └── eval_local.py           # 18-task evaluation of local models
├── config/
│   ├── profile_manager.py   # Stores/reads ~/.config/linux-helper/system_profile.json
│   └── keyring_manager.py   # Secure API key handling via system keyring
├── agent/
│   ├── events.py            # Pure-Python Signal/Slot event dispatcher
│   ├── llm_client.py        # Multi-provider LLM client wrappers
│   ├── local_llm.py         # Ollama native client: context size, streaming, text tool-call recovery
│   ├── local_setup.py       # Detect Ollama/models/GPU; install, start and pull steps as approval cards
│   ├── installer.py         # Install from downloaded files (rpm/deb/tar/zip/AppImage): inspect, plan, records, removal
│   ├── builder.py           # Build source projects into ~/.local, install manifests, find_package_for
│   ├── downloads.py         # Verified download sources (vendor pages, GitHub releases, user-pasted links)
│   ├── package_search.py    # search_packages tool: Flathub API + dnf/apt/pacman/zypper (read-only)
│   ├── prompts.py           # Dynamic system prompts (Beginner, Intermediate, Advanced)
│   ├── tools.py             # Declarations for shell proposals & desktop file creation
│   └── orchestrator.py      # HITL state machine pausing on mutating actions
├── executor/
│   ├── askpass.py           # Zenity / kdialog graphical credential handler
│   └── pty_runner.py        # Pseudo-terminal runner streaming stdout/stderr
└── ui/
    ├── app.py               # Adw.Application: CSS, icons, light/dark sync
    ├── window.py            # Main window; orchestrator signals → GLib.idle_add
    ├── home.py / chat.py    # Home screen and conversation view
    ├── action_card.py       # HITL approval → running → result card
    ├── onboarding.py        # First-run setup
    ├── local_setup.py       # Local AI setup view/dialog (runs the setup cards)
    ├── preferences.py       # Adw.PreferencesDialog
    ├── controller.py        # GTK-free settings/provider logic (unit-tested)
    ├── widgets.py           # Shared widgets (progress ring, terminal view, Markdown view)
    ├── ansi.py / markup.py  # ANSI parser; escaped Markdown → Pango
    └── style.css            # GTK CSS: shape/type only; ALL colours come from libadwaita variables

## 4. Engineering Invariants
1. Human-in-the-Loop: The agent must never execute mutating actions (running shell commands, modifying disk) without UI approval. Every tool call must trigger an approval request signal.
2. Rootless Preference: Default to user-space locations (`~/.local/opt`, `~/.local/bin`, `~/.local/share/applications`) so `sudo` is unnecessary for desktop applications.
3. Skill Adaptation:
   - Beginner: Simple, jargon-free explanations. Raw commands hidden behind a toggle. Log pane collapsed by default.
   - Intermediate: Informative explanation with read-only command preview. Log pane visible.
   - Advanced: Compact technical summary with an editable command input before approval. Log pane visible.
4. FreeDesktop Path Resolution: `.desktop` launcher files must contain absolute paths without unresolved tildes (`~`).
5. UI Thread Safety: Orchestrator signals fire on worker threads; UI code must only touch widgets via `GLib.idle_add` (see `on_main_thread` in `ui/window.py`).
6. No Markup Injection: Never pass model or command output to `set_markup` unescaped. Use `label()`/`set_text`, or `ui.markup.markdown_to_blocks`, which escapes first.
8. Special installs: the model never writes extract/link/.desktop commands. `agent/installer.py` inspects files without running them, builds one script per approval card, and records installs in `~/.local/share/linux-assistant/installs.json` so `uninstall_app` can reverse them. Archive member names are untrusted: quote everything, refuse escaping paths. The model never supplies a download URL: `agent/downloads.py` resolves app names to verified sources, and file-install tools are shown to the model only when the chat is about a download (`wants_file_tools`). Hand-written unpack/install commands for known files are swapped for the real plan. Source builds install only under `~/.local`, record a before/after manifest for removal, and stop after 3 failed attempts (`MAX_BUILD_ATTEMPTS`).
7. Theme Colours: Never hard-code UI colours in `ui/style.css` or widgets. Use libadwaita variables (`--accent-color`, `--card-bg-color`, `--warning-color`, …) or `color-mix()` of them, so the app follows each user's light/dark setting, accent colour and custom theme.
