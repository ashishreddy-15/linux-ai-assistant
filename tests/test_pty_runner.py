"""Tests for executor/pty_runner.py – is_high_risk() classification."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from executor.pty_runner import (
    AUTH_REQUIRED_MESSAGE,
    CommandRunner,
    detect_askpass,
    ensure_askpass_script,
    get_askpass_agent,
    is_high_risk,
    prepare_command,
)


class TestIsHighRisk(unittest.TestCase):
    """Validate that dangerous command patterns are correctly flagged."""

    # --- Should be HIGH RISK ---

    def test_rm_rf_root(self):
        self.assertTrue(is_high_risk("rm -rf /"))

    def test_rm_rf_root_trailing_space(self):
        self.assertTrue(is_high_risk("rm -rf / "))

    def test_rm_fr_root(self):
        """Flags -fr variant as well."""
        self.assertTrue(is_high_risk("rm -fr /"))

    def test_rm_rf_root_with_sudo(self):
        self.assertTrue(is_high_risk("sudo rm -rf /"))

    def test_mkfs_ext4(self):
        self.assertTrue(is_high_risk("mkfs.ext4 /dev/sda1"))

    def test_mkfs_no_fs_type(self):
        self.assertTrue(is_high_risk("mkfs /dev/sda"))

    def test_dd_with_if(self):
        self.assertTrue(is_high_risk("dd if=/dev/zero of=/dev/sda bs=4M"))

    def test_dd_if_dev_random(self):
        self.assertTrue(is_high_risk("sudo dd if=/dev/random of=/dev/nvme0n1"))

    def test_redirect_to_fstab(self):
        self.assertTrue(is_high_risk("echo 'something' > /etc/fstab"))

    def test_shred_block_device(self):
        self.assertTrue(is_high_risk("shred -vzn 3 /dev/sda"))

    def test_wipefs(self):
        self.assertTrue(is_high_risk("wipefs -a /dev/sda"))

    def test_fdisk_on_device(self):
        self.assertTrue(is_high_risk("fdisk /dev/sda"))

    def test_parted(self):
        self.assertTrue(is_high_risk("parted /dev/nvme0n1 mklabel gpt"))

    def test_gdisk(self):
        self.assertTrue(is_high_risk("gdisk /dev/sdb"))

    def test_sgdisk(self):
        self.assertTrue(is_high_risk("sgdisk --zap-all /dev/sda"))

    # --- Should NOT be high risk ---

    def test_safe_rm(self):
        """rm -rf on user directory is not system-destructive."""
        self.assertFalse(is_high_risk("rm -rf ~/.local/opt/postman"))

    def test_safe_pacman(self):
        self.assertFalse(is_high_risk("pacman -S neovim"))

    def test_safe_mkdir(self):
        self.assertFalse(is_high_risk("mkdir -p ~/.local/bin"))

    def test_safe_tar(self):
        self.assertFalse(is_high_risk("tar -xzf postman.tar.gz -C ~/.local/opt"))

    def test_safe_cp(self):
        self.assertFalse(is_high_risk("cp postman.desktop ~/.local/share/applications/"))

    def test_safe_chmod_file(self):
        self.assertFalse(is_high_risk("chmod +x ~/.local/bin/postman"))

    def test_safe_echo(self):
        self.assertFalse(is_high_risk("echo 'hello'"))

    def test_safe_cat_fstab(self):
        """Reading fstab is fine; only overwriting is risky."""
        self.assertFalse(is_high_risk("cat /etc/fstab"))

    def test_dd_without_if(self):
        """dd without if= is not classified as high risk (e.g. reading stdin)."""
        self.assertFalse(is_high_risk("dd of=output.img bs=4M"))


class TestPrepareCommand(unittest.TestCase):
    """Validate conversion of sudo to sudo -A and preservation of pkexec."""

    def test_sudo_converted_to_sudo_A(self):
        self.assertEqual(prepare_command("sudo pacman -Syu"), "sudo -A pacman -Syu")

    def test_sudo_with_flags_converted(self):
        self.assertEqual(prepare_command("sudo -E apt update"), "sudo -A -E apt update")

    def test_sudo_already_with_A_not_duplicated(self):
        self.assertEqual(prepare_command("sudo -A dnf install curl"), "sudo -A dnf install curl")
        self.assertEqual(prepare_command("sudo -AE pacman -S git"), "sudo -AE pacman -S git")

    def test_pkexec_not_converted(self):
        self.assertEqual(prepare_command("pkexec pacman -Syu"), "pkexec pacman -Syu")

    def test_pkexec_with_sudo_not_converted(self):
        self.assertEqual(prepare_command("pkexec sudo something"), "pkexec sudo something")

    def test_chained_sudo_both_converted(self):
        cmd = "sudo apt update && sudo apt install -y git"
        self.assertEqual(prepare_command(cmd), "sudo -A apt update && sudo -A apt install -y git")

    def test_command_without_sudo_unchanged(self):
        self.assertEqual(prepare_command("ls -la /tmp"), "ls -la /tmp")

    def test_bracketed_sudo_not_converted(self):
        self.assertEqual(prepare_command("echo '[sudo] password'"), "echo '[sudo] password'")


class TestAskpassHelpers(unittest.TestCase):
    """Validate askpass detection and script generation."""

    def test_detect_askpass_from_env(self):
        with patch.dict(os.environ, {"SUDO_ASKPASS": "/bin/true"}):
            self.assertEqual(detect_askpass(), "/bin/true")

    def test_detect_askpass_desktop_agent(self):
        with patch.dict(os.environ, {}, clear=True), patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/kssh-askpass" if cmd == "kssh-askpass" else None
            self.assertEqual(detect_askpass(), "/usr/bin/kssh-askpass")

    def test_ensure_askpass_script_creates_executable(self):
        script_path = ensure_askpass_script()
        self.assertTrue(os.path.exists(script_path))
        self.assertTrue(os.access(script_path, os.X_OK))

    def test_get_askpass_agent_returns_path(self):
        agent_path = get_askpass_agent()
        self.assertIsInstance(agent_path, str)
        self.assertTrue(len(agent_path) > 0)


class TestCommandRunnerAuthAndTimeout(unittest.TestCase):
    """Validate CommandRunner execution, sudo prompt detection, and timeout."""

    def test_normal_command_execution(self):
        runner = CommandRunner()
        outputs: list[str] = []
        finished: list[tuple[int, str]] = []
        runner.output_received.connect(outputs.append)
        runner.command_finished.connect(lambda code, summary: finished.append((code, summary)))

        runner.run("echo 'runner test successful'")
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0][0], 0)
        self.assertIn("runner test successful", finished[0][1])

    def test_sudo_prompt_fast_fail(self):
        runner = CommandRunner()
        outputs: list[str] = []
        finished: list[tuple[int, str]] = []
        runner.output_received.connect(outputs.append)
        runner.command_finished.connect(lambda code, summary: finished.append((code, summary)))

        cmd = "printf '[sudo] password for testuser: '; sleep 10"
        runner.run(cmd)

        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0][0], 1)
        self.assertIn(AUTH_REQUIRED_MESSAGE, finished[0][1])
        self.assertTrue(any(AUTH_REQUIRED_MESSAGE in out for out in outputs))

    def test_timeout_execution(self):
        runner = CommandRunner()
        finished: list[tuple[int, str]] = []
        runner.command_finished.connect(lambda code, summary: finished.append((code, summary)))

        runner.run("sleep 5", timeout=0.2)
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0][0], 124)
        self.assertIn("timed out", finished[0][1].lower())


if __name__ == "__main__":
    unittest.main()
