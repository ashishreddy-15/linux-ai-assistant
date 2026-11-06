"""Multi-provider LLM client adapter for the Linux AI Assistant.

Supports:
  - Google Gemini (via google-genai SDK, model: gemini-3.8-flash) with thinking
    budget capped at 1024 tokens.
  - Groq (via openai SDK, models: llama-3.3-70b-versatile, qwen-2.5-coder-32b)
    for high speed and free-tier accessibility.

Provides:
  - BaseLLMClient: abstract interface with standard history translation and response wrapping.
  - GeminiAgentClient: concrete adapter for Google GenAI.
  - GroqAgentClient: concrete adapter for Groq/OpenAI compatible endpoints.
  - create_llm_client: factory instantiating the requested provider client.
"""

from __future__ import annotations

import json
import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import errors, types

from agent.tools import AGENT_TOOLS, OPENAI_TOOLS, normalize_tool_args
from config.provider_registry import PROVIDER_CONFIGS

logger = logging.getLogger(__name__)

GEMINI_DEFAULT_MODEL = "gemini-3.8-flash"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
OLLAMA_DEFAULT_MODEL = "qwen2.5-coder:7b"
MODEL_ID = GEMINI_DEFAULT_MODEL

FALLBACK_MODELS: Dict[str, List[str]] = {
    k: list(v.get("fallback_models", [])) for k, v in PROVIDER_CONFIGS.items()
}
FALLBACK_MODELS["ollama"] = [
    "qwen2.5-coder:7b",
    "llama3.2:3b",
    "mistral:7b",
]

DEFAULT_TEMPERATURE = 0.1
DEFAULT_MAX_OUTPUT_TOKENS = 8192
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_RETRY_DELAY = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_JITTER_MAX = 0.25


def is_transient_error(exc: Exception) -> bool:
    """Return True if an exception represents a transient/recoverable API error.

    Handles HTTP 503 UNAVAILABLE (e.g. model experiencing high demand) and
    HTTP 429 RESOURCE_EXHAUSTED / Rate Limit across Google GenAI and OpenAI.
    """
    # Check OpenAI errors if available
    try:
        import openai

        if isinstance(
            exc,
            (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError),
        ):
            return True
    except ImportError:
        pass

    # Check google-genai errors
    if isinstance(exc, errors.APIError):
        code = getattr(exc, "code", None)
        if code in (429, 503):
            return True
        status = (getattr(exc, "status", None) or "").upper()
        if status in ("UNAVAILABLE", "RESOURCE_EXHAUSTED"):
            return True

    status_code = getattr(exc, "status_code", None)
    if status_code in (429, 500, 502, 503, 504):
        return True

    # Fallback inspection of error message/repr for wrapped or nested errors
    exc_str = str(exc).upper()
    if "503" in exc_str and "UNAVAILABLE" in exc_str:
        return True
    if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str or "RATE LIMIT" in exc_str:
        return True
    if "HIGH DEMAND" in exc_str or "OVERLOADED" in exc_str:
        return True

    return False


class BaseLLMClient(ABC):
    """Abstract base adapter interface for LLM providers."""

    @abstractmethod
    def generate(
        self,
        history: List[types.Content],
    ) -> types.GenerateContentResponse:
        """Send the full conversation history to the model and return the response."""
        pass

    @abstractmethod
    def update_system_prompt(self, system_prompt: str) -> None:
        """Update system instruction for subsequent generation requests."""
        pass

    # ------------------------------------------------------------------
    # Convenience helpers for the orchestrator
    # ------------------------------------------------------------------

    @staticmethod
    def make_user_turn(text: str) -> types.Content:
        """Wrap a plain-text user message into a Content object."""
        return types.Content(
            role="user",
            parts=[types.Part(text=text)],
        )

    @staticmethod
    def make_tool_result(
        tool_call_id: str,
        function_name: str,
        result: str,
    ) -> types.Content:
        """Wrap a tool execution result into a Content object for the next turn."""
        return types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=tool_call_id,
                        name=function_name,
                        response={"output": result},
                    )
                )
            ],
        )

    @staticmethod
    def extract_function_calls(
        response: types.GenerateContentResponse,
    ) -> list[types.FunctionCall]:
        """Extract all FunctionCall parts from the first candidate's content."""
        if not response.candidates:
            return []
        content = response.candidates[0].content
        if not content or not content.parts:
            return []
        return [
            part.function_call
            for part in content.parts
            if part.function_call is not None
        ]

    @staticmethod
    def extract_text(response: types.GenerateContentResponse) -> str:
        """Extract concatenated text parts from the first candidate."""
        if not response.candidates:
            return ""
        content = response.candidates[0].content
        if not content or not content.parts:
            return ""
        return "".join(part.text for part in content.parts if part.text)


