import os
import subprocess
import tempfile
import time
import unittest

import claude_update as cu


def fake_bin(root, rel, name="claude"):
    real = os.path.join(root, rel)
    os.makedirs(os.path.dirname(real), exist_ok=True)
    open(real, "w").close()
    link_dir = os.path.join(root, "bin")
    os.makedirs(link_dir, exist_ok=True)
    link = os.path.join(link_dir, name)
    os.symlink(real, link)
    return link


class Method(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="cu-"))

    def test_native_install_updates_with_claude_update(self):
        link = fake_bin(self.root, ".local/share/claude/versions/2.1.295")
        self.assertEqual(cu.update_command(link, "latest"), ("native", [link, "update"]))

    def test_npm_install_uses_npm_of_the_same_prefix_and_the_channel(self):
        link = fake_bin(self.root, "lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe")
        how, cmd = cu.update_command(link, "stable")
        self.assertEqual(how, "npm")
        self.assertEqual(cmd, [os.path.join(self.root, "bin", "npm"), "install", "-g", "@anthropic-ai/claude-code@stable"])

    def test_homebrew_cask(self):
        link = fake_bin(self.root, "Caskroom/claude-code/2.1.0/claude")
        self.assertEqual(cu.update_command(link, "latest"), ("brew", ["brew", "upgrade", "--cask", "claude-code"]))

    def test_unknown_install_has_no_command(self):
        link = fake_bin(self.root, "opt/somewhere/claude")
        self.assertEqual(cu.update_command(link, "latest"), ("unknown", None))

    def test_the_nested_wrapper_of_hub_sessions_is_not_the_hub_claude(self):
        nested = os.path.join(self.root, "hosts", "nested")
        os.makedirs(nested)
        for d in (nested, os.path.join(self.root, "real")):
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, "claude")
            open(p, "w").close()
            os.chmod(p, 0o755)
        env = {"PATH": os.pathsep.join([nested, os.path.join(self.root, "real")])}
        self.assertEqual(cu.find_claude(env), os.path.join(self.root, "real", "claude"))
        self.assertEqual(cu.find_claude({"HUB_CLAUDE": "/x/claude --flag", "PATH": nested}), "/x/claude")


class Versions(unittest.TestCase):
    def test_available_reads_the_channel_tag(self):
        tags = {"stable": "2.1.287", "latest": "2.1.296", "next": "2.1.296"}
        self.assertEqual(cu.available("latest", fetch=lambda url: tags), "2.1.296")
        self.assertEqual(cu.available("stable", fetch=lambda url: tags), "2.1.287")

    def test_no_network_means_unknown_not_an_error(self):
        def down(url):
            raise OSError("no route")
        self.assertIsNone(cu.available("latest", fetch=down))
        self.assertIsNone(cu.available("latest", fetch=lambda url: {"latest": "<html>"}))

    def test_newer_compares_numbers_not_strings(self):
        self.assertTrue(cu.newer("2.1.300", "2.1.296"))
        self.assertTrue(cu.newer("2.10.0", "2.9.9"))
        self.assertFalse(cu.newer("2.1.295", "2.1.295"))
        self.assertFalse(cu.newer(None, "2.1.295"))

    def test_channel_from_claude_settings(self):
        d = tempfile.mkdtemp(prefix="cu-cfg-")
        with open(os.path.join(d, "settings.json"), "w") as f:
            f.write('{"autoUpdatesChannel": "stable"}')
        self.assertEqual(cu.channel({"CLAUDE_CONFIG_DIR": d}), "stable")
        self.assertEqual(cu.channel({"CLAUDE_CONFIG_DIR": tempfile.mkdtemp()}), "latest")

    def test_version_parses_claude_output(self):
        run = lambda *a, **k: subprocess.CompletedProcess(a, 0, "2.1.295 (Claude Code)\n", "")  # noqa: E731
        self.assertEqual(cu.version("/x/claude", run=run), "2.1.295")


class Stale(unittest.TestCase):
    def test_sessions_started_before_the_install_run_the_old_version(self):
        out = "  101 Mon Oct 12 10:00:00 2026\n  102 Mon Oct 12 12:00:00 2026\n"
        run = lambda *a, **k: subprocess.CompletedProcess(a, 0, out, "")  # noqa: E731
        installed = time.mktime(time.strptime("Mon Oct 12 11:00:00 2026", "%a %b %d %H:%M:%S %Y"))
        sessions = [{"name": "old", "pid": 101}, {"name": "new", "pid": 102}, {"name": "gone", "pid": 103},
                    {"name": "nopid"}]
        self.assertEqual(cu.stale(sessions, installed, run=run), ["old"])
        self.assertEqual(cu.stale(sessions, None, run=run), [])

    def test_env_for_the_installer_has_no_hub_variables(self):
        env = cu.clean_env({"HOME": "/h", "PATH": "/bin", "HUB_SESSION": "x", "HUB_STATE_DIR": "/s",
                            "CLAUDE_CONFIG_DIR": "/c", "ANTHROPIC_API_KEY": "k"})
        self.assertEqual(env, {"HOME": "/h", "PATH": "/bin", "CLAUDE_CONFIG_DIR": "/c"})


if __name__ == "__main__":
    unittest.main()
