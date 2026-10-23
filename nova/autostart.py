"""Start Nova automatically when you log in to Windows."""
import os
import platform
import sys
from pathlib import Path

from nova.config import ROOT

LAUNCHER = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Nova.vbs"


def set_autostart(enabled: bool) -> str:
    if platform.system() != "Windows":
        return "Autostart is only set up for Windows right now."
    if not enabled:
        LAUNCHER.unlink(missing_ok=True)
        return "Nova won't start with Windows anymore."
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    # A .vbs launcher runs Nova without a console window popping up.
    LAUNCHER.write_text(
        'Set sh = CreateObject("WScript.Shell")\n'
        f'sh.CurrentDirectory = "{ROOT}"\n'
        f'sh.Run """{exe}"" ""{ROOT / "main.py"}""", 0, False\n',
        encoding="utf-8",
    )
    return f"Nova will now start every time you log in. (Launcher: {LAUNCHER})"
