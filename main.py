"""Main entry point for Linux AI Assistant.

Launches the native pywebview desktop application with the dark slate theme,
initializes the system profile, and drives the HITL orchestrator.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def _ensure_linux_system_site_packages() -> None:
    """Ensure system-level PyGObject (gi) and WebKitGTK bindings are accessible in virtualenvs."""
    try:
        import gi  # noqa: F401
    except ImportError:
        candidates = [
            f"/usr/lib64/python{sys.version_info.major}.{sys.version_info.minor}/site-packages",
            f"/usr/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages",
            "/usr/lib64/python3/dist-packages",
            "/usr/lib/python3/dist-packages",
        ]
        for candidate in candidates:
            if candidate not in sys.path and Path(candidate).is_dir():
                sys.path.append(candidate)


_ensure_linux_system_site_packages()

import webview

from agent.llm_client import create_llm_client
from agent.orchestrator import AgentOrchestrator
from agent.prompts import build_system_prompt
from config.keyring_manager import get_api_key, test_api_key, test_groq_api_key
from config.profile_manager import (
    DEFAULT_PROFILE_PATH,
    DEFAULT_SKILL_LEVEL,
    get_dynamic_session_info,
    get_or_create_profile,
    load_profile,
)
from ui.bridge import AssistantBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("linux-ai-assistant")


def run_phase1_inspection(force_refresh: bool = False, custom_path: Path | None = None) -> None:
    """Run CLI system inspection, write system_profile.json, and print the output."""
    target_path = custom_path or DEFAULT_PROFILE_PATH
    print("=" * 60)
    print("Linux AI Assistant - System Diagnostic Inspection")
    print("=" * 60)
    print(f"Target Configuration Path: {target_path}")

    profile = get_or_create_profile(path=target_path, force_refresh=force_refresh)
    print("\n--- [System Profile (Static)] ---")
    print(json.dumps(profile, indent=2))

    session_info = get_dynamic_session_info()
    print("\n--- [Dynamic Session & Storage Info] ---")
    print(json.dumps(session_info, indent=2))

    gemini_key = get_api_key("gemini")
    groq_key = get_api_key("groq")

    def _mask(k: str | None) -> str:
        if not k or not k.strip():
            return "Not configured"
        return f"{k[:4]}...{k[-4:]}" if len(k) > 8 else "Set (hidden)"

    active_prov = profile.get("active_provider", "groq")
    active_model = profile.get("model_name", "llama-3.3-70b-versatile")

    print("\n--- [Active Configuration & Keyring Status] ---")
    print("Service: linux-ai-helper")
    print(f"Active Provider: {active_prov} ({active_model})")
    print(f"Groq Key: {_mask(groq_key)}")
    print(f"Gemini Key: {_mask(gemini_key)}")
    print("=" * 60)


def launch_gui(force_onboarding: bool = False) -> int:
    """Initialize and run the pywebview desktop GUI."""
    profile = get_or_create_profile()
    dynamic = get_dynamic_session_info()
    skill_level = profile.get("skill_level", DEFAULT_SKILL_LEVEL)
    provider = profile.get("active_provider", "groq")
    model_name = profile.get("model_name")

    api_key = get_api_key(provider)
    if provider == "ollama":
        api_key = api_key or "ollama"

    system_prompt = build_system_prompt(profile, dynamic, skill_level)

    client = create_llm_client(
        provider=provider,
        api_key=api_key or "unconfigured",
        model_name=model_name,
        system_prompt=system_prompt,
    )
    orchestrator = AgentOrchestrator(client)
    bridge = AssistantBridge(orchestrator=orchestrator)

    html_path = Path(__file__).parent / "ui" / "index.html"

    window = webview.create_window(
        title="Linux AI Assistant",
        url=str(html_path.resolve()),
        js_api=bridge,
        width=920,
        height=740,
        min_size=(680, 500),
        background_color="#09090b",
    )
    bridge.set_window(window)

    webview.start(gui="gtk")
    return 0


def main() -> int:
    """Parse CLI arguments and dispatch to diagnostic or GUI."""
    parser = argparse.ArgumentParser(
        description="Linux AI Assistant - Native Desktop Assistant & Diagnostics"
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Run system diagnostic inspection and print JSON without opening GUI",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-inspection and overwrite existing system_profile.json",
    )
    parser.add_argument(
        "--test-key",
        type=str,
        help="Test a candidate Gemini API key against Google GenAI API",
    )
    parser.add_argument(
        "--test-groq-key",
        type=str,
        help="Test a candidate Groq API key against Groq API",
    )
    parser.add_argument(
        "--onboarding",
        action="store_true",
        help="Force the onboarding setup modal to display on launch",
    )
    parser.add_argument(
        "--config-path",
        type=str,
        default=None,
        help="Optional custom path for system_profile.json",
    )

    args = parser.parse_args()

    if args.test_key:
        print(f"Testing Gemini API key: {args.test_key[:4]}...")
        valid, message = test_api_key(args.test_key)
        print(f"Result: {'SUCCESS' if valid else 'FAILED'} - {message}")
        return 0 if valid else 1

    if args.test_groq_key:
        print(f"Testing Groq API key: {args.test_groq_key[:4]}...")
        valid, message = test_groq_api_key(args.test_groq_key)
        print(f"Result: {'SUCCESS' if valid else 'FAILED'} - {message}")
        return 0 if valid else 1

    if args.inspect:
        custom_path = Path(args.config_path) if args.config_path else None
        run_phase1_inspection(force_refresh=args.refresh, custom_path=custom_path)
        return 0

    return launch_gui(force_onboarding=args.onboarding)


if __name__ == "__main__":
    sys.exit(main())
