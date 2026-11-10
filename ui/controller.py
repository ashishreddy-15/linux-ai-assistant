"""Toolkit-independent application controller.

Holds everything the GTK window needs that is *not* widget code: reading and
saving preferences, rebuilding the LLM client, validating keys and gathering
the facts shown on the home screen. Keeping it free of GTK imports means it can
be unit-tested without a display.
"""

from __future__ import annotations

import logging
import os
import pwd
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from agent.llm_client import create_llm_client
from agent.orchestrator import AgentOrchestrator
from agent.prompts import build_compact_system_prompt, build_system_prompt
from config.keyring_manager import (
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
)

logger = logging.getLogger(__name__)

#: Plain-language names for the three skill levels, shown in the UI.
SKILL_LABELS: Dict[str, Tuple[str, str]] = {
    "Beginner": (
        "I'm new to Linux",
        "Plain explanations without jargon. Commands stay hidden unless you ask to see them.",
    ),
    "Intermediate": (
        "I know the basics",
        "Short explanations, and I show you each command before it runs.",
    ),
    "Advanced": (
        "I'm at home in a terminal",
        "Compact technical summaries. You can edit any command before running it.",
    ),
}

OLLAMA_DISPLAY_NAME = "Ollama (on this computer)"


def provider_display_name(provider: str) -> str:
    if provider == "ollama":
        return OLLAMA_DISPLAY_NAME
    cfg = PROVIDER_CONFIGS.get(provider, {})
    return str(cfg.get("display_name", provider)).split(" (")[0]


# Local models known to call tools reliably with thinking turned off, best
# first. Measured on a 16 GB Core Ultra 5 laptop; see README "Local AI".
RECOMMENDED_LOCAL_MODELS = ("qwen3.5:4b", "qwen3:4b-instruct", "qwen2.5:7b", "llama3.2:3b")


def provider_models(provider: str) -> List[str]:
    if provider == "ollama":
        from agent.local_llm import installed_models

        installed = installed_models()
        ranked = [m for m in RECOMMENDED_LOCAL_MODELS if m in installed]
        return ranked + [m for m in installed if m not in ranked] or list(RECOMMENDED_LOCAL_MODELS)
    return list(PROVIDER_CONFIGS.get(provider, {}).get("fallback_models", []))


def user_first_name() -> str:
    """Best-effort first name from the account's GECOS field ('' if unknown)."""
    try:
        gecos = pwd.getpwuid(os.getuid()).pw_gecos or ""
    except (KeyError, OSError):
        return ""
    full = gecos.split(",")[0].strip()
    return full.split()[0] if full else ""


def greeting(now: Optional[datetime] = None, name: Optional[str] = None) -> str:
    hour = (now or datetime.now()).hour
    if 5 <= hour < 12:
        part = "Good morning"
    elif 12 <= hour < 18:
        part = "Good afternoon"
    else:
        part = "Good evening"
    who = user_first_name() if name is None else name
    return f"{part}, {who}" if who else part


