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


NOW = 1_800_000_000.0   # a fixed «now» so that the days left are exact


def status_run(payload):
    return lambda *a, **k: subprocess.CompletedProcess(a, 0, payload, "")


class Account(unittest.TestCase):
    def test_expiry_reads_seconds_milliseconds_and_iso(self):
        self.assertEqual(cu.expiry(1_800_000_000), 1_800_000_000.0)
        self.assertEqual(cu.expiry(1_800_000_000_000), 1_800_000_000.0)
        self.assertEqual(cu.expiry("2027-01-15T08:00:00Z"), 1_800_000_000.0)
        for nothing in (None, "", "soon", True):
            self.assertIsNone(cu.expiry(nothing))

    def test_account_from_status_json(self):
        out = ('{"loggedIn": true, "authMethod": "claude.ai", "email": "owner@example.com", "subscriptionType": "max",'
               ' "loginExpiresAt": %d, "configDirectory": "/cfg"}' % (NOW + 6.5 * 86400))
        got = cu.account("claude", now=NOW, env={"CLAUDE_CONFIG_DIR": "/cfg"}, run=status_run(out))
        self.assertEqual((got["known"], got["logged_in"], got["email"], got["plan"]), (True, True, "owner@example.com", "Claude Max"))
        self.assertEqual(got["days_left"], 6)
        self.assertTrue(got["config_match"])

    def test_expiry_that_claude_does_not_report_is_none_not_expired(self):
        out = '{"loggedIn": true, "email": "owner@example.com", "loginExpiresAt": null}'
        got = cu.account("claude", now=NOW, env={"CLAUDE_CONFIG_DIR": "/cfg"}, run=status_run(out))
        self.assertIsNone(got["expires_at"])
        self.assertIsNone(got["days_left"])

    def test_an_expiry_in_the_past_is_minus_one_day(self):
        out = '{"loggedIn": true, "loginExpiresAt": %d}' % (NOW - 5)
        self.assertEqual(cu.account("claude", now=NOW, run=status_run(out))["days_left"], -1)

    def test_status_of_another_profile_is_not_a_match(self):
        out = '{"loggedIn": true, "configDirectory": "/other"}'
        got = cu.account("claude", now=NOW, env={"CLAUDE_CONFIG_DIR": "/cfg"}, run=status_run(out))
        self.assertFalse(got["config_match"])

    def test_no_json_means_unknown(self):
        self.assertEqual(cu.account("claude", run=status_run("not json")), {"known": False})
        self.assertEqual(cu.account("claude", run=status_run("")), {"known": False})

        def down(*a, **k):
            raise OSError("no claude")
        self.assertEqual(cu.account("claude", run=down), {"known": False})

    def test_json_after_a_banner_line_is_read(self):
        self.assertEqual(cu.auth_status("claude", run=status_run('update available\n{"loggedIn": false}')), {"loggedIn": False})


class Fallen(unittest.TestCase):
    def row(self, name, text, state="idle", ago=3600):
        return {"name": name, "state": state, "ts": NOW - ago, "text": text}

    def test_sessions_with_an_auth_error_are_listed_with_the_earliest_time_and_the_reason(self):
        last = [self.row("sample-b", "Failed to authenticate. OAuth session expired and could not be refreshed", ago=600),
                self.row("sample-a", "API Error: 401 authentication_failed", ago=7200)]
        got = cu.fallen(last, now=NOW)
        self.assertEqual(got["names"], ["sample-a", "sample-b"])
        self.assertEqual(got["since"], NOW - 7200)
        self.assertIn("OAuth session expired", got["reason"])

    def test_other_errors_busy_sessions_and_old_errors_are_not_fallen(self):
        last = [self.row("limit", "You've hit your session limit"), self.row("busy", "OAuth session expired", state="busy"),
                self.row("old", "OAuth session expired", ago=3 * 86400), {"state": "idle", "text": "OAuth session expired"},
                "junk"]
        self.assertEqual(cu.fallen(last, now=NOW), {"names": [], "since": None, "reason": None})

    def test_unknown_auth_wording_is_quoted_short(self):
        got = cu.fallen([self.row("sample-a", "Invalid API key. " + "x" * 400)], now=NOW)
        self.assertEqual(got["names"], ["sample-a"])
        self.assertLessEqual(len(got["reason"]), 120)


class AccountEndpoint(unittest.TestCase):
    def test_account_info_adds_the_fallen_and_caches_the_status_for_a_minute(self):
        import main
        calls = []
        keep = cu.account
        cu.account = lambda path: calls.append(path) or {"known": True, "logged_in": True}
        try:
            main.acct.update(got=None, at=0)
            last = [{"name": "sample-a", "state": "idle", "ts": time.time(), "text": "OAuth session expired"}]
            first = main.account_info(last)
            main.account_info([])
            self.assertEqual(len(calls), 1)
            self.assertEqual(first["fallen"]["names"], ["sample-a"])
            main.account_info([], fresh=True)
            self.assertEqual(len(calls), 2)
        finally:
            cu.account = keep


if __name__ == "__main__":
    unittest.main()
