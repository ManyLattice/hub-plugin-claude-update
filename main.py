"""Обновить Claude Code по слову владельца: процесс плагина знает версии и ставит новую, страница — кнопка
(«Настройки» → «Claude Code»). Говорит со страницей по HTTP на порту страницы + 11, только со своей страницы."""
import http.server
import json
import os
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse

import claude_update as cu

WEB_PORT = int(os.environ.get("HUB_WEB_PORT", "8787"))
PORT = WEB_PORT + 11
FRESH_S = 1800   # доступную версию спрашиваем не чаще раза в полчаса, «Проверить» — сразу
lock = threading.Lock()
known = {"available": None, "checked": 0}
job = {"state": "idle", "from": None, "to": None, "tail": "", "ts": 0}   # idle | running | done | failed


def info(fresh=False):
    path = cu.find_claude()
    chan = cu.channel()
    if fresh or time.time() - known["checked"] > FRESH_S:
        known.update(available=cu.available(chan), checked=time.time())
    how, cmd = cu.update_command(path, chan)
    have = cu.version(path)
    return {"path": path, "real": os.path.realpath(path), "method": how, "command": cmd, "channel": chan,
            "have": have, "available": known["available"], "checked": known["checked"],
            "newer": cu.newer(known["available"], have), "installed_at": cu.installed_at(path), "job": dict(job)}


def run_update():
    path = cu.find_claude()
    chan = cu.channel()
    how, cmd = cu.update_command(path, chan)
    before = cu.version(path)
    job.update(state="running", **{"from": before, "to": None, "tail": "", "ts": time.time()})
    if not cmd:
        job.update(state="failed", tail=f"не знаю, как обновить claude, поставленный так: {os.path.realpath(path)}")
        return
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=cu.clean_env(),
                           stdin=subprocess.DEVNULL)
        out, ok = (r.stdout + r.stderr).strip(), r.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        out, ok = repr(e), False
    after = cu.version(path)
    known["checked"] = 0
    job.update(state="done" if ok and after else "failed", to=after, tail=out[-1500:], ts=time.time())


class Handler(http.server.BaseHTTPRequestHandler):
    def ours(self):
        origin = self.headers.get("Origin") or ""
        if origin in (f"http://127.0.0.1:{WEB_PORT}", f"http://localhost:{WEB_PORT}"):
            return origin
        self.reply(403, {"error": "только со страницы этого хаба"})
        return None

    def reply(self, code, body, origin=None):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Hub")
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        origin = self.ours()
        if origin:
            self.reply(200, {}, origin)

    def do_GET(self):
        origin = self.ours()
        if not origin:
            return
        q = parse_qs(urlparse(self.path).query)
        body = info(fresh=q.get("fresh") == ["1"])
        try:
            sessions = json.loads(q.get("sessions", ["[]"])[0])
        except ValueError:
            sessions = []
        body["stale"] = cu.stale(sessions if isinstance(sessions, list) else [], body["installed_at"])
        self.reply(200, body, origin)

    def do_POST(self):
        origin = self.ours()
        if not origin:
            return
        if self.headers.get("X-Hub") != "1" or urlparse(self.path).path != "/update":
            return self.reply(400, {"error": "не то действие"}, origin)
        with lock:
            if job["state"] == "running":
                return self.reply(409, {"error": "обновление уже идёт"}, origin)
            job["state"] = "running"
        threading.Thread(target=run_update, daemon=True).start()
        self.reply(200, {"ok": True}, origin)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"claude-update: страница — 127.0.0.1:{PORT}", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
