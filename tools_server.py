#!/usr/bin/env python3
"""Jarvis's local tools, served to Claude Code over MCP (stdio).

- school_timetable / school_meal: official NEIS open API (no key needed),
  the same data haksi.kr shows, including timetable changes.
- add_reminder / list_reminders: the macOS Reminders app.
- remember / forget / list_memories: long-term memory (data/memory.json).
- open_app, set_volume, media_control, open_url, set_timer, set_dark_mode:
  control the Mac.
"""

import datetime as dt
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

import store

# Your school, from .env (see .env.example). Look the codes up at https://open.neis.go.kr ("학교기본정보" dataset).
OFFICE_CODE = os.getenv("JARVIS_NEIS_OFFICE", "")      # education office code (ATPT_OFCDC_SC_CODE), e.g. J10
SCHOOL_CODE = os.getenv("JARVIS_NEIS_SCHOOL", "")      # school code (SD_SCHUL_CODE)
GRADE = os.getenv("JARVIS_GRADE", "1")
CLASS = os.getenv("JARVIS_CLASS", "1")
NO_SCHOOL = "No school is set up yet. Add JARVIS_NEIS_OFFICE and JARVIS_NEIS_SCHOOL to .env."
NEIS = "https://open.neis.go.kr/hub/"
WEEKDAYS = "월화수목금토일"

mcp = FastMCP("jarvis")


def _load_key() -> str:
    """Free key from https://open.neis.go.kr ("인증키 신청", request an API key). Without it NEIS
    only returns the first 5 rows of any request (so periods 6-7 are cut off)."""
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "neis-key.txt")
    if os.path.exists(key_file):
        return open(key_file).read().strip()
    return os.getenv("JARVIS_NEIS_KEY", "")


NEIS_KEY = _load_key()


def _neis(endpoint: str, **params) -> tuple[list[dict], int]:
    """Returns (rows, total_rows_available)."""
    base = {"Type": "json", "pSize": 1000, "pIndex": 1, "ATPT_OFCDC_SC_CODE": OFFICE_CODE,
            "SD_SCHUL_CODE": SCHOOL_CODE, **params}
    if NEIS_KEY:
        base["KEY"] = NEIS_KEY
    url = NEIS + endpoint + "?" + urllib.parse.urlencode(base)
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.load(r)
    if endpoint not in data:          # e.g. INFO-200: no data (weekend, holiday)
        return [], 0
    return data[endpoint][1]["row"], data[endpoint][0]["head"][0]["list_total_count"]


def _parse_day(day: str) -> dt.date:
    today = dt.date.today()
    d = (day or "today").strip().lower()
    if d in ("today", "오늘"):
        return today
    if d in ("tomorrow", "내일"):
        return today + dt.timedelta(days=1)
    if d in ("yesterday", "어제"):
        return today - dt.timedelta(days=1)
    return dt.date.fromisoformat(d)


def _label(day: dt.date) -> str:
    return f"{day.isoformat()} ({WEEKDAYS[day.weekday()]})"


@mcp.tool()
def school_timetable(day: str = "today", whole_week: bool = False) -> str:
    """Class timetable for the user's class (school, grade and class from .env) from NEIS.

    day: "today", "tomorrow", "yesterday" or YYYY-MM-DD.
    whole_week: True returns Monday-Friday of the week containing `day`.
    Reflects timetable changes the school has entered into NEIS.
    """
    if not SCHOOL_CODE:
        return NO_SCHOOL
    base = _parse_day(day)
    start = base - dt.timedelta(days=base.weekday()) if whole_week else base
    days = [start + dt.timedelta(days=i) for i in range(5 if whole_week else 1)]
    out = []
    for d in days:                    # one request per day keeps us under NEIS's keyless limit
        info = timetable_day(d)
        body = ", ".join(f"{p['period']}교시 {p['subject']}" for p in info["periods"]) \
            or "수업 없음 (주말/휴일이거나 아직 등록 안 됨)"
        if info["total"] > len(info["periods"]):
            body += (f" (총 {info['total']}교시 중 {len(info['periods'])}교시까지만 표시됨: "
                     "NEIS 인증키가 없어서 나머지는 확인 불가)")
        out.append(f"{_label(d)}: {body}")
    return "\n".join(out)


def timetable_day(d: dt.date) -> dict:
    """Structured timetable for one day (also used by the desktop UI)."""
    rows, total = _neis("hisTimetable", GRADE=GRADE, CLASS_NM=CLASS,
                        ALL_TI_YMD=d.strftime("%Y%m%d"))
    periods = sorted({(int(r["PERIO"]), r["ITRT_CNTNT"]) for r in rows})
    return {"date": d.isoformat(), "total": total,
            "periods": [{"period": p, "subject": s} for p, s in periods]}


def meal_day(d: dt.date) -> list[dict]:
    """Structured meals for one day (also used by the desktop UI)."""
    rows, _ = _neis("mealServiceDietInfo", MLSV_YMD=d.strftime("%Y%m%d"))
    return [{"name": r["MMEAL_SC_NM"],
             "dishes": [re.sub(r"\s*\([\d.]+\)", "", x).strip()
                        for x in r["DDISH_NM"].split("<br/>") if x.strip()]}
            for r in rows]


