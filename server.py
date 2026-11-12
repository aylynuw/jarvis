"""Jarvis app server: the brain, voice and timers behind the desktop UI.

The UI (ui/) talks to this over HTTP and one WebSocket (/ws) that streams
state changes, tool activity and messages as they happen.
"""

import asyncio
import datetime as dt
import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import jarvis
import store
import tools_server

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.json"

# Friendly names for the activity feed.
TOOL_LABELS = {
    "school_timetable": "시간표 확인", "school_meal": "급식 확인",
    "add_reminder": "미리 알림 추가", "list_reminders": "미리 알림 확인",
    "remember": "기억 저장", "forget": "기억 삭제", "list_memories": "기억 확인",
    "open_app": "앱 열기", "open_url": "링크 열기", "set_volume": "볼륨 조절",
    "media_control": "음악 제어", "set_timer": "타이머 설정", "set_dark_mode": "다크 모드",
    "WebSearch": "웹 검색", "web_search": "웹 검색", "WebFetch": "웹페이지 읽기",
    "web_fetch": "웹페이지 읽기", "Read": "첨부 파일 읽기",
    "search_threads": "메일 검색", "get_thread": "메일 읽기", "get_message": "메일 읽기",
    "list_labels": "메일 라벨 확인", "create_draft": "메일 초안 작성",
    "update_draft": "메일 초안 수정", "list_drafts": "초안 확인", "get_draft": "초안 읽기",
    "create_file": "구글 문서 만들기", "search_files": "드라이브 검색",
    "list_recent_files": "최근 파일 확인", "read_file_content": "파일 읽기",
    "get_file_metadata": "파일 정보 확인", "read_doc": "문서 읽기", "update_doc": "문서 수정",
    "list_events": "일정 확인", "search_events": "일정 검색", "get_event": "일정 읽기",
    "list_calendars": "캘린더 확인", "create_event": "일정 추가", "suggest_time": "빈 시간 찾기",
}


def tool_label(name: str, args: dict) -> str:
    short = name.split("__")[-1]
    label = TOOL_LABELS.get(short, short.replace("_", " "))
    detail = (args.get("query") or args.get("title") or args.get("name") or args.get("fact")
              or args.get("label") or args.get("summary") or "")
    if short == "Read" and args.get("file_path"):
        detail = Path(args["file_path"]).name
    if isinstance(detail, str) and detail:
        return f"{label}: {detail[:60]}"
    return label


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"period_times": []}


# ------------------------------------------------------------------ the hub
class Hub:
    """Shared state plus a thread-safe broadcast to every open window."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sockets: set[WebSocket] = set()
        self.state = "booting"
        self.messages: list[dict] = []
        self.activity: list[dict] = []
        self.settings = {"voice_enabled": os.getenv("JARVIS_START_WAKE", "1") == "1",
                         "muted": os.getenv("JARVIS_START_MUTED", "0") == "1"}
        self.voice_status = "starting"          # starting | ready | unavailable
        self.voice_error = ""

    def emit(self, event: dict) -> None:
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(asyncio.ensure_future, self._send(event))

    async def _send(self, event: dict) -> None:
        dead = []
        for ws in list(self.sockets):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.sockets.discard(ws)

    def set_state(self, state: str) -> None:
        self.state = state
        self.emit({"type": "state", "state": state})

    def add_message(self, role: str, text: str, attachments=None) -> dict:
        msg = {"id": uuid.uuid4().hex[:8], "role": role, "text": text,
               "attachments": attachments or [], "time": dt.datetime.now().strftime("%H:%M")}
        self.messages.append(msg)
        self.messages = self.messages[-200:]
        self.emit({"type": "message", "message": msg})
        return msg

    def add_activity(self, label: str, kind: str = "tool") -> None:
        item = {"id": uuid.uuid4().hex[:8], "label": label, "kind": kind,
                "time": dt.datetime.now().strftime("%H:%M:%S")}
        self.activity.append(item)
        self.activity = self.activity[-60:]
        self.emit({"type": "activity", "item": item})

    def snapshot(self) -> dict:
        return {"state": self.state, "messages": self.messages, "activity": self.activity,
                "settings": self.settings, "voice": {"status": self.voice_status,
                                                      "error": self.voice_error}}


hub = Hub()


# ------------------------------------------------------------------ speech
class Mouth:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.last_spoke = 0.0          # when speech last ended; the wake word waits a moment after

    def say(self, text: str) -> None:
        if hub.settings["muted"] or not text:
            return
        spoken = re.split(r"\n\s*(Sources|출처)\s*:", text)[0]
        spoken = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", spoken)
        spoken = re.sub(r"https?://\S+", "", spoken)
        spoken = re.sub(r"[*_`#>]", "", spoken)
        voice = jarvis.VOICE_KO if re.search(r"[가-힣]", spoken) else jarvis.VOICE_EN
        with self.lock:
            self.proc = subprocess.Popen(["say", "-v", voice, spoken], stdin=subprocess.DEVNULL)
        hub.set_state("speaking")
        self.proc.wait()
        self.last_spoke = time.time()

    def stop(self) -> None:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()


mouth = Mouth()


# ------------------------------------------------------------------ the brain worker
class Worker(threading.Thread):
    """Runs one request at a time: think, then speak."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.jobs: queue.Queue = queue.Queue()
        self.brain: jarvis.Brain | None = None
        self.busy = False

    def submit(self, text: str, attachments: list[str] | None = None) -> None:
        self.jobs.put((text, attachments or []))

    def run(self) -> None:
        hub.add_activity("연결된 도구 확인 중", "system")
        self.brain = jarvis.Brain()
        friendly = {"claude_ai_Gmail": "Gmail", "claude_ai_Google_Calendar": "캘린더",
                    "claude_ai_Google_Docs": "구글 문서", "claude_ai_Google_Drive": "드라이브",
                    "jarvis": "학교와 맥 도구", "web": "웹 검색"}
        names = sorted({t.split("__")[1] if t.count("__") >= 2 else "web"
                        for t in self.brain.allowed})
        hub.add_activity("준비 완료: " + ", ".join(friendly.get(n, n) for n in names), "system")
        hub.set_state("idle")
        while True:
            text, attachments = self.jobs.get()
            self.busy = True
            try:
                self._handle(text, attachments)
            except Exception as e:                       # keep the app alive
                hub.add_activity(f"오류: {e}", "error")
                hub.add_message("jarvis", "Something went wrong on my end, sir.")
            finally:
                self.busy = False
                hub.set_state("idle")

    def _handle(self, text: str, attachments: list[str]) -> None:
        lower = text.lower().strip(" .!?")
        if lower in {"new conversation", "reset", "새 대화"}:
            self.brain.reset()
            hub.add_message("jarvis", "Clean slate, sir.")
            mouth.say("Clean slate, sir.")
            return
        if any(w in lower for w in jarvis.BRIEFING_WORDS) and len(lower) < 40:
            text = jarvis.BRIEFING_PROMPT
        hub.set_state("thinking")
        answer = self.brain.ask(
            text, attachments,
            on_event=lambda ev: hub.add_activity(tool_label(ev["name"], ev["input"] or {})))
        if not answer:                                  # cancelled
            hub.add_activity("중단됨", "system")
            return
        hub.add_message("jarvis", answer)
        mouth.say(answer)
        hub.emit({"type": "refresh"})                   # memories/reminders may have changed


