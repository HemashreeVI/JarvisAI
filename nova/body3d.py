"""Nova's 3D body: a frameless, always-on-top window rendering nova/body/ (three.js).

On Windows the window is *shaped*: the page sends Nova's silhouette (a list of rectangles)
several times a second and we clip the window to it with SetWindowRgn. Everything outside
her outline is truly gone - no background, and clicks go straight through to your apps.

Same interface as the 2D Buddy, so the app doesn't care which body is in use.
Python -> JS goes through evaluate_js; JS -> Python through the small _Bridge API.
"""
import ctypes
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

    def next_voice(self):
        self._body.on_next_voice()

    def quiet(self, minutes):
        self._body.on_quiet(int(minutes))

    def hide(self):
        self._body.on_hide()

    def quit(self):
        self._body.on_quit()

    def set_shape(self, rects, dpr):
        self._body._set_shape(rects, float(dpr))

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
        # the window stays big enough for the menu; Nova herself is scaled to `size`
        self.width, self.height = max(230, int(size * 2.4)), max(300, int(size * 2.9))
        self.scale = size / 120
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
        self.on_next_voice = lambda: None
        self.on_quiet = lambda minutes: None
        self.on_hide = self.hide
        self.on_quit = self.quit
        self.on_ready = lambda: None

        self._ready = False
        self._pending: list[str] = []
        self._lock = threading.Lock()
        self._walk_x = None
        self._hwnd = None
        self._shapes = 0
        self._shape_warned = False
        self._keyed = False
        self.transparency = settings["buddy"].get("transparency", "shape")
        self.visible = True
        self.window = webview.create_window(
            "Nova", url=str(BODY), js_api=_Bridge(self), width=self.width, height=self.height, x=x, y=y,
            frameless=True, easy_drag=False, on_top=True, transparent=True, resizable=False,
            background_color="#F4F1F3", shadow=False,
        )
        if IS_WINDOWS:
            self.window.events.shown += lambda: threading.Thread(target=self._prepare_native_window, daemon=True).start()

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
        self._js(f"nova.setScale({self.scale})")
        if IS_WINDOWS:
            self._js("nova.enableShaped()")
            if self.transparency == "colorkey":
                self._use_color_key()
        for code in pending:
            self._js(code)
        self.on_ready()

    def _started(self):
        if IS_WINDOWS:
            threading.Thread(target=self._follow_cursor, daemon=True, name="cursor").start()

    # Windows: shaped window
    def _find_hwnd(self):
        """Nova's own window: visible, titled "Nova", owned by this process, created by WinForms."""
        if self._hwnd:
            return self._hwnd
        import os
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def check(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != os.getpid() or not user32.IsWindowVisible(hwnd):
                return True
            title, cls = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(128)
            user32.GetWindowTextW(hwnd, title, 64)
            user32.GetClassNameW(hwnd, cls, 128)
            if title.value == "Nova" and cls.value.startswith("WindowsForms"):
                found.append(hwnd)
            return True

        user32.EnumWindows(check, 0)
        if found:
            self._hwnd = found[0]
            print(f"[nova] window found (hwnd {self._hwnd})")
        return self._hwnd

    def _prepare_native_window(self):
        """Hide everything until the first silhouette arrives, and keep Nova off the taskbar."""
        hwnd = None
        for _ in range(50):  # pywebview shows a transparent window only once the page starts loading
            hwnd = self._find_hwnd()
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd:
            return
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        if self.transparency == "shape" and self._shapes == 0:
            user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(gdi32.CreateRectRgn(0, 0, 0, 0)), True)
        GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x00000080, 0x00040000
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_style.restype = set_style.restype = ctypes.c_ssize_t
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        style = get_style(hwnd, GWL_EXSTYLE)
        set_style(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)

    def _set_shape(self, rects, dpr: float):
        hwnd = self._find_hwnd()
        if not hwnd:
            if not self._shape_warned:
                self._shape_warned = True
                print("[nova] couldn't find my window to make the background transparent")
            return
        if self.transparency == "colorkey":
            return self._use_color_key()
        from ctypes import wintypes

        class RGNDATAHEADER(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("iType", wintypes.DWORD), ("nCount", wintypes.DWORD),
                        ("nRgnSize", wintypes.DWORD), ("rcBound", wintypes.RECT)]

        boxes = []
        for x, y, w, h in rects:
            l, t = int(x * dpr), int(y * dpr)
            boxes.append(wintypes.RECT(l, t, max(l + 1, int((x + w) * dpr + 0.5)), max(t + 1, int((y + h) * dpr + 0.5))))
        gdi32, user32 = ctypes.windll.gdi32, ctypes.windll.user32
        if not boxes:
            rgn = gdi32.CreateRectRgn(0, 0, 0, 0)
        else:
            class RGNDATA(ctypes.Structure):
                _fields_ = [("rdh", RGNDATAHEADER), ("Buffer", wintypes.RECT * len(boxes))]

            data = RGNDATA()
            bound = wintypes.RECT(min(b.left for b in boxes), min(b.top for b in boxes),
                                  max(b.right for b in boxes), max(b.bottom for b in boxes))
            data.rdh = RGNDATAHEADER(ctypes.sizeof(RGNDATAHEADER), 1, len(boxes), 16 * len(boxes), bound)
            data.Buffer = (wintypes.RECT * len(boxes))(*boxes)
            gdi32.ExtCreateRegion.restype = ctypes.c_void_p
            gdi32.ExtCreateRegion.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
            rgn = gdi32.ExtCreateRegion(None, ctypes.sizeof(data), ctypes.byref(data))
        user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL]
        ok = user32.SetWindowRgn(hwnd, rgn, True)  # Windows owns the region now
        self._shapes += 1
        if self._shapes == 1 or not ok:
            area = sum(w * h for _, _, w, h in rects) / max(1, self.width * self.height)
            print(f"[nova] shaped window: {len(rects)} pieces, {area:.0%} of the box visible, ok={bool(ok)}")

    def _use_color_key(self):
        """Backup method: make the window's background colour see-through (settings: transparency = "colorkey")."""
        if self._keyed:
            return
        self._keyed = True
        try:
            from System import Action
            from System.Drawing import Color

            form = self.window.native
            key = Color.FromArgb(255, 1, 254)

            def apply():
                form.BackColor = key
                form.TransparencyKey = key

            form.Invoke(Action(apply))
            print("[nova] colour-key transparency on")
        except Exception as e:
            print(f"[nova] colour-key transparency failed: {e}")

    def _follow_cursor(self):
        """Send the mouse position so Nova's eyes follow it anywhere on screen."""
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
