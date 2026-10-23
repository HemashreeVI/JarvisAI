"""Nova's 3D body in a truly transparent window (Electron, in nova/shell/).

Only Nova and Pip are drawn - no background box - and clicks pass through to your
desktop everywhere except on her. Python and the window talk over stdin/stdout,
one JSON message per line. Same interface as the other bodies.
"""
import json
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path

SHELL = Path(__file__).resolve().parent / "shell"
IS_WINDOWS = platform.system() == "Windows"


def electron_exe() -> Path | None:
    path_txt = SHELL / "node_modules" / "electron" / "path.txt"
    if path_txt.exists():
        exe = SHELL / "node_modules" / "electron" / "dist" / path_txt.read_text().strip()
        if exe.exists():
            return exe
    return None


def ensure_electron() -> Path:
    """Install the window shell the first time (needs Node.js)."""
    exe = electron_exe()
    if exe:
        return exe
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("Node.js isn't installed. Get the LTS version from https://nodejs.org, "
                           "then run Nova again for a background-free window.")
    print("[nova] setting up my transparent window (one time, about a minute)...")
    subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=SHELL, check=True, shell=IS_WINDOWS)
    exe = electron_exe()
    if not exe:
        raise RuntimeError("Electron didn't install correctly. Try: cd nova\\shell && npm install")
    return exe


class BodyElectron:
    def __init__(self, settings: dict):
        self.exe = ensure_electron()
        size = settings["buddy"]["size"]
        self.width, self.height = int(size * 2.4), int(size * 2.9)
        self.corner = settings["buddy"].get("corner", "bottom-right")

        self.on_click = lambda: None
        self.on_text = lambda text: None
        self.on_pet = lambda: None
        self.on_toggle_mute = lambda: False
        self.on_next_voice = lambda: None
        self.on_quiet = lambda minutes: None
        self.on_hide = self.hide
        self.on_quit = self.quit
        self.on_ready = lambda: None

        self.proc = None
        self.visible = True
        self._ready = False
        self._pending: list[dict] = []
        self._lock = threading.Lock()

    # same public API as the other bodies
    def set_emotion(self, emotion: str, hold: float = 40.0):
        self._js(f"nova.setEmotion({json.dumps(emotion)}, {hold})")

    def set_activity(self, activity: str):
        self._js(f"nova.setActivity({json.dumps(activity)})")

    def say(self, text: str, emotion: str | None = None, seconds: float | None = None):
        self._js(f"nova.say({json.dumps(text)}, {json.dumps(emotion)}, {json.dumps(seconds)})")

    def perform(self, action: str):
        self._js(f"nova.perform({json.dumps(action)})")

    def wake(self):
        self._js("nova.wake()")

    def show(self):
        self.visible = True
        self._send({"cmd": "show"})

    def hide(self):
        self.visible = False
        self._send({"cmd": "hide"})

    def after(self, ms: int, fn):
        threading.Timer(ms / 1000, fn).start()

    def quit(self):
        self._send({"cmd": "quit"}, force=True)

    def run(self):
        env = {**os.environ, "NOVA_OPTS": json.dumps({"width": self.width, "height": self.height,
                                                      "corner": self.corner})}
        env.pop("ELECTRON_RUN_AS_NODE", None)
        self.proc = subprocess.Popen(
            [str(self.exe), str(SHELL)], cwd=SHELL, env=env, text=True, encoding="utf-8",
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1,
        )
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                self._dispatch(msg.get("event"), msg.get("args") or [])
            except Exception as e:
                print("[body]", e)
        self.proc.wait()

    # internals
    def _dispatch(self, event: str, args: list):
        if event == "ready":
            with self._lock:
                self._ready = True
                pending, self._pending = self._pending, []
            for msg in pending:
                self._send(msg)
            self.on_ready()
        elif event == "clicked":
            self.on_click()
        elif event == "text":
            self.on_text(args[0])
        elif event == "pet":
            self.on_pet()
        elif event == "toggle_mute":
            muted = self.on_toggle_mute()
            self.say("Okay, I'll stay quiet." if muted else "Yay, I can talk again!", "cute" if muted else "happy")
        elif event == "next_voice":
            self.on_next_voice()
        elif event == "quiet":
            self.on_quiet(int(args[0]))
        elif event == "hide":
            self.on_hide()
        elif event == "quit":
            self.on_quit()
        elif event == "log":
            print("[window]", *args)

    def _js(self, code: str):
        self._send({"js": code})

    def _send(self, msg: dict, force: bool = False):
        with self._lock:
            if not self._ready and not force:
                self._pending.append(msg)
                return
            if not self.proc or self.proc.poll() is not None:
                return
            try:
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass
