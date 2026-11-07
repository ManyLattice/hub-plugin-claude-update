// Обновить Claude Code по слову владельца: «Настройки» → «Claude Code». Процесс плагина (main.py, порт страницы + 11)
// знает версии и ставит новую тем способом, каким claude поставлен; потом страница просит хаб-сессию перезапустить
// хаб, когда сессии свободны (сам плагин весь хаб перезапускать не может). Без автоматики: только по кнопке.
// Ниже блок «Аккаунт Claude»: кто вошёл и до какого числа (процесс плагина), какие сессии упали на ошибке входа
// (по снимку страницы) и вход заново: сам вход ведёт поток ядра (/api/setup), а после успеха плагин один раз пишет
// каждой упавшей сессии «продолжи».
let hub = null, data = null, err = null, at = 0, timer = null, note = "", confirming = false;
// какое обновление уже передано хабу: job.ts из процесса плагина; в браузере — чтобы перезагрузка страницы не просила снова
const SENT = "claude-update.sent";
const sent = () => Number(localStorage.getItem(SENT)) || 0;
const POLL_MS = 60000, RUN_MS = 2000;
let acc = null, accErr = null, setup = null, setupBad = false, accTimer = null, accStarted = false, rev = 0;
let loginNote = "", startErr = "", codeErr = "", draft = "", sending = false, pendingStart = 0, fastUntil = 0, codeBusy = false;
// какой вход уже обработан («продолжи» разослано): job.started из потока входа ядра; как SENT выше
const SENT_LOGIN = "claude-update.login-sent";
const sentLogin = () => Number(localStorage.getItem(SENT_LOGIN)) || 0;
const LOGIN_SHOW_S = 600, SOON_DAYS = 7, TERMINAL = "Войди в терминале: claude auth login";

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
function versionsBox() {
  const el = hub.el;
  const box = el("div", "settings-sec");
  if (!data) {
    box.append(el("p", "hint", err || "Смотрю версии…"));
    return box;
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
  return box;
}


function render() {
  const root = rootEl;
  if (!root) return;
  const el = hub.el;
  const focused = document.activeElement?.classList?.contains("cu-code-in");
  const wrap = el("div", "cu-wrap");
  wrap.append(versionsBox(), el("hr", "cu-sep"), accountBox());
  root.replaceChildren(wrap);
  const inp = focused && root.querySelector(".cu-code-in");
  if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
}

// ---- аккаунт Claude ----
const pluralDays = (n) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? "день" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "дня" : "дней"}`;
const day = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString("ru", { day: "2-digit", month: "2-digit", year: "numeric" }) : null);
const loginJob = () => (setup?.items || []).find((i) => i.id === "login")?.job || null;
const hasSetup = () => (hub.snapshot()?.features || []).includes("setup");
const safeUrl = (u) => { try { return new URL(u).protocol === "https:" ? u : null; } catch { return null; } };

// последняя запись ленты каждой живой сессии, если это ошибка: процесс плагина сам решает, какая из них про вход
function lastRows() {
  const snap = hub.snapshot() || {};
  const out = [];
  for (const s of snap.sessions || []) {
    if (["removed", "closed", "failed"].includes(s.state)) continue;
    const row = [...(snap.timelines?.[s.name] || [])].reverse().find((x) => x.kind === "out" || x.kind === "reply");
    if (row?.error) out.push({ name: s.name, state: s.state, ts: row.ts, text: String(row.text || "").slice(0, 240) });
  }
  return out.slice(0, 80);
}

async function loadSetup() {
  if (!hasSetup()) { setup = null; setupBad = true; return; }
  try {
    const r = await fetch("/api/setup?full=0", { headers: { "X-Hub": "1" } });
    const j = r.ok ? await r.json() : null;
    if (j && Array.isArray(j.items)) { setup = j; setupBad = false; } else { setup = null; setupBad = true; }
  } catch { setup = null; setupBad = true; }
}

async function loadAccount(fresh = false) {
  try {
    const r = await fetch(`${base()}/account?fresh=${fresh ? 1 : 0}&last=${encodeURIComponent(JSON.stringify(lastRows()))}`);
    acc = await r.json(); accErr = null;
  } catch { acc = null; accErr = "Процесс плагина не отвечает (страница открыта не на этом Mac или плагин выключен)."; }
}

async function tickAccount(fresh = false) {
  clearTimeout(accTimer);
  await loadSetup();
  const j = loginJob();
  const running = j?.state === "running";
  if (pendingStart && j && j.started >= pendingStart - 2) pendingStart = 0;
  if (!running || !acc) await loadAccount(fresh);
  const recent = j && Date.now() / 1000 - (j.ts || 0) < LOGIN_SHOW_S;   // давнему входу «продолжи» не пишем: только свежему
  if (j?.state === "done" && recent && j.started > sentLogin() && !sending) await continueFallen(j);
  accTimer = setTimeout(() => tickAccount(), running || Date.now() < fastUntil ? RUN_MS : POLL_MS);
  rev++;
  render();
}

// вход выполнен: каждой упавшей на ошибке входа сессии один раз «продолжи» (метка по job.started не даёт повторить при перезагрузке)
async function continueFallen(j) {
  sending = true;
  localStorage.setItem(SENT_LOGIN, String(j.started));
  rev++; render();
  await loadAccount(true);
  const names = acc?.fallen?.names || [], ok = [], bad = [];
  for (const name of names) {
    const r = await hub.post("/api/send", { session: name, action: true, text: "продолжи" });
    (r.error ? bad : ok).push(name);
  }
  loginNote = !names.length ? "Упавших на ошибке входа сессий не было."
    : (ok.length ? `Написал «продолжи» сессиям: ${ok.join(", ")}.` : "") + (bad.length ? ` Не написалось: ${bad.join(", ")}.` : "");
  sending = false;
  rev++; render();
}

async function startLogin() {
  loginNote = ""; startErr = ""; codeErr = ""; draft = "";
  pendingStart = Date.now() / 1000; fastUntil = Date.now() + 30000;
  rev++; render();
  const r = await hub.post("/api/setup/run", { step: "login" });
  if (r.error) { pendingStart = 0; startErr = `Не вышло начать вход: ${r.error}. ${TERMINAL}`; }
  tickAccount();
}

async function cancelLogin() {
  await hub.post("/api/setup/cancel", { step: "login" });
  tickAccount();
}

async function sendCode(text) {
  codeBusy = true; codeErr = "";
  rev++; render();
  const r = await hub.post("/api/setup/input", { step: "login", text });
  codeBusy = false;
  if (r.error) codeErr = `Код не принят: ${r.error}`; else draft = "";
  tickAccount();
}

const WHY = { code: "Claude не принял код. Войди заново и вставь код целиком.", timeout: "Время вышло (10 минут). Попробуй ещё раз.",
              start: "Не удалось запустить claude.", gone: "Процесс входа пропал. Попробуй ещё раз." };

function actions(...btns) {
  const row = hub.el("div", "cu-acts");
  row.append(...btns);
  return row;
}

function btn(label, cls, fn) {
  const b = hub.el("button", `btn small ${cls}`.trim(), label);
  b.type = "button";
  b.addEventListener("click", fn);
  return b;
}

function status(kind, text) {
  const d = hub.el("div", "cu-st", null);
  d.dataset.s = kind;
  d.append(hub.el("span", "cu-dot"), text);
  return d;
}

function facts(rows) {
  const dl = hub.el("dl", "about-facts");
  for (const [k, v] of rows) if (v) dl.append(hub.el("dt", null, k), hub.el("dd", null, v));
  return dl;
}

function whoFacts(extra = []) {
  return [["Вошёл", acc.email], ["План", acc.plan], ["Вход действует до", day(acc.expires_at) || "срок Claude не сообщает"], ...extra];
}

function accountBox() {
  const el = hub.el;
  const box = el("div", "settings-sec");
  box.append(el("p", "cu-sub", "Аккаунт Claude"));
  if (!acc) { box.append(el("p", "hint", accErr || "Смотрю аккаунт…")); return box; }
  const j = loginJob();
  const recent = j && Date.now() / 1000 - (j.ts || 0) < LOGIN_SHOW_S;
  if (pendingStart && Date.now() / 1000 - pendingStart < 15) box.append(status("wait", "Запускаю вход…"), actions(btn("Отмена", "quiet", cancelLogin)));
  else if (j?.state === "running") loginRunning(box, j);
  else if (j?.state === "done" && recent) loginDone(box);
  else idle(box, j?.state === "failed" && recent ? j : null);
  return box;
}

function loginRunning(box, j) {
  const el = hub.el, local = !!setup?.local;
  const url = safeUrl(local ? j.local_url || j.url : j.url || j.local_url);
  if (j.code_sent) {
    box.append(status("wait", "Проверяю вход…"), el("p", "hint", "Код отправлен, жду ответ Claude (до минуты)."), actions(btn("Отмена", "quiet", cancelLogin)));
    return;
  }
  if (!url) { box.append(status("wait", "Запускаю вход…"), actions(btn("Отмена", "quiet", cancelLogin))); return; }
  box.append(status("wait", "Идёт вход"), el("p", "hint", local
    ? "Нажми «Открыть страницу входа» и войди в Claude: страница вернётся сюда сама. Если она покажет код, вставь его ниже."
    : "1. Нажми «Открыть страницу входа» и войди в Claude. 2. Страница покажет код - вставь его сюда."));
  const a = el("a", "btn small primary", "Открыть страницу входа");
  a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
  box.append(actions(a, btn("Отмена", "quiet", cancelLogin)));
  const f = el("form", "cu-code");
  const inp = el("input", "cu-code-in");
  inp.type = "text"; inp.name = "code"; inp.autocomplete = "off"; inp.spellcheck = false;
  inp.placeholder = "Код со страницы входа"; inp.setAttribute("aria-label", "Код со страницы входа");
  inp.value = draft;
  inp.addEventListener("input", () => { draft = inp.value; });
  const go = el("button", "btn small", "Отправить");
  go.disabled = codeBusy;
  f.addEventListener("submit", (e) => { e.preventDefault(); if (draft.trim()) sendCode(draft.trim()); });
  f.append(inp, go);
  box.append(f);
  if (codeErr) box.append(el("p", "cu-err", codeErr));
  box.append(el("p", "cu-hint", local ? "Ссылка и код живут 10 минут." : "Ссылка и код живут 10 минут. Эта страница на другом устройстве, не на хабе: войти можно и с телефона, код вставляется сюда."));
}

function loginDone(box) {
  const el = hub.el;
  box.append(status("ok", "Вход выполнен"), facts(whoFacts()));
  box.append(el("p", "hint", "Работающие сессии подхватят вход сами при следующем запросе (до полуминуты)."
    + (sending ? " Пишу упавшим сессиям…" : loginNote ? ` ${loginNote}` : "")));
}

function idle(box, failed) {
  const el = hub.el;
  const fell = acc.fallen?.names || [];
  const left = acc.days_left;
  const can = hasSetup() && !setupBad;
  let kind = "ok", label = "В порядке", hint = "", primary = false, go = "Войти заново", rows = [];
  if (!acc.known) { kind = "wait"; label = "Статус входа неизвестен"; hint = "claude не показал статус входа (старая версия Claude Code?)."; go = "Войти"; }
  else if (fell.length) {
    kind = "off"; label = "Вход слетел"; primary = true;
    rows = [["Был вход", [acc.email, acc.plan].filter(Boolean).join(" · ")], ["Слетел", acc.fallen.since ? `${when(acc.fallen.since)} (по ошибке сессии)` : null],
            ["Упали сессии", `${fell.length}: ${fell.join(", ")}`]];
  } else if (!acc.logged_in) {
    kind = "warn"; label = "Вход не выполнен"; go = "Войти";
    hint = "claude на этом Mac не знает аккаунта. Если сессии хаба ходят по ключу API или через пул аккаунтов, так и должно быть.";
  } else if (left !== null && left < 0) { kind = "warn"; label = "Срок входа вышел"; primary = true; hint = "Зайди заново: иначе сессии начнут падать с «OAuth session expired»."; }
  else if (left !== null && left <= SOON_DAYS) {
    kind = "warn"; primary = true; label = `Истекает скоро: ${left === 0 ? "меньше суток" : "осталось " + pluralDays(left)}`;
    hint = "Зайди заново до этой даты - иначе сессии начнут падать с «OAuth session expired».";
  }
  box.append(status(kind, label));
  if (fell.length) box.append(facts(rows), el("p", "cu-err", `Сессии хаба не могут ходить к Claude: «${acc.fallen.reason}». ${can ? "Войди заново - после этого напишу упавшим «продолжи»." : ""}`.trim()));
  else if (acc.known && acc.logged_in) box.append(facts([...whoFacts(), ["Проверено", when(acc.checked)]]));
  if (hint) box.append(el("p", "hint", hint));
  if (acc.known && acc.config_match === false) box.append(el("p", "cu-hint", `Статус относится к профилю ${acc.config_dir}, это не профиль плагина (CLAUDE_CONFIG_DIR).`));
  if (failed) box.append(el("p", "cu-err", WHY[failed.why] || (failed.text ? `Вход не вышел: ${failed.text}` : "Вход не вышел.")));
  if (startErr) box.append(el("p", "cu-err", startErr));
  if (can) box.append(actions(btn(go, primary ? "primary" : "", startLogin)));
  else box.append(el("p", "hint", TERMINAL));
}

export default function register(h) {
  hub = h;
  hub.addStyle("web/cu.css");
  hub.addSettings({
    id: "claude-update", title: "Claude Code",
    render(root) {
      rootEl = root;
      if (Date.now() - at > POLL_MS) load();
      if (!accStarted) { accStarted = true; tickAccount(); }
      if (!root.firstChild || root._d !== data || root._n !== note || root._r !== rev) { root._d = data; root._n = note; root._r = rev; render(); }
    },
  });
}
