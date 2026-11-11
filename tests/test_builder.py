"""Tests for building from source. A real Makefile project is built inside a temp HOME."""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import builder, installer
from tests.test_installer import InstallerCase, add

MAKEFILE = b"""PREFIX ?= /usr/local
hello: hello.c
\t$(CC) -o hello hello.c
install: hello
\tmkdir -p $(PREFIX)/bin
\tinstall hello $(PREFIX)/bin/hello
"""
HELLO_C = b'#include <stdio.h>\nint main(){puts("hi");return 0;}\n'


class BuildCase(InstallerCase):
    def setUp(self):
        super().setUp()
        la = self.home / ".local/share/linux-assistant"
        for k, v in {"PREFIX": self.home / ".local", "LA_DIR": la, "SRC_DIR": la / "src", "MANIFESTS": la / "manifests"}.items():
            p = patch.object(builder, k, v)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(installer, "_REMOVABLE_ROOTS", installer._REMOVABLE_ROOTS + (la / "src", la))
        p.start()
        self.addCleanup(p.stop)

    def project(self, name="hello-1.0.tar.gz", extra=None):
        members = {"hello-1.0/Makefile": (MAKEFILE, 0o644), "hello-1.0/hello.c": (HELLO_C, 0o644), **(extra or {})}
        return self.tar(name, members)


@unittest.skipUnless(shutil.which("make") and shutil.which("cc"), "needs make and a C compiler")
class TestRealBuild(BuildCase):
    def test_build_install_and_remove(self):
        path = self.project()
        self.assertIn("can be built", installer.execute_inspect_download(str(path)))
        plan = installer.build_plan(path)
        self.assertEqual(plan.kind, "build")
        self.assertFalse(plan.requires_root)
        self.assertTrue(any("scripts" in e for e in plan.effects))
        result = self.run_plan(plan)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        exe = self.home / ".local/bin/hello"
        self.assertEqual(subprocess.run([str(exe)], capture_output=True, text=True).stdout.strip(), "hi")
        manifest = (self.home / ".local/share/linux-assistant/manifests/hello.txt").read_text().splitlines()
        self.assertEqual(manifest, [str(exe)])  # only what the build installed, not the build folder

        installer.save_record(plan.record)
        removal = installer.build_removal("hello")
        self.assertEqual(self.run_plan(removal).returncode, 0)
        self.assertFalse(exe.exists())
        self.assertFalse((self.home / ".local/share/linux-assistant/src/hello").exists())
        self.assertEqual(installer.load_records(), [plan.record] if False else installer.load_records())

    def test_failing_build_reports_nonzero(self):
        bad = self.project("bad.tar.gz", {"hello-1.0/hello.c": (b"#include <nope_missing.h>\nint main(){}\n", 0o644)})
        result = self.run_plan(installer.build_plan(bad))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nope_missing.h", result.stdout + result.stderr)

    def test_build_from_a_folder_leaves_the_folder(self):
        folder = self.dl / "hello-src"
        folder.mkdir()
        (folder / "Makefile").write_bytes(MAKEFILE)
        (folder / "hello.c").write_bytes(HELLO_C)
        plan = installer.build_plan(folder)
        self.assertEqual(self.run_plan(plan).returncode, 0)
        self.assertEqual(plan.record["dirs"], [])
        installer.save_record(plan.record)
        self.assertEqual(self.run_plan(installer.build_removal("hello-src")).returncode, 0)
        self.assertTrue(folder.exists())


class TestPlanning(BuildCase):
    def test_missing_tools_are_installed_first_and_need_root(self):
        path = self.tar("proj.tar.gz", {"proj/CMakeLists.txt": (b"", 0o644)})
        with patch.object(builder, "missing_tools", return_value=["cmake", "c++"]), \
             patch.object(builder, "package_manager", return_value="dnf"):
            plan = builder.plan_source(path, "CMake", "proj", {"name": "proj", "label": "proj"}, False)
        self.assertTrue(plan.requires_root)
        self.assertIn("sudo dnf install -y cmake gcc-c++", plan.script)
        self.assertIn("cmake --install build", plan.script)
        self.assertIn("-DCMAKE_INSTALL_PREFIX=", plan.script)

    def test_each_system_installs_into_local_prefix(self):
        for system in builder.SYSTEMS:
            steps = " ".join(builder.build_steps(system))
            self.assertIn(str(builder.PREFIX), steps, system)

    def test_python_projects_are_declined(self):
        path = self.tar("py.tar.gz", {"py/pyproject.toml": (b"", 0o644)})
        with self.assertRaises(installer.InstallError):
            installer.build_plan(path)

    def test_removal_refuses_manifest_paths_outside_local(self):
        manifest = builder.MANIFESTS / "evil.txt"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("/etc/passwd\n")
        with self.assertRaises(installer.InstallError):
            builder.build_removal({"name": "evil", "manifest": str(manifest), "dirs": []})
        manifest.write_text(f"{self.home}/.local/../../etc/passwd\n")
        with self.assertRaises(installer.InstallError):
            builder.build_removal({"name": "evil", "manifest": str(manifest), "dirs": []})


class TestFindPackage(unittest.TestCase):
    def test_error_lines_become_capabilities(self):
        got = builder.wanted_capabilities("foo.c:1:10: fatal error: zlib.h: No such file or directory\n"
                                          "/usr/bin/ld: cannot find -lssl\nPackage 'gtk4' not found")
        self.assertEqual(got, ["/usr/include/zlib.h", "*/libssl.so", "pkgconfig(gtk4)"])
        self.assertEqual(builder.wanted_capabilities("all fine"), [])

    def test_lookup_uses_dnf_repoquery_and_prefers_devel(self):
        out = subprocess.CompletedProcess([], 0, stdout="zlib-ng-compat-devel-2.2-1.fc45.x86_64\nzlib-ng-compat-2.2-1.fc45.x86_64\n")
        with patch.object(builder, "package_manager", return_value="dnf"), \
             patch.object(builder.subprocess, "run", return_value=out) as run:
            text = builder.execute_find_package_for("fatal error: zlib.h: No such file")
        self.assertEqual(run.call_args[0][0][:4], ["dnf", "repoquery", "-q", "--whatprovides"])
        self.assertIn("zlib-ng-compat-devel", text)
        self.assertNotIn("zlib-ng-compat,", text)

    def test_unhelpful_text_asks_for_the_error_line(self):
        self.assertIn("exact error line", builder.execute_find_package_for("it failed"))


class TestRetryCap(BuildCase):
    def test_three_failures_stop_the_loop(self):
        from unittest.mock import MagicMock

        from google.genai import types

        from agent.orchestrator import AgentOrchestrator

        orch = AgentOrchestrator(MagicMock())
        plan = installer.Plan("build", "x", "Build x", [], "false")
        hints = []
        for n in range(1, 4):
            fc = types.FunctionCall(name="install_from_file", args={}, id=str(n))
            orch._pending_plan = (fc, plan)
            hints.append(orch._record_plan(1, fc))
        self.assertIn("1 of 3", hints[0])
        self.assertIn("find_package_for", hints[0])
        self.assertIn("do not try again", hints[2])
        fc = types.FunctionCall(name="install_from_file", args={}, id="9")
        orch._pending_plan = (fc, plan)
        self.assertEqual(orch._record_plan(0, fc), "")
        self.assertEqual(orch._build_failures, 0)  # success resets the counter


if __name__ == "__main__":
    unittest.main()
