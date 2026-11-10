"""Read-only package search for the search_packages tool.

Small models invent package names (e.g. "org.postman.Postman" instead of
Flathub's real "com.getpostman.Postman"). This tool lets the model look up
exact IDs before it proposes an install.

Sources:
  * Flathub's public search API — fast (~2 s) and works even when the Flathub
    remote isn't configured locally (``flatpak search`` refreshes remote
    metadata and can take close to a minute). Note: the search term is sent
    to flathub.org.
  * The system package manager (dnf, apt, pacman or zypper), offline/cached.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from typing import List, Optional, Sequence, Tuple

FLATHUB_SEARCH_URL = "https://flathub.org/api/v2/search"
FLATHUB_REMOTE_ADD = (
    "flatpak remote-add --user --if-not-exists flathub "
    "https://dl.flathub.org/repo/flathub.flatpakrepo"
)
_QUERY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,47}$")
_MAX_FLATHUB = 5
_MAX_NATIVE = 8

Hit = Tuple[str, str]  # (exact name / ID, description)


def _run(args: Sequence[str], timeout: float = 25.0) -> str:
    try:
        res = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return res.stdout if res.returncode == 0 else ""


# ---------------------------------------------------------------------------
# Flatpak / Flathub
# ---------------------------------------------------------------------------


def search_flathub(query: str, timeout: float = 8.0) -> Optional[List[Hit]]:
    """Top Flathub apps for *query*, or None if Flathub couldn't be reached."""
    req = urllib.request.Request(
        FLATHUB_SEARCH_URL,
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "linux-ai-assistant"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    hits: List[Hit] = []
    for hit in (data.get("hits") or [])[:_MAX_FLATHUB]:
        app_id = hit.get("app_id")
        if not app_id:
            continue
        desc = f"{hit.get('name', '')}: {hit.get('summary', '')}".strip(": ")
        if len(desc) > 80:
            desc = desc[:77].rstrip() + "…"
        installs = hit.get("installs_last_month")
        if isinstance(installs, int) and installs:
            desc += f" ({_compact(installs)} installs/month)"
        if hit.get("verification_verified"):
            desc += " [verified]"
        hits.append((str(app_id), desc))
    return hits


def _compact(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def flatpak_state() -> Tuple[bool, bool, set[str]]:
    """(flatpak available, flathub remote configured, installed app IDs)."""
    if not shutil.which("flatpak"):
        return False, False, set()
    remotes = _run(["flatpak", "remotes", "--columns=name"], timeout=10).split()
    installed = set(_run(["flatpak", "list", "--app", "--columns=application"], timeout=10).split())
    return True, "flathub" in remotes, installed


# ---------------------------------------------------------------------------
# System package manager
# ---------------------------------------------------------------------------


def detect_package_manager() -> Optional[str]:
    for pm in ("dnf", "apt", "pacman", "zypper"):
        if shutil.which(pm):
            return pm
    return None


def _parse_pairs(text: str, sep: str) -> List[Hit]:
    out: List[Hit] = []
    for line in text.splitlines():
        if sep in line:
            name, desc = line.split(sep, 1)
            if name.strip():
                out.append((name.strip(), desc.strip()))
    return out


def search_native(pm: str, query: str) -> List[Hit]:
    term = query.replace(" ", "-").lower()
    if pm == "dnf":
        text = _run(["dnf", "repoquery", "--quiet", "--available",
                     "--queryformat", "%{name}|%{summary}\n", f"*{term}*"])
        hits = sorted(set(_parse_pairs(text, "|")), key=lambda h: (h[0] != term, len(h[0]), h[0]))
    elif pm == "apt":
        hits = _parse_pairs(_run(["apt-cache", "search", "--names-only", "--", term]), " - ")
        hits.sort(key=lambda h: (h[0] != term, len(h[0])))
    elif pm == "pacman":
        lines = _run(["pacman", "-Ss", "--", term]).splitlines()
        hits = []
        for i in range(0, len(lines) - 1, 2):
            head = lines[i].split()
            if head:
                hits.append((head[0].split("/")[-1], lines[i + 1].strip()))
    elif pm == "zypper":
        hits = []
        for line in _run(["zypper", "--non-interactive", "--no-refresh", "search", "--", term]).splitlines():
            cols = [c.strip() for c in line.split("|")]
            if len(cols) >= 4 and cols[1] and cols[1] != "Name" and not set(cols[1]) <= {"-"}:
                hits.append((cols[1], cols[3]))
    else:
        hits = []
    return hits[:_MAX_NATIVE]


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------


def execute_search_packages(query: str) -> str:
    """Search Flathub and the system package manager; text for the model."""
    query = (query or "").strip()
    if not _QUERY_RE.match(query):
        return "ERROR: Search for a plain app or package name, e.g. 'vlc' or 'postman'."

    sections: List[str] = []
    has_flatpak, flathub_ready, installed = flatpak_state()
    if has_flatpak:
        hits = search_flathub(query)
        if hits is None:
            sections.append("Flathub: could not be reached (no internet?).")
        elif hits:
            lines = ["Flathub apps (install with: flatpak install --user -y flathub <ID>; no password):"]
            for app_id, desc in hits:
                mark = " [already installed]" if app_id in installed else ""
                lines.append(f"  {app_id} — {desc}{mark}")
            if not flathub_ready:
                lines.append(f"  Note: Flathub is not set up on this computer yet. First run: {FLATHUB_REMOTE_ADD}")
            sections.append("\n".join(lines))
        else:
            sections.append("Flathub: no matching apps.")

    pm = detect_package_manager()
    if pm:
        hits = search_native(pm, query)
        if hits:
            lines = [f"{pm} packages (system-wide, need sudo):"]
            lines += [f"  {name} — {desc}" for name, desc in hits]
            sections.append("\n".join(lines))
        else:
            sections.append(f"{pm}: no matching packages.")

    if not sections:
        return "No package sources found on this computer (no flatpak or known package manager)."
    sections.append("Use the exact ID or package name shown above. Never guess one that isn't listed.")
    return "\n\n".join(sections)