worker = Worker()


# ------------------------------------------------------------------ voice
class Voice(threading.Thread):
    """Wake word + push-to-talk. Ignores the wake word while Jarvis is busy."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.ptt = threading.Event()
        self.ears: jarvis.Ears | None = None

    def run(self) -> None:
        try:
            self.ears = jarvis.Ears()
            hub.voice_status = "ready"
        except Exception as e:
            hub.voice_status, hub.voice_error = "unavailable", str(e)[:200]
            hub.emit({"type": "voice", "status": "unavailable", "error": hub.voice_error})
            return
        hub.emit({"type": "voice", "status": "ready"})
        ears = self.ears
        while True:
            ears.wake.reset()
            ears._drain()
            triggered = False
            hits = 0
            while not triggered:
                chunk = ears._read()
                if self.ptt.is_set():
                    triggered = True
                    continue
                quiet_since = time.time() - mouth.last_spoke
                if not hub.settings["voice_enabled"] or worker.busy or quiet_since < 2.0:
                    hits = 0                      # never wake on our own voice
                    continue
                score = max(ears.wake.predict(chunk).values())
                hits = hits + 1 if score >= jarvis.WAKE_THRESHOLD else 0
                triggered = hits >= 2             # two 80 ms chunks in a row, not a blip
            self.ptt.clear()
            if worker.busy:
                continue
            mouth.stop()
            subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"])
            hub.set_state("listening")
            text = ears.transcribe(ears.record_question())
            if not text:
                hub.set_state("idle")
                hub.add_activity("음성을 알아듣지 못함", "system")
                continue
            hub.add_message("user", text)
            worker.submit(text)


voice = Voice()


# ------------------------------------------------------------------ timers
def announce_timer(text: str) -> None:
    mouth.say(text)
    if not worker.busy and hub.state == "speaking":
        hub.set_state("idle")              # nothing else returns the core to idle after this one


def timer_loop() -> None:
    while True:
        time.sleep(1)
        for t in store.due_timers():
            hub.add_activity(f"타이머 종료: {t['label']}", "timer")
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
            subprocess.Popen(["osascript", "-e",
                              f'display notification "{t["label"]}" with title "JARVIS 타이머"'])
            hub.emit({"type": "timer_done", "timer": t, "refresh": True})
            if not worker.busy:
                threading.Thread(target=announce_timer, daemon=True,
                                 args=(f"Sir, your {t['label']} timer is done.",)).start()


# ------------------------------------------------------------------ data for the panels
_today_cache: dict = {}


def today_data() -> dict:
    today = dt.date.today()
    cached = _today_cache.get("data")
    if cached and cached["date"] == today.isoformat() and time.time() - _today_cache["at"] < 600:
        return cached
    try:
        if not tools_server.SCHOOL_CODE:
            raise RuntimeError(tools_server.NO_SCHOOL)
        tt = tools_server.timetable_day(today)
        meals = tools_server.meal_day(today)
        error = ""
    except Exception as e:
        tt, meals, error = {"periods": [], "total": 0}, [], str(e)[:200]
    data = {"date": today.isoformat(), "weekday": "월화수목금토일"[today.weekday()],
            "periods": tt["periods"], "total": tt["total"], "meals": meals,
            "period_times": load_config().get("period_times", []), "error": error}
    if not error:
        _today_cache.update(data=data, at=time.time())
    return data


def reminders_data() -> list[dict]:
    script = (
        'set out to ""\n'
        'tell application "Reminders"\n'
        '  repeat with r in (reminders whose completed is false)\n'
        '    set d to ""\n'
        '    try\n      set d to ((due date of r) as «class isot» as string)\n    end try\n'
        '    set out to out & (name of r) & tab & d & linefeed\n'
        '  end repeat\n'
        'end tell\n'
        'return out')
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "Reminders unavailable")
    items = []
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        name, _, due = line.partition("\t")
        items.append({"title": name, "due": due or None})
    items.sort(key=lambda r: r["due"] or "9999")
    return items


# ------------------------------------------------------------------ HTTP
app = FastAPI(title="JARVIS")


class Ask(BaseModel):
    text: str
    attachments: list[str] = []


class Settings(BaseModel):
    voice_enabled: bool | None = None
    muted: bool | None = None


class NewMemory(BaseModel):
    text: str


@app.on_event("startup")
async def startup() -> None:
    hub.loop = asyncio.get_running_loop()
    if not worker.is_alive():
        worker.start()
        voice.start()
        threading.Thread(target=timer_loop, daemon=True).start()


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    hub.sockets.add(socket)
    await socket.send_json({"type": "snapshot", **hub.snapshot()})
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        hub.sockets.discard(socket)


@app.get("/api/state")
def get_state():
    return hub.snapshot()


@app.post("/api/ask")
def ask(body: Ask):
    text = body.text.strip()
    if not text and not body.attachments:
        raise HTTPException(400, "Empty question")
    for name in body.attachments:
        if not (jarvis.UPLOADS / name).is_file():
            raise HTTPException(400, f"Unknown attachment {name}")
    if not text:
        text = "이 파일 좀 봐줘. 문제라면 풀이 과정을 설명해줘."
    hub.add_message("user", text, body.attachments)
    mouth.stop()
    worker.submit(text, body.attachments)
    return {"ok": True}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    jarvis.UPLOADS.mkdir(parents=True, exist_ok=True)
    original = Path(file.filename or "file").name
    stem = re.sub(r"[^\w가-힣.-]+", "_", Path(original).stem)[:40] or "file"
    suffix = Path(original).suffix.lower()[:8]
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".pdf", ".txt", ".md"}:
        raise HTTPException(400, "사진, PDF, 텍스트 파일만 올릴 수 있어요.")
    name = f"{dt.datetime.now():%m%d-%H%M%S}-{stem}{suffix}"
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "20MB보다 큰 파일은 올릴 수 없어요.")
    (jarvis.UPLOADS / name).write_bytes(data)
    return {"name": name, "original": original, "size": len(data)}


@app.get("/api/uploads/{name}")
def get_upload(name: str):
    path = (jarvis.UPLOADS / Path(name).name)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


@app.post("/api/listen")
def listen():
    if hub.voice_status != "ready":
        raise HTTPException(409, hub.voice_error or "Voice is not available")
    if worker.busy:
        raise HTTPException(409, "Jarvis is busy")
    voice.ptt.set()
    return {"ok": True}


@app.post("/api/stop")
def stop():
    mouth.stop()
    if worker.brain:
        worker.brain.cancel()
    return {"ok": True}


@app.post("/api/settings")
def settings(body: Settings):
    for key, value in body.model_dump().items():
        if value is not None:
            hub.settings[key] = value
    if hub.settings["muted"]:
        mouth.stop()
    hub.emit({"type": "settings", "settings": hub.settings})
    return hub.settings


@app.get("/api/today")
def today():
    return today_data()


@app.get("/api/reminders")
def reminders():
    try:
        return {"items": reminders_data(), "error": ""}
    except Exception as e:
        return {"items": [], "error": str(e)[:200]}


@app.get("/api/memories")
def memories():
    return {"items": store.memories()}


@app.post("/api/memories")
def add_memory(body: NewMemory):
    if not body.text.strip():
        raise HTTPException(400, "Empty memory")
    item = store.remember(body.text)
    hub.emit({"type": "refresh"})
    return item


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str):
    if not store.forget(memory_id):
        raise HTTPException(404)
    return {"ok": True}


@app.get("/api/timers")
def timers():
    return {"items": store.timers()}


@app.delete("/api/timers/{timer_id}")
def delete_timer(timer_id: str):
    if not store.cancel_timer(timer_id):
        raise HTTPException(404)
    return {"ok": True}


@app.middleware("http")
async def no_cache(request, call_next):
    """Always serve the latest UI files (the desktop window caches aggressively)."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/", StaticFiles(directory=HERE / "ui", html=True), name="ui")
