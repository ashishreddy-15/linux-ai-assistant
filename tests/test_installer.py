"""Tests for installing from downloaded files. Scripts run for real, inside a temp HOME."""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from agent import installer


def add(tf: tarfile.TarFile, name: str, data: bytes = b"x", mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), mode
    tf.addfile(info, io.BytesIO(data))


class InstallerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        home = self.tmp / "home"
        self.dl = home / "Downloads"
        self.dl.mkdir(parents=True)
        local = home / ".local"
        patches = {
            "HOME": home, "OPT": local / "opt", "BIN": local / "bin", "APPS": local / "share/applications",
            "ICONS": local / "share/icons", "THEMES": local / "share/themes", "FONTS": local / "share/fonts",
            "RECORD_FILE": local / "share/linux-assistant/installs.json", "DOWNLOAD_DIRS": (self.dl,),
            "_REMOVABLE_ROOTS": (local / "opt", local / "share/icons", local / "share/themes", local / "share/fonts"),
            "_REMOVABLE_FILES": (local / "bin", local / "share/applications"),
        }
        for k, v in patches.items():
            p = patch.object(installer, k, v)
            p.start()
            self.addCleanup(p.stop)
        self.home = home

    def tar(self, name: str, members: dict) -> Path:
        path = self.dl / name
        with tarfile.open(path, "w:gz") as tf:
            for n, (data, mode) in members.items():
                add(tf, n, data, mode)
        return path

    def run_plan(self, plan):
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", "-c", plan.script], capture_output=True, text=True, env=env)


class TestApps(InstallerCase):
    def app(self):
        return self.tar("Antigravity-1.2.tar.gz", {
            "Antigravity/antigravity": (b"#!/bin/sh\necho hi\n", 0o755),
            "Antigravity/chrome-sandbox": (b"x", 0o755),
            "Antigravity/lib/libfoo.so": (b"x", 0o755),
            "Antigravity/resources/app/icon-256.png": (b"png", 0o644),
        })

    def test_find_by_fuzzy_name(self):
        self.app()
        self.assertEqual(installer.find_download("antigravity").name, "Antigravity-1.2.tar.gz")
        with self.assertRaises(installer.InstallError):
            installer.find_download("nothing-like-it")

    def test_picks_the_real_executable(self):
        cls = installer.classify_archive(installer.list_archive(self.app()))
        self.assertEqual(cls["kind"], "app")
        self.assertEqual(cls["exec"], "antigravity")
        self.assertTrue(cls["icon"].endswith("icon-256.png"))

    def test_install_then_remove(self):
        plan = installer.build_plan(self.app())
        self.assertFalse(plan.requires_root)
        result = self.run_plan(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        exe = self.home / ".local/opt/antigravity/antigravity"
        self.assertTrue(exe.exists())
        desktop = (self.home / ".local/share/applications/antigravity.desktop").read_text()
        self.assertIn(f"Exec={exe}", desktop)
        self.assertNotIn("~", desktop)
        self.assertTrue((self.home / ".local/bin/antigravity").is_symlink())
        self.assertNotEqual(self.run_plan(plan).returncode, 0)  # installing twice is refused

        installer.save_record(plan.record)
        removal = installer.build_removal("antigravity")
        self.assertEqual(self.run_plan(removal).returncode, 0)
        self.assertFalse(exe.exists())
        self.assertFalse((self.home / ".local/bin/antigravity").is_symlink())

    def test_zip_with_wrapper_dir(self):
        path = self.dl / "tool.zip"
        with zipfile.ZipFile(path, "w") as zf:
            info = zipfile.ZipInfo("tool/tool")
            info.external_attr = 0o755 << 16
            zf.writestr(info, "#!/bin/sh\n")
        result = self.run_plan(installer.build_plan(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / ".local/opt/tool/tool").exists())

    def test_appimage(self):
        path = self.dl / "Cool-App.AppImage"
        path.write_bytes(b"\x7fELF" + b"\0" * 100)
        plan = installer.build_plan(path)
        self.assertEqual(self.run_plan(plan).returncode, 0)
        self.assertTrue(os.access(self.home / ".local/opt/cool-app/Cool-App.AppImage", os.X_OK))


class TestHarderArchives(InstallerCase):
    def test_vendor_installer_runs_from_work_folder_and_shows_script(self):
        path = self.tar("tool-1.0.tar.gz", {
            "tool-1.0/install.sh": (b"#!/bin/bash\ntouch \"$HOME/installed-by-vendor\"\n", 0o755),
            "tool-1.0/data.bin": (b"x", 0o644),
        })
        self.assertIn("own installer", installer.execute_inspect_download(str(path)))
        plan = installer.build_plan(path)
        self.assertEqual(plan.kind, "installer")
        self.assertIn("touch", plan.notes[0])
        result = self.run_plan(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / "installed-by-vendor").exists())
        installer.save_record(plan.record)
        with self.assertRaises(installer.InstallError):
            installer.build_removal("tool")

    def test_launcher_script_app(self):
        path = self.tar("jtool.tar.gz", {
            "jtool/bin/jtool.sh": (b"#!/bin/sh\n", 0o755), "jtool/lib/a.jar": (b"x", 0o644),
            "jtool/install-docs.sh": (b"#!/bin/sh\n", 0o755)})
        plan = installer.build_plan(path)
        self.assertEqual(self.run_plan(plan).returncode, 0)
        self.assertTrue((self.home / ".local/opt/jtool/bin/jtool.sh").exists())
        self.assertTrue(any("script" in n for n in plan.notes))

    def test_missing_permission_bits(self):
        path = self.tar("flat.tar.gz", {"flat/flat": (b"#!/bin/sh\n", 0o644), "flat/readme.txt": (b"x", 0o644)})
        plan = installer.build_plan(path)
        self.assertEqual(self.run_plan(plan).returncode, 0)
        self.assertTrue(os.access(self.home / ".local/opt/flat/flat", os.X_OK))

    def test_several_programs_lists_the_others(self):
        path = self.tar("suite.tar.gz", {
            "suite/alpha": (b"x", 0o755), "suite/beta": (b"x", 0o755), "suite/gamma": (b"x", 0o755)})
        plan = installer.build_plan(path)
        self.assertTrue(any("Other programs" in n for n in plan.notes))
        clear = installer.build_plan(self.tar("solo.tar.gz", {"solo/solo": (b"x", 0o755), "solo/other": (b"x", 0o755)}))
        self.assertFalse(any("Other programs" in n for n in clear.notes))

    def test_bundled_desktop_file_supplies_category_and_name(self):
        path = self.tar("edit.tar.gz", {
            "edit/edit": (b"x", 0o755),
            "edit/edit.desktop": (b"[Desktop Entry]\nName=Super Edit\nCategories=Development;TextEditor;\nExec=evil\n", 0o644)})
        self.assertEqual(self.run_plan(installer.build_plan(path)).returncode, 0)
        text = (self.home / ".local/share/applications/edit.desktop").read_text()
        self.assertIn("Name=Super Edit", text)
        self.assertIn("Categories=Development;TextEditor;", text)
        self.assertNotIn("evil", text)

    def test_electron_without_sandbox_support_gets_flag(self):
        path = self.tar("el.tar.gz", {"el/el": (b"x", 0o755), "el/chrome-sandbox": (b"x", 0o755)})
        with patch.object(installer, "sandbox_works", return_value=False):
            plan = installer.build_plan(path)
        self.run_plan(plan)
        self.assertIn("--no-sandbox", (self.home / ".local/share/applications/el.desktop").read_text())
        with patch.object(installer, "sandbox_works", return_value=True):
            plan = installer.build_plan(path)
        self.assertNotIn("--no-sandbox", plan.script)

    def test_missing_library_warns_but_succeeds(self):
        path = self.tar("lib.tar.gz", {"lib/lib": (b"#!/bin/sh\n", 0o755)})
        plan = installer.build_plan(path)
        self.assertIn("ldd", plan.script)
        self.assertEqual(self.run_plan(plan).returncode, 0)


