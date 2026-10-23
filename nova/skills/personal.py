"""Remembering things about you, notes, and finding files."""
import os
import platform
import time
from datetime import datetime
from pathlib import Path

from nova.config import DATA_DIR
from nova.tools import ctx, tool

NOTES = DATA_DIR / "notes.md"


@tool("Remember a fact about the user for the future (birthday, likes, friends' names...).",
      key="Short label, e.g. 'birthday' or 'favourite food'", value="The fact")
def remember(key: str, value: str) -> str:
    ctx.memory.remember(key, value)
    return f"I'll remember that your {key} is {value}."


@tool("Forget a remembered fact.", key="The label of the fact")
def forget(key: str) -> str:
    return f"Forgotten {key}." if ctx.memory.forget(key) else f"I had nothing saved for {key}."


@tool("Write down a note.", text="The note")
def add_note(text: str) -> str:
    with open(NOTES, "a", encoding="utf-8") as f:
        f.write(f"- [{datetime.now():%Y-%m-%d %H:%M}] {text}\n")
    return "Noted."


@tool("Read back saved notes.", count="How many recent notes, default 5")
def read_notes(count: int = 5) -> str:
    if not NOTES.exists():
        return "You have no notes yet."
    lines = NOTES.read_text(encoding="utf-8").strip().splitlines()
    return "\n".join(lines[-count:]) or "You have no notes yet."


@tool("Find files on the computer by name and optionally open the best match.",
      name="Part of the file name", open_it="true to open the first match")
def find_file(name: str, open_it: bool = False) -> str:
    home = Path.home()
    roots = [home / d for d in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "OneDrive")]
    needle, hits, deadline = name.lower(), [], time.time() + 6
    for root in filter(Path.exists, roots):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "node_modules", "__pycache__"))]
            hits += [Path(dirpath) / f for f in filenames if needle in f.lower()]
            if len(hits) >= 10 or time.time() > deadline:
                break
    if not hits:
        return f"I couldn't find any file named like {name}."
    if open_it:
        target = hits[0]
        if platform.system() == "Windows":
            os.startfile(target)
        else:
            import subprocess
            subprocess.Popen(["open" if platform.system() == "Darwin" else "xdg-open", str(target)])
        return f"Opened {target.name}."
    return "Found: " + "; ".join(str(h) for h in hits[:5])
