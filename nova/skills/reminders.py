"""Reminders and alarms that survive restarts. Nova pops up and speaks when they're due."""
import json
import platform
import threading
import time
import uuid
from datetime import datetime, timedelta

from nova.config import DATA_DIR
from nova.tools import ctx, tool

FILE = DATA_DIR / "reminders.json"
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: list[dict]):
    FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _parse_when(time_24h: str, date: str, minutes_from_now: int) -> datetime:
    now = datetime.now()
    if minutes_from_now:
        return now + timedelta(minutes=minutes_from_now)
    clean = time_24h.strip().upper().replace(".", ":")
    for fmt in ("%H:%M", "%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
        try:
            t = datetime.strptime(clean, fmt).time()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"I didn't understand the time '{time_24h}'.")
    day = datetime.strptime(date, "%Y-%m-%d").date() if date else now.date()
    when = datetime.combine(day, t)
    if when <= now and not date:
        when += timedelta(days=1)  # "7:00" when it's already 8pm means tomorrow
    return when


@tool("Set a reminder or alarm. Give EITHER minutes_from_now OR time_24h (plus optional date).",
      message="What to remind about, e.g. 'Wake up!' or 'Call amma'",
      time_24h="Time like 07:30 or 18:00",
      date="Date as YYYY-MM-DD, empty for today/tomorrow",
      minutes_from_now="Minutes from now, e.g. 10")
def set_reminder(message: str, time_24h: str = "", date: str = "", minutes_from_now: int = 0) -> str:
    when = _parse_when(time_24h, date, minutes_from_now)
    with _lock:
        items = _load()
        items.append({"id": uuid.uuid4().hex[:6], "message": message, "when": when.isoformat(timespec="minutes")})
        _save(items)
    return f"Reminder set for {when.strftime('%I:%M %p on %A %d %B')}: {message}."


@tool("List all upcoming reminders and alarms.")
def list_reminders() -> str:
    items = sorted(_load(), key=lambda r: r["when"])
    if not items:
        return "You have no reminders."
    return "; ".join(f"{datetime.fromisoformat(r['when']).strftime('%a %I:%M %p')} - {r['message']}" for r in items)


@tool("Cancel reminders. Use 'all' to cancel every reminder.", which="Part of the reminder text, or 'all'")
def cancel_reminder(which: str) -> str:
    with _lock:
        items = _load()
        keep = [] if which.lower() == "all" else [r for r in items if which.lower() not in r["message"].lower()]
        _save(keep)
    removed = len(items) - len(keep)
    return f"Cancelled {removed} reminder(s)." if removed else "I couldn't find that reminder."


def _chime():
    if platform.system() == "Windows":
        import winsound
        for f in (880, 1175, 1568):
            winsound.Beep(f, 140)


def _watcher():
    while True:
        now = datetime.now()
        with _lock:
            items = _load()
            due = [r for r in items if datetime.fromisoformat(r["when"]) <= now]
            if due:
                _save([r for r in items if r not in due])
        for r in due:
            try:
                _chime()
            except Exception:
                pass
            ctx.notify(f"Reminder: {r['message']}", "excited")
        time.sleep(10)


threading.Thread(target=_watcher, daemon=True, name="reminders").start()
