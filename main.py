"""Start Nova.

    python main.py            floating desktop robot (default)
    python main.py --terminal chat in the terminal, no window
    python main.py --no-voice skip microphone and speakers
    python main.py --2d       use the simple 2D body
    python main.py --autostart on|off   start Nova whenever you log in (Windows)
"""
import argparse
import os
import warnings

# quiet the speech model's download warnings (harmless on Windows)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
warnings.filterwarnings("ignore", module="huggingface_hub")

import sys  # noqa: E402

if sys.version_info >= (3, 15):
    print(f"\nYou're running Python {sys.version.split()[0]}, a preview version many of Nova's packages don't support yet.")
    print("Start Nova with Python 3.14 instead:   py -3.14 main.py   (or double-click start_nova.bat)\n")

try:
    from nova.app import NovaApp  # noqa: E402
    from nova.config import load_settings  # noqa: E402
except ModuleNotFoundError as e:
    print(f"\nNova is missing a package: {e.name}")
    print(f"This Python is: {sys.executable}")
    print("Install everything Nova needs into it with:\n")
    print(f"    py -{sys.version_info.major}.{sys.version_info.minor} -m pip install -r requirements.txt\n")
    print("(Or start Nova with a Python that already has them:   py -3.14 main.py)\n")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Nova, your personal desktop robot")
    parser.add_argument("--terminal", action="store_true", help="chat in the terminal instead of the floating robot")
    parser.add_argument("--no-voice", action="store_true", help="disable microphone and speech")
    parser.add_argument("--2d", dest="flat", action="store_true", help="use the simple 2D body instead of 3D")
    parser.add_argument("--autostart", choices=["on", "off"], help="start Nova when you log in (Windows)")
    args = parser.parse_args()

    if args.autostart:
        from nova.autostart import set_autostart
        print(set_autostart(args.autostart == "on"))
        return

    settings = load_settings()
    if args.no_voice:
        settings["voice"]["enabled"] = False
    if args.flat:
        settings["buddy"]["style"] = "2d"
    print("Waking Nova up...")
    app = NovaApp(settings, gui=not args.terminal)
    app.startup_report()
    if args.terminal:
        app.run_terminal()
    else:
        app.run_gui()


if __name__ == "__main__":
    main()
