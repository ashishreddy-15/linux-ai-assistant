# Project Architecture & Rules: Linux AI Assistant

## 1. Overview
A lightweight native Linux desktop GUI assistant built with Python and pywebview (WebKitGTK). It assists users in installing packages, running diagnostics, and unpacking tarball applications (such as Postman) via an LLM with a strict Human-in-the-Loop (HITL) approval step and a live terminal log stream.

## 2. Tech Stack & Dependencies
- Language: Python 3.11+ (Managed with `uv` or standard venv)
- Package & Environment Manager: `uv` (recommended) or `pip`
- GUI Framework: pywebview (WebKitGTK) — native desktop window with a modern, beginner-friendly HTML/CSS/JS frontend.
- LLM Providers: Groq (via `openai` SDK, targeting `llama-3.3-70b-versatile`, `qwen-2.5-coder-32b`) and Google GenAI SDK (`google-genai`), targeting `gemini-3.8-flash`.
- Secret Storage: Linux keyring via the Python `keyring` library.
- Threading & Concurrency: All LLM networking and subprocess executions run on background daemon threads communicating via thread-safe signal/callback dispatch. Never block the UI loop.

## 3. Directory Layout
linux-ai-helper/
├── AGENTS.md
├── pyproject.toml
├── main.py
├── config/
│   ├── profile_manager.py   # Stores/reads ~/.config/linux-helper/system_profile.json
│   └── keyring_manager.py   # Secure API key handling via system keyring
├── agent/
│   ├── events.py            # Pure-Python Signal/Slot event dispatcher
│   ├── llm_client.py        # Multi-provider LLM client wrappers
│   ├── prompts.py           # Dynamic system prompts (Beginner, Intermediate, Advanced)
│   ├── tools.py             # Declarations for shell proposals & desktop file creation
│   └── orchestrator.py      # HITL state machine pausing on mutating actions
├── executor/
│   ├── askpass.py           # Zenity / kdialog graphical credential handler
│   └── pty_runner.py        # Pseudo-terminal runner streaming stdout/stderr
└── ui/
    ├── bridge.py            # Python ↔ pywebview JavaScript API bridge
    ├── index.html           # Single-page UI with chat column & terminal drawer
    ├── app.css              # Obsidian / Raycast dark theme (#09090b canvas, #10b981 emerald)
    └── app.js               # Frontend event dispatch, ANSI parser & markdown renderer

## 4. Engineering Invariants
1. Human-in-the-Loop: The agent must never execute mutating actions (running shell commands, modifying disk) without UI approval. Every tool call must trigger an approval request signal.
2. Rootless Preference: Default to user-space locations (`~/.local/opt`, `~/.local/bin`, `~/.local/share/applications`) so `sudo` is unnecessary for desktop applications.
3. Skill Adaptation:
   - Beginner: Simple, jargon-free explanations. Raw commands hidden behind a toggle. Log pane collapsed by default.
   - Intermediate: Informative explanation with read-only command preview. Log pane visible.
   - Advanced: Compact technical summary with an editable command input before approval. Log pane visible.
4. FreeDesktop Path Resolution: `.desktop` launcher files must contain absolute paths without unresolved tildes (`~`).
