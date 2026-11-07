"""Какой claude у хаба, как он поставлен, есть ли новее и кто из сессий работает на старом."""
import datetime
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

REGISTRY = "https://registry.npmjs.org/-/package/@anthropic-ai/claude-code/dist-tags"
VERSION = re.compile(r"\d+\.\d+\.\d+")


def find_claude(env=os.environ):
    """Тот же claude, что запускает хаб (core/daemon.claude_command): HUB_CLAUDE или первый в PATH, кроме обёртки
    hosts/nested, которую видят сессии хаба."""
    if env.get("HUB_CLAUDE"):
        return env["HUB_CLAUDE"].split()[0]
    dirs = [d for d in (env.get("PATH") or "").split(os.pathsep) if d and not d.rstrip("/").endswith("hosts/nested")]
    return shutil.which("claude", path=os.pathsep.join(dirs)) or os.path.expanduser("~/.local/bin/claude")


def method(path):
    """Как поставлен claude — по настоящему файлу за ссылкой: (способ, команда обновления или None)."""
    real = os.path.realpath(path)
    if "/.local/share/claude/versions/" in real:
        return "native", [path, "update"]
    if "/node_modules/@anthropic-ai/claude-code/" in real:
        prefix = real.split("/lib/node_modules/")[0] if "/lib/node_modules/" in real else None
        npm = os.path.join(prefix, "bin", "npm") if prefix else shutil.which("npm")
        return "npm", [npm or "npm", "install", "-g", "@anthropic-ai/claude-code@{channel}"]
    if "/Caskroom/claude-code/" in real:
        return "brew", ["brew", "upgrade", "--cask", "claude-code"]
    return "unknown", None


def version(path, run=subprocess.run):
    try:
        out = run([path, "--version"], capture_output=True, text=True, timeout=30, env=clean_env()).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = VERSION.search(out or "")
    return m.group(0) if m else None


def installed_at(path):
    """Когда поставлена нынешняя версия: время файла за ссылкой."""
    try:
        return os.path.getmtime(os.path.realpath(path))
    except OSError:
        return None


def channel(env=os.environ):
    """Канал обновлений Claude Code (autoUpdatesChannel в его settings.json): latest по умолчанию или stable."""
    base = env.get("CLAUDE_CONFIG_DIR") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".claude")
    try:
        with open(os.path.join(base, "settings.json")) as f:
            got = json.load(f).get("autoUpdatesChannel")
    except (OSError, ValueError, AttributeError):
        got = None
    return got if got in ("latest", "stable") else "latest"


def available(chan, fetch=None):
    """Версия на канале из реестра npm (туда же смотрит установщик Claude Code) или None."""
    try:
        if fetch:
            tags = fetch(REGISTRY)
        else:
            with urllib.request.urlopen(REGISTRY, timeout=15) as r:
                tags = json.load(r)
    except Exception:  # noqa: BLE001 — нет сети: версия неизвестна, не ошибка плагина
        return None
    got = (tags or {}).get(chan)
    return got if isinstance(got, str) and VERSION.fullmatch(got) else None


def newer(a, b):
    """a новее b."""
    if not a or not b:
        return False
    return tuple(map(int, a.split("."))) > tuple(map(int, b.split(".")))


def started(pids, run=subprocess.run):
    """Время старта процессов: {pid: unix}; кого нет — нет в ответе."""
    pids = [int(p) for p in pids if str(p).isdigit()]
    if not pids:
        return {}
    try:
        out = run(["ps", "-o", "pid=,lstart=", "-p", ",".join(map(str, pids))], capture_output=True, text=True,
                  timeout=10, env={**clean_env(), "LC_ALL": "C"}).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    got = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            try:
                got[int(parts[0])] = time.mktime(time.strptime(" ".join(parts[1].split()), "%a %b %d %H:%M:%S %Y"))
            except ValueError:
                pass
    return got


