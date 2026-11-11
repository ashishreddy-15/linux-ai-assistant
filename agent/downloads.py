"""Find and download installers from verified sources.

The model never supplies a URL. It passes an app name; this module resolves it
to a source it can vouch for (a vendor's own site or GitHub's release API) or
to a link the *user* pasted into the chat. The download itself is one approval
card that shows the domain and size and verifies the SHA-256 when one is
published.
"""

from __future__ import annotations

import json
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from agent.installer import HOME, InstallError, Plan, _quote, slugify

DOWNLOADS = HOME / "Downloads"
TIMEOUT = 20
MAX_PAGE = 2_000_000
URL_RE = re.compile(r"https://[^\s<>\"')\]]+")
UNWANTED = re.compile(r"(mac|darwin|osx|win(32|64|dows)?|\.exe|\.msi|\.dmg|\.pkg|\.sig|\.asc|\.sha|\.txt|\.json|\.yml|"
                      r"\.blockmap|source|src|debug|symbols)", re.I)
ARCH_WORDS = {"x86_64": ("x64", "x86_64", "amd64", "x86-64"), "aarch64": ("arm64", "aarch64", "arm")}


@dataclass
class Source:
    label: str
    url: str
    filename: str
    size: int = 0
    sha256: str = ""
    note: str = ""

    @property
    def host(self) -> str:
        return urllib.parse.urlparse(self.url).hostname or ""


# ---------------------------------------------------------------------------
# Network (replaced in tests)
# ---------------------------------------------------------------------------

