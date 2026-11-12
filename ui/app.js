// JARVIS desktop UI. Talks to server.py over /api and one WebSocket.
"use strict";

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};

const STATE_TEXT = {
  booting: "준비 중",
  idle: "대기 중",
  listening: "듣고 있어요",
  thinking: "생각하는 중",
  speaking: "말하는 중",
};

const app = {
  state: "booting",
  voice: { status: "starting", error: "" },
  settings: { voice_enabled: true, muted: false },
  attachments: [],          // {id, name (server), original, status, preview}
  messageCount: 0,
  today: null,
  timers: [],
};

function friendly(err) {
  return err instanceof TypeError ? "JARVIS 서버에 연결하지 못했어요" : err.message;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : undefined,
    ...options,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  // (network failures throw before this point; see friendly() below)
  if (!res.ok) throw new Error((data && data.detail) || `요청 실패 (${res.status})`);
  return data;
}

// ------------------------------------------------------------ state + status
function setState(state) {
  app.state = state;
  document.body.dataset.state = state;
  const busy = state === "thinking" || state === "speaking" || state === "listening";
  $("#stop").hidden = !(state === "thinking" || state === "speaking");
  $("#mic").disabled = busy || app.voice.status !== "ready";
  $("#mic").classList.toggle("is-live", state === "listening");
  $("#core-state").textContent = STATE_TEXT[state] || state;
  renderStatus();
}

function renderStatus() {
  let text = STATE_TEXT[app.state] || "";
  if (app.state === "idle") {
    if (app.voice.status === "ready" && app.settings.voice_enabled) text = "‘Hey Jarvis’라고 부르거나 아래에 입력하세요";
    else if (app.voice.status === "ready") text = "음성 호출 꺼짐. 마이크 버튼이나 입력창을 쓰세요";
    else if (app.voice.status === "unavailable") text = "마이크를 쓸 수 없어요. 입력창으로 물어보세요";
    else text = "음성 기능 불러오는 중";
  }
  $("#status").textContent = text;
  const wake = $("#wake");
  wake.checked = app.settings.voice_enabled;
  wake.disabled = app.voice.status === "unavailable";
  $("#mute").setAttribute("aria-pressed", String(app.settings.muted));
  $("#mute").textContent = app.settings.muted ? "소리 켜기" : "소리 끄기";
}

// ------------------------------------------------------------ messages
function linkify(text) {
  const frag = document.createDocumentFragment();
  const pattern = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|(https?:\/\/[^\s)]+)/g;
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    frag.append(text.slice(last, m.index));
    const a = el("a", null, m[1] || m[3]);
    a.href = m[2] || m[3];
    a.target = "_blank";
    a.rel = "noopener";
    frag.append(a);
    last = m.index + m[0].length;
  }
  frag.append(text.slice(last));
  return frag;
}

