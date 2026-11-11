#!/usr/bin/env python3
"""Evaluate local models on typical assistant tasks.

    uv run python scripts/eval_local.py qwen3.5:4b [other-model ...]

Each task runs through the app's real Ollama client and compact prompt.
Read-only tools (search_packages, inspect_manual, read_file_snippet) are
executed for real, as in the app, and fed back to the model; the run stops at
the first proposed change (which is checked, never executed) or a text reply.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Callable, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.genai import types  # noqa: E402

from agent import installer  # noqa: E402
from agent.local_llm import OllamaAgentClient  # noqa: E402
from agent.package_search import execute_search_packages  # noqa: E402
from agent.prompts import build_compact_system_prompt  # noqa: E402
from agent.tools import (  # noqa: E402
    READONLY_TOOLS,
    execute_inspect_manual,
    execute_read_file_snippet,
)
from config.profile_manager import get_dynamic_session_info, get_or_create_profile  # noqa: E402

MAX_STEPS = 4


def user(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def flathub_failure() -> List[types.Content]:
    call = types.FunctionCall(name="propose_shell_command", id="call_0", args={
        "command": "flatpak install --user -y flathub org.videolan.VLC", "explanation": "install",
        "requires_root": False})
    return [
        user("install vlc"),
        types.Content(role="model", parts=[types.Part(function_call=call)]),
        types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id="call_0", name="propose_shell_command", response={"output": (
                "Command failed with exit code 1. Terminal output: error: No remote refs found for 'flathub'\n\n"
                "Please acknowledge this failure to the user, diagnose the error based on the terminal output, "
                "and propose a corrective tool call to resolve it.")}))]),
    ]


def app_install(app_id: str) -> Callable[[str], bool]:
    """Correct if it installs the exact Flathub ID, or first sets up Flathub."""
    return lambda c: (app_id in c and "flatpak" in c) or "remote-add" in c


def has(*words: str) -> Callable[[str], bool]:
    return lambda c: any(re.search(w, c) for w in words)


# (name, history, expected: "change" | "text", check on proposed command, should search first)
TASKS = [
    ("install vlc", [user("I want to watch videos, can you install VLC?")], "change", app_install("org.videolan.VLC"), True),
    ("install postman", [user("install postman")], "change", app_install("com.getpostman.Postman"), True),
    ("install spotify", [user("can you get spotify for me")], "change", app_install("com.spotify.Client"), True),
    ("install obs", [user("I want to record my screen with OBS")], "change", app_install("com.obsproject.Studio"), True),
    ("install htop", [user("install htop")], "change", has(r"\bhtop\b"), True),
    ("disk usage", [user("what is taking up space in my home folder?")], "change", has(r"\bdu\b", r"\bdf\b", "ncdu"), False),
    ("memory usage", [user("what's using all my memory?")], "change", has(r"\bfree\b", r"\bps\b", r"\btop\b"), False),
    ("update system", [user("update my system")], "change", has(r"dnf .*(upgrade|update)", "flatpak update"), False),
    ("wifi check", [user("is my wifi working? check it")], "change", has(r"\bnmcli\b", r"\bip\b", r"\bping\b"), False),
    ("chit-chat", [user("hi! what can you help me with?")], "text", None, False),
    ("vague", [user("fix it")], "text", None, False),
    ("file: proton rpm", [user("I downloaded the Proton VPN rpm, install it")], "change", has(r"^/.*protonvpn"), False),
    ("file: antigravity", [user("I downloaded the Antigravity tarball. Install it and add it to my app menu.")], "change", has(r"^/.*Antigravity"), False),
    ("file: cursor theme", [user("install the GoogleDot cursor theme I downloaded")], "change", has(r"^/.*GoogleDot"), False),
    ("download: ayugram", [user("download AyuGram for me, I don't have the file")], "change", has(r"(?i)ayugram"), False),
    ("recover: no flathub", flathub_failure(), "change", has("remote-add"), False),
]


def make_fixtures() -> None:
    """Fake downloads so file-install tasks run without touching the real Downloads folder."""
    import io
    import tarfile
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="eval-downloads-"))
    installer.DOWNLOAD_DIRS = (d,)
    (d / "protonvpn-stable-release-1.0.3-1.noarch.rpm").write_bytes(b"\xed\xab\xee\xdb" + b"\0" * 64)

    def pack(name: str, members: dict) -> None:
        with tarfile.open(d / name, "w:gz") as tf:
            for n, mode in members.items():
                info = tarfile.TarInfo(n)
                info.size, info.mode = 1, mode
                tf.addfile(info, io.BytesIO(b"x"))

    pack("Antigravity-linux-x64.tar.gz", {"Antigravity/antigravity": 0o755, "Antigravity/resources/icon.png": 0o644})
    pack("GoogleDot-Black.tar.gz", {"GoogleDot-Black/index.theme": 0o644, "GoogleDot-Black/cursors/left_ptr": 0o644})


def run_readonly(fc: types.FunctionCall) -> str:
    args = fc.args or {}
    if fc.name == "search_packages":
        return execute_search_packages(str(args.get("query", "")))
    if fc.name == "inspect_manual":
        return execute_inspect_manual(str(args.get("command_or_topic", "")))
    if fc.name == "inspect_download":
        from agent import downloads

        kind, value = downloads.inspect_or_offer(str(args.get("name", "")), installer._package_manager(), [])
        return value if kind == "text" else f"DOWNLOAD OFFERED: {value.title}"
    if fc.name == "read_file_snippet":
        return execute_read_file_snippet(str(args.get("file_path", "")), int(args.get("max_lines", 100)))
    return f"ERROR: unknown tool {fc.name}"


def run_task(client: OllamaAgentClient, history: List[types.Content]) -> dict:
    history = list(history)
    used: List[str] = []
    start = time.monotonic()
    for _ in range(MAX_STEPS):
        resp = client.generate(history)
        content = resp.candidates[0].content
        history.append(content)
        parts = content.parts or []
        calls = [p.function_call for p in parts if p.function_call]
        text = " ".join(p.text for p in parts if p.text).strip()
        changes = [c for c in calls if c.name not in READONLY_TOOLS]
        if changes:
            c = changes[0]
            command = str((c.args or {}).get("command") or (c.args or {}).get("path") or (c.args or {}).get("name", ""))
            swapped = installer.handwritten_install_target(command) if c.name == "propose_shell_command" else None
            if swapped:  # the app swaps in the verified plan for hand-written unpack/install commands
                command = str(swapped)
            return {"kind": "change", "tool": c.name, "command": command,
                    "title": (c.args or {}).get("title", ""), "used": used, "secs": time.monotonic() - start}
        if not calls:
            return {"kind": "text", "text": text, "used": used, "secs": time.monotonic() - start}
        results = []
        for c in calls:
            used.append(c.name)
            out = run_readonly(c)
            if out.startswith("DOWNLOAD OFFERED"):
                return {"kind": "change", "tool": c.name, "command": str((c.args or {}).get("name", "")),
                        "title": out, "used": used, "secs": time.monotonic() - start}
            results.append(types.Part(function_response=types.FunctionResponse(
                id=c.id or "", name=c.name, response={"output": out})))
        history.append(types.Content(role="user", parts=results))
    return {"kind": "stuck", "used": used, "secs": time.monotonic() - start}


def evaluate(model: str) -> None:
    make_fixtures()
    prompt = build_compact_system_prompt(get_or_create_profile(), get_dynamic_session_info(), "Beginner")
    client = OllamaAgentClient(system_prompt=prompt, model=model)
    print(f"=== {model}", flush=True)
    score = searched = installs = 0
    times = []
    for name, history, expect, check, should_search in TASKS:
        r = run_task(client, history)
        ok = r["kind"] == expect and (check is None or check(r.get("command", "")))
        if should_search:
            installs += 1
            searched += "search_packages" in r["used"]
        score += ok
        times.append(r["secs"])
        what = r.get("command") or r.get("text", "")[:70] or r["kind"]
        looks = f" [{', '.join(r['used'])}]" if r["used"] else ""
        print(f"{'OK' if ok else 'NO'}  {name:20s} {r['secs']:5.0f}s  {what[:90]}{looks}", flush=True)
    times.sort()
    print(json.dumps({"model": model, "score": f"{score}/{len(TASKS)}",
                      "searched_before_install": f"{searched}/{installs}",
                      "median_s": round(times[len(times) // 2])}), flush=True)


if __name__ == "__main__":
    for m in sys.argv[1:] or ["qwen3.5:4b"]:
        evaluate(m)
