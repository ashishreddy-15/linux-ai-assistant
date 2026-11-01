"""Centralized registry for supported LLM providers and configuration metadata.

Contains metadata for the 5 free/cloud providers (Groq, Gemini, OpenRouter, Cerebras, Mistral)
including API base URLs, key validation constraints, fallback models, and user guidance steps.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class ProviderConfig(TypedDict, total=False):
    display_name: str
    base_url: Optional[str]
    key_prefix: Optional[str]
    min_key_len: int
    default_model: str
    fallback_models: List[str]
    portal_url: str
    guide_title: str
    guide_steps: List[str]


PROVIDER_CONFIGS: Dict[str, ProviderConfig] = {
    "groq": {
        "display_name": "Groq Cloud (Ultra-Fast Free Tier)",
        "base_url": "https://api.groq.com/openai/v1",
        "key_prefix": "gsk_",
        "min_key_len": 24,
        "default_model": "llama-3.3-70b-versatile",
        "fallback_models": ["llama-3.3-70b-versatile", "qwen-2.5-32b", "llama-3.1-8b-instant"],
        "portal_url": "https://console.groq.com/keys",
        "guide_title": "Groq Cloud API Key (Free)",
        "guide_steps": [
            "Sign in at console.groq.com with GitHub or Google (no credit card).",
            "Click 'Create API Key' in the left API Keys sidebar.",
            "Copy the 'gsk_...' key and paste below.",
        ],
    },
    "gemini": {
        "display_name": "Google Gemini (AI Studio Free Tier)",
        "base_url": None,  # Handled by google-genai SDK
        "key_prefix": "AIzaSy",
        "min_key_len": 28,
        "default_model": "gemini-2.5-flash",
        "fallback_models": ["gemini-2.5-flash", "gemini-1.5-flash"],
        "portal_url": "https://aistudio.google.com/apikey",
        "guide_title": "Google AI Studio API Key (Free)",
        "guide_steps": [
            "Sign in at aistudio.google.com/apikey with your Google account.",
            "Click 'Create API Key' -> 'Create key in new project'.",
            "Copy the 'AIzaSy...' key and paste below.",
        ],
    },
    "openrouter": {
        "display_name": "OpenRouter (Free Open-Weight Models)",
        "base_url": "https://openrouter.ai/api/v1",
        "key_prefix": "sk-or-v1-",
        "min_key_len": 32,
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        "fallback_models": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "qwen/qwen-2.5-coder-32b-instruct:free",
            "mistralai/mistral-small-24b-instruct-2501:free",
            "openrouter/free",
        ],
        "portal_url": "https://openrouter.ai/keys",
        "guide_title": "OpenRouter Free Key",
        "guide_steps": [
            "Sign in at openrouter.ai/keys with GitHub or Google.",
            "Click 'Create Key', choose a name, and leave credit limit unset.",
            "Copy the 'sk-or-v1-...' key. Compatible with all ':free' models.",
        ],
    },
    "cerebras": {
        "display_name": "Cerebras Inference (Ultra-Fast Free Llama 3.3)",
        "base_url": "https://api.cerebras.ai/v1",
        "key_prefix": "csk-",
        "min_key_len": 24,
        "default_model": "llama-3.3-70b",
        "fallback_models": ["llama-3.3-70b", "llama3.1-8b"],
        "portal_url": "https://cloud.cerebras.ai/",
        "guide_title": "Cerebras Cloud API Key (Free)",
        "guide_steps": [
            "Sign in at cloud.cerebras.ai with Google or GitHub.",
            "Go to 'API Keys' and click 'Create API Key'.",
            "Copy the 'csk-...' key and paste below.",
        ],
    },
    "mistral": {
        "display_name": "Mistral AI (La Plateforme Free Experiment)",
        "base_url": "https://api.mistral.ai/v1",
        "key_prefix": None,
        "min_key_len": 32,
        "default_model": "mistral-small-latest",
        "fallback_models": ["mistral-small-latest", "codestral-latest", "mistral-large-latest"],
        "portal_url": "https://console.mistral.ai/api-keys/",
        "guide_title": "Mistral La Plateforme Key (Free Experiment)",
        "guide_steps": [
            "Sign in at console.mistral.ai (free experiment tier, no credit card).",
            "Go to 'API Keys' and click 'Create new key'.",
            "Copy the key and paste below.",
        ],
    },
}

