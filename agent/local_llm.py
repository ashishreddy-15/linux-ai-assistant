"""Local model support via Ollama's native API.

Why not Ollama's OpenAI-compatible endpoint? Because it can't set the context
window. Ollama's default is small, and when the system prompt, tool schemas
and conversation outgrow it, the start of the conversation is cut off without
any error and the model "forgets" its instructions. The native /api/chat
endpoint lets us:

  * set ``num_ctx`` and ``keep_alive`` (keep the model loaded between turns),
  * stream tokens to the UI (essential on a laptop CPU),
  * get tool-call arguments as JSON objects.

Small models also often write a tool call as text instead of using the tool
interface; ``parse_text_tool_calls`` recovers those so the turn isn't lost.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from google.genai import types

from agent.llm_client import BaseLLMClient
from agent.tools import OPENAI_TOOLS, normalize_tool_args

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_NUM_CTX = 8192


def default_num_thread() -> int:
    """Threads for CPU work. Ollama's own default can be as low as 2 on hybrid
    (P-core + E-core) laptop chips; measured on a 12-core Core Ultra 5, 8 threads
    were ~2.5x faster than the default and slightly faster than all 12."""
    return max(1, min(8, (os.cpu_count() or 4) - 2))

# Budget for the conversation itself: context minus the reply and a margin
# for tool schemas the runtime adds.
_REPLY_RESERVE_TOKENS = 1536
_TOOL_RESULT_MAX_CHARS = 3000

TOOL_NAMES = {t["function"]["name"] for t in OPENAI_TOOLS}


class LocalModelError(RuntimeError):
    """A problem the user can fix (Ollama not running, model not downloaded…)."""


# ---------------------------------------------------------------------------
# Compact tool schemas
# ---------------------------------------------------------------------------

_SHORT_DESCRIPTIONS = {
    "propose_shell_command": "Propose ONE shell command. The user must approve it before it runs.",
    "create_desktop_entry": "Propose adding an app launcher to the user's app menu.",
    "read_file_snippet": "Read the first lines of a text file (read-only, no approval needed).",
    "inspect_manual": "Show the man page or --help for one command name (read-only).",
    "search_packages": "Find exact app IDs and package names (read-only). Use before every install.",
}


def compact_tools() -> List[Dict[str, Any]]:
    """OPENAI_TOOLS with short descriptions and without alias parameters.

    Every token of the schema is re-read by the model on each turn, and
    duplicate "alias" parameters confuse small models.
    """
    out = []
    for tool in OPENAI_TOOLS:
        fn = tool["function"]
        params = json.loads(json.dumps(fn["parameters"]))
        required = set(params.get("required", []))
        props = params.get("properties", {})
        for name in list(props):
            desc = props[name].get("description", "")
            if desc.startswith("Alias for"):
                del props[name]
                continue
            props[name]["description"] = desc.split(". ")[0].split(", e.g.")[0][:120]
        params["required"] = [r for r in params.get("required", []) if r in props and r in required]
        out.append({
            "type": "function",
            "function": {
                "name": fn["name"],
                "description": _SHORT_DESCRIPTIONS.get(fn["name"], fn["description"][:160]),
                "parameters": params,
            },
        })
    return out


# ---------------------------------------------------------------------------
# History conversion and trimming
# ---------------------------------------------------------------------------


def _approx_tokens(message: Dict[str, Any]) -> int:
    return len(json.dumps(message, ensure_ascii=False)) // 4 + 4


def history_to_ollama_messages(history: List[types.Content], system_prompt: str) -> List[Dict[str, Any]]:
    """Convert the shared Content history into Ollama /api/chat messages."""
    messages: List[Dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for item in history:
        parts = item.parts or []
        if item.role == "model":
            text = "".join(p.text for p in parts if p.text)
            calls = [
                {"function": {"name": p.function_call.name,
                              "arguments": normalize_tool_args(p.function_call.name, p.function_call.args)}}
                for p in parts if p.function_call is not None
            ]
            msg: Dict[str, Any] = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = calls
            messages.append(msg)
            continue
        for p in parts:
            if p.function_response is not None:
                resp = p.function_response.response or {}
                content = str(resp.get("output", "") if isinstance(resp, dict) else resp)
                if len(content) > _TOOL_RESULT_MAX_CHARS:
                    content = "[…earlier output trimmed…]\n" + content[-_TOOL_RESULT_MAX_CHARS:]
                messages.append({"role": "tool", "tool_name": p.function_response.name, "content": content})
            elif p.text is not None:
                messages.append({"role": "user", "content": p.text})
    return messages


def trim_messages(messages: List[Dict[str, Any]], budget_tokens: int) -> List[Dict[str, Any]]:
    """Drop the oldest turns until the conversation fits *budget_tokens*.

    The system prompt is always kept, and cuts only happen right before a user
    message, so a tool call is never separated from its result.
    """
    system = [m for m in messages[:1] if m["role"] == "system"]
    rest = messages[len(system):]
    used = sum(_approx_tokens(m) for m in system)
    starts = [i for i, m in enumerate(rest) if m["role"] == "user"]
    for start in starts:
        tail = rest[start:]
        if used + sum(_approx_tokens(m) for m in tail) <= budget_tokens:
            return system + tail
    # Even the latest turn is too big: keep it anyway (tool output is capped).
    return system + (rest[starts[-1]:] if starts else rest)


# ---------------------------------------------------------------------------
# Tool calls written as text
# ---------------------------------------------------------------------------

_TAGGED_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_FENCED_RE = re.compile(r"```(?:json|tool_call|tool_code)?\s*(\{.*?\})\s*```", re.DOTALL)


def _as_call(obj: Any) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("function"), dict):
        obj = obj["function"]
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
    if name not in TOOL_NAMES:
        return None
    args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    return (name, args) if isinstance(args, dict) else None


def parse_text_tool_calls(content: str) -> Tuple[str, List[Tuple[str, Dict[str, Any]]]]:
    """Find tool calls a model wrote as text. Returns (remaining_text, calls)."""
    calls: List[Tuple[str, Dict[str, Any]]] = []
    remaining = content
    for pattern in (_TAGGED_RE, _FENCED_RE):
        for match in pattern.finditer(content):
            try:
                call = _as_call(json.loads(match.group(1)))
            except json.JSONDecodeError:
                call = None
            if call:
                calls.append(call)
                remaining = remaining.replace(match.group(0), "")
        if calls:
            return remaining.strip(), calls
    stripped = content.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            call = _as_call(json.loads(stripped))
        except json.JSONDecodeError:
            call = None
        if call:
            return "", [call]
    return content, []


_ECHO_RE = re.compile(r"""^\s*(?:echo|printf)\s+(?:-[a-z]+\s+)?(["'])(.+)\1\s*$""", re.DOTALL)


def echo_as_question(name: str, args: Dict[str, Any]) -> Optional[str]:
    """Small models sometimes "ask" by proposing `echo "question?"`.

    That would show the user an approval card for a no-op command; return the
    message so it can be shown as ordinary reply text instead.
    """
    if name != "propose_shell_command":
        return None
    match = _ECHO_RE.match(str(args.get("command") or ""))
    if not match or any(op in match.group(2) for op in ("$(", "`")):
        return None
    return match.group(2).strip()


def _might_be_tool_text(text: str) -> bool:
    head = text.lstrip()[:12]
    return head.startswith(("{", "<tool_call", "```"))


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OllamaAgentClient(BaseLLMClient):
    """Streams chat turns from a local Ollama server via /api/chat."""

    def __init__(
        self,
        system_prompt: str,
        model: str,
        base_url: str = OLLAMA_URL,
        num_ctx: int = DEFAULT_NUM_CTX,
        temperature: float = 0.1,
        keep_alive: str = "30m",
        timeout: float = 600.0,
        num_thread: Optional[int] = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._num_ctx = num_ctx
        self._temperature = temperature
        self._keep_alive = keep_alive
        self._timeout = timeout
        self._num_thread = num_thread or default_num_thread()
        self._tools = compact_tools()
        self._think_supported: Optional[bool] = None
        self.last_stats: Dict[str, Any] = {}

    def update_system_prompt(self, system_prompt: str) -> None:
        self._system_prompt = system_prompt

    @property
    def model(self) -> str:
        return self._model

    # ------------------------------------------------------------------

    def _request_body(self, messages: List[Dict[str, Any]], use_tools: bool = True) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "keep_alive": self._keep_alive,
            "options": {
                "num_ctx": self._num_ctx,
                "temperature": self._temperature,
                "num_thread": self._num_thread,
            },
        }
        if use_tools:
            body["tools"] = self._tools
        if self._think_supported is not False:
            # Reasoning models think for minutes on a laptop CPU; turn it off.
            body["think"] = False
        return body

    def _stream(self, body: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        req = urllib.request.Request(
            f"{self._base_url}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                for raw in resp:
                    line = raw.strip()
                    if line:
                        chunk = json.loads(line)
                        if chunk.get("error"):
                            raise LocalModelError(str(chunk["error"]))
                        yield chunk
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except json.JSONDecodeError:
                pass
            raise LocalModelError(str(detail)) from exc
        except urllib.error.URLError as exc:
            raise LocalModelError(
                "Can't reach the local AI (Ollama) at "
                f"{self._base_url}. Is it running? ({exc.reason})"
            ) from exc

    def generate(
        self,
        history: List[types.Content],
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> types.GenerateContentResponse:
        messages = history_to_ollama_messages(history, self._system_prompt)
        messages = trim_messages(messages, self._num_ctx - _REPLY_RESERVE_TOKENS)
        body = self._request_body(messages)
        try:
            return self._run(body, on_chunk)
        except LocalModelError as exc:
            text = str(exc).lower()
            if "think" in text and self._think_supported is not False:
                self._think_supported = False
                return self._run(self._request_body(messages), on_chunk)
            if "does not support tools" in text:
                raise LocalModelError(
                    f"The model “{self._model}” can't use tools, so it can't run commands. "
                    "Pick a model that supports tools in Preferences."
                ) from exc
            if "not found" in text and "model" in text:
                raise LocalModelError(
                    f"The model “{self._model}” isn't downloaded yet. Run: ollama pull {self._model}"
                ) from exc
            raise

    def _run(self, body: Dict[str, Any], on_chunk: Optional[Callable[[str], None]]) -> types.GenerateContentResponse:
        content = ""
        emitted = 0
        holding = False
        native_calls: List[Dict[str, Any]] = []
        for chunk in self._stream(body):
            msg = chunk.get("message") or {}
            piece = msg.get("content") or ""
            native_calls.extend(msg.get("tool_calls") or [])
            if piece:
                content += piece
                if not holding and emitted == 0 and _might_be_tool_text(content):
                    holding = True
                if on_chunk and not holding and len(content) > emitted:
                    on_chunk(content[emitted:])
                    emitted = len(content)
            if chunk.get("done"):
                self.last_stats = {
                    k: chunk.get(k) for k in (
                        "total_duration", "load_duration", "prompt_eval_count",
                        "prompt_eval_duration", "eval_count", "eval_duration",
                    )
                }
        if self._think_supported is None:
            self._think_supported = True

        calls: List[Tuple[str, Dict[str, Any]]] = []
        for tc in native_calls:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if fn.get("name"):
                calls.append((fn["name"], args))
        text = content
        if not calls:
            text, calls = parse_text_tool_calls(content)
            if calls:
                logger.info("Recovered %d tool call(s) written as text by %s", len(calls), self._model)
        if on_chunk and holding and text.strip() and not calls:
            on_chunk(text)  # it was ordinary text after all

        kept: List[Tuple[str, Dict[str, Any]]] = []
        for name, args in calls:
            question = echo_as_question(name, args)
            if question is None:
                kept.append((name, args))
            else:
                text = f"{text.strip()}\n\n{question}".strip()
                if on_chunk:
                    on_chunk(question)
        calls = kept

        parts: List[types.Part] = []
        if text.strip():
            parts.append(types.Part(text=text.strip()))
        for i, (name, args) in enumerate(calls):
            parts.append(types.Part(function_call=types.FunctionCall(
                name=name, args=normalize_tool_args(name, args), id=f"call_{i}",
            )))
        return types.GenerateContentResponse(candidates=[
            types.Candidate(content=types.Content(role="model", parts=parts), finish_reason="STOP")
        ])


# ---------------------------------------------------------------------------
# Server helpers (blocking — call from a worker thread)
# ---------------------------------------------------------------------------


def _get_json(path: str, base_url: str = OLLAMA_URL, timeout: float = 3.0) -> Dict[str, Any]:
    with urllib.request.urlopen(f"{base_url.rstrip('/')}{path}", timeout=timeout) as resp:
        return json.loads(resp.read())


def server_version(base_url: str = OLLAMA_URL) -> Optional[str]:
    try:
        return str(_get_json("/api/version", base_url).get("version"))
    except Exception:
        return None


def installed_models(base_url: str = OLLAMA_URL) -> List[str]:
    try:
        return [m["name"] for m in _get_json("/api/tags", base_url).get("models", [])]
    except Exception:
        return []


def model_supports_tools(model: str, base_url: str = OLLAMA_URL) -> Optional[bool]:
    """Ask Ollama whether *model* declares tool support (None if unknown)."""
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/show",
        data=json.dumps({"model": model}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            caps = json.loads(resp.read()).get("capabilities")
    except Exception:
        return None
    return None if caps is None else "tools" in caps


def probe_tool_calling(client: OllamaAgentClient) -> Tuple[bool, str]:
    """Check the model really emits a tool call (natively or as text)."""
    probe = [types.Content(role="user", parts=[types.Part(
        text="Look up the manual for the command 'ls' using the inspect_manual tool."
    )])]
    try:
        resp = client.generate(probe)
    except LocalModelError as exc:
        return False, str(exc)
    calls = BaseLLMClient.extract_function_calls(resp)
    if any(c.name == "inspect_manual" for c in calls):
        return True, "Model can use tools"
    return False, "This model answered without using a tool, so it can't run commands reliably."
