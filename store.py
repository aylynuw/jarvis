"""Small JSON stores shared by the tool server (which Claude runs) and the app.

data/memory.json  long-term facts Jarvis remembers about you
data/timers.json  timers set by voice; the app fires them
"""

import datetime as dt
import json
import os
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def load_env(path: Path = HERE / ".env") -> None:
    """Personal settings (school, class, city) live in .env, which never goes to git.
    Real environment variables win over the file."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()
MEMORY_FILE = DATA / "memory.json"
TIMERS_FILE = DATA / "timers.json"


def _load(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(path: Path, items: list[dict]) -> None:
    DATA.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)               # atomic: never a half-written file


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ memory
def memories() -> list[dict]:
    return _load(MEMORY_FILE)


def remember(text: str) -> dict:
    items = memories()
    item = {"id": uuid.uuid4().hex[:8], "text": text.strip(), "created": _now()}
    items.append(item)
    _save(MEMORY_FILE, items)
    return item


def forget(memory_id: str) -> bool:
    items = memories()
    kept = [m for m in items if m["id"] != memory_id]
    _save(MEMORY_FILE, kept)
    return len(kept) != len(items)


# ------------------------------------------------------------------ timers
def timers() -> list[dict]:
    return _load(TIMERS_FILE)


def add_timer(seconds: int, label: str) -> dict:
    items = [t for t in timers() if not t.get("fired")]
    fire_at = dt.datetime.now() + dt.timedelta(seconds=seconds)
    item = {"id": uuid.uuid4().hex[:8], "label": label.strip() or "Timer",
            "fire_at": fire_at.isoformat(timespec="seconds"), "fired": False}
    items.append(item)
    _save(TIMERS_FILE, items)
    return item


def due_timers() -> list[dict]:
    """Marks due timers as fired and returns them."""
    items = timers()
    now = dt.datetime.now()
    due = [t for t in items if not t["fired"] and dt.datetime.fromisoformat(t["fire_at"]) <= now]
    if due:
        for t in due:
            t["fired"] = True
        _save(TIMERS_FILE, [t for t in items if not t["fired"]])
    return due


def cancel_timer(timer_id: str) -> bool:
    items = timers()
    kept = [t for t in items if t["id"] != timer_id]
    _save(TIMERS_FILE, kept)
    return len(kept) != len(items)
