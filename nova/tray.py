"""System-tray icon so you can hide, quiet or turn off Nova anytime (needs: pip install pystray)."""
from PIL import Image, ImageDraw


def _icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 8, 60, 56), radius=18, fill=(247, 244, 246, 255))
    d.rounded_rectangle((11, 16, 53, 46), radius=11, fill=(14, 17, 29, 255))
    for x in (22, 42):
        d.ellipse((x - 5, 24, x + 5, 38), fill=(94, 240, 255, 255))
    return img


def start_tray(app):
    try:
        import pystray
    except ImportError:
        return None

    def item(text, fn, **kw):
        return pystray.MenuItem(text, lambda icon, _item: fn(), **kw)

    menu = pystray.Menu(
        item("Show / hide Nova", app.toggle_visible, default=True),
        item("Talk to Nova", app.listen_now),
        item("Cheer me up", lambda: app.body_do("perform", "cheer")),
        pystray.Menu.SEPARATOR,
        item("Quiet for 1 hour", lambda: app.set_quiet(60)),
        item("Quiet until I say", lambda: app.set_quiet(24 * 60)),
        item("Wake up (end quiet)", lambda: app.set_quiet(0)),
        pystray.Menu.SEPARATOR,
        item("Turn off Nova", app.shutdown),
    )
    icon = pystray.Icon("Nova", _icon_image(), "Nova", menu)
    icon.run_detached()
    return icon
