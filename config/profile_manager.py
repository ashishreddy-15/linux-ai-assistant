"""System profile detection and configuration manager for Linux AI Assistant.

Detects static distribution details, hardware metrics, and package management
capabilities on first launch, saving them to ~/.config/linux-helper/system_profile.json.
Provides fast dynamic queries for active desktop session and storage.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.provider_registry import PROVIDER_CONFIGS

CONFIG_DIR = Path.home() / ".config" / "linux-helper"
DEFAULT_PROFILE_PATH = CONFIG_DIR / "system_profile.json"

PRIMARY_PACKAGE_MANAGERS = ("pacman", "dnf", "apt", "zypper")
SECONDARY_PACKAGE_HELPERS = ("flatpak", "snap", "yay", "paru")

DEFAULT_SKILL_LEVEL = "Intermediate"
VALID_SKILL_LEVELS = ("Beginner", "Intermediate", "Advanced")

DEFAULT_THEME = "system"  # follow the desktop light/dark setting
VALID_THEMES = ("dark", "light", "system")

DEFAULT_PROVIDER = "groq"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"
VALID_PROVIDERS = tuple(list(PROVIDER_CONFIGS.keys()) + ["ollama"])

PROVIDER_MODELS = {
    k: list(v.get("fallback_models", [])) for k, v in PROVIDER_CONFIGS.items()
}
PROVIDER_MODELS["ollama"] = [
    "qwen3.5:4b",
    "llama3.2:3b",
    "mistral:7b",
]


def detect_os_release() -> Dict[str, str]:
    """Detect OS name, version, and ID from /etc/os-release or standard fallback."""
    os_info = {
        "name": "Linux",
        "version": "unknown",
        "id": "linux",
        "pretty_name": "Linux",
    }

    # Attempt standard library os-release reader (Python 3.10+)
    try:
        if hasattr(platform, "freedesktop_os_release"):
            data = platform.freedesktop_os_release()
            os_info["name"] = data.get("NAME") or os_info["name"]
            os_info["version"] = (
                data.get("VERSION")
                or data.get("VERSION_ID")
                or data.get("BUILD_ID")
                or "rolling"
            )
            os_info["id"] = data.get("ID") or os_info["id"]
            os_info["pretty_name"] = data.get("PRETTY_NAME") or os_info["name"]
            return os_info
    except Exception:
        pass

    # Manual fallback parser for /etc/os-release or /usr/lib/os-release
    for candidate in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        if candidate.is_file():
            try:
                with candidate.open(encoding="utf-8") as f:
                    parsed = {}
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        parsed[k.strip()] = v.strip().strip('"').strip("'")

                    os_info["name"] = parsed.get("NAME", os_info["name"])
                    os_info["version"] = (
                        parsed.get("VERSION")
                        or parsed.get("VERSION_ID")
                        or parsed.get("BUILD_ID")
                        or "rolling"
                    )
                    os_info["id"] = parsed.get("ID", os_info["id"])
                    os_info["pretty_name"] = parsed.get("PRETTY_NAME", os_info["name"])
                    return os_info
            except Exception:
                pass

    return os_info


def detect_primary_package_manager() -> Optional[str]:
    """Detect the system's primary package manager (pacman, dnf, apt, zypper)."""
    for pm in PRIMARY_PACKAGE_MANAGERS:
        if shutil.which(pm):
            return pm
    return None


def detect_secondary_helpers() -> List[str]:
    """Detect available secondary package and build helpers (flatpak, snap, yay, paru)."""
    available = []
    for helper in SECONDARY_PACKAGE_HELPERS:
        if shutil.which(helper):
            available.append(helper)
    return available


def detect_cpu_architecture() -> str:
    """Detect the CPU machine architecture."""
    return platform.machine() or "unknown"