class TestThemes(InstallerCase):
    def test_cursor_theme(self):
        path = self.tar("GoogleDot-Black.tar.gz", {
            "GoogleDot-Black/index.theme": (b"[Icon Theme]", 0o644),
            "GoogleDot-Black/cursors/left_ptr": (b"c", 0o644),
        })
        plan = installer.build_plan(path)
        self.assertEqual(plan.kind, "cursor-theme")
        self.assertEqual(self.run_plan(plan).returncode, 0)
        self.assertTrue((self.home / ".local/share/icons/GoogleDot-Black/cursors/left_ptr").exists())


class TestSafety(InstallerCase):
    def test_escaping_archive_refused(self):
        path = self.dl / "evil.tar"
        with tarfile.open(path, "w") as tf:
            add(tf, "../../etc/evil")
        with self.assertRaises(installer.InstallError):
            installer.list_archive(path)

    def test_escaping_symlink_refused(self):
        path = self.dl / "evil2.tar"
        with tarfile.open(path, "w") as tf:
            info = tarfile.TarInfo("app/link")
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            tf.addfile(info)
        with self.assertRaises(installer.InstallError):
            installer.list_archive(path)

    def test_hostile_names_are_quoted(self):
        path = self.tar("x.tar.gz", {"app/$(touch pwned)": (b"#!/bin/sh\n", 0o755)})
        plan = installer.build_plan(path)
        self.run_plan(plan)
        self.assertFalse((self.home / "pwned").exists())
        self.assertFalse(Path("pwned").exists())

    def test_source_is_not_installed(self):
        path = self.tar("proj.tar.gz", {"proj/CMakeLists.txt": (b"", 0o644)})
        self.assertIn("source code", installer.execute_inspect_download(str(path)))
        with self.assertRaises(installer.InstallError):
            installer.build_plan(path)

    def test_removal_refuses_paths_outside_managed_dirs(self):
        installer.save_record({"name": "bad", "kind": "app", "dirs": ["/usr"], "files": []})
        with self.assertRaises(installer.InstallError):
            installer.build_removal("bad")

    def test_unknown_app_removal_lists_known(self):
        installer.save_record({"name": "known", "kind": "app", "dirs": [], "files": []})
        with self.assertRaises(installer.InstallError) as ctx:
            installer.build_removal("other")
        self.assertIn("known", str(ctx.exception))


class TestPackages(InstallerCase):
    def test_rpm_plan_needs_root_and_warns_when_unsigned(self):
        path = self.dl / "protonvpn-stable-release-1.0.rpm"
        path.write_bytes(b"\xed\xab\xee\xdb" + b"\0" * 20)
        info = {"name": "protonvpn-stable-release", "version": "1.0-1", "vendor": "Proton", "summary": "repo",
                "adds_repo": True, "signed": False, "has_scripts": False}
        with patch.object(installer, "inspect_rpm", return_value=info), \
             patch.object(installer, "_package_manager", return_value="dnf"):
            plan = installer.build_plan(path)
        self.assertTrue(plan.requires_root)
        self.assertIn("sudo dnf install -y", plan.script)
        self.assertTrue(any("repository" in e for e in plan.effects))
        self.assertTrue(any("not signed" in e for e in plan.effects))


if __name__ == "__main__":
    unittest.main()
