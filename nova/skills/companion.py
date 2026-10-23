"""Nova's body moves, quiet mode, and what you did today."""
from nova.tools import ctx, tool

MOVES = ["spin", "dance", "jump", "wave", "hug", "walk", "crawl", "cheer", "party"]


@tool("Move your 3D body. 'cheer' is a whole routine (wave, spin, dance, crawl, hug) to cheer the user up.",
      action="One of: " + ", ".join(MOVES))
def perform(action: str) -> str:
    action = action.lower().strip()
    if action not in MOVES:
        return f"I can't do '{action}'. I can: {', '.join(MOVES)}."
    ctx.app.body_do("perform", action)
    return f"(You are now doing: {action}.)"


@tool("Go quiet: no check-ins, no wake word, for a while. Use when the user wants focus or says to stop/turn off.",
      minutes="How long, e.g. 60. 0 turns quiet mode off.")
def quiet_mode(minutes: int = 60) -> str:
    ctx.app.set_quiet(minutes)
    return "Quiet mode off." if minutes <= 0 else f"Quiet for {minutes} minutes. (Click me or say my name when needed.)"


@tool("Summarize what the user did on the computer today (or another day): time per activity and top apps.",
      day="YYYY-MM-DD, empty for today")
def activity_today(day: str = "") -> str:
    if not ctx.app.companion:
        return "Activity watching is turned off."
    return ctx.app.companion.summary(day or None)
