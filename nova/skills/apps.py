"""Open and close apps. On Windows Nova can find ANY app in your Start menu."""
import difflib
import os
import platform
import subprocess
import webbrowser
from pathlib import Path

from nova.tools import tool

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# Common names -> Windows launch command and process name.
KNOWN_APPS = {
    "chrome": ("chrome", "chrome.exe"),
    "edge": ("msedge", "msedge.exe"),
    "firefox": ("firefox", "firefox.exe"),
    "notepad": ("notepad", "notepad.exe"),
    "calculator": ("calc", "CalculatorApp.exe"),
    "paint": ("mspaint", "mspaint.exe"),
    "file explorer": ("explorer", "explorer.exe"),
    "settings": ("ms-settings:", "SystemSettings.exe"),
    "task manager": ("taskmgr", "Taskmgr.exe"),
    "command prompt": ("cmd", "cmd.exe"),
    "word": ("winword", "WINWORD.EXE"),
    "excel": ("excel", "EXCEL.EXE"),
    "powerpoint": ("powerpnt", "POWERPNT.EXE"),
    "outlook": ("outlook", "OUTLOOK.EXE"),
    "vs code": ("code", "Code.exe"),
    "spotify": ("spotify:", "Spotify.exe"),
    "whatsapp": ("whatsapp:", "WhatsApp.exe"),
    "telegram": ("tg:", "Telegram.exe"),
    "discord": ("discord:", "Discord.exe"),
    "zoom": ("zoommtg:", "Zoom.exe"),
    "vlc": ("vlc", "vlc.exe"),
    "camera": ("microsoft.windows.camera:", "WindowsCamera.exe"),
}
ALIASES = {"google chrome": "chrome", "microsoft edge": "edge", "explorer": "file explorer", "files": "file explorer",
           "vscode": "vs code", "visual studio code": "vs code", "code": "vs code", "calc": "calculator",
           "cmd": "command prompt", "terminal": "command prompt", "ms word": "word", "microsoft word": "word"}
WEB_APPS = {"youtube": "https://www.youtube.com", "gmail": "https://mail.google.com",
            "instagram": "https://www.instagram.com", "chatgpt": "https://chat.openai.com",
            "github": "https://github.com", "netflix": "https://www.netflix.com",
            "linkedin": "https://www.linkedin.com", "google": "https://www.google.com"}


def _normalize(name: str) -> str:
    name = name.lower().strip().removeprefix("the ").removesuffix(" app").strip()
    return ALIASES.get(name, name)


def _start_menu_shortcuts() -> dict[str, Path]:
    roots = [Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
             Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs"]
    found = {}
    for root in roots:
        if root.exists():
            for lnk in root.rglob("*.lnk"):
                found.setdefault(lnk.stem.lower(), lnk)
    return found


@tool("Open an app or program on the computer (e.g. chrome, spotify, vs code, notepad, whatsapp).",
      name="The app's name")
def open_app(name: str) -> str:
    key = _normalize(name)
    if key in WEB_APPS:
        webbrowser.open(WEB_APPS[key])
        return f"Opened {key}."
    if IS_WINDOWS:
        if key in KNOWN_APPS:
            try:
                os.startfile(KNOWN_APPS[key][0])
                return f"Opened {key}."
            except OSError:
                pass  # not installed under that command; try the Start menu
        shortcuts = _start_menu_shortcuts()
        match = difflib.get_close_matches(key, shortcuts.keys(), n=1, cutoff=0.6) or \
            [s for s in shortcuts if key in s][:1]
        if match:
            os.startfile(shortcuts[match[0]])
            return f"Opened {match[0]}."
        return f"I couldn't find an app called {name} on this PC."
    if IS_MAC:
        r = subprocess.run(["open", "-a", name], capture_output=True)
        return f"Opened {name}." if r.returncode == 0 else f"I couldn't find {name}."
    try:
        subprocess.Popen([key.replace(" ", "-")], start_new_session=True)
        return f"Opened {name}."
    except FileNotFoundError:
        return f"I couldn't find {name}."


@tool("Close a running app.", name="The app's name")
def close_app(name: str) -> str:
    import psutil
    key = _normalize(name)
    targets = {KNOWN_APPS[key][1].lower()} if key in KNOWN_APPS else set()
    closed = 0
    for proc in psutil.process_iter(["name"]):
        pname = (proc.info["name"] or "").lower()
        if pname in targets or key.replace(" ", "") in pname.replace(" ", ""):
            try:
                proc.terminate()
                closed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    return f"Closed {name}." if closed else f"{name} doesn't seem to be running."