class GeminiAgentClient(BaseLLMClient):
    """Thin, synchronous wrapper around the Google GenAI generate_content API."""

    def __init__(
        self,
        api_key: str,
        system_prompt: str,
        model: str = GEMINI_DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_retry_delay: float = DEFAULT_INITIAL_RETRY_DELAY,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._max_retries = max(0, max_retries)
        self._initial_retry_delay = initial_retry_delay
        self._backoff_factor = backoff_factor
        self._config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            tools=[AGENT_TOOLS],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.AUTO,
                )
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True,  # We handle tool calls manually in the orchestrator
            ),
            thinking_config=types.ThinkingConfig(
                thinking_budget=1024,
            ),
        )

    def update_system_prompt(self, system_prompt: str) -> None:
        """Update system instruction for subsequent generation requests."""
        self._config.system_instruction = system_prompt

    @classmethod
    def from_keyring(
        cls,
        system_prompt: str,
        model: str = GEMINI_DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> "GeminiAgentClient":
        """Create a GeminiAgentClient reading the API key from the keyring."""
        from config.keyring_manager import get_api_key

        key = get_api_key("gemini")
        if not key:
            raise RuntimeError(
                "No Gemini API key found in system keyring. "
                "Please configure one via the onboarding dialog."
            )
        return cls(
            api_key=key,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
        )

    def generate(
        self,
        history: List[types.Content],
    ) -> types.GenerateContentResponse:
        """Send the full conversation history to Gemini and return the response."""
        last_exception: Optional[Exception] = None
        total_attempts = self._max_retries + 1

        for attempt in range(total_attempts):
            try:
                logger.debug(
                    "Sending %d history items to model %s (attempt %d/%d)",
                    len(history),
                    self._model,
                    attempt + 1,
                    total_attempts,
                )
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=history,
                    config=self._config,
                )
                logger.debug(
                    "Model response finish reason: %s",
                    response.candidates[0].finish_reason
                    if getattr(response, "candidates", None)
                    else "N/A",
                )
                return response
            except Exception as exc:
                if not is_transient_error(exc):
                    logger.debug("Non-transient error raised from API (%s), not retrying.", exc)
                    raise

                last_exception = exc
                if attempt < self._max_retries:
                    delay = (self._initial_retry_delay * (self._backoff_factor ** attempt)) + random.uniform(0.0, DEFAULT_JITTER_MAX)
                    logger.warning(
                        "Transient error encountered from Gemini API (%s). Retrying in %.2fs (attempt %d/%d)...",
                        exc,
                        delay,
                        attempt + 1,
                        self._max_retries,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "All %d retries exhausted for transient Gemini API error: %s",
                        self._max_retries,
                        exc,
                    )

        if last_exception is not None:
            raise last_exception
        raise RuntimeError("generate() failed without returning a response or raising an exception.")


