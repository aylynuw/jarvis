#!/usr/bin/env python3
"""Jarvis: say "Hey Jarvis", ask a question, hear the answer.

Pipeline:
  microphone -> openWakeWord ("hey_jarvis") -> record until you stop talking
  -> faster-whisper (local speech-to-text) -> Claude Code CLI (web search,
  school timetable, Gmail, Google Docs, Calendar, Reminders)
  -> macOS `say` (spoken answer)

Run:  ./run.sh            (voice mode)
      ./run.sh --text     (type instead of speaking, handy for testing)
      ./run.sh --tools    (show which tools Jarvis can use, then exit)
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

import store

# ---------------------------------------------------------------- settings
SAMPLE_RATE = 16000
CHUNK = 1280                      # 80 ms, what openWakeWord expects
WAKE_THRESHOLD = float(os.getenv("JARVIS_WAKE_THRESHOLD", "0.5"))
WHISPER_MODEL = os.getenv("JARVIS_WHISPER_MODEL", "base")   # tiny/base/small
CLAUDE_MODEL = os.getenv("JARVIS_CLAUDE_MODEL", "")         # "" = your default
VOICE_EN = os.getenv("JARVIS_VOICE", "Daniel")              # British, very butler
VOICE_KO = os.getenv("JARVIS_VOICE_KO", "Yuna")
HOME_CITY = os.getenv("JARVIS_CITY", "Seoul, South Korea")  # for weather
ABOUT_USER = os.getenv("JARVIS_ABOUT_USER", "a high-school student in Korea")
SILENCE_SECONDS = 1.2             # stop recording after this much quiet
MAX_RECORD_SECONDS = 15

HERE = Path(__file__).resolve().parent
WORKDIR = HERE / ".claude-workdir"   # empty folder Claude runs in, so it can't touch your files
UPLOADS = WORKDIR / "uploads"        # homework files you drop into the app; the only files Claude may read
MCP_CONFIG = HERE / "jarvis-mcp.json"

# ---------------------------------------------------------------- tool access
# Only these tools are visible to Jarvis. Everything else you have connected
# (Figma, Adobe, Roblox, ...) is blocked, which keeps answers fast and cheap
# and means Jarvis can never send, delete or trash anything.
ALLOWED_TOOLS = {
    "WebSearch", "WebFetch", "Read",
    # Gmail: read, plus writing DRAFTS (never sending)
    "mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread",
    "mcp__claude_ai_Gmail__get_message", "mcp__claude_ai_Gmail__list_labels",
    "mcp__claude_ai_Gmail__create_draft", "mcp__claude_ai_Gmail__update_draft",
    "mcp__claude_ai_Gmail__list_drafts", "mcp__claude_ai_Gmail__get_draft",
    # Google Drive / Docs: create and edit docs, find files
    "mcp__claude_ai_Google_Drive__create_file", "mcp__claude_ai_Google_Drive__search_files",
    "mcp__claude_ai_Google_Drive__list_recent_files", "mcp__claude_ai_Google_Drive__get_file_metadata",
    "mcp__claude_ai_Google_Drive__read_file_content",
    "mcp__claude_ai_Google_Docs__read_doc", "mcp__claude_ai_Google_Docs__update_doc",
}
# Google Calendar (once connected on claude.ai): read events and add new ones.
# Changing, deleting and responding to events stay blocked.
CALENDAR_READ = re.compile(
    r"^mcp__claude_ai_Google_Calendar__(list|get|search|find|query|suggest_time$|create_event$)")


def write_mcp_config() -> None:
    """Point Claude at tools_server.py with absolute paths (Claude runs in WORKDIR)."""
    config = {"mcpServers": {"jarvis": {
        "command": sys.executable, "args": [str(HERE / "tools_server.py")]}}}
    MCP_CONFIG.write_text(json.dumps(config, indent=2))


def discover_tools() -> list[str]:
    """Ask Claude Code which tools exist, without making a model call.

    The first line of stream-json output lists every tool; we read it and
    stop the process before it ever talks to the model.
    """
    WORKDIR.mkdir(exist_ok=True)
    write_mcp_config()
    proc = subprocess.Popen(
        ["claude", "-p", "ok", "--output-format", "stream-json", "--verbose",
         "--disable-slash-commands", "--tools", "WebSearch", "WebFetch", "Read",
         "--mcp-config", str(MCP_CONFIG)],
        cwd=WORKDIR, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    tools: list[str] = []
    try:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "system" and msg.get("subtype") == "init":
                tools = msg.get("tools", [])
                break
    finally:
        proc.kill()
        proc.wait()
    return tools


def tool_policy() -> tuple[list[str], list[str]]:
    """Returns (allowed, denied). Denied tools are removed from Claude's context."""
    available = discover_tools()
    allowed = [t for t in available if t in ALLOWED_TOOLS or CALENDAR_READ.match(t)
               or t.startswith("mcp__jarvis__")]          # all of tools_server.py
    denied = [t for t in available if t.startswith("mcp__") and t not in allowed]
    return allowed, denied