def detect_total_ram() -> Dict[str, Any]:
    """Detect total system RAM in bytes and human-readable gigabytes."""
    total_bytes = 0

    # POSIX sysconf
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_bytes = pages * page_size
    except Exception:
        pass

    # Fallback to /proc/meminfo
    if total_bytes <= 0:
        meminfo_path = Path("/proc/meminfo")
        if meminfo_path.is_file():
            try:
                with meminfo_path.open(encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            parts = line.split()
                            if len(parts) >= 2:
                                total_bytes = int(parts[1]) * 1024
                            break
            except Exception:
                pass

    total_gb = round(total_bytes / (1024**3), 2) if total_bytes > 0 else 0.0
    return {
        "total_ram_bytes": total_bytes,
        "total_ram_gb": total_gb,
    }


def detect_static_system_profile() -> Dict[str, Any]:
    """Perform a full static inspection of system details."""
    os_info = detect_os_release()
    primary_pm = detect_primary_package_manager()
    secondary_helpers = detect_secondary_helpers()
    cpu_arch = detect_cpu_architecture()
    ram_info = detect_total_ram()

    return {
        "os_name": os_info["name"],
        "os_version": os_info["version"],
        "os_id": os_info["id"],
        "os_pretty_name": os_info["pretty_name"],
        "primary_package_manager": primary_pm,
        "secondary_helpers": secondary_helpers,
        "cpu_architecture": cpu_arch,
        "total_ram_bytes": ram_info["total_ram_bytes"],
        "total_ram_gb": ram_info["total_ram_gb"],
        "skill_level": DEFAULT_SKILL_LEVEL,
        "theme": DEFAULT_THEME,
        "active_provider": DEFAULT_PROVIDER,
        "model_name": DEFAULT_GROQ_MODEL,
    }


def save_profile(
    profile: Dict[str, Any], path: Path | str = DEFAULT_PROFILE_PATH
) -> Path:
    """Save the profile dictionary to disk formatted as JSON."""
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    with profile_path.open("w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    return profile_path


def load_profile(path: Path | str = DEFAULT_PROFILE_PATH) -> Optional[Dict[str, Any]]:
    """Load profile from disk if it exists and is valid JSON."""
    profile_path = Path(path)
    if not profile_path.is_file():
        return None
    try:
        with profile_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_or_create_profile(
    path: Path | str = DEFAULT_PROFILE_PATH,
    force_refresh: bool = False,
    preserve_preferences: bool = False,
) -> Dict[str, Any]:
    """Retrieve existing profile or detect and write on initial launch."""
    if not force_refresh:
        existing = load_profile(path)
        if existing:
            # Backfill provider settings if missing from earlier versions
            dirty = False
            if "skill_level" not in existing:
                existing["skill_level"] = DEFAULT_SKILL_LEVEL
                dirty = True
            if "theme" not in existing:
                existing["theme"] = DEFAULT_THEME
                dirty = True
            if "active_provider" not in existing:
                existing["active_provider"] = DEFAULT_PROVIDER
                dirty = True
            if "model_name" not in existing:
                prov = existing.get("active_provider")
                if prov in PROVIDER_CONFIGS:
                    default_model = PROVIDER_CONFIGS[prov].get("default_model", DEFAULT_GROQ_MODEL)
                elif prov == "ollama":
                    default_model = DEFAULT_OLLAMA_MODEL
                else:
                    default_model = DEFAULT_GROQ_MODEL
                existing["model_name"] = default_model
                dirty = True
            if dirty:
                save_profile(existing, path)
            return existing

    existing = load_profile(path) if preserve_preferences else None
    profile = detect_static_system_profile()
    if existing:
        if "skill_level" in existing:
            profile["skill_level"] = existing["skill_level"]
        if "theme" in existing:
            profile["theme"] = existing["theme"]
        if "active_provider" in existing:
            profile["active_provider"] = existing["active_provider"]
        if "model_name" in existing:
            profile["model_name"] = existing["model_name"]

    save_profile(profile, path)
    return profile


def update_skill_level(
    skill_level: str, path: Path | str = DEFAULT_PROFILE_PATH
) -> Dict[str, Any]:
    """Update skill level in profile configuration."""
    if skill_level not in VALID_SKILL_LEVELS:
        raise ValueError(
            f"Invalid skill_level '{skill_level}'. Must be one of {VALID_SKILL_LEVELS}"
        )

    profile = get_or_create_profile(path)
    profile["skill_level"] = skill_level
    save_profile(profile, path)
    return profile


def update_theme(
    theme: str, path: Path | str = DEFAULT_PROFILE_PATH
) -> Dict[str, Any]:
    """Update UI color theme in profile configuration ('dark', 'light', 'system')."""
    theme_clean = theme.strip().lower()
    if theme_clean not in VALID_THEMES:
        raise ValueError(
            f"Invalid theme '{theme}'. Must be one of {VALID_THEMES}"
        )

    profile = get_or_create_profile(path)
    profile["theme"] = theme_clean
    save_profile(profile, path)
    return profile


def update_provider_settings(
    active_provider: str,
    model_name: Optional[str] = None,
    path: Path | str = DEFAULT_PROFILE_PATH,
) -> Dict[str, Any]:
    """Update active provider and model in profile configuration."""
    provider_clean = active_provider.strip().lower()
    if provider_clean not in VALID_PROVIDERS:
        raise ValueError(
            f"Invalid provider '{active_provider}'. Must be one of {VALID_PROVIDERS}"
        )

    if not model_name:
        if provider_clean in PROVIDER_CONFIGS:
            model_name = PROVIDER_CONFIGS[provider_clean].get("default_model", DEFAULT_GROQ_MODEL)
        elif provider_clean == "ollama":
            model_name = DEFAULT_OLLAMA_MODEL
        else:
            model_name = DEFAULT_GROQ_MODEL

    profile = get_or_create_profile(path)
    profile["active_provider"] = provider_clean
    profile["model_name"] = model_name
    save_profile(profile, path)
    return profile


def get_dynamic_session_info() -> Dict[str, Any]:
    """Fast dynamic helper for session attributes and available disk space on /home."""
    session_type = os.environ.get("XDG_SESSION_TYPE", "unknown")
    current_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "unknown")

    # Available disk space on /home via shutil.disk_usage
    disk_target = "/home" if Path("/home").exists() else str(Path.home())
    try:
        usage = shutil.disk_usage(disk_target)
        disk_total = usage.total
        disk_used = usage.used
        disk_free = usage.free
    except Exception:
        disk_total, disk_used, disk_free = 0, 0, 0

    return {
        "xdg_session_type": session_type,
        "xdg_current_desktop": current_desktop,
        "disk_target": disk_target,
        "home_disk_total_bytes": disk_total,
        "home_disk_used_bytes": disk_used,
        "home_disk_free_bytes": disk_free,
        "home_disk_free_gb": round(disk_free / (1024**3), 2) if disk_total > 0 else 0.0,
        "home_disk_total_gb": round(disk_total / (1024**3), 2) if disk_total > 0 else 0.0,
    }