def http_get(url: str, accept: str = "*/*") -> bytes:
    if not url.startswith("https://"):
        raise InstallError("Only https downloads are allowed.")
    req = urllib.request.Request(url, headers={"User-Agent": "linux-assistant", "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read(MAX_PAGE)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise InstallError(f"I couldn't reach {urllib.parse.urlparse(url).hostname}: {exc}") from exc


def remote_size(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "linux-assistant"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return int(resp.headers.get("Content-Length") or 0)
    except (urllib.error.URLError, OSError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# System facts
# ---------------------------------------------------------------------------

def os_release() -> Dict[str, str]:
    try:
        text = Path("/etc/os-release").read_text()
    except OSError:
        return {}
    return {k: v.strip('"') for k, _, v in (line.partition("=") for line in text.splitlines()) if k}


def machine() -> str:
    import platform

    return "aarch64" if platform.machine() in ("aarch64", "arm64") else "x86_64"


def format_rank(name: str, pm: str) -> int:
    """Lower is better, for this system's package manager."""
    low = name.lower()
    order = {"dnf": (".rpm",), "zypper": (".rpm",), "apt": (".deb",)}.get(pm, ()) + (".appimage", ".tar.gz", ".tgz", ".tar.xz", ".zip")
    for i, ext in enumerate(order):
        if low.endswith(ext):
            return i
    return 99


def wrong_arch(name: str) -> bool:
    mine = ARCH_WORDS[machine()]
    other = ARCH_WORDS["aarch64" if machine() == "x86_64" else "x86_64"]
    low = name.lower()
    return any(w in low for w in other) and not any(w in low for w in mine)


# ---------------------------------------------------------------------------
# Source finders
# ---------------------------------------------------------------------------

def github_sources(repo: str, pm: str = "") -> List[Source]:
    data = json.loads(http_get(f"https://api.github.com/repos/{repo}/releases/latest", "application/vnd.github+json"))
    out = []
    for a in data.get("assets", []):
        name = a.get("name", "")
        if UNWANTED.search(name) or wrong_arch(name) or format_rank(name, pm) == 99:
            continue
        digest = (a.get("digest") or "").removeprefix("sha256:")
        out.append(Source(name, a["browser_download_url"], name, int(a.get("size") or 0),
                          digest if re.fullmatch(r"[0-9a-f]{64}", digest) else "", f"release {data.get('tag_name', '')}"))
    out.sort(key=lambda s: (format_rank(s.filename, pm), len(s.filename)))
    return out


def proton_sources(_pm: str = "") -> List[Source]:
    rel = os_release()
    if rel.get("ID") != "fedora":
        raise InstallError("I only have Proton's verified download for Fedora. Proton's Linux page lists other distributions.")
    base = f"https://repo.protonvpn.com/fedora-{rel.get('VERSION_ID', '')}-stable/protonvpn-stable-release/"
    names = re.findall(r'href="(protonvpn-stable-release-([\d.]+)-(\d+)\.noarch\.rpm)"', http_get(base).decode("utf-8", "replace"))
    if not names:
        raise InstallError(f"Proton has no package listed for Fedora {rel.get('VERSION_ID')} yet.")
    best = max(names, key=lambda n: (tuple(int(x) for x in n[1].split(".")), int(n[2])))[0]
    return [Source("Proton VPN", base + best, best, 0, "",
                   "This installs Proton's repository; install the `proton-vpn-gnome-desktop` package from it afterwards")]


def antigravity_sources(_pm: str = "") -> List[Source]:
    page = http_get("https://antigravity.google/download").decode("utf-8", "replace")
    key = "linux-arm" if machine() == "aarch64" else "linux-x64"
    out: Dict[str, Source] = {}
    for url in sorted(set(re.findall(r"https://[^\"'\s\\]+?/" + key + r"/[^\"'\s\\]+?\.tar\.gz", page))):
        host = urllib.parse.urlparse(url).hostname or ""
        if host not in ("edgedl.me.gvt1.com", "storage.googleapis.com", "dl.google.com"):
            continue
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        out[name] = Source(name.removesuffix(".tar.gz"), url, name)
    return list(out.values())


# name aliases -> finder. Each finder reads the vendor's own page or API.
VENDORS: Dict[str, Callable[[str], List[Source]]] = {
    "protonvpn": proton_sources,
    "proton vpn": proton_sources,
    "antigravity": antigravity_sources,
    "ayugram": lambda pm: github_sources("AyuGram/AyuGramDesktop", pm),
}


def user_urls(texts: List[str]) -> List[str]:
    return [u.rstrip(".,;") for t in texts for u in URL_RE.findall(t)]


def resolve(name: str, pm: str, pasted: List[str]) -> Source:
    """Return exactly one source, or raise with the options for the model to ask about."""
    q = (name or "").strip()
    if not q:
        raise InstallError("No app name was given.")

    # A link the user pasted themselves.
    if q.startswith("https://"):
        if q not in pasted:
            raise InstallError("I can only download links the user pasted in the chat. Ask them to paste the link.")
        fname = urllib.parse.unquote(urllib.parse.urlparse(q).path.rsplit("/", 1)[-1]) or "download"
        return Source(fname, q, fname, remote_size(q), "", "a link you pasted; no checksum is available")

    # owner/repo on GitHub.
    m = re.fullmatch(r"(?:https://github\.com/)?([\w.-]+/[\w.-]+?)(?:\.git)?/?", q)
    candidates: List[Source]
    if m and "/" in q:
        candidates = github_sources(m.group(1), pm)
    else:
        key = next((k for k in VENDORS if slugify(k) == slugify(q) or slugify(q) in slugify(k) or slugify(k) in slugify(q)), None)
        if key is None:
            raise InstallError(f"I don't have a verified download source for “{q}”. Ask the user to download the file "
                               "themselves (then use inspect_download) or paste the link here.")
        candidates = VENDORS[key](pm)
        # Narrow by extra words the user used ("antigravity ide").
        words = [w for w in re.split(r"[^a-z0-9]+", q.lower()) if w and w not in key.split()]
        if words:
            narrowed = [c for c in candidates if all(w in c.label.lower() for w in words)]
            candidates = narrowed or candidates
    if not candidates:
        raise InstallError("I found no download for this system (checked the right format and CPU).")
    if len(candidates) > 1:
        best = format_rank(candidates[0].filename, pm)
        top = [c for c in candidates if format_rank(c.filename, pm) == best]
        if len(top) > 1 and not m:
            labels = ", ".join(f"“{c.label}”" for c in top)
            raise InstallError(f"There is more than one download: {labels}. Ask the user which one, then call download_app "
                               "again with that exact name.")
        candidates = top[:1]
    src = candidates[0]
    src.size = src.size or remote_size(src.url)
    return src


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def _free_name(filename: str) -> str:
    clean = re.sub(r"[^\w. ()+-]", "_", filename).strip(". ") or "download"
    path = DOWNLOADS / clean
    stem, dot, ext = clean.partition(".")
    n = 1
    while path.exists():
        path = DOWNLOADS / f"{stem}-{n}{dot}{ext}"
        n += 1
    return path.name


def build_download(name: str, pm: str, pasted: List[str]) -> Plan:
    src = resolve(name, pm, pasted)
    dest = DOWNLOADS / _free_name(src.filename)
    part = dest.with_name(dest.name + ".part")
    size = ("unknown size" if not src.size else f"{src.size / 1e6:.0f} MB" if src.size >= 1e6 else f"{src.size / 1e3:.0f} KB")
    effects = [f"Downloads {src.label} ({size}) from {src.host}", f"Saves it to {dest}",
               "Checks the SHA-256 checksum" if src.sha256 else "The vendor doesn't publish a checksum for this file",
               "Nothing is installed yet"]
    steps = ["set -e", f"mkdir -p {_quote(DOWNLOADS)}",
             f"curl -fL --proto '=https' --tlsv1.2 --retry 3 -o {_quote(part)} {_quote(src.url)}"]
    if src.sha256:
        steps.append(f"echo {shlex.quote(src.sha256 + '  ' + str(part))} | sha256sum -c --quiet - "
                     f"|| {{ rm -f {_quote(part)}; echo 'Checksum did not match, so I deleted the file.'; exit 1; }}")
    steps += [f"mv -- {_quote(part)} {_quote(dest)}",
              f"echo 'Downloaded to {dest}. Next: call inspect_download with that path.'"]
    return Plan("download", slugify(src.label), f"Download {src.label}", effects, "\n".join(steps),
                notes=[src.note] if src.note else [], record={})


def inspect_or_offer(name: str, pm: str, pasted: List[str]):
    """inspect_download's brain: describe a local file, or offer a verified download.

    Returns ("text", message) or ("plan", Plan).
    """
    from agent import installer

    try:
        installer.find_download(name)
    except InstallError as not_found:
        try:
            return "plan", build_download(name, pm, pasted)
        except InstallError as why:
            if "verified download source" in str(why) or "pasted" in str(why):
                return "text", f"ERROR: {not_found}"
            return "text", f"ERROR: {not_found} {why}"
    return "text", installer.execute_inspect_download(name)