# ---------------------------------------------------------------- personality
SYSTEM_PROMPT = """You are JARVIS, a calm, witty British AI butler and personal agent, speaking out loud to your user.

About the user: {about}. Home city for weather: {city}.
Current local time: {now}.

How you speak:
- Your reply is read aloud by text-to-speech. Plain sentences only: no markdown, no lists, no emoji, no URLs.
- Short: 1-3 sentences unless asked for detail. Reply in the user's language (Korean in, Korean out).
- Address the user as "sir" now and then, not every sentence.

What you can do (use tools, never guess):
- Timetable and lunch: school_timetable and school_meal (official NEIS data, includes timetable changes).
- Calendar: Google Calendar tools if available. If none are available and the user asks about their calendar, say the Google Calendar connector needs to be connected on claude.ai.
  You may ADD events (create_event) on the user's primary calendar when they ask, using Asia/Seoul time; default to 1 hour if no end time is given. Afterwards confirm the title, day and time in one sentence. You cannot change or delete events: if asked, say they need to do that in Google Calendar.
- Email: search and summarise Gmail. You can write a reply or new email as a DRAFT (create_draft / update_draft) when asked; it lands in their Gmail Drafts for them to review and send. You can never send, delete or label mail, so never claim to have sent anything.
- Memory: things you know about the user are listed under "Memories" below. When they say "remember ..." or share something clearly worth keeping (exam dates, goals, preferences), save it with remember. Use forget when asked to.
- The Mac: open_app, open_url, set_volume, media_control (Spotify/Music), set_timer, set_dark_mode. Just do it and confirm in a few words.
- Homework help: the user may attach files (photos of problems, PDFs, notes). They are listed as "Attached files"; open each with Read before answering. For a problem, explain the method step by step so they learn it, then give the answer. When on screen (not voice-only) you may use a few short lines; still no markdown symbols.
- Documents: when asked to make a doc, write the full content yourself, then create it in Google Drive with create_file using contentMimeType "text/html" (proper <h1>/<h2>/<p>/<ul> HTML), which Drive converts to a Google Doc. Give it a clear title. Afterwards just say the title and that it's in their Drive. To change an existing doc, find it with search_files and use update_doc.
- Reminders: add_reminder / list_reminders (Apple Reminders). Convert phrases like "at 7" or "tomorrow morning" into "YYYY-MM-DD HH:MM" using the current time above.
- Anything current (news, weather, prices, scores): WebSearch.

Morning briefing: when the user asks for one, gather today's timetable, today's lunch, today's calendar events (if available), unread or important emails from the last 24 hours, open reminders due today, and today's weather in {city}. Then deliver it as one short spoken paragraph, most important first."""

BRIEFING_PROMPT = ("Give me my morning briefing: today's classes, lunch, calendar, "
                   "important new emails, reminders due today and the weather.")


# ---------------------------------------------------------------- speaking
def speak(text: str, wait: bool = True):
    print(f"\nJARVIS: {text}\n")                     # full text, sources included
    spoken = re.split(r"\n\s*(Sources|출처)\s*:", text)[0]   # don't read links aloud
    spoken = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", spoken)  # [label](url) -> label
    spoken = re.sub(r"https?://\S+", "", spoken)
    spoken = re.sub(r"[*_`#>]", "", spoken)
    voice = VOICE_KO if re.search(r"[가-힣]", spoken) else VOICE_EN
    if wait:
        subprocess.run(["say", "-v", voice, spoken], stdin=subprocess.DEVNULL)
        return None
    return subprocess.Popen(["say", "-v", voice, spoken], stdin=subprocess.DEVNULL)