class OpenAIAgentClient(BaseLLMClient):
    """Synchronous adapter wrapping OpenAI-compatible chat completions."""

    def __init__(
        self,
        api_key: str,
        system_prompt: str,
        model: str = GROQ_DEFAULT_MODEL,
        base_url: str = "https://api.groq.com/openai/v1",
        default_headers: Optional[Dict[str, str]] = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_retry_delay: float = DEFAULT_INITIAL_RETRY_DELAY,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    ) -> None:
        from openai import OpenAI

        headers: Dict[str, str] = {}
        if default_headers:
            headers.update(default_headers)
        if base_url and "openrouter.ai" in str(base_url):
            headers.setdefault("HTTP-Referer", "https://github.com/linux-ai-assistant")
            headers.setdefault("X-Title", "Linux AI Assistant")

        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            default_headers=headers if headers else None,
        )
        self._system_prompt = system_prompt
        self._model = model
        self._temperature = temperature
        self._max_retries = max(0, max_retries)
        self._initial_retry_delay = initial_retry_delay
        self._backoff_factor = backoff_factor

    def update_system_prompt(self, system_prompt: str) -> None:
        """Update system prompt for subsequent requests."""
        self._system_prompt = system_prompt

    @classmethod
    def from_keyring(
        cls,
        system_prompt: str,
        model: str = GROQ_DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        provider: str = "groq",
    ) -> "OpenAIAgentClient":
        """Create a client reading the API key from the keyring."""
        from config.keyring_manager import get_api_key

        key = get_api_key(provider)
        if not key:
            raise RuntimeError(
                f"No {provider.capitalize()} API key found in system keyring. "
                "Please configure one via the onboarding dialog."
            )
        base_url = (
            PROVIDER_CONFIGS.get(provider, {}).get("base_url")
            or "https://api.groq.com/openai/v1"
        )
        return cls(
            api_key=key,
            system_prompt=system_prompt,
            model=model,
            base_url=base_url,
            temperature=temperature,
        )

    def _history_to_messages(
        self, history: List[types.Content]
    ) -> List[Dict[str, Any]]:
        """Convert standard types.Content history to OpenAI-compatible messages."""
        messages: List[Dict[str, Any]] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})

        last_tool_call_ids: List[str] = []

        for item in history:
            role = item.role
            parts = item.parts or []

            if role == "user":
                for part in parts:
                    if part.function_response is not None:
                        fr = part.function_response
                        resp = fr.response or {}
                        content_str = (
                            resp.get("output", "")
                            if isinstance(resp, dict)
                            else str(resp)
                        )
                        call_id = fr.id or (
                            last_tool_call_ids.pop(0) if last_tool_call_ids else "call_0"
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "name": fr.name,
                                "content": content_str,
                            }
                        )
                    elif part.text is not None:
                        messages.append({"role": "user", "content": part.text})

            elif role == "model":
                text_accum = ""
                tool_calls: List[Dict[str, Any]] = []
                for part in parts:
                    if part.text:
                        text_accum += part.text
                    if part.function_call is not None:
                        fc = part.function_call
                        call_id = fc.id or f"call_{len(tool_calls)}"
                        clean_args = normalize_tool_args(fc.name, fc.args)

                        tool_calls.append(
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": fc.name,
                                    "arguments": json.dumps(clean_args),
                                },
                            }
                        )
                        last_tool_call_ids.append(call_id)

                assistant_msg: Dict[str, Any] = {"role": "assistant"}
                if text_accum:
                    assistant_msg["content"] = text_accum
                elif tool_calls:
                    assistant_msg["content"] = None
                else:
                    assistant_msg["content"] = ""

                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls

                messages.append(assistant_msg)

        return messages

    def generate(
        self,
        history: List[types.Content],
    ) -> types.GenerateContentResponse:
        """Send the full conversation history to Groq and return GenerateContentResponse."""
        messages = self._history_to_messages(history)
        total_attempts = self._max_retries + 1
        last_exception: Optional[Exception] = None

        for attempt in range(total_attempts):
            try:
                logger.debug(
                    "Sending %d messages to Groq model %s (attempt %d/%d)",
                    len(messages),
                    self._model,
                    attempt + 1,
                    total_attempts,
                )
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=OPENAI_TOOLS,
                    tool_choice="auto",
                    temperature=self._temperature,
                )

                choice = response.choices[0]
                msg = choice.message
                parts: List[types.Part] = []

                if msg.content:
                    parts.append(types.Part(text=msg.content))

                if getattr(msg, "tool_calls", None):
                    for tc in msg.tool_calls:
                        func_args = {}
                        if tc.function.arguments:
                            try:
                                func_args = json.loads(tc.function.arguments)
                            except Exception:
                                func_args = {}
                        func_args = normalize_tool_args(tc.function.name, func_args)
                        parts.append(
                            types.Part(
                                function_call=types.FunctionCall(
                                    name=tc.function.name,
                                    args=func_args,
                                    id=tc.id,
                                )
                            )
                        )

                content = types.Content(role="model", parts=parts)
                finish_reason = "STOP"
                if choice.finish_reason:
                    reason_upper = choice.finish_reason.upper()
                    if reason_upper in ("STOP", "MAX_TOKENS", "SAFETY", "RECITATION", "OTHER"):
                        finish_reason = reason_upper

                candidate = types.Candidate(
                    content=content,
                    finish_reason=finish_reason,
                )
                return types.GenerateContentResponse(candidates=[candidate])

            except Exception as exc:
                if not is_transient_error(exc):
                    logger.debug("Non-transient error raised from Groq API (%s), not retrying.", exc)
                    raise

                last_exception = exc
                if attempt < self._max_retries:
                    delay = (self._initial_retry_delay * (self._backoff_factor ** attempt)) + random.uniform(0.0, DEFAULT_JITTER_MAX)
                    logger.warning(
                        "Transient error encountered from Groq API (%s). Retrying in %.2fs (attempt %d/%d)...",
                        exc,
                        delay,
                        attempt + 1,
                        self._max_retries,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "All %d retries exhausted for transient Groq API error: %s",
                        self._max_retries,
                        exc,
                    )

        if last_exception is not None:
            raise last_exception
        raise RuntimeError("generate() failed without returning a response or raising an exception.")


