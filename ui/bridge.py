"""Bridge between pywebview JavaScript frontend and Python backend services."""

from __future__ import annotations

import json
import logging
import webbrowser
from typing import Any, Dict, List, Optional

from agent.llm_client import create_llm_client
from agent.orchestrator import AgentOrchestrator
from agent.prompts import build_system_prompt
from config.keyring_manager import (
    check_ollama_status,
    delete_api_key,
    fetch_available_models,
    get_api_key,
    save_api_key,
    test_provider_api_key,
    validate_key_format,
)
from config.profile_manager import (
    DEFAULT_SKILL_LEVEL,
    DEFAULT_THEME,
    PROVIDER_CONFIGS,
    VALID_PROVIDERS,
    VALID_SKILL_LEVELS,
    VALID_THEMES,
    get_dynamic_session_info,
    get_or_create_profile,
    load_profile,
    save_profile,
    update_provider_settings,
    update_skill_level,
    update_theme,
)

logger = logging.getLogger(__name__)


class AssistantBridge:
    """Exposes Python backend methods and streams events to the webview window."""

    def __init__(self, orchestrator: AgentOrchestrator, window: Any = None) -> None:
        self._orchestrator = orchestrator
        self._window = window
        self._setup_event_listeners()

    def set_window(self, window: Any) -> None:
        """Assign the pywebview window handle once created."""
        self._window = window

    def _setup_event_listeners(self) -> None:
        """Connect orchestrator signals to webview JS event dispatcher."""
        self._orchestrator.response_chunk.connect(
            lambda chunk: self._dispatch("response_chunk", chunk)
        )
        self._orchestrator.response_ready.connect(
            lambda text: self._dispatch("response_ready", text)
        )
        self._orchestrator.approval_requested.connect(
            lambda payload: self._dispatch("approval_requested", payload)
        )
        self._orchestrator.command_output.connect(
            lambda line: self._dispatch("command_output", line)
        )
        self._orchestrator.command_finished.connect(
            lambda code, summary: self._dispatch(
                "command_finished", {"exit_code": code, "summary": summary}
            )
        )
        self._orchestrator.error_occurred.connect(
            lambda err: self._dispatch("error_occurred", err)
        )
        self._orchestrator.state_changed.connect(
            lambda state: self._dispatch("state_changed", state)
        )

    def _dispatch(self, event_name: str, payload: Any) -> None:
        """Evaluate JS callback in the webview window."""
        if self._window is not None:
            try:
                js_str = f"if (window.__onAssistantEvent) window.__onAssistantEvent({json.dumps(event_name)}, {json.dumps(payload)});"
                self._window.evaluate_js(js_str)
            except Exception as exc:
                logger.debug("Failed to dispatch event to webview: %s", exc)

    # --- UI Client API ---

    def submit_prompt(self, text: str) -> bool:
        """Send a user message to the orchestrator."""
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        self._orchestrator.send_message(cleaned)
        return True

    def approve_command(self, command: str) -> None:
        """Approve proposed shell command or action."""
        self._orchestrator.handle_user_approval(command)

    def reject_command(self, reason: str = "") -> None:
        """Reject proposed command with optional reason."""
        self._orchestrator.handle_user_rejection(reason)

    def abort_execution(self) -> None:
        """Abort currently running command."""
        self._orchestrator.abort()

    def clear_chat(self) -> None:
        """Clear conversation history."""
        self._orchestrator.clear_history()

    def get_initial_state(self) -> Dict[str, Any]:
        """Fetch system profile, dynamic session info, and provider config."""
        profile = load_profile() or get_or_create_profile()
        session = get_dynamic_session_info()
        active_provider = profile.get("active_provider", "groq")
        saved_key = get_api_key(active_provider) or ""

        # Mask key for preview
        masked_key = ""
        if saved_key:
            if len(saved_key) > 8:
                masked_key = f"{saved_key[:4]}...{saved_key[-4:]}"
            else:
                masked_key = "********"

        providers_meta = {}
        for p_key, p_cfg in PROVIDER_CONFIGS.items():
            providers_meta[p_key] = {
                "display_name": p_cfg.get("display_name", p_key),
                "portal_url": p_cfg.get("portal_url", ""),
                "default_model": p_cfg.get("default_model", ""),
                "models": p_cfg.get("models", []),
                "is_local": p_cfg.get("is_local", False),
                "key_prefix": p_cfg.get("key_prefix", ""),
                "min_key_len": p_cfg.get("min_key_len", 0),
                "has_key": bool(get_api_key(p_key)),
            }

        return {
            "profile": profile,
            "session": session,
            "state": self._orchestrator.state.value,
            "providers": providers_meta,
            "active_provider": active_provider,
            "current_model": profile.get("model_name", ""),
            "has_active_key": bool(saved_key),
            "masked_key": masked_key,
            "skill_level": profile.get("skill_level", DEFAULT_SKILL_LEVEL),
            "theme": profile.get("theme", DEFAULT_THEME),
        }

    def refresh_system_profile(self) -> Dict[str, Any]:
        """Force re-detection of OS release, package managers, and dynamic storage."""
        profile = get_or_create_profile(force_refresh=True, preserve_preferences=True)
        session = get_dynamic_session_info()

        # Update active system prompt on orchestrator with new OS details
        skill_level = profile.get("skill_level", DEFAULT_SKILL_LEVEL)
        sys_prompt = build_system_prompt(profile, session, skill_level)
        self._orchestrator.update_system_prompt(sys_prompt)

        return {
            "status": "success",
            "profile": profile,
            "session": session,
        }

    def save_settings(
        self,
        skill_level: str,
        provider: str,
        model_name: str,
        api_key: Optional[str] = None,
        theme: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update system settings, save API key, and reconfigure the active LLM client."""
        profile = load_profile() or get_or_create_profile()

        # Update skill level
        valid_level = (
            skill_level
            if skill_level in VALID_SKILL_LEVELS
            else profile.get("skill_level", DEFAULT_SKILL_LEVEL)
        )
        update_skill_level(valid_level)

        # Update theme if specified
        if theme:
            valid_theme = theme.lower() if theme.lower() in VALID_THEMES else profile.get("theme", DEFAULT_THEME)
            update_theme(valid_theme)

        # Update provider and model
        valid_provider = (
            provider.lower()
            if provider.lower() in VALID_PROVIDERS
            else profile.get("active_provider", "groq")
        )
        update_provider_settings(valid_provider, model_name=model_name)

        # Save key if provided
        if api_key and api_key.strip():
            save_api_key(api_key.strip(), provider=valid_provider)

        # Re-fetch profile and rebuild system prompt
        updated_profile = load_profile() or get_or_create_profile()
        dynamic_session = get_dynamic_session_info()
        sys_prompt = build_system_prompt(updated_profile, dynamic_session, valid_level)

        effective_key = get_api_key(valid_provider) or ""
        new_client = create_llm_client(
            provider=valid_provider,
            api_key=effective_key,
            model_name=model_name,
            system_prompt=sys_prompt,
        )
        self._orchestrator.set_client(new_client)

        return {"status": "success", "profile": updated_profile}

    def set_theme(self, theme: str) -> Dict[str, Any]:
        """Directly update and persist UI theme preference ('dark', 'light', 'system')."""
        valid_theme = theme.lower() if theme.lower() in VALID_THEMES else DEFAULT_THEME
        updated = update_theme(valid_theme)
        return {"status": "success", "theme": valid_theme, "profile": updated}

    def test_key(self, provider: str, api_key: str) -> Dict[str, Any]:
        """Validate API key format and test credentials with provider API."""
        is_valid_format = validate_key_format(provider, api_key)
        if not is_valid_format and provider != "ollama":
            return {
                "valid": False,
                "message": "Key format is invalid (check prefix or minimum length).",
                "format_valid": False,
                "models": [],
            }

        success, msg = test_provider_api_key(provider, api_key)
        models = []
        if success:
            models = fetch_available_models(provider, api_key)

        return {
            "valid": success,
            "message": msg,
            "format_valid": is_valid_format,
            "models": models,
        }

    def check_ollama(self) -> Dict[str, Any]:
        """Check if local Ollama daemon is reachable."""
        running = check_ollama_status()
        models = []
        if running:
            models = fetch_available_models("ollama")
        return {"running": running, "models": models}

    def open_external_url(self, url: str) -> bool:
        """Open a link in the user's default browser."""
        try:
            return webbrowser.open(url)
        except Exception as exc:
            logger.warning("Failed to open URL %s: %s", url, exc)
            return False
