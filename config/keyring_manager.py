"""Keyring manager for securely storing and validating API keys (Gemini and Groq).

Uses the Linux system keyring (e.g., SecretService via gnome-keyring or ksecretscheme)
under the service name 'linux-ai-helper'.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple
import keyring
from keyring.errors import KeyringError, PasswordDeleteError

logger = logging.getLogger(__name__)

SERVICE_NAME = "linux-ai-helper"
GEMINI_KEY_NAME = "gemini_api_key"
GROQ_KEY_NAME = "groq_api_key"
KEY_NAME = GEMINI_KEY_NAME


def _key_name_for_provider(provider: str) -> str:
    """Return the keyring item key name for the given provider."""
    return GROQ_KEY_NAME if provider.strip().lower() == "groq" else GEMINI_KEY_NAME


def save_api_key(api_key: str, provider: str = "gemini") -> None:
    """Save the API key for the specified provider in the system keyring."""
    key = api_key.strip()
    if not key:
        raise ValueError("API key cannot be empty.")
    key_name = _key_name_for_provider(provider)
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
        return keyring.get_password(SERVICE_NAME, key_name)
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
        # Not found in keyring, treat as successfully removed
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


def fetch_available_models(provider: str, api_key: str) -> list[str]:
    """Dynamically fetch available chat-capable model IDs for the given provider."""
    from agent.llm_client import fetch_available_models as _fetch

    return _fetch(provider, api_key)
