"""Build well-behaved source projects and install them into ~/.local.

Supported: CMake, Meson, Cargo (Rust), Go, Autotools and plain Makefiles.
Building runs the project's own scripts, so the card says so. Everything
installs under ``~/.local`` (no password), except the build tools themselves,
which are installed first if missing. A before/after snapshot of ``~/.local``
becomes an install manifest, which is what makes removal possible.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent import installer
from agent.installer import HOME, InstallError, Plan, _quote, app_name, slugify

LA_DIR = HOME / ".local" / "share" / "linux-assistant"
SRC_DIR = LA_DIR / "src"
MANIFESTS = LA_DIR / "manifests"
PREFIX = HOME / ".local"

# build system -> programs it needs (checked with `which`)
NEEDS = {
    "CMake": ["cmake", "make", "c++", "pkg-config"],
    "Meson": ["meson", "ninja", "c++", "pkg-config"],
    "Cargo (Rust)": ["cargo", "cc"],
    "Go": ["go"],
    "Autotools": ["make", "cc", "pkg-config"],
    "Make": ["make", "cc"],
}

# program -> package name per package manager
PACKAGES = {
    "cmake": {"dnf": "cmake", "apt": "cmake", "pacman": "cmake", "zypper": "cmake"},
    "make": {"dnf": "make", "apt": "make", "pacman": "make", "zypper": "make"},
    "c++": {"dnf": "gcc-c++", "apt": "g++", "pacman": "gcc", "zypper": "gcc-c++"},
    "cc": {"dnf": "gcc", "apt": "gcc", "pacman": "gcc", "zypper": "gcc"},
    "pkg-config": {"dnf": "pkgconf-pkg-config", "apt": "pkg-config", "pacman": "pkgconf", "zypper": "pkg-config"},
    "meson": {"dnf": "meson", "apt": "meson", "pacman": "meson", "zypper": "meson"},
    "ninja": {"dnf": "ninja-build", "apt": "ninja-build", "pacman": "ninja", "zypper": "ninja"},
    "cargo": {"dnf": "cargo", "apt": "cargo", "pacman": "rust", "zypper": "cargo"},
    "go": {"dnf": "golang", "apt": "golang-go", "pacman": "go", "zypper": "go"},
}
INSTALL_CMD = {
    "dnf": "sudo dnf install -y", "apt": "sudo apt install -y",
    "pacman": "sudo pacman -S --needed --noconfirm", "zypper": "sudo zypper --non-interactive install",
}

JOBS = '"$(nproc)"'
SYSTEMS = ("CMake", "Meson", "Cargo (Rust)", "Go", "Autotools", "Make")


def build_steps(system: str) -> List[str]:
    """Commands for a build system, with the install prefix resolved at call time."""
    prefix = str(PREFIX)
    return {
        "CMake": [f'cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="{prefix}"',
                  f"cmake --build build -j{JOBS}", "cmake --install build"],
        "Meson": [f'meson setup build --prefix="{prefix}"', "meson compile -C build", "meson install -C build"],
        "Cargo (Rust)": [f'cargo install --path . --root "{prefix}"'],
        "Go": [f'GOBIN="{prefix}/bin" go install ./...'],
        "Autotools": [f'./configure --prefix="{prefix}"', f"make -j{JOBS}", "make install"],
        "Make": [f"make -j{JOBS}", f'make install PREFIX="{prefix}" prefix="{prefix}"'],
    }[system]


def package_manager() -> str:
    pm = installer._package_manager()
    return pm or ("pacman" if shutil.which("pacman") else "")


def missing_tools(system: str) -> List[str]:
    out = []
    for prog in NEEDS.get(system, []):
        if prog == "c++":
            ok = bool(shutil.which("c++") or shutil.which("g++"))
        elif prog == "cc":
            ok = bool(shutil.which("cc") or shutil.which("gcc"))
        elif prog == "pkg-config":
            ok = bool(shutil.which("pkg-config") or shutil.which("pkgconf"))
        else:
            ok = bool(shutil.which(prog))
        if not ok:
            out.append(prog)
    return out


def detect_system(first_level: set) -> Optional[str]:
    for marker, system in installer.SOURCE_MARKERS.items():
        if marker in first_level:
            return system
    return None


def _manifest_path(name: str) -> Path:
    return MANIFESTS / f"{name}.txt"


def plan_source(path: Path, system: str, top: Optional[str], base: Dict[str, Any], is_zip: bool) -> Plan:
    """Build a plan for a source archive (path is a file) or a directory (top is None, is_zip False)."""
    if system not in SYSTEMS:
        raise InstallError(f"This is a {system} project; I can only build CMake, Meson, Rust, Go, Autotools and "
                           "Makefile projects so far. Suggest the project's own instructions.")
    pm = package_manager()
    missing = missing_tools(system)
    if missing and pm not in INSTALL_CMD:
        raise InstallError(f"Building needs {', '.join(missing)}, and I don't know how to install them on this system.")
    name = app_name(top or installer._stem(path) if path.is_file() else path.name)
    work = path if path.is_dir() else SRC_DIR / name
    manifest = _manifest_path(name)
    steps = ["set -e"]
    effects = []
    if missing:
        pkgs = sorted({PACKAGES[m][pm] for m in missing})
        steps.append(f"{INSTALL_CMD[pm]} {' '.join(pkgs)}")
        effects.append(f"First installs the build tools ({', '.join(pkgs)}); this needs your password")
    if path.is_file():
        steps += [f"rm -rf {_quote(work)}", installer._unpack_command(path, top, work, is_zip)]
    steps += [f"cd {_quote(work)}", 'marker="$(mktemp)"', 'trap \'rm -f "$marker"\' EXIT', "sleep 1"]
    steps += build_steps(system)
    steps += [f"mkdir -p {_quote(MANIFESTS)}",
              f'find {_quote(PREFIX)} \\( -type f -o -type l \\) -newer "$marker" -not -path {_quote(str(LA_DIR) + "/*")} '
              f"> {_quote(manifest)}",
              f'echo "Installed $(wc -l < {_quote(manifest)}) files into {PREFIX}."',
              f"grep '^{PREFIX}/bin/' {_quote(manifest)} | head -10 || true"]
    effects += [f"Builds {name} ({system}) in {work}", f"Installs it into {PREFIX} (no password needed)",
                "Building runs the project's own scripts, so only build code you trust"]
    notes = [f"If it's a graphical app and no menu entry appears, ask me to add one. Make sure {PREFIX}/bin is on your PATH."]
    rec = {**base, "name": name, "kind": "built", "dirs": [] if path.is_dir() else [str(work)],
           "files": [], "manifest": str(manifest)}
    return Plan("build", name, f"Build and install {name}", effects, "\n".join(steps), bool(missing), notes=notes, record=rec)


def describe_source(system: Optional[str]) -> str:
    if system not in SYSTEMS:
        return (f"source code ({system or 'unknown build system'}). I can't build this kind of project yet; "
                "tell the user and point them to the project's own instructions.")
    missing = missing_tools(system)
    extra = f" It first needs to install: {', '.join(missing)}." if missing else ""
    return f"source code ({system}). It can be built and installed into ~/.local.{extra}"


def build_removal(rec: Dict[str, Any]) -> Plan:
    """Delete exactly the files the build installed (from its manifest), plus the build folder."""
    label = rec.get("label") or rec["name"]
    try:
        lines = Path(rec["manifest"]).read_text().splitlines()
    except (OSError, KeyError) as exc:
        raise InstallError(f"The list of installed files for {label} is missing, so I can't remove it safely.") from exc
    files = []
    for f in lines:
        norm = Path(os.path.normpath(f))
        if not f.startswith("/") or PREFIX not in norm.parents or LA_DIR in norm.parents:
            raise InstallError("The install list contains a path outside ~/.local. Not removing anything.")
        files.append(f)
    steps = ["set -e"]
    for i in range(0, len(files), 40):
        steps.append("rm -f -- " + " ".join(_quote(f) for f in files[i:i + 40]))
    for d in rec.get("dirs", []):
        if not installer._under(d, (SRC_DIR,)):
            raise InstallError(f"The record lists {d}, outside the places I manage. Not removing anything.")
        steps.append(f"rm -rf -- {_quote(d)}")
    steps += [f"rm -f -- {_quote(rec['manifest'])}", f"update-desktop-database {_quote(installer.APPS)} 2>/dev/null || true",
              f"echo 'Removed {label}.'"]
    return Plan("remove", rec["name"], f"Remove {label}",
                [f"Deletes the {len(files)} files it installed under ~/.local", "Deletes its build folder"],
                "\n".join(steps), record={"forget": rec["name"]})


# ---------------------------------------------------------------------------
# find_package_for: map a build error to the package that fixes it
# ---------------------------------------------------------------------------

_ERROR_PATTERNS = [
    (re.compile(r"fatal error:\s*([\w./+-]+\.h\w*)"), "/usr/include/{}"),
    (re.compile(r"cannot find -l([\w+.-]+)"), "*/lib{}.so"),
    (re.compile(r"No package '([\w.+-]+)' found"), "pkgconfig({})"),
    (re.compile(r"Package '?([\w.+-]+)'? not found"), "pkgconfig({})"),
    (re.compile(r"Dependency \"?([\w.+-]+)\"? not found"), "pkgconfig({})"),
    (re.compile(r"Could NOT find ([\w.+-]+)|Could not find a package configuration file provided by \"([\w.+-]+)\""), "pkgconfig({})"),
    (re.compile(r"([\w.+-]+\.(?:h|hpp))\b: No such file"), "/usr/include/{}"),
    (re.compile(r"command not found: (\w+)|(\w+): command not found"), "/usr/bin/{}"),
]


def wanted_capabilities(text: str) -> List[str]:
    wanted: List[str] = []
    seen: List[str] = []
    for rx, template in _ERROR_PATTERNS:
        for m in rx.finditer(text or ""):
            value = next((g for g in m.groups() if g), "")
            if value and any(v.endswith("/" + value) for v in seen):
                continue  # the same header, already found with its folder
            if value:
                seen.append(value)
            if value and len(value) < 80:
                cap = template.format(value)
                if cap not in wanted:
                    wanted.append(cap)
    return wanted[:4]


def execute_find_package_for(error_text: str) -> str:
    caps = wanted_capabilities(error_text)
    if not caps:
        return ("I couldn't tell what is missing from that text. Pass the exact error line, for example "
                "'fatal error: zlib.h: No such file or directory'.")
    pm = package_manager()
    if pm != "dnf":
        return f"Looking up packages is only built in for dnf systems. Missing: {', '.join(caps)}. Suggest searching with the system package manager."
    out = []
    for cap in caps:
        pkgs: List[str] = []
        # Headers live under /usr/include; fall back to any include directory (e.g. /usr/include/foo-2/x.h).
        for query in ([cap, "*/include/" + cap.removeprefix("/usr/include/"),
                                                                     "*/include/*/" + cap.removeprefix("/usr/include/")] if cap.startswith("/usr/include/") else [cap]):
            try:
                proc = subprocess.run(["dnf", "repoquery", "-q", "--whatprovides", query], capture_output=True, text=True, timeout=90)
            except (OSError, subprocess.SubprocessError):
                return "The package lookup failed (dnf not responding)."
            pkgs = sorted({re.sub(r"-\d.*$", "", ln.strip()) for ln in proc.stdout.splitlines() if ln.strip()})
            if pkgs:
                break
        # Prefer -devel packages for headers/libraries.
        devel = [p for p in pkgs if p.endswith("-devel")] or pkgs
        out.append(f"{cap}: " + (", ".join(devel[:4]) if devel else "no package provides it"))
    return ("Missing pieces and the packages that provide them:\n" + "\n".join(out) +
            "\nInstall the matching package with `sudo dnf install -y <package>`, then try the build again.")
