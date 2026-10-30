# Project Architecture & Rules: Linux AI Assistant

## 1. Overview
A lightweight native Linux desktop GUI assistant built with Python and PySide6 (Qt6). It assists users in installing packages, running diagnostics, and unpacking tarball applications (such as Postman) via an LLM with a strict Human-in-the-Loop (HITL) approval step and a live terminal log stream.

## 2. Tech Stack & Dependencies
- Language: Python 3.11+
- GUI Framework: PySide6 (Qt6) — native widgets only, no webviews, no Electron.
- LLM Providers: Groq (via `openai` SDK, targeting `llama-3.3-70b-versatile`, `qwen-2.5-coder-32b`) and Google GenAI SDK (`google-genai`), targeting `gemini-3.8-flash`.
- Secret Storage: Linux keyring via the Python `keyring` library.
- Threading & Concurrency: All LLM networking and subprocess executions must run on background QThreads using Qt Signal/Slot communication. Never block the Qt GUI thread.

## 3. Directory Layout
linux-ai-helper/
├── AGENTS.md
├── pyproject.toml
├── main.py
├── config/
│   ├── profile_manager.py   # Stores/reads ~/.config/linux-helper/system_profile.json
│   └── keyring_manager.py   # Secure API key handling via system keyring
├── agent/
│   ├── llm_client.py        # google-genai wrapper with tool-calling support
│   ├── prompts.py           # Dynamic system prompts (Beginner, Intermediate, Advanced)
│   ├── tools.py             # Declarations for shell proposals & desktop file creation
│   └── orchestrator.py      # HITL state machine pausing on mutating actions
├── executor/
│   └── pty_runner.py        # Pseudo-terminal runner streaming stdout/stderr
└── ui/
    ├── main_window.py       # Centered reading column & collapsible terminal drawer
    ├── onboarding_modal.py  # First-run modal (system preview, skill level, API key)
    ├── approval_card.py     # Custom card widget for Approve / Edit / Cancel
    └── theme.qss            # Pure neutral dark stylesheet (#080808 canvas, #121212 surfaces, #10b981 emerald)

## 4. Engineering Invariants
1. Human-in-the-Loop: The agent must never execute mutating actions (running shell commands, modifying disk) without UI approval. Every tool call must trigger an approval request signal.
2. Rootless Preference: Default to user-space locations (`~/.local/opt`, `~/.local/bin`, `~/.local/share/applications`) so `sudo` is unnecessary for desktop applications.
3. Skill Adaptation:
   - Beginner: Simple, jargon-free explanations. Raw commands hidden behind a toggle. Log pane collapsed by default.
   - Intermediate: Informative explanation with read-only command preview. Log pane visible.
   - Advanced: Compact technical summary with an editable command input before approval. Log pane visible.
4. FreeDesktop Path Resolution: `.desktop` launcher files must contain absolute paths without unresolved tildes (`~`).