def stale(sessions, since, run=subprocess.run):
    """Имена сессий, чей claude запущен раньше, чем поставлена нынешняя версия: работают на старой."""
    if not since:
        return []
    pids = {int(s["pid"]): s["name"] for s in sessions if str(s.get("pid") or "").isdigit()}
    at = started(pids, run)
    return sorted(name for pid, name in pids.items() if pid in at and at[pid] < since - 1)


def clean_env(env=os.environ):
    """Окружение для claude и установщика: без переменных хаба и сессии — только то, что нужно для входа и сети."""
    keep = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "TMPDIR", "SHELL", "CLAUDE_CONFIG_DIR",
            "HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "NO_PROXY", "no_proxy")
    return {k: env[k] for k in keep if env.get(k)}


def update_command(path, chan):
    how, cmd = method(path)
    return how, [c.replace("{channel}", chan) for c in cmd] if cmd else None


AUTH_FAIL = re.compile(r"OAuth session expired|could not be refreshed|authentication_failed|failed to authenticate|"
                       r"token (has been )?(revoked|expired)|invalid api key|please run /login|api error: 401\b", re.I)
EXPIRED = re.compile(r"OAuth session expired and could not be refreshed", re.I)
FELL_MAX_AGE = 86400    # an auth error older than a day is not a session waiting for a login
SOON_DAYS = 7
PLANS = {"max": "Claude Max", "pro": "Claude Pro", "team": "Claude Team", "enterprise": "Claude Enterprise",
         "free": "Claude Free"}


def expiry(value):
    """loginExpiresAt in unix seconds from a number (seconds or milliseconds) or an ISO string; None when Claude does
    not say."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 1e11 else float(value)
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def auth_status(path, run=subprocess.run):
    """`claude auth status --json` of the claude the hub runs, or None when it gives no JSON (an older Claude Code)."""
    try:
        out = run([path, "auth", "status", "--json"], capture_output=True, text=True, timeout=30, env=clean_env(),
                  stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    start = (out or "").find("{")
    try:
        got = json.loads(out[start:]) if start >= 0 else None
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def account(path, now=None, env=os.environ, run=subprocess.run):
    """The account of this claude for the page: {known, logged_in, email, plan, expires_at, days_left, config_dir,
    config_match}. Not known (known False) when status gives no JSON. config_match is False when status speaks of
    another profile than CLAUDE_CONFIG_DIR of the plugin process (the hub's sessions use that one)."""
    st = auth_status(path, run)
    if st is None:
        return {"known": False}
    now = time.time() if now is None else now
    at = expiry(st.get("loginExpiresAt"))
    sub = str(st.get("subscriptionType") or "").lower()
    asked = os.path.realpath(os.path.expanduser(env.get("CLAUDE_CONFIG_DIR") or os.path.join(env.get("HOME") or "~", ".claude")))
    got = st.get("configDirectory")
    return {"known": True, "logged_in": bool(st.get("loggedIn")), "email": st.get("email") or None,
            "plan": PLANS.get(sub) or (f"Claude {sub.capitalize()}" if sub else None), "expires_at": at,
            "days_left": None if at is None else int((at - now) // 86400) if at >= now else -1,
            "config_dir": got, "config_match": not got or os.path.realpath(os.path.expanduser(str(got))) == asked}


def fallen(last, now=None, states_busy=("busy", "starting", "dialog")):
    """Sessions whose last answer is an auth error: last = [{name, state, ts, text}], the last row of each session's
    feed that is an error. -> {names, since, reason} (since: the earliest error, reason: what Claude said)."""
    now = time.time() if now is None else now
    hit = [x for x in last if isinstance(x, dict) and isinstance(x.get("name"), str) and x.get("state") not in states_busy
           and AUTH_FAIL.search(str(x.get("text") or "")) and now - float(x.get("ts") or 0) <= FELL_MAX_AGE]
    if not hit:
        return {"names": [], "since": None, "reason": None}
    text = str(hit[0].get("text") or "")
    reason = "OAuth session expired and could not be refreshed" if EXPIRED.search(text) else " ".join(text.split())[:120]
    return {"names": sorted(x["name"] for x in hit), "since": min(float(x.get("ts") or 0) for x in hit), "reason": reason}
