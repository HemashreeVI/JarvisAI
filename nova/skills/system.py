"""Time, PC status, volume/media keys, screenshots and power."""
import platform
import subprocess
from datetime import datetime
from pathlib import Path

from nova.tools import tool

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# Windows virtual-key codes for media keys
VK = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD, "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1}


@tool("Get the current date, time and day of the week.")
def get_datetime() -> str:
    return datetime.now().strftime("It's %I:%M %p on %A, %d %B %Y.")


@tool("Check the PC's battery, CPU and memory usage.")
def system_status() -> str:
    import psutil
    parts = []
    battery = psutil.sensors_battery()
    if battery:
        state = "charging" if battery.power_plugged else "on battery"
        parts.append(f"Battery {battery.percent:.0f}% ({state})")
    parts.append(f"CPU {psutil.cpu_percent(interval=0.5):.0f}%")
    mem = psutil.virtual_memory()
    parts.append(f"RAM {mem.used / 1e9:.1f} of {mem.total / 1e9:.1f} GB used")
    disk = psutil.disk_usage(str(Path.home()))
    parts.append(f"Disk {disk.free / 1e9:.0f} GB free")
    return ", ".join(parts) + "."


def _press_media_key(name: str):
    if IS_WINDOWS:
        import ctypes
        code = VK[name]
        ctypes.windll.user32.keybd_event(code, 0, 0, 0)
        ctypes.windll.user32.keybd_event(code, 0, 2, 0)  # key up
    else:
        import pyautogui
        pyautogui.press({"volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute",
                         "play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack"}[name])


@tool("Control volume or media playback.",
      action="One of: volume_up, volume_down, mute, play_pause, next, previous",
      steps="For volume: how many steps (each is ~2%). Default 5.")
def media_control(action: str, steps: int = 5) -> str:
    action = action.lower().strip().replace(" ", "_")
    if action not in VK:
        return f"Unknown action {action}."
    for _ in range(max(1, min(steps, 50)) if action.startswith("volume") else 1):
        _press_media_key(action)
    return f"Done: {action.replace('_', ' ')}."


@tool("Take a screenshot of the screen and save it.")
def take_screenshot() -> str:
    from PIL import ImageGrab
    folder = Path.home() / "Pictures" / "Nova Screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / datetime.now().strftime("screenshot_%Y-%m-%d_%H-%M-%S.png")
    ImageGrab.grab().save(path)
    return f"Screenshot saved to {path}."


@tool("Lock, sleep, shut down or restart the computer.", dangerous=True,
      action="One of: lock, sleep, shutdown, restart, cancel_shutdown")
def power(action: str) -> str:
    action = action.lower().strip()
    if IS_WINDOWS:
        commands = {
            "lock": "rundll32.exe user32.dll,LockWorkStation",
            "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
            "shutdown": "shutdown /s /t 30",
            "restart": "shutdown /r /t 30",
            "cancel_shutdown": "shutdown /a",
        }
    elif IS_MAC:
        commands = {
            "lock": "pmset displaysleepnow",
            "sleep": "pmset sleepnow",
            "shutdown": "osascript -e 'tell app \"System Events\" to shut down'",
            "restart": "osascript -e 'tell app \"System Events\" to restart'",
        }
    else:
        commands = {"lock": "loginctl lock-session", "sleep": "systemctl suspend",
                    "shutdown": "shutdown -h +1", "restart": "shutdown -r +1", "cancel_shutdown": "shutdown -c"}
    if action not in commands:
        return f"I can't do '{action}' on this computer."
    subprocess.Popen(commands[action], shell=True)
    if action in ("shutdown", "restart"):
        return f"{action.title()} in 30 seconds. Say 'cancel shutdown' to stop it."
    return f"Done: {action}."