GroqAgentClient = OpenAIAgentClient


def create_llm_client(
    provider: str,
    api_key: str = "",
    model_name: Optional[str] = None,
    system_prompt: str = "",
    **kwargs: Any,
) -> BaseLLMClient:
    """Factory creating an LLM client for the specified provider."""
    prov = (provider or "").strip().lower()

    if "api_key" in kwargs:
        api_key = kwargs["api_key"]
    if "model_name" in kwargs:
        model_name = kwargs["model_name"]
    elif "model" in kwargs:
        model_name = kwargs["model"]

    # Detect if positional arguments were passed as (provider, model_name, api_key)
    if prov in PROVIDER_CONFIGS:
        cfg = PROVIDER_CONFIGS[prov]
        key_prefix = cfg.get("key_prefix")
        if model_name and key_prefix and model_name.startswith(key_prefix) and not (api_key and api_key.startswith(key_prefix)):
            api_key, model_name = model_name, api_key

    if prov == "gemini":
        cfg = PROVIDER_CONFIGS.get("gemini", {})
        model = model_name or cfg.get("default_model", GEMINI_DEFAULT_MODEL)
        return GeminiAgentClient(api_key=api_key, system_prompt=system_prompt, model=model)

    elif prov == "ollama":
        model = model_name or OLLAMA_DEFAULT_MODEL
        dummy_key = api_key or "ollama"
        return OpenAIAgentClient(
            api_key=dummy_key,
            system_prompt=system_prompt,
            model=model,
            base_url="http://127.0.0.1:11434/v1",
        )

    elif prov in PROVIDER_CONFIGS:
        cfg = PROVIDER_CONFIGS[prov]
        model = model_name or cfg.get("default_model", "")
        base_url = cfg.get("base_url") or "https://api.groq.com/openai/v1"
        return OpenAIAgentClient(
            api_key=api_key,
            system_prompt=system_prompt,
            model=model,
            base_url=base_url,
        )

    else:
        valid_list = list(PROVIDER_CONFIGS.keys()) + ["ollama"]
        raise ValueError(
            f"Unknown provider '{provider}'. Must be one of {valid_list}."
        )