function addMessage(msg) {
  const li = el("li", `msg is-${msg.role}`);
  li.append(el("span", "msg-who", `${msg.role === "user" ? "나" : "JARVIS"}  ${msg.time || ""}`));
  if (msg.attachments && msg.attachments.length) {
    const files = el("div", "msg-files");
    for (const name of msg.attachments) {
      if (/\.(png|jpe?g|gif|webp)$/i.test(name)) {
        const img = el("img");
        img.src = `/api/uploads/${encodeURIComponent(name)}`;
        img.alt = "첨부한 사진";
        img.width = 120; img.height = 90;
        files.append(img);
      } else {
        files.append(el("span", "msg-file", name.replace(/^\d{4}-\d{6}-/, "")));
      }
    }
    li.append(files);
  }
  const body = el("p", "msg-body");
  body.append(linkify(msg.text));
  li.append(body);
  $("#messages").append(li);
  app.messageCount += 1;
  $("#starters").hidden = app.messageCount > 0;
  document.body.classList.add("has-messages");
  li.scrollIntoView({ block: "end", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

// ------------------------------------------------------------ activity
function addActivity(item, prepend = true) {
  const li = el("li", `act is-${item.kind || "tool"}`);
  li.append(el("span", "act-time", item.time), el("span", "act-label", item.label));
  const list = $("#activity");
  prepend ? list.prepend(li) : list.append(li);
  while (list.children.length > 60) list.lastChild.remove();
}

function renderActivity(items) {
  const list = $("#activity");
  list.replaceChildren();
  if (!items.length) {
    list.append(el("li", "empty", "Jarvis가 도구를 쓰면 여기에 순서대로 보여요."));
    return;
  }
  for (const item of items.slice().reverse()) addActivity(item, false);
}

// ------------------------------------------------------------ today: timetable, ring, meals
function parseTimes(range) {
  const m = /^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$/.exec(range || "");
  if (!m) return null;
  return { start: +m[1] * 60 + +m[2], end: +m[3] * 60 + +m[4] };
}

function currentPeriod(today) {
  const now = new Date();
  const mins = now.getHours() * 60 + now.getMinutes();
  const times = (today.period_times || []).map(parseTimes);
  let current = null, past = new Set();
  times.forEach((t, i) => {
    if (!t) return;
    if (mins >= t.start && mins < t.end) current = i + 1;
    if (mins >= t.end) past.add(i + 1);
  });
  return { current, past, hasTimes: times.some(Boolean) };
}

function arcPath(cx, cy, r, a0, a1) {
  const rad = (a) => ((a - 90) * Math.PI) / 180;
  const x0 = cx + r * Math.cos(rad(a0)), y0 = cy + r * Math.sin(rad(a0));
  const x1 = cx + r * Math.cos(rad(a1)), y1 = cy + r * Math.sin(rad(a1));
  const large = a1 - a0 > 180 ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

function renderRing(today) {
  const svgNS = "http://www.w3.org/2000/svg";
  const ring = $("#ring-periods");
  ring.replaceChildren();
  const known = today ? today.periods : [];
  const n = Math.max(today ? today.total : 0, known.length, 0) || 7;
  const { current, past } = today ? currentPeriod(today) : { current: null, past: new Set() };
  const gap = 4, span = 360 / n;
  for (let i = 1; i <= n; i++) {
    const a0 = (i - 1) * span + gap / 2, a1 = i * span - gap / 2;
    const p = known.find((x) => x.period === i);
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("d", arcPath(120, 120, 96, a0, a1));
    const cls = ["seg"];
    if (!today || !today.periods.length) cls.push("is-missing");
    else if (!p) cls.push("is-missing");
    else if (i === current) cls.push("is-now");
    else if (past.has(i)) cls.push("is-past");
    path.setAttribute("class", cls.join(" "));
    const title = document.createElementNS(svgNS, "title");
    title.textContent = p ? `${i}교시 ${p.subject}` : `${i}교시`;
    path.append(title);
    ring.append(path);
    const mid = ((a0 + a1) / 2 - 90) * Math.PI / 180;
    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", (120 + 113 * Math.cos(mid)).toFixed(1));
    label.setAttribute("y", (120 + 113 * Math.sin(mid)).toFixed(1));
    label.setAttribute("class", `seg-label${i === current ? " is-now" : ""}`);
    label.textContent = String(i);
    ring.append(label);
  }
}

function renderTicks() {
  const svgNS = "http://www.w3.org/2000/svg";
  const g = $("#ring-ticks");
  for (let i = 0; i < 60; i++) {
    const a = (i * 6 - 90) * Math.PI / 180;
    const major = i % 5 === 0;
    const r0 = major ? 68 : 71, r1 = 76;
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", (120 + r0 * Math.cos(a)).toFixed(2));
    line.setAttribute("y1", (120 + r0 * Math.sin(a)).toFixed(2));
    line.setAttribute("x2", (120 + r1 * Math.cos(a)).toFixed(2));
    line.setAttribute("y2", (120 + r1 * Math.sin(a)).toFixed(2));
    line.setAttribute("class", major ? "tick is-major" : "tick");
    g.append(line);
  }
}

function renderToday(today) {
  app.today = today;
  const d = new Date(today.date + "T00:00:00");
  $("#today-date").textContent = `${d.getMonth() + 1}월 ${d.getDate()}일 ${today.weekday}`;
  const list = $("#periods");
  list.replaceChildren();
  const note = $("#periods-note");
  note.hidden = true;
  note.classList.remove("note-error");

  if (today.error) {
    list.append(el("li", "empty", "시간표를 불러오지 못했어요. 인터넷 연결을 확인해 주세요."));
    note.hidden = false;
    note.classList.add("note-error");
    note.textContent = today.error;
  } else if (!today.periods.length) {
    list.append(el("li", "empty", "오늘은 수업이 없어요."));
  } else {
    const { current, past } = currentPeriod(today);
    const n = Math.max(today.total, today.periods.length);
    for (let i = 1; i <= n; i++) {
      const p = today.periods.find((x) => x.period === i);
      const li = el("li", "period");
      if (!p) li.classList.add("is-missing");
      else if (i === current) { li.classList.add("is-now"); li.setAttribute("aria-current", "time"); }
      else if (past.has(i)) li.classList.add("is-past");
      li.append(el("span", "period-n", `${i}교시`), el("span", "period-subject", p ? p.subject : "확인 불가"));
      const t = parseTimes((today.period_times || [])[i - 1]);
      if (t) {
        const hh = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
        li.append(el("span", "period-time", hh(t.start)));
      }
      list.append(li);
    }
    if (today.total > today.periods.length) {
      note.hidden = false;
      note.textContent = `${today.periods.length + 1}~${today.total}교시는 NEIS 인증키(neis-key.txt)가 있어야 보여요.`;
    }
  }
  renderRing(today);
  renderCorePeriod(today);

  const meals = $("#meals");
  meals.replaceChildren();
  if (today.error) meals.append(el("p", "empty", "급식 정보를 불러오지 못했어요."));
  else if (!today.meals.length) meals.append(el("p", "empty", "오늘은 급식 정보가 없어요."));
  for (const meal of today.meals) {
    const box = el("div", "meal");
    box.append(el("p", "meal-name", meal.name));
    const ul = el("ul", "meal-dishes");
    for (const dish of meal.dishes) ul.append(el("li", null, dish));
    box.append(ul);
    meals.append(box);
  }
}

function renderCorePeriod(today) {
  const out = $("#core-period");
  if (!today || today.error) { out.textContent = ""; return; }
  if (!today.periods.length) { out.textContent = "오늘은 수업 없는 날"; return; }
  const { current, hasTimes } = currentPeriod(today);
  const p = today.periods.find((x) => x.period === current);
  if (p) out.textContent = `지금 ${p.period}교시 ${p.subject}`;
  else if (hasTimes) out.textContent = `오늘 ${Math.max(today.total, today.periods.length)}교시까지`;
  else out.textContent = `오늘 ${Math.max(today.total, today.periods.length)}교시, 1교시 ${today.periods[0].subject}`;
}

async function loadToday() {
  try {
    renderToday(await api("/api/today"));
  } catch (e) {
    renderToday({ date: new Date().toISOString().slice(0, 10), weekday: "", periods: [], total: 0, meals: [], error: friendly(e) });
  }
}

// ------------------------------------------------------------ reminders
function formatDue(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(+d)) return iso;
  const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return `${d.getMonth() + 1}월 ${d.getDate()}일 ${time}`;
}

async function loadReminders() {
  const list = $("#reminders");
  const btn = $("#reload-reminders");
  btn.disabled = true;
  try {
    const data = await api("/api/reminders");
    list.replaceChildren();
    if (data.error) {
      const li = el("li", "empty");
      li.append("미리 알림 앱에 접근하지 못했어요. 시스템 설정에서 허용한 뒤 새로고침을 눌러 주세요.");
      list.append(li);
    } else if (!data.items.length) {
      list.append(el("li", "empty", "남은 미리 알림이 없어요. ‘7시에 공부하라고 알려줘’라고 해 보세요."));
    }
    for (const r of data.items.slice(0, 12)) {
      const li = el("li", "row");
      const text = el("span", "row-text", r.title);
      if (r.due) text.append(el("span", "row-sub", formatDue(r.due)));
      li.append(text);
      list.append(li);
    }
  } catch (e) {
    list.replaceChildren(el("li", "empty", `불러오지 못했어요: ${friendly(e)}`));
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------ timers
async function loadTimers() {
  try {
    app.timers = (await api("/api/timers")).items.filter((t) => !t.fired);
  } catch { app.timers = []; }
  renderTimers();
}

function renderTimers() {
  const panel = $("#timers-panel");
  const list = $("#timers");
  panel.hidden = !app.timers.length;
  list.replaceChildren();
  for (const t of app.timers) {
    const li = el("li", "row");
    const text = el("span", "row-text", t.label);
    const left = el("span", "row-sub");
    left.dataset.fireAt = t.fire_at;
    text.append(left);
    const cancel = el("button", "icon-btn is-small");
    cancel.type = "button";
    cancel.setAttribute("aria-label", `${t.label} 타이머 취소`);
    cancel.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
    cancel.addEventListener("click", async () => {
      cancel.disabled = true;
      try { await api(`/api/timers/${t.id}`, { method: "DELETE" }); } catch { /* already gone */ }
      loadTimers();
    });
    li.append(text, cancel);
    list.append(li);
  }
  tickTimers();
}

function tickTimers() {
  for (const node of document.querySelectorAll("[data-fire-at]")) {
    const left = Math.max(0, Math.round((new Date(node.dataset.fireAt) - Date.now()) / 1000));
    node.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} 남음`;
  }
}

// ------------------------------------------------------------ memories
async function loadMemories() {
  const list = $("#memories");
  try {
    const { items } = await api("/api/memories");
    list.replaceChildren();
    $("#memory-count").textContent = items.length ? `${items.length}개` : "";
    if (!items.length) {
      const li = el("li", "empty");
      li.append("아직 기억한 게 없어요. ", el("strong", null, "‘내 시험 범위 기억해’"), "처럼 말하면 여기에 남아요.");
      list.append(li);
    }
    for (const m of items.slice().reverse()) list.append(memoryRow(m));
  } catch (e) {
    list.replaceChildren(el("li", "empty", `기억을 불러오지 못했어요: ${friendly(e)}`));
  }
}

function memoryRow(m) {
  const li = el("li", "row");
  const text = el("span", "row-text", m.text);
  text.append(el("span", "row-sub", formatDue(m.created)));
  const del = el("button", "icon-btn is-small");
  del.type = "button";
  del.setAttribute("aria-label", `기억 삭제: ${m.text}`);
  del.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  del.addEventListener("click", async () => {
    del.disabled = true;
    try {
      await api(`/api/memories/${m.id}`, { method: "DELETE" });
    } catch (e) {
      del.disabled = false;
      return;
    }
    // Undo instead of a confirm dialog.
    li.classList.add("is-undo");
    text.replaceChildren("삭제했어요.");
    const undo = el("button", "link-btn", "되돌리기");
    undo.type = "button";
    undo.addEventListener("click", async () => {
      undo.disabled = true;
      await api("/api/memories", { method: "POST", body: JSON.stringify({ text: m.text }) });
      loadMemories();
    });
    del.replaceWith(undo);
    undo.focus();
    setTimeout(() => { if (li.isConnected && li.classList.contains("is-undo")) loadMemories(); }, 6000);
  });
  li.append(text, del);
  return li;
}

// ------------------------------------------------------------ attachments
function renderAttachments() {
  const list = $("#attachments");
  list.replaceChildren();
  for (const a of app.attachments) {
    const li = el("li", `att is-${a.status}`);
    if (a.preview) {
      const img = el("img");
      img.src = a.preview; img.alt = ""; img.width = 32; img.height = 32;
      li.append(img);
    }
    li.append(el("span", "att-name", a.status === "error" ? `${a.original}: ${a.error}` : a.original));
    const rm = el("button", "icon-btn is-small");
    rm.type = "button";
    rm.setAttribute("aria-label", `${a.original} 첨부 빼기`);
    rm.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
    rm.addEventListener("click", () => {
      app.attachments = app.attachments.filter((x) => x !== a);
      renderAttachments();
    });
    li.append(rm);
    list.append(li);
  }
}

async function uploadFiles(files) {
  for (const file of files) {
    const a = {
      original: file.name || "붙여넣은 이미지.png", status: "uploading", name: null, error: "",
      preview: file.type.startsWith("image/") ? URL.createObjectURL(file) : null,
    };
    app.attachments.push(a);
    renderAttachments();
    const form = new FormData();
    form.append("file", file, a.original);
    try {
      const res = await api("/api/upload", { method: "POST", body: form });
      a.name = res.name;
      a.status = "ready";
    } catch (e) {
      a.status = "error";
      a.error = friendly(e);
    }
    renderAttachments();
  }
}

// ------------------------------------------------------------ composer
async function send(text) {
  const help = $("#composer-help");
  const pending = app.attachments.some((a) => a.status === "uploading");
  if (pending) { help.textContent = "파일을 올리는 중이에요. 잠시만요."; return; }
  const names = app.attachments.filter((a) => a.status === "ready").map((a) => a.name);
  if (!text.trim() && !names.length) { $("#input").focus(); return; }
  const btn = $("#send");
  btn.setAttribute("aria-busy", "true");
  try {
    await api("/api/ask", { method: "POST", body: JSON.stringify({ text, attachments: names }) });
    $("#input").value = "";
    autoGrow();
    app.attachments = [];
    renderAttachments();
    help.classList.remove("is-error");
    help.textContent = "숙제 사진은 클립 버튼, 붙여넣기, 끌어다 놓기로 올려요. Enter로 보내요.";
  } catch (e) {
    help.classList.add("is-error");
    help.textContent = `보내지 못했어요: ${friendly(e)}`;
    $("#input").focus();
  } finally {
    btn.removeAttribute("aria-busy");
  }
}

function autoGrow() {
  const input = $("#input");
  input.style.blockSize = "auto";
  input.style.blockSize = `${Math.min(input.scrollHeight, 144)}px`;
}

// ------------------------------------------------------------ socket
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case "snapshot":
        $("#messages").replaceChildren();
        app.messageCount = 0;
        document.body.classList.remove("has-messages");
        app.settings = msg.settings;
        app.voice = msg.voice;
        for (const m of msg.messages) addMessage(m);
        if (!msg.messages.length) $("#starters").hidden = false;
        renderActivity(msg.activity);
        setState(msg.state);
        break;
      case "state": setState(msg.state); break;
      case "message": addMessage(msg.message); break;
      case "activity":
        if ($("#activity .empty")) $("#activity").replaceChildren();
        addActivity(msg.item);
        if (/타이머 설정/.test(msg.item.label)) setTimeout(loadTimers, 1500);
        break;
      case "voice": app.voice = { status: msg.status, error: msg.error || "" }; setState(app.state); break;
      case "settings": app.settings = msg.settings; renderStatus(); break;
      case "refresh": loadMemories(); loadReminders(); loadTimers(); break;
      case "timer_done": loadTimers(); break;
    }
  };
  ws.onclose = () => {
    $("#status").textContent = "서버와 연결이 끊겼어요. 다시 연결하는 중";
    setTimeout(connect, 1500);
  };
}

// ------------------------------------------------------------ wiring
function setView(view) {
  document.body.dataset.view = view;
  for (const tab of document.querySelectorAll(".tab")) tab.setAttribute("aria-pressed", String(tab.dataset.view === view));
  if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
}

function init() {
  renderTicks();
  renderRing(null);
  setView(["chat", "today", "log"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "chat");

  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send($("#input").value); });
  $("#input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("#input").value); }
  });
  $("#input").addEventListener("input", autoGrow);
  $("#input").addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); uploadFiles(files); }
  });
  $("#file").addEventListener("change", (e) => { uploadFiles([...e.target.files]); e.target.value = ""; });
  const main = $(".col-main");
  main.addEventListener("dragover", (e) => { e.preventDefault(); });
  main.addEventListener("drop", (e) => { e.preventDefault(); uploadFiles([...e.dataTransfer.files]); });

  for (const chip of document.querySelectorAll("[data-ask]")) chip.addEventListener("click", () => send(chip.dataset.ask));

  $("#mic").addEventListener("click", async () => {
    try { await api("/api/listen", { method: "POST" }); }
    catch (e) { $("#composer-help").textContent = friendly(e); }
  });
  $("#stop").addEventListener("click", () => api("/api/stop", { method: "POST" }));
  $("#wake").addEventListener("change", (e) => api("/api/settings", { method: "POST", body: JSON.stringify({ voice_enabled: e.target.checked }) }));
  $("#mute").addEventListener("click", () => api("/api/settings", { method: "POST", body: JSON.stringify({ muted: !app.settings.muted }) }));
  $("#reload-reminders").addEventListener("click", loadReminders);
  for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => setView(tab.dataset.view));

  $("#memory-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#memory-input");
    const help = $("#memory-help");
    if (!input.value.trim()) {
      input.setAttribute("aria-invalid", "true");
      help.textContent = "기억할 내용을 적어 주세요.";
      input.focus();
      return;
    }
    input.removeAttribute("aria-invalid");
    help.textContent = "";
    try {
      await api("/api/memories", { method: "POST", body: JSON.stringify({ text: input.value }) });
      input.value = "";
      loadMemories();
    } catch (err) {
      help.textContent = friendly(err);
    }
  });

  connect();
  loadToday();
  loadReminders();
  loadMemories();
  loadTimers();
  setInterval(tickTimers, 1000);
  setInterval(() => { if (app.today) { renderRing(app.today); renderCorePeriod(app.today); } }, 60_000);
  setInterval(loadToday, 30 * 60_000);
}

init();
