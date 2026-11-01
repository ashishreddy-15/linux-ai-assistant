"""Keyring manager for securely storing and validating API keys across all providers.

Uses the Linux system keyring (e.g., SecretService via gnome-keyring or ksecretscheme)
under the service name 'linux-ai-assistant'.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple
import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from config.provider_registry import PROVIDER_CONFIGS

logger = logging.getLogger(__name__)

SERVICE_NAME = "linux-ai-assistant"
GEMINI_KEY_NAME = "gemini_api_key"
GROQ_KEY_NAME = "groq_api_key"
OLLAMA_KEY_NAME = "ollama_api_key"
KEY_NAME = GEMINI_KEY_NAME


def _key_name_for_provider(provider: str) -> str:
    """Return the keyring item key name for the given provider."""
    p = (provider or "gemini").strip().lower()
    return f"{p}_api_key"


def validate_key_format(provider: str, key: str) -> bool:
    """Validate API key format for a provider against prefix and minimum length rules.

    Parameters
    ----------
    provider:
        Provider name (e.g., 'groq', 'gemini', 'openrouter', 'cerebras', 'mistral')
    key:
        API key string to validate

    Returns
    -------
    bool:
        True if format passes validation rules, False otherwise.
    """
    if not key or not isinstance(key, str):
        return False
    cleaned_key = key.strip()
    p = (provider or "").strip().lower()
    config = PROVIDER_CONFIGS.get(p)
    if not config:
        return False

    key_prefix = config.get("key_prefix")
    if key_prefix and not cleaned_key.startswith(key_prefix):
        return False

    min_key_len = config.get("min_key_len", 0)
    if len(cleaned_key) < min_key_len:
        return False

    return True


def save_api_key(
    arg1: str,
    arg2: Optional[str] = None,
    provider: Optional[str] = None,
) -> None:
    """Save the API key for the specified provider in the system keyring.

    Supports both save_api_key(provider, key) and save_api_key(key, provider).
    """
    if provider is not None:
        target_provider = provider
        target_key = arg1
    elif arg2 is not None:
        p1 = arg1.strip().lower()
        p2 = arg2.strip().lower()
        if p1 in PROVIDER_CONFIGS or p1 == "ollama":
            target_provider = p1
            target_key = arg2
        elif p2 in PROVIDER_CONFIGS or p2 == "ollama":
            target_provider = p2
            target_key = arg1
        else:
            target_key = arg1
            target_provider = arg2
    else:
        target_key = arg1
        target_provider = "gemini"

    key = target_key.strip()
    if not key:
        raise ValueError("API key cannot be empty.")
    key_name = _key_name_for_provider(target_provider)
    keyring.set_password(SERVICE_NAME, key_name, key)


def set_api_key(api_key: str, provider: str = "gemini") -> None:
    """Alias for save_api_key."""
    save_api_key(api_key, provider=provider)


def get_api_key(provider: str = "gemini") -> Optional[str]:
    """Retrieve the API key for the specified provider from the system keyring.

    Returns None if not stored or on retrieval failure.
    """
    key_name = _key_name_for_provider(provider)
    try:
        val = keyring.get_password(SERVICE_NAME, key_name)
        if val is None and SERVICE_NAME != "linux-ai-helper":
            val = keyring.get_password("linux-ai-helper", key_name)
        return val
    except KeyringError as exc:
        logger.warning("Failed to access system keyring: %s", exc)
        return None


def delete_api_key(provider: str = "gemini") -> bool:
    """Delete the API key for the specified provider from the system keyring.

    Returns True if deleted or already absent, False on unexpected keyring errors.
    """
    key_name = _key_name_for_provider(provider)
    try:
        keyring.delete_password(SERVICE_NAME, key_name)
        return True
    except PasswordDeleteError:
        # Not found in primary service name, try legacy fallback if different
        if SERVICE_NAME != "linux-ai-helper":
            try:
                keyring.delete_password("linux-ai-helper", key_name)
            except Exception:
                pass
        return True
    except KeyringError as exc:
        logger.warning("Failed to delete key from system keyring: %s", exc)
        return False


def save_gemini_api_key(api_key: str) -> None:
    """Save Gemini API key."""
    save_api_key(api_key, "gemini")


def get_gemini_api_key() -> Optional[str]:
    """Retrieve Gemini API key."""
    return get_api_key("gemini")


def delete_gemini_api_key() -> bool:
    """Delete Gemini API key."""
    return delete_api_key("gemini")


def save_groq_api_key(api_key: str) -> None:
    """Save Groq API key."""
    save_api_key(api_key, "groq")


def get_groq_api_key() -> Optional[str]:
    """Retrieve Groq API key."""
    return get_api_key("groq")


def delete_groq_api_key() -> bool:
    """Delete Groq API key."""
    return delete_api_key("groq")


def test_api_key(api_key: str) -> Tuple[bool, str]:
    """Execute a minimal request to verify the provided Gemini API key.

    Returns a tuple of (is_valid, status_or_error_message).
    """
    cleaned_key = api_key.strip() if api_key else ""
    if not cleaned_key:
        return False, "API key cannot be empty."

    try:
        from google import genai

        client = genai.Client(api_key=cleaned_key)
        # Verify key validity by querying the models list
        iterator = client.models.list()
        next(iter(iterator), None)
        return True, "API key verified successfully."
    except Exception as exc:
        error_msg = str(exc)
        return False, error_msg


def test_groq_api_key(
    api_key: str, model: Optional[str] = None
) -> Tuple[bool, str]:
    """Execute a request to verify the provided Groq API key.

    Returns a tuple of (is_valid, status_or_error_message).
    """
    cleaned_key = api_key.strip() if api_key else ""
    if not cleaned_key:
        return False, "API key cannot be empty."

    try:
        from openai import OpenAI

        client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=cleaned_key,
        )
        client.models.list()
        target = f" with {model}" if model else ""
        return True, f"API key verified successfully{target}."
    except Exception as exc:
        return False, str(exc)


def check_ollama_status(
    base_url: str = "http://127.0.0.1:11434/v1",
) -> Tuple[bool, str]:
    """Ping the Ollama local daemon /models endpoint using OpenAI client.

    Returns (True, "Ollama is running locally") or
    (False, "Could not connect to Ollama daemon on localhost:11434").
    """
    try:
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key="ollama", timeout=3.0)
        client.models.list()
        return True, "Ollama is running locally"
    except Exception:
        return False, "Could not connect to Ollama daemon on localhost:11434"


def test_provider_api_key(
    provider: str, api_key: str, model: Optional[str] = None
) -> Tuple[bool, str]:
    """Execute a request to verify the provided API key for any supported provider.

    Returns a tuple of (is_valid, status_or_error_message).
    """
    p = (provider or "").strip().lower()
    cleaned_key = api_key.strip() if api_key else ""

    if p == "ollama":
        return check_ollama_status()

    if not cleaned_key:
        return False, "API key cannot be empty."

    if p == "gemini":
        return test_api_key(cleaned_key)

    try:
        from openai import OpenAI

        cfg = PROVIDER_CONFIGS.get(p, {})
        base_url = cfg.get("base_url") or "https://api.groq.com/openai/v1"
        headers = {}
        if "openrouter.ai" in base_url:
            headers["HTTP-Referer"] = "https://github.com/linux-ai-assistant"
            headers["X-Title"] = "Linux AI Assistant"

        client = OpenAI(
            base_url=base_url,
            api_key=cleaned_key,
            default_headers=headers if headers else None,
        )
        client.models.list()
        target = f" with {model}" if model else ""
        return True, f"API key verified successfully{target}."
    except Exception as exc:
        return False, str(exc)


def fetch_available_models(provider: str, api_key: str = "") -> list[str]:
    """Dynamically fetch available chat-capable model IDs for the given provider."""
    from agent.llm_client import fetch_available_models as _fetch

    return _fetch(provider, api_key)
