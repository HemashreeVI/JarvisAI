"""Nova's 3D body in a truly transparent window (Electron, in nova/shell/).

Only Nova and Pip are drawn - no background box - and clicks pass through to your
desktop everywhere except on her. Python and the window talk over stdin/stdout,
one JSON message per line, over a local socket. Same interface as the other bodies.
If the window can't start, `failed` is set and the app falls back to the basic window.
"""
import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from nova.config import DATA_DIR

SHELL = Path(__file__).resolve().parent / "shell"
LOG = DATA_DIR / "window.log"
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
    if not (SHELL / "node_modules" / "electron").exists():
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=SHELL, check=True, shell=IS_WINDOWS)
    exe = electron_exe()
    if not exe:
        # Newer npm versions skip packages' install scripts, so Electron never downloads
        # its app. Run that download step ourselves.
        node = shutil.which("node")
        if node:
            print("[nova] downloading the window app (about 100 MB)...")
            subprocess.run([node, str(SHELL / "node_modules" / "electron" / "install.js")], cwd=SHELL, check=False)
        exe = electron_exe()
    if not exe:
        raise RuntimeError("Electron didn't install correctly. Try: cd nova\\shell && npm install")
    return exe


class BodyElectron:
    def __init__(self, settings: dict):
        self.exe = ensure_electron()
        size = settings["buddy"]["size"]
        # the window stays big enough for the menu; Nova herself is scaled to `size`
        self.width, self.height = max(230, int(size * 2.4)), max(300, int(size * 2.9))
        self.scale = size / 120
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
        self.conn = None
        self.failed = False
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

    def home(self):
        """Put Nova back in her corner."""
        self._send({"cmd": "home"})

    def after(self, ms: int, fn):
        threading.Timer(ms / 1000, fn).start()

    def quit(self):
        self._send({"cmd": "quit"}, force=True)

    def _close_leftovers(self):
        """A previous run that crashed can leave a hidden window process behind, which locks its files."""
        import psutil
        target = str(self.exe).lower()
        stale = []
        for proc in psutil.process_iter(["exe"]):
            try:
                if (proc.info["exe"] or "").lower() == target:
                    stale.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        for proc in stale:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if stale:
            psutil.wait_procs(stale, timeout=5)
            print(f"[nova] closed {len(stale)} leftover window process(es) from an earlier run")

    def run(self):
        self._close_leftovers()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(0.5)
        env = {**os.environ, "NOVA_OPTS": json.dumps({"width": self.width, "height": self.height,
                                                      "corner": self.corner, "port": server.getsockname()[1]})}
        env.pop("ELECTRON_RUN_AS_NODE", None)
        log = open(LOG, "w", encoding="utf-8", errors="replace")
        extra = os.environ.get("NOVA_ELECTRON_FLAGS", "").split()  # for testing / troubleshooting
        self.proc = subprocess.Popen([str(self.exe), *extra, str(SHELL)], cwd=SHELL, env=env,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        deadline = time.time() + 40
        while self.conn is None and time.time() < deadline and self.proc.poll() is None:
            try:
                self.conn, _ = server.accept()
            except socket.timeout:
                pass
        server.close()
        if self.conn is None:
            self._give_up("the window didn't start")
            return
        for line in self.conn.makefile("r", encoding="utf-8", errors="replace"):
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                self._dispatch(msg.get("event"), msg.get("args") or [])
            except Exception as e:
                print("[body]", e)
        self.proc.wait()
        if not self._ready:
            self._give_up("the window closed before Nova appeared")

    def _give_up(self, why: str):
        self.failed = True
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        try:
            tail = [l for l in LOG.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()][-8:]
        except OSError:
            tail = []
        print(f"[nova] transparent window failed: {why}. Details in {LOG}")
        for line in tail:
            print("   ", line[:200])

    # internals
    def _dispatch(self, event: str, args: list):
        if event == "ready":
            with self._lock:
                self._ready = True
                pending, self._pending = [{"js": f"nova.setScale({self.scale})"}] + self._pending, []
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
            if not self.conn:
                return
            try:
                self.conn.sendall((json.dumps(msg) + "\n").encode("utf-8"))
            except OSError:
                pass
