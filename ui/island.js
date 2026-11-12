// JARVIS overlay: a HUD panel that drops down from the top of the screen when Jarvis wakes.
// Listens to the same WebSocket as the dashboard. The native window is shown and hidden by
// app.py through window.pywebview.api; in a plain browser those calls are skipped.
import { mountCore } from "./jarvis-core.js";

document.documentElement.classList.add("js");   // the folded state only exists once this script runs
const $ = (s) => document.querySelector(s);
const body = document.body;
const params = new URLSearchParams(location.search);
if (params.get("nw")) document.documentElement.style.setProperty("--nw", `${params.get("nw")}px`);
if (params.get("nh")) document.documentElement.style.setProperty("--nh", `${params.get("nh")}px`);

const native = (name, ...args) => window.pywebview?.api?.[name]?.(...args).catch?.(() => {});
const ISLAND_MS = 560;

const ui = { state: "booting", heard: "", answer: "", tool: "", booted: false, hideTimer: 0, offTimer: 0, hover: false };

// ------------------------------------------------------------ open and close
function setMode(mode) {
  clearTimeout(ui.offTimer);
  if (mode === "off") {
    body.dataset.mode = "off";
    // After the fold, the window goes invisible and click-through and the core stops drawing.
    ui.offTimer = setTimeout(() => { native("hide"); body.dataset.sleep = ""; }, ISLAND_MS);
    return;
  }
  native("show", mode === "full");           // only the full island has buttons to click
  if (body.dataset.mode === "off") {
    delete body.dataset.sleep;
    requestAnimationFrame(() => requestAnimationFrame(() => { body.dataset.mode = mode; }));
  } else {
    body.dataset.mode = mode;
  }
}

function closeLater(ms) {
  clearTimeout(ui.hideTimer);
  ui.hideTimer = setTimeout(function tryClose() {
    if (ui.hover) { ui.hideTimer = setTimeout(tryClose, 1500); return; }
    if (ui.state === "idle") setMode("off");
  }, ms);
}

// ------------------------------------------------------------ text
const LABELS = { booting: "Jarvis 준비 중", listening: "듣고 있어요", thinking: "생각 중", speaking: "Jarvis", idle: "Jarvis" };

function plain(text) {
  // Speech and the island show the same answer: drop links and markdown marks, keep the words.
  return text
    .split(/\n\s*(?:Sources|출처)\s*:/)[0]
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/[*_`#>]/g, "")
    .trim();
}

function renderAnswer() {
  const box = $("#answer");
  box.replaceChildren(...plain(ui.answer).split(/\n{2,}/).filter(Boolean).map((para) => {
    const p = document.createElement("p");
    p.textContent = para.replace(/\n/g, " ");
    return p;
  }));
  box.scrollTop = 0;
}

function render() {
  $("#c-label").textContent = ui.state === "idle" && !ui.booted ? "Jarvis 준비 중" : LABELS[ui.state] || "Jarvis";
  let detail = "";
  if (ui.state === "listening") detail = "말씀하세요";
  if (ui.state === "thinking") detail = ui.tool || ui.heard;
  if (ui.state === "booting") detail = "도구를 연결하고 있어요";
  if (ui.ready) { $("#c-label").textContent = "준비됐어요"; detail = "“Hey Jarvis”라고 불러 주세요"; }
  $("#c-detail").textContent = detail;
  $("#f-label").textContent = ui.state === "speaking" ? "Jarvis 말하는 중" : "Jarvis";
  $("#heard").textContent = ui.heard;
  $("#stop").hidden = !(ui.state === "speaking" || ui.state === "thinking");
}

// ------------------------------------------------------------ state machine
function onState(state) {
  const prev = ui.state;
  ui.state = state;
  body.dataset.state = state;
  if (state !== "idle") ui.ready = false;
  if (state === "booting") setMode("compact");
  if (state === "listening") { ui.heard = ""; ui.answer = ""; ui.tool = ""; clearTimeout(ui.hideTimer); setMode("compact"); }
  if (state === "thinking") { ui.tool = ""; clearTimeout(ui.hideTimer); if (body.dataset.mode !== "full") setMode("compact"); }
  if (state === "speaking") { clearTimeout(ui.hideTimer); if (ui.answer) setMode("full"); else setMode("compact"); }
  if (state === "idle") {
    if (!ui.booted) {
      ui.booted = true;
      if (prev === "booting") { ui.ready = true; setMode("compact"); closeLater(2600); }
    } else if (body.dataset.mode === "full") {
      closeLater(Math.min(14000, 5000 + plain(ui.answer).length * 25));
    } else {
      closeLater(prev === "listening" ? 900 : 1500);
    }
  }
  render();
}

function onMessage(m) {
  if (m.role === "user") { ui.heard = m.text; ui.answer = ""; }
  if (m.role === "jarvis") {
    ui.answer = m.text;
    renderAnswer();
    setMode("full");
    if (ui.state === "idle") closeLater(Math.min(14000, 5000 + plain(ui.answer).length * 25));
  }
  render();
}

// ------------------------------------------------------------ socket
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "snapshot") {
      ui.booted = msg.state !== "booting";
      onState(msg.state);
    }
    if (msg.type === "state") onState(msg.state);
    if (msg.type === "message") onMessage(msg.message);
    if (msg.type === "activity" && msg.item.kind === "tool") { ui.tool = msg.item.label; render(); }
    if (msg.type === "timer_done") {
      ui.heard = ""; ui.answer = msg.timer?.label ? `${msg.timer.label} 타이머가 끝났어요.` : "타이머가 끝났어요.";
      renderAnswer(); setMode("full"); closeLater(8000); render();
    }
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

// ------------------------------------------------------------ actions
$("#stop").addEventListener("click", () => fetch("/api/stop", { method: "POST" }));
$("#open").addEventListener("click", () => { native("open_dashboard"); if (!window.pywebview) open("/", "_blank"); });
$("#close").addEventListener("click", () => { clearTimeout(ui.hideTimer); setMode("off"); });
$("#panel-f").addEventListener("pointerenter", () => { ui.hover = true; });
$("#panel-f").addEventListener("pointerleave", () => { ui.hover = false; });
addEventListener("keydown", (e) => { if (e.key === "Escape") setMode("off"); });

const core = mountCore($("#core"), { getState: () => ui.state, background: "--color-glass-solid", parallax: false, tilt: 0.5, distance: 12.2, glow: 0.45 });
if (core) new MutationObserver(() => { if (matchMedia("(prefers-reduced-motion: reduce)").matches) core.redraw(); })
  .observe(body, { attributes: true, attributeFilter: ["data-state"] });

body.dataset.mode = "off";
body.dataset.sleep = "";
render();
connect();

// For local checks in a browser (scratch capture scripts drive the island through this).
window.__island = { onState, onMessage };