class AppController:
    """Preferences, provider configuration and system facts for the UI."""

    def __init__(self, orchestrator: AgentOrchestrator) -> None:
        self.orchestrator = orchestrator

    # ------------------------------------------------------------------
    # Reading state
    # ------------------------------------------------------------------

    def profile(self) -> Dict[str, Any]:
        return load_profile() or get_or_create_profile()

    def skill_level(self) -> str:
        level = self.profile().get("skill_level", DEFAULT_SKILL_LEVEL)
        return level if level in VALID_SKILL_LEVELS else DEFAULT_SKILL_LEVEL

    def theme(self) -> str:
        theme = self.profile().get("theme", DEFAULT_THEME)
        return theme if theme in VALID_THEMES else DEFAULT_THEME

    def active_provider(self) -> str:
        provider = self.profile().get("active_provider", "groq")
        return provider if provider in VALID_PROVIDERS else "groq"

    def has_usable_ai(self) -> bool:
        provider = self.active_provider()
        if provider == "ollama":
            from agent.local_llm import installed_models, server_version

            return server_version() is not None and bool(installed_models())
        return bool(get_api_key(provider))

    def needs_onboarding(self) -> bool:
        profile = self.profile()
        return not profile.get("onboarding_complete") and not self.has_usable_ai()

    def system_facts(self) -> Dict[str, Any]:
        """Facts for the home-screen status strip."""
        profile = self.profile()
        session = get_dynamic_session_info()
        os_name = profile.get("os_name") or "Linux"
        version = str(profile.get("os_version") or "").split(" ")[0]
        desktop = session.get("xdg_current_desktop") or "unknown"
        session_type = (session.get("xdg_session_type") or "").capitalize()
        total = session.get("home_disk_total_bytes") or 0
        used = session.get("home_disk_used_bytes") or 0
        provider = self.active_provider()
        return {
            "os": f"{os_name} {version}".strip(),
            "desktop": f"{desktop} · {session_type}" if session_type and desktop != "unknown" else desktop,
            "disk_free_gb": float(session.get("home_disk_free_gb") or 0.0),
            "disk_used_fraction": (used / total) if total else 0.0,
            "provider": provider,
            "provider_name": provider_display_name(provider),
            "ai_ready": self.has_usable_ai(),
        }

    def status_line(self) -> str:
        """One lowercase line for the home screen, e.g.
        'fedora 45 · hyprland · 444 GB free · groq'."""
        profile = self.profile()
        session = get_dynamic_session_info()
        distro = str(profile.get("os_id") or profile.get("os_name") or "linux").lower()
        version = str(profile.get("os_version") or "").split(" ")[0]
        desktop = str(session.get("xdg_current_desktop") or "").split(":")[-1].lower()
        free = float(session.get("home_disk_free_gb") or 0.0)
        parts = [f"{distro} {version}".strip()]
        if desktop and desktop != "unknown":
            parts.append(desktop)
        if free:
            parts.append(f"{free:.0f} GB free")
        if self.has_usable_ai():
            parts.append(provider_display_name(self.active_provider()).split()[0].lower())
        return " · ".join(parts)

    def system_prompt(self, profile: Optional[Dict[str, Any]] = None, level: Optional[str] = None) -> str:
        """Full prompt for cloud models; a compact one for small local models."""
        profile = profile or self.profile()
        level = level or self.skill_level()
        build = build_compact_system_prompt if self.active_provider() == "ollama" else build_system_prompt
        return build(profile, get_dynamic_session_info(), level)

    # ------------------------------------------------------------------
    # Writing state
    # ------------------------------------------------------------------

    def _update_profile(self, **changes: Any) -> Dict[str, Any]:
        profile = self.profile()
        profile.update(changes)
        save_profile(profile)
        return profile

    def rebuild_client(self) -> None:
        """Recreate the LLM client from the saved profile and keyring."""
        profile = self.profile()
        provider = self.active_provider()
        api_key = get_api_key(provider) or ("ollama" if provider == "ollama" else "")
        prompt = self.system_prompt(profile)
        client = create_llm_client(
            provider=provider,
            api_key=api_key or "unconfigured",
            model_name=profile.get("model_name"),
            system_prompt=prompt,
        )
        self.orchestrator.set_client(client)

    def set_skill_level(self, level: str) -> None:
        if level not in VALID_SKILL_LEVELS:
            raise ValueError(f"Unknown skill level: {level}")
        profile = self._update_profile(skill_level=level)
        self.orchestrator.update_system_prompt(self.system_prompt(profile, level))

    def set_theme(self, theme: str) -> None:
        if theme not in VALID_THEMES:
            raise ValueError(f"Unknown theme: {theme}")
        self._update_profile(theme=theme)

    def set_provider(
        self, provider: str, model_name: Optional[str] = None, api_key: Optional[str] = None
    ) -> None:
        if provider not in VALID_PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        if api_key and api_key.strip():
            save_api_key(api_key.strip(), provider=provider)
        models = provider_models(provider)
        model = model_name or (models[0] if models else None)
        self._update_profile(active_provider=provider, model_name=model)
        self.rebuild_client()

    def complete_onboarding(self) -> None:
        self._update_profile(onboarding_complete=True)

    def refresh_system_profile(self) -> None:
        current = self.profile()
        refreshed = get_or_create_profile(force_refresh=True, preserve_preferences=True)
        if current.get("onboarding_complete"):
            refreshed["onboarding_complete"] = True
            save_profile(refreshed)
        self.orchestrator.update_system_prompt(self.system_prompt(refreshed))

    # ------------------------------------------------------------------
    # Network checks (call from a worker thread – these block)
    # ------------------------------------------------------------------

    def check_local_model(self, model: Optional[str] = None) -> Tuple[bool, str, List[str]]:
        """Is Ollama running, is a model downloaded, and can it use tools?"""
        from agent.local_llm import (
            OllamaAgentClient,
            installed_models,
            model_supports_tools,
            probe_tool_calling,
            server_version,
        )

        if server_version() is None:
            return False, "Ollama isn't running.", []
        models = provider_models("ollama")
        installed = installed_models()
        if not installed:
            return False, f"Ollama is running but has no models. Download one with: ollama pull {RECOMMENDED_LOCAL_MODELS[0]}", []
        model = model or models[0]
        if model_supports_tools(model) is False:
            return False, f"“{model}” can't use tools, so it can't run commands. Pick another model.", models
        ok, message = probe_tool_calling(OllamaAgentClient(system_prompt="", model=model))
        return ok, (f"{model} is ready" if ok else message), models

    def test_key(self, provider: str, api_key: str) -> Tuple[bool, str, List[str]]:
        """Return (ok, message, models)."""
        if provider == "ollama":
            return self.check_local_model()
        if not validate_key_format(provider, api_key):
            cfg = PROVIDER_CONFIGS.get(provider, {})
            prefix = cfg.get("key_prefix")
            hint = f" It should start with “{prefix}”." if prefix else ""
            return False, f"That doesn't look like a {provider_display_name(provider)} key.{hint}", []
        ok, message = test_provider_api_key(provider, api_key)
        if not ok:
            return False, "The key was rejected. Check that you copied all of it.", []
        return True, "Key works", fetch_available_models(provider, api_key)