# ---------------------------------------------------------------- the brain
class Brain:
    """Talks to Claude Code in headless mode. Keeps one conversation going."""

    def __init__(self) -> None:
        WORKDIR.mkdir(exist_ok=True)
        self.session_id = None
        print("Checking which tools are connected...")
        self.allowed, self.denied = tool_policy()
        if not self.allowed:
            print("Warning: couldn't list tools; is `claude` logged in?", file=sys.stderr)

    def describe_tools(self) -> str:
        groups: dict[str, list[str]] = {}
        for t in self.allowed:
            parts = t.split("__")
            server = parts[1] if len(parts) > 2 else "built-in"
            groups.setdefault(server, []).append(parts[-1])
        lines = [f"  {s}: {', '.join(n)}" for s, n in groups.items()]
        if not any(CALENDAR_READ.match(t) for t in self.allowed):
            lines.append("  (Google Calendar not connected)")
        return "\n".join(lines)

    def _system_prompt(self) -> str:
        now = dt.datetime.now().strftime("%A %Y-%m-%d %H:%M")
        prompt = SYSTEM_PROMPT.format(about=ABOUT_USER, city=HOME_CITY, now=now)
        mems = store.memories()
        prompt += "\n\nMemories (what you know about the user):\n" + (
            "\n".join(f"- [{m['id']}] {m['text']}" for m in mems) or "- (nothing yet)")
        return prompt

    def ask(self, question: str, attachments: list[str] | None = None, on_event=None) -> str:
        """Ask Claude. on_event(dict) receives {"type": "tool", "name", "input"} as tools run."""
        if attachments:
            question += "\n\nAttached files (open with Read):\n" + "\n".join(
                f"- {UPLOADS / name}" for name in attachments)
        cmd = [
            "claude", "-p", question,
            "--output-format", "stream-json", "--verbose",
            "--disable-slash-commands",
            "--mcp-config", str(MCP_CONFIG),
            "--tools", "WebSearch", "WebFetch", "Read",
            # Read is limited to the uploads folder; anything else is refused.
            "--allowedTools", *[t for t in self.allowed if t != "Read"], "Read(./uploads/**)",
            "--append-system-prompt", self._system_prompt(),
        ]
        if self.denied:
            cmd += ["--disallowedTools", *self.denied]
        if CLAUDE_MODEL:
            cmd += ["--model", CLAUDE_MODEL]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        UPLOADS.mkdir(parents=True, exist_ok=True)
        result = None
        errlog = open(WORKDIR / "claude-errors.log", "w")   # a file, so a full pipe can't stall us
        proc = subprocess.Popen(cmd, cwd=WORKDIR, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=errlog, text=True)
        self.proc = proc
        deadline = time.time() + 300
        try:
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "assistant" and on_event:
                    for block in msg.get("message", {}).get("content", []):
                        if block.get("type") in ("tool_use", "server_tool_use"):
                            on_event({"type": "tool", "name": block.get("name", ""),
                                      "input": block.get("input", {})})
                elif msg.get("type") == "result":
                    result = msg
                if time.time() > deadline:
                    proc.kill()
                    return "My apologies, sir, that took far too long. Shall we try again?"
        finally:
            proc.wait()
            errlog.close()
            self.proc = None
        if result is None:
            if proc.returncode and proc.returncode < 0:      # cancelled
                return ""
            print((WORKDIR / "claude-errors.log").read_text()[-2000:], file=sys.stderr)
            return "I'm afraid I couldn't reach my brain just now."
        if result.get("is_error"):
            print("Claude error:", result.get("result"), file=sys.stderr)
            return "Something went wrong on my end, sir. Try asking again."
        self.session_id = result.get("session_id", self.session_id)
        return (result.get("result") or "").strip() or "I have nothing to add, sir."

    def cancel(self) -> None:
        proc = getattr(self, "proc", None)
        if proc and proc.poll() is None:
            proc.kill()

    def reset(self) -> None:
        self.session_id = None