def fetch_available_models(provider: str, api_key: str = "") -> List[str]:
    """Dynamically fetch available chat-capable model IDs for the given provider.

    Parameters
    ----------
    provider:
        'groq', 'gemini', 'openrouter', 'cerebras', 'mistral', or 'ollama'
    api_key:
        Provider API key (optional for local providers like Ollama)

    Returns
    -------
    list[str]
        List of available model IDs, falling back to sensible defaults on failure.
    """
    prov = (provider or "").strip().lower()
    cleaned_key = (api_key or "").strip()

    if prov in PROVIDER_CONFIGS:
        fallback = list(PROVIDER_CONFIGS[prov].get("fallback_models", []))
    else:
        fallback = list(FALLBACK_MODELS.get(prov, []))

    if prov != "ollama" and not cleaned_key:
        return fallback

    if prov == "ollama":
        try:
            from openai import OpenAI

            client = OpenAI(
                base_url="http://127.0.0.1:11434/v1",
                api_key=cleaned_key or "ollama",
                timeout=3.0,
            )
            response = client.models.list()
            raw_data = getattr(response, "data", response)
            models_data = (
                list(raw_data) if not isinstance(raw_data, (list, tuple)) else raw_data
            )
            models: List[str] = []
            for m in models_data:
                model_id = getattr(m, "id", None)
                if not model_id and isinstance(m, dict):
                    model_id = m.get("id")
                elif not model_id and isinstance(m, str):
                    model_id = m
                if model_id and isinstance(model_id, str):
                    models.append(model_id)

            if models:
                seen = set()
                deduped = [x for x in models if not (x in seen or seen.add(x))]
                return deduped
            return fallback
        except Exception as exc:
            logger.warning("Failed to fetch Ollama models (%s); using fallbacks.", exc)
            return fallback

    elif prov == "gemini":
        try:
            from google import genai

            client = genai.Client(api_key=cleaned_key)
            models = []
            for m in client.models.list():
                actions = getattr(m, "supported_actions", None) or getattr(
                    m, "supported_generation_methods", None
                )
                if actions is not None:
                    if not any("generatecontent" in str(a).lower() for a in actions):
                        continue
                name = getattr(m, "name", "")
                if not name and isinstance(m, dict):
                    name = m.get("name", "")
                elif not name and isinstance(m, str):
                    name = m
                if not isinstance(name, str) or not name:
                    continue
                if name.startswith("models/"):
                    name = name[len("models/"):]
                models.append(name)

            if models:
                seen = set()
                deduped = [x for x in models if not (x in seen or seen.add(x))]
                return deduped
        except Exception as exc:
            logger.warning("Failed to fetch Gemini models (%s); using fallbacks.", exc)

    elif prov in PROVIDER_CONFIGS:
        try:
            from openai import OpenAI

            cfg = PROVIDER_CONFIGS[prov]
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
            response = client.models.list()
            raw_data = getattr(response, "data", response)
            models_data = (
                list(raw_data) if not isinstance(raw_data, (list, tuple)) else raw_data
            )
            non_chat_keywords = (
                "whisper",
                "audio",
                "tts",
                "embed",
                "moderation",
                "guard",
            )
            models = []
            for m in models_data:
                model_id = getattr(m, "id", None)
                if not model_id and isinstance(m, dict):
                    model_id = m.get("id")
                elif not model_id and isinstance(m, str):
                    model_id = m
                if not model_id or not isinstance(model_id, str):
                    continue
                if any(kw in model_id.lower() for kw in non_chat_keywords):
                    continue
                models.append(model_id)

            # Specific filtering for OpenRouter: prioritize free-tier models ending with :free
            if prov == "openrouter":
                free_models = [m for m in models if m.endswith(":free") or ":free" in m]
                if free_models:
                    models = free_models

            if models:
                seen = set()
                deduped = [x for x in models if not (x in seen or seen.add(x))]
                return deduped
        except Exception as exc:
            logger.warning(
                "Failed to fetch %s models (%s); using fallbacks.",
                prov.capitalize(),
                exc,
            )

    return fallback
