"""Detect and set up the local AI (Ollama) for the current user.

Every step that changes the computer is returned as an approval-card payload
(the same dicts the orchestrator emits), so setup follows the app's
human-in-the-loop rule: nothing is installed, started or downloaded until the
user approves the exact command.

Steps, in order:
  1. install  – Ollama into ~/.local/opt/ollama (no sudo)
  2. start    – a systemd *user* service (or, without one, start it now)
  3. pull     – download a model sized for this computer's memory
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.local_llm import installed_models, server_version

INSTALL_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-ollama-user.sh"
SERVICE_NAME = "ollama.service"

# (max total RAM in GB, model, download size in GB, note)
MODEL_TIERS = (
    (8.5, "qwen3.5:2b", 2.7, "a smaller model for computers with 8 GB of memory or less"),
    (24.5, "qwen3.5:4b", 3.3, "a good balance of speed and quality for 16 GB of memory"),
    (float("inf"), "qwen3.5:9b", 6.6, "a larger, smarter model for computers with plenty of memory"),
)


@dataclass
class LocalStatus:
    binary: Optional[str] = None
    version: Optional[str] = None  # set when the server answers
    models: List[str] = field(default_factory=list)
    nvidia: bool = False
    integrated_gpu: bool = False
    user_systemd: bool = False
    service_installed: bool = False
    ram_gb: float = 0.0

    @property
    def running(self) -> bool:
        return self.version is not None


def _user_bin_ollama() -> Path:
    return Path.home() / ".local" / "bin" / "ollama"


def find_binary() -> Optional[str]:
    found = shutil.which("ollama")
    if found:
        return found
    local = _user_bin_ollama()
    return str(local) if local.exists() else None


def _gpu_vendors() -> List[str]:
    vendors = []
    for vendor_file in Path("/sys/class/drm").glob("card*/device/vendor"):
        try:
            vendors.append(vendor_file.read_text().strip().lower())
        except OSError:
            pass
    return vendors


def detect(ram_gb: float = 0.0) -> LocalStatus:
    """Gather everything setup needs. Blocking (HTTP + /sys): use a worker thread."""
    vendors = _gpu_vendors()
    status = LocalStatus(
        binary=find_binary(),
        version=server_version(),
        nvidia=Path("/proc/driver/nvidia/version").exists() or "0x10de" in vendors,
        integrated_gpu="0x8086" in vendors or "0x1002" in vendors,
        user_systemd=bool(shutil.which("systemctl")) and Path(f"/run/user/{os.getuid()}/systemd").exists(),
        service_installed=(Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME).exists(),
        ram_gb=ram_gb,
    )
    if status.running:
        status.models = installed_models()
    return status


def recommend_model(ram_gb: float) -> tuple[str, float, str]:
    """(model, download GB, why) for a machine with *ram_gb* of memory."""
    for limit, model, size, why in MODEL_TIERS:
        if (ram_gb or 16.0) <= limit:
            return model, size, why
    return MODEL_TIERS[-1][1:]  # pragma: no cover


def usable_model(status: LocalStatus) -> Optional[str]:
    """The best installed model we know works, else any installed model."""
    known = [tier[1] for tier in MODEL_TIERS]
    for model in known:
        if model in status.models:
            return model
    return status.models[0] if status.models else None


# ---------------------------------------------------------------------------
# Step payloads (rendered by ui.action_card.ActionCard)
# ---------------------------------------------------------------------------


def _payload(title: str, command: str, explanation: str, effects: List[str]) -> Dict[str, Any]:
    return {
        "tool_name": "local_setup",
        "title": title,
        "command": command,
        "explanation": explanation,
        "effects": effects,
        "requires_root": False,
        "high_risk": False,
    }


def install_step() -> Dict[str, Any]:
    return _payload(
        "Install Ollama, the local AI engine",
        f"bash {shlex.quote(str(INSTALL_SCRIPT))}",
        "Ollama runs AI models on your own computer. It installs into your home folder, "
        "so no password is needed.",
        [
            "Downloads about 1.4 GB from Ollama's official GitHub releases and checks it",
            "Installs into ~/.local/opt/ollama",
            "Changes nothing outside your home folder",
        ],
    )


def _gpu_env(status: LocalStatus) -> Dict[str, str]:
    env = {"OLLAMA_HOST": "127.0.0.1:11434"}
    if status.integrated_gpu and not status.nvidia:
        # Ollama skips integrated GPUs by default; on Intel laptops the GPU
        # reads prompts ~3x faster than the CPU.
        env["OLLAMA_IGPU_ENABLE"] = "1"
    return env


def service_unit(binary: str, status: LocalStatus) -> str:
    env_lines = "\n".join(f"Environment={k}={v}" for k, v in _gpu_env(status).items())
    return (
        "[Unit]\n"
        "Description=Ollama local AI server (used by Linux Assistant)\n\n"
        "[Service]\n"
        f"ExecStart={binary} serve\n"
        f"{env_lines}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def start_step(status: LocalStatus) -> Dict[str, Any]:
    binary = os.path.realpath(status.binary or str(_user_bin_ollama()))
    if status.user_systemd:
        unit_dir = "~/.config/systemd/user"
        command = (
            f"mkdir -p {unit_dir} && cat > {unit_dir}/{SERVICE_NAME} <<'EOF'\n"
            f"{service_unit(binary, status)}"
            "EOF\n"
            f"systemctl --user daemon-reload && systemctl --user enable --now {SERVICE_NAME}"
        )
        return _payload(
            "Start the local AI when you log in",
            command,
            "Ollama runs quietly in the background and is only reachable from this computer.",
            [
                "Adds a small background service for your account only",
                "Starts it now and at every login",
                f"Turn it off any time: systemctl --user disable --now {SERVICE_NAME}",
            ],
        )
    env = " ".join(f"{k}={v}" for k, v in _gpu_env(status).items())
    return _payload(
        "Start the local AI",
        f"setsid -f env {env} {shlex.quote(binary)} serve >/dev/null 2>&1",
        "Ollama runs in the background until you log out.",
        ["Starts Ollama for this session", "Only reachable from this computer"],
    )


def pull_step(status: LocalStatus) -> Dict[str, Any]:
    model, size, why = recommend_model(status.ram_gb)
    binary = status.binary or str(_user_bin_ollama())
    return _payload(
        f"Download the AI model ({size:g} GB)",
        f"{shlex.quote(binary)} pull {model}",
        f"{model} is {why}.",
        [
            f"Downloads {model}, about {size:g} GB, into ~/.ollama",
            "After this, the assistant works without internet",
            f"Remove it any time: ollama rm {model}",
        ],
    )


def next_step(status: LocalStatus) -> Optional[Dict[str, Any]]:
    """The next approval card needed, or None when the local AI is ready."""
    if status.binary is None:
        return install_step()
    if not status.running:
        return start_step(status)
    if usable_model(status) is None:
        return pull_step(status)
    return None


def checklist(status: LocalStatus) -> List[tuple[str, bool]]:
    model = usable_model(status) or recommend_model(status.ram_gb)[0]
    return [
        ("ollama installed", status.binary is not None),
        ("ollama running", status.running),
        (f"model {model}", usable_model(status) is not None),
    ]

