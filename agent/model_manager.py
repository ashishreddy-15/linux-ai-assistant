"""Manage local Ollama models: a curated catalog, downloads with progress, run and stop.

Everything here talks to the local Ollama server over its HTTP API and is
GTK-free, so it can be unit-tested and driven from worker threads. These are
actions the *user* starts in the Models window (not the AI agent), so they do
not go through the approval cards.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from agent.local_llm import OLLAMA_URL, LocalModelError


@dataclass(frozen=True)
class CatalogModel:
    name: str  # exact Ollama tag
    label: str
    size_gb: float  # download size (from the Ollama registry)
    note: str
    tested: bool = False  # evaluated with this app's task set


# Tags and sizes were checked against registry.ollama.ai.
CATALOG = (
    CatalogModel("qwen3.5:2b", "Qwen 3.5 · 2B", 2.7, "Small and quick. For computers with 8 GB of memory.", True),
    CatalogModel("qwen3.5:4b", "Qwen 3.5 · 4B", 3.3, "The default: a good balance for 16 GB of memory.", True),
    CatalogModel("qwen3.5:9b", "Qwen 3.5 · 9B", 6.6, "Smarter, but slower. Needs plenty of memory.", False),
    CatalogModel("phi4-mini", "Phi-4 mini", 2.5, "Small model from Microsoft with tool support.", False),
    CatalogModel("gemma4:e4b", "Gemma 4 · E4B", 6.6, "Google's small model for agent tasks.", False),
    CatalogModel("granite4.1:8b", "Granite 4.1 · 8B", 5.3, "IBM's model, built for tool use.", False),
    CatalogModel("llama3.2:3b", "Llama 3.2 · 3B", 2.0, "Fast, but unreliable with tools: it may suggest unrelated commands. Not recommended.", False),
)


def canonical(name: str) -> str:
    """Ollama lists 'phi4-mini' as 'phi4-mini:latest'."""
    return name if ":" in name else f"{name}:latest"


def same_model(a: str, b: str) -> bool:
    return canonical(a) == canonical(b)


@dataclass
class PullProgress:
    status: str
    completed: int = 0
    total: int = 0

    @property
    def fraction(self) -> Optional[float]:
        return (self.completed / self.total) if self.total else None


class PullCancelled(Exception):
    """The user stopped the download. Ollama keeps the finished part, so it can resume."""


# ---------------------------------------------------------------------------
# HTTP helpers (blocking: call from a worker thread)
# ---------------------------------------------------------------------------

def _request(path: str, body: Optional[Dict[str, Any]] = None, method: str = "POST",
             base_url: str = OLLAMA_URL, timeout: float = 600.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{base_url.rstrip('/')}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _error_text(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            return json.loads(exc.read()).get("error") or f"HTTP {exc.code}"
        except Exception:
            return f"HTTP {exc.code}"
    return str(exc)


def installed_sizes(base_url: str = OLLAMA_URL) -> Dict[str, int]:
    """{model name: size in bytes} for every downloaded model."""
    with _request("/api/tags", method="GET", base_url=base_url, timeout=4) as resp:
        return {m["name"]: int(m.get("size") or 0) for m in json.loads(resp.read()).get("models", [])}


def running_models(base_url: str = OLLAMA_URL) -> List[str]:
    """Names of the models currently loaded in memory."""
    with _request("/api/ps", method="GET", base_url=base_url, timeout=4) as resp:
        return [m["name"] for m in json.loads(resp.read()).get("models", [])]


def load_model(name: str, keep_alive: str = "30m", base_url: str = OLLAMA_URL) -> None:
    """Load a model into memory so the first answer isn't slow."""
    try:
        with _request("/api/generate", {"model": name, "prompt": "", "stream": False, "keep_alive": keep_alive},
                      base_url=base_url, timeout=900) as resp:
            resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise LocalModelError(f"Couldn't start {name}: {_error_text(exc)}") from exc


def unload_model(name: str, base_url: str = OLLAMA_URL) -> None:
    """Free the memory a loaded model uses."""
    try:
        with _request("/api/generate", {"model": name, "prompt": "", "stream": False, "keep_alive": 0},
                      base_url=base_url, timeout=60) as resp:
            resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise LocalModelError(f"Couldn't stop {name}: {_error_text(exc)}") from exc


def delete_model(name: str, base_url: str = OLLAMA_URL) -> None:
    try:
        with _request("/api/delete", {"model": name}, method="DELETE", base_url=base_url, timeout=30) as resp:
            resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise LocalModelError(f"Couldn't remove {name}: {_error_text(exc)}") from exc


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

class ModelPuller:
    """Download one model, reporting overall progress and supporting cancel.

    A model is several layers; Ollama reports each separately, so progress is
    summed over the layers seen so far.
    """

    def __init__(self, name: str, base_url: str = OLLAMA_URL) -> None:
        self.name = name
        self._base_url = base_url
        self._cancelled = threading.Event()
        self._response = None
        self._layers: Dict[str, List[int]] = {}

    def cancel(self) -> None:
        self._cancelled.set()
        resp = self._response
        if resp is not None:
            try:
                resp.close()  # unblocks the reading thread
            except Exception:
                pass

    def run(self, on_progress: Callable[[PullProgress], None]) -> None:
        """Blocking. Returns when the model is ready; raises PullCancelled or LocalModelError."""
        try:
            self._response = _request("/api/pull", {"model": self.name, "stream": True},
                                      base_url=self._base_url, timeout=120)
        except (urllib.error.URLError, OSError) as exc:
            raise LocalModelError(f"Couldn't start the download: {_error_text(exc)}") from exc
        try:
            for raw in self._response:
                if self._cancelled.is_set():
                    raise PullCancelled()
                line = raw.strip()
                if not line:
                    continue
                msg = json.loads(line)
                if msg.get("error"):
                    raise LocalModelError(str(msg["error"]))
                progress = self._update(msg)
                on_progress(progress)
                if msg.get("status") == "success":
                    return
        except PullCancelled:
            raise
        except (OSError, ValueError) as exc:
            if self._cancelled.is_set():
                raise PullCancelled() from exc
            raise LocalModelError(f"The download was interrupted: {exc}") from exc
        finally:
            try:
                self._response.close()
            except Exception:
                pass
        if self._cancelled.is_set():
            raise PullCancelled()
        raise LocalModelError("The download ended before it finished.")

    def _update(self, msg: Dict[str, Any]) -> PullProgress:
        digest = msg.get("digest")
        if digest and msg.get("total"):
            self._layers[digest] = [int(msg.get("completed") or 0), int(msg["total"])]
        if msg.get("status") == "success":
            for layer in self._layers.values():
                layer[0] = layer[1]
        completed = sum(c for c, _ in self._layers.values())
        total = sum(t for _, t in self._layers.values())
        return PullProgress(str(msg.get("status", "")), completed, total)


def format_bytes(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"