# ---------------------------------------------------------------- listening
class Ears:
    def __init__(self) -> None:
        import sounddevice as sd
        from faster_whisper import WhisperModel
        from openwakeword.model import Model
        import openwakeword.utils

        print("Loading wake word model...")
        openwakeword.utils.download_models(model_names=["hey_jarvis"])
        self.wake = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        print(f"Loading speech recognition ({WHISPER_MODEL})...")
        self.whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                     dtype="int16", blocksize=CHUNK)
        self.stream.start()
        self.noise_floor = self._calibrate()

    def _read(self) -> np.ndarray:
        data, _ = self.stream.read(CHUNK)
        return data[:, 0]

    def _drain(self) -> None:
        while self.stream.read_available >= CHUNK:
            self.stream.read(CHUNK)

    def _calibrate(self) -> float:
        print("Measuring room noise, stay quiet for a second...")
        levels = [np.abs(self._read()).mean() for _ in range(12)]
        return float(np.median(levels))

    def wait_for_wake_word(self) -> None:
        self.wake.reset()
        self._drain()
        while True:
            scores = self.wake.predict(self._read())
            if max(scores.values()) >= WAKE_THRESHOLD:
                return

    def record_question(self) -> np.ndarray:
        self._drain()
        threshold = max(self.noise_floor * 2.5, 300)
        frames, quiet, heard_voice = [], 0.0, False
        start = time.time()
        while time.time() - start < MAX_RECORD_SECONDS:
            chunk = self._read()
            frames.append(chunk)
            if np.abs(chunk).mean() > threshold:
                heard_voice, quiet = True, 0.0
            else:
                quiet += CHUNK / SAMPLE_RATE
            if heard_voice and quiet >= SILENCE_SECONDS:
                break
            if not heard_voice and quiet >= 5:      # nobody said anything
                return np.array([], dtype=np.int16)
        return np.concatenate(frames)

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size == 0:
            return ""
        segments, _ = self.whisper.transcribe(audio.astype(np.float32) / 32768.0,
                                              beam_size=1, vad_filter=True)
        # Drop what Whisper itself thinks is noise, not speech (room sounds,
        # a false wake), so Jarvis doesn't answer gibberish.
        kept = [s.text for s in segments
                if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0]
        return " ".join(kept).strip()


# ---------------------------------------------------------------- main loop
BRIEFING_WORDS = ("good morning", "morning briefing", "briefing", "좋은 아침", "브리핑")


def handle(brain: Brain, text: str) -> bool:
    """Returns False when the user wants to quit."""
    lower = text.lower().strip(" .!?")
    if lower in {"goodbye", "exit", "quit", "shut down", "go to sleep", "잘 자", "종료"}:
        speak("Very good, sir. Shutting down.")
        return False
    if lower in {"new conversation", "forget that", "reset"}:
        brain.reset()
        speak("Clean slate, sir.")
        return True
    if any(w in lower for w in BRIEFING_WORDS) and len(lower) < 40:
        text = BRIEFING_PROMPT
    print("(thinking...)")
    filler = speak("One moment, sir.", wait=False)
    answer = brain.ask(text)
    if filler:
        filler.wait()
    speak(answer)
    return True


def text_mode(brain: Brain) -> None:
    speak("JARVIS online. Type your questions, sir.")
    while True:
        try:
            q = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q and not handle(brain, q):
            break


def voice_mode(brain: Brain) -> None:
    ears = Ears()
    speak("JARVIS online. Say hey Jarvis when you need me.")
    while True:
        print('Listening for "Hey Jarvis"...')
        ears.wait_for_wake_word()
        subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"])
        speak("Yes, sir?")
        text = ears.transcribe(ears.record_question())
        if not text:
            speak("I didn't catch that.")
            continue
        print(f"You: {text}")
        if not handle(brain, text):
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JARVIS voice assistant")
    parser.add_argument("--text", action="store_true", help="type instead of speaking")
    parser.add_argument("--tools", action="store_true", help="list available tools and exit")
    args = parser.parse_args()
    brain = Brain()
    print("Tools:\n" + brain.describe_tools())
    if args.tools:
        sys.exit(0)
    try:
        text_mode(brain) if args.text else voice_mode(brain)
    except KeyboardInterrupt:
        print("\nGoodbye.")
