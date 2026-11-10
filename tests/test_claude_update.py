import json
import os
import shutil
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
        self.assertEqual(cu.account("claude", now=NOW, env={"CLAUDE_CONFIG_DIR": "/cfg"}, run=status_run(out))["days_left"], -1)

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


TOKEN_STATUS = ('{"loggedIn": true, "authMethod": "oauth_token", "apiProvider": "firstParty", '
                '"configDirectory": "%s", "loginExpiresAt": null}')


class TokenSignIn(unittest.TestCase):
    def profile(self, claude_json=None, settings=None):
        """A fake profile: CLAUDE_CONFIG_DIR/.claude.json (as the claude binary reads it) and settings.json."""
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        cfg = os.path.join(d, "cfg")
        os.makedirs(cfg)
        if claude_json is not None:
            with open(os.path.join(cfg, ".claude.json"), "w") as f:
                json.dump(claude_json, f)
        if settings is not None:
            with open(os.path.join(cfg, "settings.json"), "w") as f:
                json.dump(settings, f)
        return cfg

    def got(self, cfg, status=None):
        return cu.account("claude", now=NOW, env={"CLAUDE_CONFIG_DIR": cfg}, run=status_run(status or TOKEN_STATUS % cfg))

    def test_email_and_plan_come_from_the_profile_when_status_has_none(self):
        cfg = self.profile({"oauthAccount": {"emailAddress": "owner@example.com", "organizationType": "claude_max",
                                             "organizationRateLimitTier": "default_claude_max_5x", "accountUuid": "u-1"}})
        got = self.got(cfg)
        self.assertEqual((got["email"], got["plan"], got["from_profile"]), ("owner@example.com", "Claude Max 5x", True))
        self.assertTrue(got["token"])
        self.assertNotIn("u-1", json.dumps(got))

    def test_team_seat_shows_the_seat_limit(self):
        cfg = self.profile({"oauthAccount": {"emailAddress": "owner@example.com", "organizationType": "claude_team",
                                             "userRateLimitTier": "default_claude_max_5x", "organizationRateLimitTier": "default_x"}})
        self.assertEqual(self.got(cfg)["plan"], "Claude Team \u00b7 Max 5x")

    def test_plan_of_known_and_unknown_types(self):
        self.assertEqual(cu.plan_of("claude_pro", None, None), "Claude Pro")
        self.assertEqual(cu.plan_of("claude_enterprise", None, None), "Claude Enterprise")
        self.assertEqual(cu.plan_of("claude_max", None, "default_claude_max_20x"), "Claude Max 20x")
        self.assertEqual(cu.plan_of("claude_max", None, None), "Claude Max")
        self.assertEqual(cu.plan_of("claude_future", None, None), "claude_future")
        self.assertEqual(cu.plan_of(None, "default_claude_max_5x", None), "Claude Max 5x")
        self.assertIsNone(cu.plan_of(None, None, "default_raven"))

    def test_nothing_in_the_profile_leaves_email_and_plan_empty(self):
        for cfg in (self.profile(), self.profile({"other": 1}), self.profile({"oauthAccount": "junk"})):
            got = self.got(cfg)
            self.assertEqual((got["email"], got["plan"], got["from_profile"], got["token"]), (None, None, False, True))
        broken = self.profile()
        with open(os.path.join(broken, ".claude.json"), "w") as f:
            f.write("{not json")
        self.assertIsNone(self.got(broken)["email"])

    def test_status_that_names_the_account_is_not_overwritten_by_the_profile(self):
        cfg = self.profile({"oauthAccount": {"emailAddress": "other@example.com", "organizationType": "claude_pro"}})
        out = '{"loggedIn": true, "authMethod": "claude.ai", "email": "owner@example.com", "subscriptionType": "max"}'
        got = self.got(cfg, out)
        self.assertEqual((got["email"], got["plan"], got["from_profile"], got["token"]), ("owner@example.com", "Claude Max", False, False))

    def test_the_profile_of_another_account_does_not_lend_its_plan(self):
        cfg = self.profile({"oauthAccount": {"emailAddress": "other@example.com", "organizationType": "claude_pro"}})
        got = self.got(cfg, '{"loggedIn": true, "authMethod": "claude.ai", "email": "owner@example.com"}')
        self.assertEqual((got["email"], got["plan"]), ("owner@example.com", None))

    def test_not_logged_in_does_not_read_the_profile(self):
        cfg = self.profile({"oauthAccount": {"emailAddress": "owner@example.com"}})
        got = self.got(cfg, '{"loggedIn": false}')
        self.assertIsNone(got["email"])

    def test_profile_file_follows_the_config_dir_rule_of_claude(self):
        self.assertEqual(cu.profile_file({"CLAUDE_CONFIG_DIR": "/cfg", "HOME": "/h"}), "/cfg/.claude.json")
        self.assertEqual(cu.profile_file({"HOME": "/h"}), "/h/.claude.json")

    def test_token_in_settings_is_found_by_name_and_never_returned(self):
        cfg = self.profile(settings={"env": {"CLAUDE_CODE_OAUTH_TOKEN": "sample-token-value", "HTTPS_PROXY": "x"}})
        got = self.got(cfg)
        self.assertTrue(got["token_in_settings"])
        self.assertNotIn("sample-token-value", json.dumps(got))
        self.assertFalse(self.got(self.profile(settings={"env": {"HTTPS_PROXY": "x"}}))["token_in_settings"])
        self.assertFalse(self.got(self.profile())["token_in_settings"])

    def test_a_login_in_the_browser_is_not_a_token(self):
        cfg = self.profile(settings={"env": {"CLAUDE_CODE_OAUTH_TOKEN": "sample-token-value"}})
        got = self.got(cfg, '{"loggedIn": true, "authMethod": "claude.ai", "email": "owner@example.com", "subscriptionType": "pro"}')
        self.assertFalse(got["token"])
        self.assertFalse(got["token_in_settings"])


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