@mcp.tool()
def school_meal(day: str = "today") -> str:
    """School lunch/dinner menu for the user's school from NEIS. day: today/tomorrow/YYYY-MM-DD."""
    if not SCHOOL_CODE:
        return NO_SCHOOL
    d = _parse_day(day)
    meals = meal_day(d)
    if not meals:
        return f"{_label(d)}: 급식 정보 없음"
    return f"{_label(d)}\n" + "\n".join(f"{m['name']}: {', '.join(m['dishes'])}" for m in meals)


def _osascript(script: str) -> str:
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return out.stdout.strip()


def _as_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


@mcp.tool()
def add_reminder(title: str, due: str = "", notes: str = "") -> str:
    """Add a reminder to the macOS Reminders app (default list).

    due: optional local date-time "YYYY-MM-DD HH:MM" (24h). Leave empty for no due time.
    """
    props = f"name:{_as_str(title)}"
    if notes:
        props += f", body:{_as_str(notes)}"
    script = ""
    if due:
        when = dt.datetime.strptime(due.strip(), "%Y-%m-%d %H:%M")
        script = (
            "set d to current date\n"
            f"set year of d to {when.year}\nset month of d to {when.month}\n"
            f"set day of d to {when.day}\nset hours of d to {when.hour}\n"
            f"set minutes of d to {when.minute}\nset seconds of d to 0\n"
        )
        props += ", due date:d, remind me date:d"
    script += f'tell application "Reminders" to make new reminder with properties {{{props}}}'
    _osascript(script)
    return f"Reminder added: {title}" + (f" at {due}" if due else "")


@mcp.tool()
def list_reminders() -> str:
    """List incomplete reminders (title and due date) from the macOS Reminders app."""
    script = (
        'set out to ""\n'
        'tell application "Reminders"\n'
        '  repeat with r in (reminders whose completed is false)\n'
        '    set out to out & name of r\n'
        '    try\n      set out to out & " | due " & ((due date of r) as string)\n    end try\n'
        '    set out to out & linefeed\n'
        '  end repeat\n'
        'end tell\n'
        'return out'
    )
    return _osascript(script) or "No open reminders."


# ------------------------------------------------------------------ memory
@mcp.tool()
def remember(fact: str) -> str:
    """Save a fact about the user to long-term memory (kept across restarts).

    Use when the user says "remember ...", or shares something clearly worth
    keeping (exam dates, preferences, goals). One short self-contained sentence.
    """
    item = store.remember(fact)
    return f"Remembered (id {item['id']}): {item['text']}"


@mcp.tool()
def list_memories() -> str:
    """List everything in long-term memory, with ids."""
    items = store.memories()
    return "\n".join(f"{m['id']}: {m['text']}" for m in items) or "Memory is empty."


@mcp.tool()
def forget(memory_id: str) -> str:
    """Delete one memory by id (get ids from list_memories)."""
    return "Forgotten." if store.forget(memory_id) else f"No memory with id {memory_id}."


# ------------------------------------------------------------------ the Mac
@mcp.tool()
def open_app(name: str) -> str:
    """Open (or bring to front) a macOS app by name, e.g. "Safari", "Notes", "Spotify"."""
    out = subprocess.run(["open", "-a", name], capture_output=True, text=True, timeout=20)
    if out.returncode != 0:
        return f"Couldn't open {name}: {out.stderr.strip()}"
    return f"Opened {name}."


@mcp.tool()
def open_url(url: str) -> str:
    """Open a web page in the default browser. Only http(s) URLs."""
    if not re.match(r"^https?://", url):
        return "Only http(s) links can be opened."
    subprocess.run(["open", url], timeout=20)
    return f"Opened {url}"


@mcp.tool()
def set_volume(level: int) -> str:
    """Set the Mac's output volume, 0-100."""
    level = max(0, min(100, int(level)))
    _osascript(f"set volume output volume {level}")
    return f"Volume set to {level}."


@mcp.tool()
def media_control(action: str) -> str:
    """Control music playback in Spotify or Apple Music (whichever is running).

    action: "play", "pause", "next", "previous" or "now_playing".
    """
    commands = {"play": "play", "pause": "pause", "next": "next track",
                "previous": "previous track"}
    for app in ("Spotify", "Music"):
        running = _osascript(f'application "{app}" is running') == "true"
        if not running:
            continue
        if action == "now_playing":
            return _osascript(
                f'tell application "{app}" to (name of current track) & " by " & (artist of current track)')
        if action not in commands:
            return "Unknown action."
        _osascript(f'tell application "{app}" to {commands[action]}')
        return f"{app}: {action}."
    if action == "play":
        _osascript('tell application "Music" to play')
        return "Music: play."
    return "Neither Spotify nor Music is running."


@mcp.tool()
def set_timer(minutes: float, label: str = "Timer") -> str:
    """Start a countdown timer. Jarvis announces it out loud when it's done.

    minutes: can be fractional (0.5 = 30 seconds). label: what it's for.
    """
    seconds = int(round(float(minutes) * 60))
    if seconds <= 0:
        return "The timer needs a positive length."
    item = store.add_timer(seconds, label)
    return f"Timer '{item['label']}' set, ends at {item['fire_at'][11:16]}."


@mcp.tool()
def set_dark_mode(enabled: bool) -> str:
    """Turn macOS dark mode on or off."""
    flag = "true" if enabled else "false"
    _osascript(f'tell application "System Events" to tell appearance preferences to set dark mode to {flag}')
    return f"Dark mode {'on' if enabled else 'off'}."


if __name__ == "__main__":
    mcp.run()
