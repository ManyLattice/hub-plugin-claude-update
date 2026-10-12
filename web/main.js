// Обновить Claude Code по слову владельца: «Настройки» → «Claude Code». Процесс плагина (main.py, порт страницы + 11)
// знает версии и ставит новую тем способом, каким claude поставлен; потом страница просит хаб-сессию перезапустить
// хаб, когда сессии свободны (сам плагин весь хаб перезапускать не может). Без автоматики: только по кнопке.
let hub = null, data = null, err = null, at = 0, timer = null, note = "", confirming = false;
// какое обновление уже передано хабу: job.ts из процесса плагина; в браузере — чтобы перезагрузка страницы не просила снова
const SENT = "claude-update.sent";
const sent = () => Number(localStorage.getItem(SENT)) || 0;
const POLL_MS = 60000, RUN_MS = 2000;

const base = () => `http://${location.hostname}:${Number(location.port || 80) + 11}`;
const LIVE = ["starting", "idle", "busy", "dialog", "held"];
const HOW = { native: "установщик Claude Code (claude update)", npm: "npm (install -g)", brew: "Homebrew (brew upgrade)",
              unknown: "неизвестно как" };
const hubSession = () => (hub.snapshot()?.sessions || []).find((s) => s.kind === "hub");
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString("ru", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");

async function load(fresh = false) {
  at = Date.now();
  const sessions = (hub.snapshot()?.sessions || []).filter((s) => s.pid && LIVE.includes(s.state)).map((s) => ({ name: s.name, pid: s.pid }));
  try {
    const r = await fetch(`${base()}/?fresh=${fresh ? 1 : 0}&sessions=${encodeURIComponent(JSON.stringify(sessions))}`);
    data = await r.json(); err = null;
  } catch { data = null; err = "Процесс плагина не отвечает (страница открыта не на этом Mac или плагин выключен)."; }
  const j = data?.job;
  if (j?.state === "done" && j.ts > sent()) await restartHub(j);
  clearTimeout(timer);
  timer = setTimeout(() => load(), j?.state === "running" ? RUN_MS : POLL_MS);
  render();
}

// обновилось — хаб перезапустит хаб-сессия (у плагина на это нет права); версия не сменилась — перезапуск не нужен
async function restartHub(j) {
  localStorage.setItem(SENT, String(j.ts));
  if (!j.to || j.to === j.from) { note = `Уже последняя версия: ${j.to || j.from}.`; return; }
  const h = hubSession();
  if (!h) { note = `Claude Code обновлён: ${j.from} → ${j.to}. Хаб-сессии нет — перезапусти хаб сам: hub restart.`; return; }
  const r = await hub.post("/api/send", { session: h.name, action: true, text: `Claude Code обновлён: ${j.from} → ${j.to} `
    + "(кнопка в «Настройки» → «Claude Code»). Перезапусти хаб, когда сессии свободны: "
    + `hub restart "Claude Code ${j.to}". Сессии до перезапуска работают на старой версии.` });
  note = r.error ? `Claude Code обновлён (${j.from} → ${j.to}), но хабу не написалось: ${r.error}. Перезапусти: hub restart.`
    : `Claude Code обновлён: ${j.from} → ${j.to}. Хаб перезапустится, когда сессии свободны.`;
}

async function startUpdate() {
  confirming = false; note = "";
  try {
    const r = await fetch(`${base()}/update`, { method: "POST", headers: { "X-Hub": "1" } });
    const j = await r.json();
    if (!r.ok) note = `Не вышло: ${j.error || r.status}`;
  } catch { note = "Процесс плагина не отвечает."; }
  load();
}

async function askRestart() {
  const h = hubSession();
  if (!h) { note = "Хаб-сессии нет — перезапусти хаб сам: hub restart."; return render(); }
  const r = await hub.post("/api/send", { session: h.name, action: true, text: `Перезапусти хаб, когда сессии свободны: `
    + `hub restart "Claude Code ${data?.have}" — часть сессий работает на старой версии claude (кнопка в «Настройки» → «Claude Code»).` });
  note = r.error ? `Не вышло: ${r.error}` : "Отправлено хабу: перезапустит, когда сессии свободны.";
  render();
}

let rootEl = null;
function render() {
  const root = rootEl;
  if (!root) return;
  const el = hub.el;
  const box = el("div", "settings-sec");
  if (!data) {
    box.append(el("p", "hint", err || "Смотрю версии…"));
    return root.replaceChildren(box);
  }
  const j = data.job || {};
  const dl = el("dl", "about-facts");
  const facts = [
    ["Установлен", data.have ? `${data.have} · с ${when(data.installed_at)}` : "не отвечает"],
    ["Доступна", data.available ? `${data.available} · канал ${data.channel}` : "неизвестно (нет связи с реестром)"],
    ["Как поставлен", HOW[data.method] || data.method],
    ["Файл", data.path],
    ["Проверено", when(data.checked)],
  ];
  for (const [k, v] of facts) dl.append(el("dt", null, k), el("dd", null, String(v)));
  box.append(dl);
  const stale = data.stale || [];
  let line = j.state === "running" ? `Обновляю Claude Code${j.from ? " с " + j.from : ""}… (до нескольких минут)`
    : j.state === "failed" ? "Обновить не вышло — вывод ниже." : note
    || (data.newer ? `Есть новее: ${data.available}. Обновлю и попрошу хаб перезапуститься, когда сессии свободны.`
      : stale.length ? `Последняя версия стоит, но ${stale.length} сесс. работают на старой: ${stale.join(", ")}.`
      : "Стоит последняя версия.");
  box.append(el("p", "hint", line));
  const row = el("div", "upd-actions");
  const busy = j.state === "running";
  if (data.newer && data.command && !busy) {
    const b = el("button", "btn small primary", confirming ? `Точно обновить до ${data.available}?` : `Обновить до ${data.available}`);
    b.type = "button";
    b.addEventListener("click", () => { if (confirming) startUpdate(); else { confirming = true; render(); } });
    row.append(b);
  }
  if (!data.newer && stale.length && !busy) {
    const b = el("button", "btn small", "Перезапустить хаб");
    b.type = "button";
    b.addEventListener("click", askRestart);
    row.append(b);
  }
  const c = el("button", "btn small", "Проверить");
  c.type = "button";
  c.disabled = busy;
  c.addEventListener("click", () => { note = ""; data = { ...data, checked: 0 }; load(true); });
  row.append(c);
  box.append(row);
  if (data.newer && !data.command) box.append(el("p", "hint", `Как обновить такой claude, плагин не знает: ${data.real}`));
  if (j.state === "failed" && j.tail) box.append(el("pre", "cu-tail", j.tail));
  box.append(el("p", "hint", "Тот же claude у других хабов на этом Mac (например, личного): после обновления их сессии "
    + "работают на старой версии до перезапуска того хаба — там та же вкладка покажет «Перезапустить хаб»."));
  root.replaceChildren(box);
}

export default function register(h) {
  hub = h;
  hub.addStyle("web/cu.css");
  hub.addSettings({
    id: "claude-update", title: "Claude Code",
    render(root) {
      rootEl = root;
      if (Date.now() - at > POLL_MS) load();
      if (!root.firstChild || root._d !== data || root._n !== note) { root._d = data; root._n = note; render(); }
    },
  });
}
