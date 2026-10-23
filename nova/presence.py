"""Knowing what's happening on the PC: lid open/close, lock/unlock, sleep/wake,
which app you're using and whether you're at the keyboard.

Windows gets the full set via the Win32 API (no extra installs). Other systems get
sleep/wake detection only.
"""
import ctypes
import platform
import threading
import time

IS_WINDOWS = platform.system() == "Windows"


class SystemEvents:
    """Calls on_event(name) with: lid_open, lid_close, lock, unlock, suspend, resume, shutdown."""

    def __init__(self, on_event):
        self.on_event = on_event

    def start(self):
        target = self._windows_loop if IS_WINDOWS else self._sleep_gap_loop
        threading.Thread(target=target, daemon=True, name="system-events").start()

    def _emit(self, name):
        try:
            self.on_event(name)
        except Exception as e:
            print("[presence]", e)

    def _sleep_gap_loop(self):
        """Without OS hooks: if the clock jumped, the computer was asleep."""
        last = time.time()
        while True:
            time.sleep(5)
            now = time.time()
            if now - last > 40:
                self._emit("resume")
            last = now

    def _windows_loop(self):
        from ctypes import wintypes

        user32, kernel32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
        wtsapi32 = ctypes.WinDLL("wtsapi32")
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                        ("Data4", ctypes.c_ubyte * 8)]

        class POWERBROADCAST_SETTING(ctypes.Structure):
            _fields_ = [("PowerSetting", GUID), ("DataLength", wintypes.DWORD), ("Data", ctypes.c_ubyte * 4)]

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        # {BA3E0F4D-B817-4094-A2D1-D56379E6A0F3}: the laptop lid switch
        LID = GUID(0xBA3E0F4D, 0xB817, 0x4094, (ctypes.c_ubyte * 8)(0xA2, 0xD1, 0xD5, 0x63, 0x79, 0xE6, 0xA0, 0xF3))
        WM_POWERBROADCAST, WM_WTSSESSION_CHANGE, WM_QUERYENDSESSION = 0x0218, 0x02B1, 0x0011
        PBT_APMSUSPEND, PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC, PBT_POWERSETTINGCHANGE = 0x4, 0x7, 0x12, 0x8013
        WTS_SESSION_LOCK, WTS_SESSION_UNLOCK = 0x7, 0x8

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), wintypes.DWORD]
        user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
        first_lid = [True]  # Windows reports the current lid state right after registering; ignore that one

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_POWERBROADCAST:
                if wparam == PBT_POWERSETTINGCHANGE and lparam:
                    setting = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
                    if bytes(setting.PowerSetting)[:16] == bytes(LID)[:16]:
                        if first_lid[0]:
                            first_lid[0] = False
                        else:
                            self._emit("lid_open" if setting.Data[0] else "lid_close")
                elif wparam == PBT_APMSUSPEND:
                    self._emit("suspend")
                elif wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
                    self._emit("resume")
                return 1
            if msg == WM_WTSSESSION_CHANGE:
                if wparam == WTS_SESSION_LOCK:
                    self._emit("lock")
                elif wparam == WTS_SESSION_UNLOCK:
                    self._emit("unlock")
                return 0
            if msg == WM_QUERYENDSESSION:
                self._emit("shutdown")
                return 1
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)  # keep a reference or it gets garbage collected
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=hinst, lpszClassName="NovaPresence")
        user32.RegisterClassW(ctypes.byref(wc))
        # A hidden top-level window (message-only windows don't receive power broadcasts)
        hwnd = user32.CreateWindowExW(0, "NovaPresence", "Nova", 0, 0, 0, 0, 0, None, None, hinst, None)
        user32.RegisterPowerSettingNotification(hwnd, ctypes.byref(LID), 0)
        wtsapi32.WTSRegisterSessionNotification(hwnd, 0)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# what you're doing
CATEGORIES = [
    ("coding", ["code.exe", "pycharm", "idea64", "devenv", "sublime", "cursor", "android studio", "jupyter",
                "windowsterminal", "powershell", "cmd.exe", "github", "stack overflow", "leetcode"]),
    ("meeting", ["zoom", "teams", "meet.google", "webex"]),
    ("studying", ["acrobat", ".pdf", "winword", "onenote", "notion", "obsidian", "coursera", "udemy", "nptel",
                  "geeksforgeeks", "docs.google", "w3schools", "khan academy", "excel", "powerpnt"]),
    ("fun", ["youtube", "instagram", "netflix", "prime video", "hotstar", "reddit", "twitter", " x ", "facebook",
             "steam", "epic games", "valorant", "minecraft", "discord"]),
    ("chatting", ["whatsapp", "telegram", "messenger"]),
]


def categorize(app: str, title: str) -> str:
    text = f" {app} {title} ".lower()
    for name, keys in CATEGORIES:
        if any(k in text for k in keys):
            return name
    if app.lower() in BROWSERS:
        return "browsing"
    return "other"


BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe")
FRIENDLY = {"code": "VS Code", "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "acrord32": "Acrobat",
            "windowsterminal": "Terminal", "pycharm64": "PyCharm", "idea64": "IntelliJ", "msedge": "Edge"}


def friendly_app(app: str, title: str) -> str:
    """'chrome.exe' + 'lofi - YouTube - Google Chrome' -> 'YouTube'; 'WINWORD.EXE' -> 'Word'."""
    if app.lower() in BROWSERS and " - " in title:
        parts = [p.strip() for p in title.split(" - ")]
        return parts[-2] if len(parts) >= 2 else parts[0]
    base = app.lower().removesuffix(".exe")
    return FRIENDLY.get(base, base.capitalize())


def foreground_window() -> tuple[str, str] | None:
    """(process name, window title) of the app you're looking at."""
    if not IS_WINDOWS:
        return None
    import psutil
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        app = psutil.Process(pid.value).name()
    except Exception:
        app = "unknown"
    return app, buf.value


def idle_seconds() -> float:
    """Seconds since the last key press or mouse move."""
    if not IS_WINDOWS:
        return 0.0
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0
