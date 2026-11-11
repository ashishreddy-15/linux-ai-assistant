"""Install apps from files the user downloaded (RPM, DEB, tarball, AppImage…).

Everything here is deterministic code, not model output. The model only picks
a tool and a file; this module inspects the file *without extracting or
running it* and builds the whole install script. Each script is shown to the
user as one approval card, and a record is kept so the app can be removed
again later.

Untrusted-input rules: archive member names are only ever used through
``shlex.quote``; archives with absolute or ``..`` paths, control characters,
or absurd sizes are refused.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path.home()
OPT = HOME / ".local" / "opt"
BIN = HOME / ".local" / "bin"
APPS = HOME / ".local" / "share" / "applications"
ICONS = HOME / ".local" / "share" / "icons"
THEMES = HOME / ".local" / "share" / "themes"
FONTS = HOME / ".local" / "share" / "fonts"
RECORD_FILE = HOME / ".local" / "share" / "linux-assistant" / "installs.json"

DOWNLOAD_DIRS = (HOME / "Downloads", HOME / "Desktop", HOME)
MAX_MEMBERS = 200_000
MAX_BYTES = 8 * 1024**3
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar.zst", ".tar", ".zip")
SOURCE_MARKERS = {
    "CMakeLists.txt": "CMake", "meson.build": "Meson", "Cargo.toml": "Cargo (Rust)",
    "go.mod": "Go", "configure": "Autotools", "Makefile": "Make",
    "setup.py": "Python", "pyproject.toml": "Python",
}
_ICON_EXT = (".png", ".svg", ".xpm")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class InstallError(Exception):
    """A problem the user should hear about in plain words."""


@dataclass
class Plan:
    """A ready-to-approve install: one script plus what to remember afterwards."""

    kind: str
    name: str
    title: str
    effects: List[str]
    script: str
    requires_root: bool = False
    notes: List[str] = field(default_factory=list)
    record: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Finding and classifying files
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-._")
    return slug or "app"


def _stem(path: Path) -> str:
    name = path.name
    for suffix in ARCHIVE_SUFFIXES + (".AppImage", ".appimage", ".rpm", ".deb", ".run", ".sh"):
        if name.lower().endswith(suffix.lower()):
            return name[: -len(suffix)]
    return path.stem


def find_download(query: str) -> Path:
    """Resolve a path, or fuzzy-match a name in Downloads/Desktop."""
    q = (query or "").strip()
    if not q:
        raise InstallError("No file name was given.")
    p = Path(q).expanduser()
    if p.is_absolute() or "/" in q:
        if p.is_file():
            return p.resolve()
        raise InstallError(f"There is no file at {p}.")
    words = [w for w in re.split(r"[^a-z0-9]+", q.lower()) if w]
    hits: List[Path] = []
    for d in DOWNLOAD_DIRS:
        if not d.is_dir():
            continue
        try:
            entries = [e for e in d.iterdir() if e.is_file()]
        except OSError:
            continue
        for e in entries:
            if all(w in e.name.lower() for w in words):
                hits.append(e)
        if hits:
            break
    if not hits:
        raise InstallError(f"I couldn't find a file matching “{q}” in Downloads.")
    hits.sort(key=lambda e: e.stat().st_mtime, reverse=True)
    return hits[0].resolve()


def _file_type(path: Path) -> str:
    with path.open("rb") as fh:
        head = fh.read(16)
    low = path.name.lower()
    if head[:4] == b"\xed\xab\xee\xdb" or low.endswith(".rpm"):
        return "rpm"
    if head[:7] == b"!<arch>" and low.endswith(".deb"):
        return "deb"
    if head[:4] == b"\x7fELF":
        return "appimage" if low.endswith(".appimage") else "binary"
    if low.endswith(ARCHIVE_SUFFIXES):
        return "archive"
    if low.endswith((".run", ".sh")) or head[:2] == b"#!":
        return "script"
    return "unknown"


# ---------------------------------------------------------------------------
# Archives (listed in-process, never extracted during inspection)
# ---------------------------------------------------------------------------

@dataclass
class Member:
    path: str
    is_dir: bool
    executable: bool
    size: int


def _safe_member(name: str) -> str:
    if _CONTROL.search(name):
        raise InstallError("The archive contains file names with control characters, so I won't unpack it.")
    parts = Path(name).parts
    if name.startswith("/") or ".." in parts:
        raise InstallError("The archive has paths that would escape its folder, so I won't unpack it.")
    return "/".join(p for p in parts if p not in ("", "."))


def list_archive(path: Path) -> List[Member]:
    members: List[Member] = []
    total = 0
    try:
        if path.name.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    mode = (info.external_attr >> 16) & 0o7777
                    members.append(Member(_safe_member(info.filename), info.is_dir(), bool(mode & 0o111), info.file_size))
                    total += info.file_size
                    if len(members) > MAX_MEMBERS:
                        raise InstallError("The archive has an unreasonable number of files.")
        else:
            with tarfile.open(path) as tf:
                for info in tf:
                    if info.issym() or info.islnk():
                        _safe_member(info.name)
                        target = info.linkname
                        if target.startswith("/") or ".." in Path(target).parts:
                            raise InstallError("The archive has links pointing outside its folder, so I won't unpack it.")
                        members.append(Member(_safe_member(info.name), False, False, 0))
                    elif info.isdir() or info.isfile():
                        members.append(Member(_safe_member(info.name), info.isdir(), bool(info.mode & 0o111), info.size))
                        total += info.size
                    else:
                        raise InstallError("The archive contains special files (devices or pipes), so I won't unpack it.")
                    if len(members) > MAX_MEMBERS:
                        raise InstallError("The archive has an unreasonable number of files.")
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as exc:
        raise InstallError(f"I couldn't read this archive ({exc}).") from exc
    if total > MAX_BYTES:
        raise InstallError("The archive would unpack to more than 8 GB, so I won't do it automatically.")
    return [m for m in members if m.path]


def _top_level(members: List[Member]) -> Optional[str]:
    tops = {m.path.split("/")[0] for m in members}
    if len(tops) != 1:
        return None
    top = next(iter(tops))
    # A lone top-level *file* is not a wrapper directory.
    if any(m.path == top and not m.is_dir for m in members):
        return None
    return top


def _rel(member_path: str, top: Optional[str]) -> str:
    return member_path[len(top) + 1:] if top and member_path.startswith(top + "/") else member_path


def classify_archive(members: List[Member]) -> Dict[str, Any]:
    top = _top_level(members)
    rels = {_rel(m.path, top): m for m in members if m.path != top}
    names = set(rels)
    first_level = {r.split("/")[0] for r in names}

    for marker, system in SOURCE_MARKERS.items():
        if marker in first_level:
            return {"kind": "source", "top": top, "build_system": system}
    if "index.theme" in names and any(r.startswith("cursors/") for r in names):
        return {"kind": "cursor-theme", "top": top}
    if "index.theme" in names and ({"gtk-3.0", "gtk-4.0", "gnome-shell", "metacity-1"} & first_level):
        return {"kind": "gtk-theme", "top": top}
    if "index.theme" in names:
        return {"kind": "icon-theme", "top": top}
    fonts = [r for r in names if r.lower().endswith((".ttf", ".otf", ".ttc"))]
    if fonts and len(fonts) >= len([m for m in members if not m.is_dir]) // 2:
        return {"kind": "fonts", "top": top}

    candidates = [
        (r, m) for r, m in rels.items()
        if not m.is_dir and m.executable and not r.endswith((".so", ".sh", ".py")) and ".so." not in r
        and not re.search(r"(^|/)(chrome-sandbox|chrome_crashpad_handler|crashpad_handler|xdg-\w+)$", r)
    ]
    stem = slugify(top or "")
    candidates.sort(key=lambda rm: (
        0 if re.sub(r"[^a-z0-9]", "", Path(rm[0]).name.lower()) in re.sub(r"[^a-z0-9]", "", stem) or
        re.sub(r"[^a-z0-9]", "", stem) in re.sub(r"[^a-z0-9]", "", Path(rm[0]).name.lower()) else 1,
        rm[0].count("/"), -rm[1].size,
    ))
    if candidates:
        icons = [r for r in names if r.lower().endswith(_ICON_EXT)]
        icons.sort(key=lambda r: (
            0 if re.search(r"(256|512|128)", r) else 1,
            0 if r.lower().endswith(".png") else 1,
            0 if re.search(r"(icon|logo|" + re.escape(stem) + ")", r.lower()) else 1,
            r.count("/"),
        ))
        return {"kind": "app", "top": top, "exec": candidates[0][0], "icon": icons[0] if icons else ""}
    return {"kind": "unknown-archive", "top": top}


# ---------------------------------------------------------------------------
# Package files
# ---------------------------------------------------------------------------

def _run(args: List[str], timeout: int = 20) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout + proc.stderr).strip()


def inspect_rpm(path: Path) -> Dict[str, Any]:
    q = _run(["rpm", "-qp", "--queryformat", "%{NAME}\n%{VERSION}-%{RELEASE}\n%{VENDOR}\n%{SUMMARY}\n", "--", str(path)])
    lines = q.splitlines() + [""] * 4
    files = _run(["rpm", "-qpl", "--", str(path)], 30).splitlines()
    sig = _run(["rpm", "-Kv", "--", str(path)])
    return {
        "name": lines[0], "version": lines[1], "vendor": lines[2], "summary": lines[3],
        "adds_repo": any(f.startswith("/etc/yum.repos.d/") or f.startswith("/etc/zypp/repos.d/") for f in files),
        "signed": bool(re.search(r"Signature, key ID|signatures OK", sig)) and "NOT OK" not in sig,
        "has_scripts": bool(_run(["rpm", "-qp", "--scripts", "--", str(path)])),
    }


def inspect_deb(path: Path) -> Dict[str, Any]:
    out = _run(["dpkg-deb", "-f", str(path), "Package", "Version", "Maintainer", "Description"])
    fields = dict(m.groups() for m in re.finditer(r"^(\w+): (.*)$", out, re.M))
    return {"name": fields.get("Package", ""), "version": fields.get("Version", ""),
            "vendor": fields.get("Maintainer", ""), "summary": fields.get("Description", "")}


def _package_manager() -> str:
    for pm in ("dnf", "zypper", "apt"):
        if shutil.which(pm):
            return pm
    return ""


# ---------------------------------------------------------------------------
# Inspection (read-only) and planning
# ---------------------------------------------------------------------------

def inspect_file(path: Path) -> Dict[str, Any]:
    """Describe a downloaded file. Never extracts or runs it."""
    kind = _file_type(path)
    info: Dict[str, Any] = {"path": str(path), "file": path.name, "kind": kind,
                            "size_mb": round(path.stat().st_size / 1e6, 1)}
    if kind == "rpm":
        info.update(inspect_rpm(path))
    elif kind == "deb":
        info.update(inspect_deb(path))
    elif kind == "archive":
        members = list_archive(path)
        cls = classify_archive(members)
        info.update({"what": cls["kind"], "build_system": cls.get("build_system")})
        info["files"] = sum(1 for m in members if not m.is_dir)
    return info


def describe(info: Dict[str, Any]) -> str:
    """Text for the model: what the file is and what to do next."""
    kind = info["kind"]
    head = f"{info['file']} ({info['size_mb']} MB, {kind})"
    if kind in ("rpm", "deb"):
        extra = []
        if info.get("adds_repo"):
            extra.append("it adds a software repository")
        if info.get("signed") is not None and kind == "rpm":
            extra.append("signed" if info["signed"] else "NOT signed or key unknown")
        return (f"{head}: package {info.get('name')} {info.get('version')} by {info.get('vendor') or 'unknown vendor'}. "
                f"{info.get('summary', '')} {'; '.join(extra)}.\nNext: call install_from_file with path={info['path']}")
    if kind == "archive":
        what = info.get("what")
        if what == "source":
            return (f"{head}: source code ({info.get('build_system')}). Building from source is not supported yet; "
                    "tell the user it needs to be compiled and suggest the project's own instructions.")
        if what == "unknown-archive":
            return f"{head}: an archive with no recognisable app, theme or font. Ask the user what it is for."
        return f"{head}: {what} ({info.get('files')} files). Next: call install_from_file with path={info['path']}"
    if kind == "appimage":
        return f"{head}: an AppImage. Next: call install_from_file with path={info['path']}"
    if kind in ("binary", "script"):
        return f"{head}: a {kind}. I can't verify what it does, so tell the user to check where it came from before running it."
    return f"{head}: not a recognised installable file."


def _quote(p: Any) -> str:
    return shlex.quote(str(p))


def _desktop_script(name: str, label: str, exec_path: Path, icon: str, comment: str) -> str:
    from agent.tools import render_desktop_entry

    desktop = APPS / f"{name}.desktop"
    text = render_desktop_entry(label, str(exec_path), icon, "Utility;", comment)
    return (f"mkdir -p {_quote(APPS)}\n"
            f"printf '%s\\n' {_quote(text.rstrip())} > {_quote(desktop)}\n"
            f"update-desktop-database {_quote(APPS)} 2>/dev/null || true")


def _guard_free(path: Path) -> str:
    return (f"if [ -e {_quote(path)} ]; then echo 'Already installed at {path}. "
            f"Remove it first.'; exit 1; fi")


def build_plan(path: Path) -> Plan:
    info = inspect_file(path)
    kind = info["kind"]
    label = (info.get("name") or _stem(path)).replace("-", " ").replace("_", " ")
    name = slugify(info.get("name") or _stem(path))
    now = time.strftime("%Y-%m-%d %H:%M")
    base = {"name": name, "label": label, "source": str(path), "date": now}

    if kind in ("rpm", "deb"):
        pm = _package_manager()
        if kind == "rpm" and pm not in ("dnf", "zypper"):
            raise InstallError("This is an RPM package, but this system doesn't use RPM-based tools.")
        if kind == "deb" and pm != "apt":
            raise InstallError("This is a DEB package, but this system doesn't use apt.")
        install = {"dnf": "sudo dnf install -y", "zypper": "sudo zypper --non-interactive install", "apt": "sudo apt install -y"}[pm]
        remove = {"dnf": "sudo dnf remove -y", "zypper": "sudo zypper --non-interactive remove", "apt": "sudo apt remove -y"}[pm]
        pkg = info.get("name") or name
        effects = [f"Installs {pkg} {info.get('version', '')} system-wide".strip(), "Needs your password"]
        if info.get("adds_repo"):
            effects.append("Adds the vendor's software repository, so it can update with your system")
        if kind == "rpm" and not info.get("signed"):
            effects.append("Warning: this package is not signed or its key isn't known")
        return Plan("package", name, f"Install {label}", effects,
                    f"set -e\n{install} {_quote(path)}", True,
                    notes=["If this only added a repository, install the vendor's app package next."] if info.get("adds_repo") else [],
                    record={**base, "kind": "package", "package": pkg, "remove": f"{remove} {shlex.quote(pkg)}"})

    if kind == "appimage":
        dest = OPT / name
        exe = dest / path.name
        link = BIN / name
        script = "\n".join([
            "set -e", _guard_free(dest),
            f"mkdir -p {_quote(dest)} {_quote(BIN)}",
            f"cp -- {_quote(path)} {_quote(exe)}", f"chmod +x {_quote(exe)}",
            f"ln -sf {_quote(exe)} {_quote(link)}",
            _desktop_script(name, label, exe, "", f"{label} (AppImage)"),
            f"echo 'Installed {label}.'",
        ])
        return Plan("appimage", name, f"Install {label}",
                    [f"Copies the AppImage to {dest}", "Adds it to your app menu", "No password needed"], script,
                    record={**base, "kind": "appimage", "dirs": [str(dest)], "files": [str(link), str(APPS / f"{name}.desktop")]})

    if kind == "archive":
        members = list_archive(path)
        cls = classify_archive(members)
        what, top = cls["kind"], cls["top"]
        if what in ("source", "unknown-archive"):
            raise InstallError(describe(info))
        is_zip = path.name.lower().endswith(".zip")
        stem = slugify(top or _stem(path))

        if what in ("cursor-theme", "icon-theme", "gtk-theme", "fonts"):
            root = {"cursor-theme": ICONS, "icon-theme": ICONS, "gtk-theme": THEMES, "fonts": FONTS}[what]
            dest = root / (top or stem)
            if is_zip:
                extract = (f"mkdir -p {_quote(dest)} && unzip -q -- {_quote(path)} -d {_quote(dest)}"
                           if not top else f"mkdir -p {_quote(root)} && unzip -q -- {_quote(path)} -d {_quote(root)}")
            else:
                extract = (f"mkdir -p {_quote(dest)} && tar -xf {_quote(path)} -C {_quote(dest)} --no-same-owner"
                           + (" --strip-components=1" if top else ""))
            tail = {"fonts": "fc-cache -f 2>/dev/null || true",
                    "gtk-theme": "true", "icon-theme": f"gtk-update-icon-cache -q {_quote(dest)} 2>/dev/null || true",
                    "cursor-theme": "true"}[what]
            pretty = {"cursor-theme": "cursor theme", "icon-theme": "icon theme", "gtk-theme": "app theme", "fonts": "fonts"}[what]
            how = ("Pick it in Settings → Appearance (or Tweaks)." if what != "fonts" else "The fonts are available in apps after a restart.")
            return Plan(what, stem, f"Install the {top or stem} {pretty}",
                        [f"Unpacks it into {dest}", "Only affects your account; no password needed", how],
                        "\n".join(["set -e", _guard_free(dest), extract, tail, f"echo 'Installed. {how}'"]),
                        record={**base, "name": stem, "kind": what, "dirs": [str(dest)]})

        # Application bundle
        dest = OPT / stem
        exe = dest / cls["exec"]  # paths in `cls` are relative to the unwrapped root
        link = BIN / stem
        icon = str(dest / cls["icon"]) if cls.get("icon") else ""
        if is_zip and top:
            unpack = (f"mkdir -p {_quote(OPT)} && tmp=$(mktemp -d {_quote(OPT)}/.unpack.XXXXXX) && "
                      f"unzip -q -- {_quote(path)} -d \"$tmp\" && mv \"$tmp\"/{_quote(top)} {_quote(dest)} && rmdir \"$tmp\"")
        elif is_zip:
            unpack = f"mkdir -p {_quote(dest)} && unzip -q -- {_quote(path)} -d {_quote(dest)}"
        else:
            unpack = (f"mkdir -p {_quote(dest)} && tar -xf {_quote(path)} -C {_quote(dest)} --no-same-owner"
                      + (" --strip-components=1" if top else ""))
        script = "\n".join([
            "set -e", _guard_free(dest), unpack,
            f"chmod +x {_quote(exe)}", f"mkdir -p {_quote(BIN)}", f"ln -sf {_quote(exe)} {_quote(link)}",
            _desktop_script(stem, (top or stem).replace("-", " ").replace("_", " "), exe, icon, f"{stem} (installed by Linux Assistant)"),
            f"echo 'Installed {stem}.'",
        ])
        notes = []
        if re.search(r"electron|chrome-sandbox", " ".join(m.path for m in members[:5000]), re.I):
            notes.append("This looks like an Electron app; if it won't start, it may need to be launched with --no-sandbox.")
        return Plan("app", stem, f"Install {stem}",
                    [f"Unpacks it into {dest}", f"Adds a launcher to your app menu and `{stem}` to your terminal",
                     "No password needed"], script, notes=notes,
                    record={**base, "name": stem, "kind": "app", "dirs": [str(dest)],
                            "files": [str(link), str(APPS / f"{stem}.desktop")]})

    raise InstallError(describe(info))


# ---------------------------------------------------------------------------
# Install records and removal
# ---------------------------------------------------------------------------

def load_records() -> List[Dict[str, Any]]:
    try:
        data = json.loads(RECORD_FILE.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_records(records: List[Dict[str, Any]]) -> None:
    RECORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RECORD_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, indent=2))
    tmp.replace(RECORD_FILE)


def save_record(record: Dict[str, Any]) -> None:
    records = [r for r in load_records() if r.get("name") != record.get("name")]
    records.append(record)
    _save_records(records)


def forget_record(name: str) -> None:
    _save_records([r for r in load_records() if r.get("name") != name])


_REMOVABLE_ROOTS = (OPT, ICONS, THEMES, FONTS)
_REMOVABLE_FILES = (BIN, APPS)


def _under(path: str, roots: tuple) -> bool:
    p = Path(path).resolve() if Path(path).exists() or Path(path).is_symlink() else Path(os.path.normpath(path))
    if Path(path).is_symlink():
        p = Path(os.path.normpath(path))
    return any(root in p.parents for root in roots)


def find_record(query: str) -> Dict[str, Any]:
    records = load_records()
    q = slugify(query)
    for r in records:
        if q in (r.get("name"), slugify(r.get("label", ""))):
            return r
    matches = [r for r in records if q and q in r.get("name", "")]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(r["name"] for r in records) or "none"
    raise InstallError(f"I have no record of installing “{query}”. Apps I installed from files: {names}.")


def build_removal(query: str) -> Plan:
    rec = find_record(query)
    label = rec.get("label") or rec["name"]
    if rec.get("kind") == "package":
        return Plan("remove", rec["name"], f"Remove {label}", [f"Removes {rec['package']} from your system", "Needs your password"],
                    f"set -e\n{rec['remove']}", True, record={"forget": rec["name"]})
    steps = ["set -e"]
    for f in rec.get("files", []):
        if not _under(f, _REMOVABLE_FILES):
            raise InstallError(f"The install record lists {f}, which is outside the places I manage. Not removing anything.")
        steps.append(f"rm -f -- {_quote(f)}")
    for d in rec.get("dirs", []):
        if not _under(d, _REMOVABLE_ROOTS):
            raise InstallError(f"The install record lists {d}, which is outside the places I manage. Not removing anything.")
        steps.append(f"rm -rf -- {_quote(d)}")
    steps.append(f"update-desktop-database {_quote(APPS)} 2>/dev/null || true")
    steps.append(f"echo 'Removed {label}.'")
    return Plan("remove", rec["name"], f"Remove {label}",
                [f"Deletes {', '.join(rec.get('dirs', []))}", "Removes its menu entry", "Your own files and settings elsewhere stay"],
                "\n".join(steps), record={"forget": rec["name"]})


# ---------------------------------------------------------------------------
# Tool entry points
# ---------------------------------------------------------------------------

def execute_inspect_download(query: str) -> str:
    try:
        return describe(inspect_file(find_download(query)))
    except InstallError as exc:
        return f"ERROR: {exc}"
    except OSError as exc:
        return f"ERROR: couldn't read the file ({exc})."
