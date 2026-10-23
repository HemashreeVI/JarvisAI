"""Nova's 3D body: a transparent, frameless, always-on-top window rendering nova/body/ (three.js).

Same interface as the 2D Buddy, so the app doesn't care which body is in use.
Python -> JS goes through evaluate_js; JS -> Python through the small _Bridge API.
"""
import json
import platform
import threading
import time
from pathlib import Path

import webview

BODY = Path(__file__).resolve().parent / "body" / "index.html"
IS_WINDOWS = platform.system() == "Windows"


class _Bridge:
    """Methods JavaScript can call as window.pywebview.api.<name>(). Underscored attrs stay private."""

    def __init__(self, body: "Body3D"):
        self._body = body

    def ready(self):
        self._body._on_ready()

    def clicked(self):
        self._body.on_click()

    def text(self, text):
        self._body.on_text(text)

    def pet(self):
        self._body.on_pet()

    def toggle_mute(self):
        muted = self._body.on_toggle_mute()
        self._body.say("Okay, I'll stay quiet." if muted else "Yay, I can talk again!", "cute" if muted else "happy")

    def quiet(self, minutes):
        self._body.on_quiet(int(minutes))

    def quit(self):
        self._body.on_quit()

    def move_by(self, dx, dy):
        w = self._body.window
        w.move(int(w.x + dx), int(w.y + dy))

    def nudge(self, dx):
        """Walk/crawl: slide the window along the screen. Tells JS when it hits an edge."""
        b, w = self._body, self._body.window
        b._walk_x = (b._walk_x if b._walk_x is not None else float(w.x)) + float(dx)
        lo, hi = 0, b.screen_w - b.width
        hit = not lo <= b._walk_x <= hi
        b._walk_x = max(lo, min(hi, b._walk_x))
        if int(b._walk_x) != w.x:
            w.move(int(b._walk_x), w.y)
        return {"hit_edge": hit}


class Body3D:
    def __init__(self, settings: dict):
        size = settings["buddy"]["size"]
        self.width, self.height = int(size * 2.1), int(size * 2.55)
        screen = webview.screens[0] if webview.screens else None
        self.screen_w = screen.width if screen else 1920
        self.screen_h = screen.height if screen else 1080
        corner = settings["buddy"].get("corner", "bottom-right")
        x = self.screen_w - self.width - 16 if "right" in corner else 16
        y = self.screen_h - self.height - 48 if "bottom" in corner else 16

        self.on_click = lambda: None
        self.on_text = lambda text: None
        self.on_pet = lambda: None
        self.on_toggle_mute = lambda: False
        self.on_quiet = lambda minutes: None
        self.on_quit = self.quit
        self.on_ready = lambda: None

        self._ready = False
        self._pending: list[str] = []
        self._lock = threading.Lock()
        self._walk_x = None
        self.visible = True
        self.window = webview.create_window(
            "Nova", url=str(BODY), js_api=_Bridge(self), width=self.width, height=self.height, x=x, y=y,
            frameless=True, easy_drag=False, on_top=True, transparent=True, resizable=False,
            background_color="#000000", shadow=False,
        )

    # thread-safe public API (same as the 2D Buddy)
    def set_emotion(self, emotion: str, hold: float = 40.0):
        self._js(f"nova.setEmotion({json.dumps(emotion)}, {hold})")

    def set_activity(self, activity: str):
        self._js(f"nova.setActivity({json.dumps(activity)})")

    def say(self, text: str, emotion: str | None = None, seconds: float | None = None):
        self._js(f"nova.say({json.dumps(text)}, {json.dumps(emotion)}, {json.dumps(seconds)})")

    def perform(self, action: str):
        self._walk_x = None
        self._js(f"nova.perform({json.dumps(action)})")

    def wake(self):
        self._js("nova.wake()")

    def show(self):
        self.visible = True
        self.window.show()

    def hide(self):
        self.visible = False
        self.window.hide()

    def after(self, ms: int, fn):
        threading.Timer(ms / 1000, fn).start()

    def quit(self):
        self.window.destroy()

    def run(self):
        webview.start(self._started, http_server=True)

    # internals
    def _js(self, code: str):
        with self._lock:
            if not self._ready:
                self._pending.append(code)
                return
        try:
            self.window.evaluate_js(code)
        except Exception as e:
            print("[body3d]", e)

    def _on_ready(self):
        with self._lock:
            self._ready = True
            pending, self._pending = self._pending, []
        for code in pending:
            self._js(code)
        self.on_ready()

    def _started(self):
        if IS_WINDOWS:
            threading.Thread(target=self._follow_cursor, daemon=True, name="cursor").start()

    def _follow_cursor(self):
        """Send the mouse position so Nova's eyes follow it anywhere on screen."""
        import ctypes
        from ctypes import wintypes

        pt = wintypes.POINT()
        last = None
        while True:
            time.sleep(0.1)
            if not self._ready or not self.visible:
                continue
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            cx = self.window.x + self.width * 0.6
            cy = self.window.y + self.height * 0.45
            nx, ny = round((pt.x - cx) / 500, 2), round((pt.y - cy) / 400, 2)
            if (nx, ny) != last:
                last = (nx, ny)
                self._js(f"nova.pointer({nx}, {ny})")
