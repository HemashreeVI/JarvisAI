"""Start Nova.

    python main.py            floating desktop robot (default)
    python main.py --terminal chat in the terminal, no window
    python main.py --no-voice skip microphone and speakers
    python main.py --2d       use the simple 2D body
    python main.py --autostart on|off   start Nova whenever you log in (Windows)
"""
import argparse

from nova.app import NovaApp
from nova.config import load_settings


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
    app = NovaApp(settings, gui=not args.terminal)
    if args.terminal:
        app.run_terminal()
    else:
        app.run_gui()


if __name__ == "__main__":
    main()
