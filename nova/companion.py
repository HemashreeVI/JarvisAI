"""Nova as a companion: welcomes you when you open the laptop, says goodbye when you close it,
notices how you're working, checks in on you, and reminds you to take care of yourself.

Everything it sees stays on your PC (data/activity.json). Turn any part off in settings.toml.
"""
import json
import random
import threading
import time
from datetime import date, datetime, timedelta

from nova.config import DATA_DIR
from nova.presence import SystemEvents, categorize, foreground_window, friendly_app, idle_seconds

ACTIVITY_FILE = DATA_DIR / "activity.json"
SAMPLE_EVERY = 20  # seconds
IDLE_AFTER = 180   # no input for this long = away from keyboard

WELCOME = {
    "morning": ["Good morning {name}! You're here! Pip and I missed you!",
                "Morning {name}! Yay, a brand new day together!",
                "Rise and shine {name}! I've been waiting for you!"],
    "afternoon": ["Hiii {name}! Welcome back! I missed you!",
                  "{name}! You're back! Pip, wake up, {name} is here!",
                  "Yay, {name}'s here! What are we doing this afternoon?"],
    "evening": ["Good evening {name}! I'm so happy you're back!",
                "Welcome back {name}! How was your day?",
                "{name}! There you are! Tell me everything about your day!"],
    "night": ["Oh hi {name}! Up late, huh? I'm here with you.",
              "Welcome back {name}! It's late, so let's not stay up too long, okay?"],
}
GOODBYE = {
    "lid_close": ["Bye {name}! I'll miss you!", "See you soon {name}! Pip will guard the desktop.",
                  "Aww, going already? Bye bye {name}!", "Take care {name}! Come back soon!"],
    "lock": ["See you in a bit {name}!", "I'll wait right here!", "Okay, I'll keep your seat warm!"],
    "suspend": ["Nap time! Bye {name}!", "Going to sleep... see you soon {name}!"],
    "shutdown": ["Bye {name}! Thank you for today!", "Good night {name}. You did great today."],
    "quit": ["Bye {name}! Call me whenever you need me.", "Okay, turning off. I'll miss you {name}!"],
}
NIGHT_GOODBYE = ["Good night {name}! Sleep well, okay?", "Night night {name}! Sweet dreams!"]
BREAK_LINES = ["You've been working for {mins} minutes straight! Stretch with me? Maybe drink some water?",
               "{mins} minutes of hard work! Your eyes need a little break. Look far away for 20 seconds?",
               "Hey {name}, {mins} minutes already! Quick break? I'll keep watch."]
FUN_NUDGE = ["Hehe, still on {app}? Want me to set a 10-minute timer?",
             "{mins} minutes of {app}! Fun, but should we get back to work soon?"]
CHECKIN_FALLBACK = ["How's it going, {name}? Feeling okay?", "How are you feeling right now, {name}?",
                    "Need any help with what you're doing?", "Are you taking care of yourself today?"]


def _part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 23:
        return "evening"
    return "night"


def _away_text(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes"
    if seconds < 86400:
        return f"{seconds / 3600:.0f} hours"
    return f"{seconds / 86400:.0f} days"


class Companion:
    def __init__(self, app, settings: dict):
        self.app = app
        self.cfg = {"welcome": True, "goodbye": True, "watch_activity": True, "break_after_minutes": 50,
                    "checkin_every_minutes": 90, "fun_nudge_minutes": 40, "late_night_hour": 23,
                    **settings.get("companion", {})}
        self.name = settings["user"]["name"]
        self._last_welcome = 0.0
        self._last_goodbye = 0.0
        # live activity state
        self.current = None           # (category, app, title)
        self.active_streak = 0.0      # seconds of activity without a real break
        self.since_checkin = 0.0
        self.category_streak = (None, 0.0)
        self.was_idle_for = 0.0
        self.late_night_warned: date | None = None
        self.activity = self._load_activity()

    # lifecycle
    def start(self):
        SystemEvents(self.on_system_event).start()
        if self.cfg["watch_activity"]:
            threading.Thread(target=self._watch, daemon=True, name="activity").start()

    def on_system_event(self, event: str):
        print(f"[nova] system: {event}")
        if event in ("lid_open", "unlock", "resume"):
            self.welcome(event)
        elif event in ("lid_close", "lock", "suspend", "shutdown"):
            self.goodbye(event)

    # welcome / goodbye
    def welcome(self, reason: str = "start"):
        if not self.cfg["welcome"] or time.time() - self._last_welcome < 90:
            return  # lid open + resume + unlock all fire together: greet once
        self._last_welcome = time.time()
        self.active_streak = 0.0
        last_bye = self.app.memory.data.get("last_goodbye")
        away = time.time() - last_bye if last_bye else None
        self.app.body_do("perform", random.choice(["spin", "jump", "wave"]))
        self.app.show_emotion("excited")
        threading.Thread(target=self._welcome_message, args=(away,), daemon=True).start()

    def _welcome_message(self, away):
        hour = datetime.now().hour
        part = _part_of_day(hour)
        text = None
        if self.app.brain_ready():
            facts = self.app.memory.facts
            last = self.app.memory.last_mood()
            context = [f"It's {part}, {datetime.now():%A %I:%M %p}."]
            if away:
                context.append(f"They were away for {_away_text(away)}.")
            if last:
                context.append(f"Last time they seemed {last['emotion']}.")
            if facts:
                context.append("Things you know: " + "; ".join(f"{k}: {v}" for k, v in list(facts.items())[:8]))
            mind = getattr(self.app.memory, "mind", None)
            if mind is not None:
                soon = mind.upcoming(3)
                if soon:
                    context.append("Coming up: " + "; ".join(m.describe() for m in soon[:3]) + ".")
            text = self.app.brain.generate(
                f"{self.name} just opened the laptop. " + " ".join(context) +
                " Welcome them back like an excited little robot friend who missed them: 1-2 short sentences. "
                "If something you know is relevant today (an exam, a plan), mention it warmly. If they were sad "
                "last time, gently ask if they feel better.", timeout=8)
        if not text:
            text = random.choice(WELCOME[part]).format(name=self.name)
        self.app.express(text, "excited")

    def goodbye(self, reason: str = "quit"):
        if not self.cfg["goodbye"] or time.time() - self._last_goodbye < 20:
            return
        self._last_goodbye = time.time()
        self.app.memory.data["last_goodbye"] = time.time()
        self.app.memory.save()
        hour = datetime.now().hour
        lines = NIGHT_GOODBYE if (hour >= 22 or hour < 4) and reason != "lock" else GOODBYE.get(reason, GOODBYE["quit"])
        # Instant template, no thinking: the laptop may be asleep in two seconds.
        self.app.express(random.choice(lines).format(name=self.name), "love" if reason != "lock" else "cute")
        self.app.body_do("perform", "wave")

    # watching how you work
    def _watch(self):
        while True:
            time.sleep(SAMPLE_EVERY)
            try:
                self._sample()
            except Exception as e:
                print("[companion]", e)

    def _sample(self):
        idle = idle_seconds()
        fg = foreground_window()
        if fg is None:
            return
        if idle > IDLE_AFTER:
            self.was_idle_for += SAMPLE_EVERY
            if self.was_idle_for > 300:
                self.active_streak = 0.0  # a real break
            return
        if self.was_idle_for > 15 * 60 and not self.app.quiet():
            self.app.express(random.choice([f"Welcome back {self.name}!", "Oh, you're back! Hi!",
                                            "Yay, you're back!"]), "happy")
        self.was_idle_for = 0.0

        app_name, title = fg
        category = categorize(app_name, title)
        self.current = (category, friendly_app(app_name, title), title[:120])
        self.active_streak += SAMPLE_EVERY
        self.since_checkin += SAMPLE_EVERY
        cat, secs = self.category_streak
        self.category_streak = (category, secs + SAMPLE_EVERY if cat == category else SAMPLE_EVERY)
        self._record(category, self.current[1])

        if self.app.quiet() or self.app.busy():
            return
        mins = int(self.active_streak // 60)
        now = datetime.now()
        if now.hour >= self.cfg["late_night_hour"] or now.hour < 4:
            if self.late_night_warned != now.date() and mins >= 10:
                self.late_night_warned = now.date()
                self.app.express(f"It's {now:%I:%M} already, {self.name}. Don't stay up too late, okay? "
                                 "Your brain needs sleep to remember all this.", "caring")
                return
        if mins >= self.cfg["break_after_minutes"]:
            self.active_streak = 0.0
            self.app.express(random.choice(BREAK_LINES).format(mins=mins, name=self.name), "caring")
            return
        cat, secs = self.category_streak
        if cat == "fun" and secs >= self.cfg["fun_nudge_minutes"] * 60 and 9 <= now.hour < 22:
            self.category_streak = (cat, 0.0)
            self.app.express(random.choice(FUN_NUDGE).format(app=self.current[1], mins=int(secs // 60)), "cute")
            return
        if self.since_checkin >= self.cfg["checkin_every_minutes"] * 60 and category != "meeting":
            self.since_checkin = 0.0
            self.check_in()

    def check_in(self):
        """Ask a short caring question about what you're doing, then listen for the answer."""
        text = None
        if self.current and self.app.brain_ready():
            cat, app_name, title = self.current
            text = self.app.brain.generate(
                f"{self.name} has been {cat} in {app_name} (window: '{title}') for about "
                f"{int(self.category_streak[1] // 60)} minutes. Ask ONE short, warm check-in question about how "
                "it's going or how they feel, like a caring friend. Don't lecture.", timeout=10)
        text = text or random.choice(CHECKIN_FALLBACK).format(name=self.name)
        self.app.express(text, "caring")
        self.app.listen_after_speaking()

    def describe_now(self) -> str:
        """One line for the brain's prompt, so Nova knows what you're up to."""
        if not self.current:
            return ""
        cat, app_name, title = self.current
        mins = int(self.category_streak[1] // 60)
        return f"Right now they're {cat} in {app_name} ('{title[:60]}') for {mins} min; active {int(self.active_streak // 60)} min without a break."

    # daily activity log
    def _load_activity(self) -> dict:
        try:
            return json.loads(ACTIVITY_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _record(self, category: str, app_name: str):
        day = self.activity.setdefault(date.today().isoformat(), {"categories": {}, "apps": {}})
        day["categories"][category] = day["categories"].get(category, 0) + SAMPLE_EVERY
        day["apps"][app_name] = day["apps"].get(app_name, 0) + SAMPLE_EVERY
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        self.activity = {d: v for d, v in self.activity.items() if d >= cutoff}
        try:
            ACTIVITY_FILE.write_text(json.dumps(self.activity, indent=1), encoding="utf-8")
        except OSError:
            pass

    def summary(self, day: str | None = None) -> str:
        day = day or date.today().isoformat()
        data = self.activity.get(day)
        if not data:
            return "I haven't seen any activity for that day."
        cats = sorted(data["categories"].items(), key=lambda kv: -kv[1])
        apps = sorted(data["apps"].items(), key=lambda kv: -kv[1])[:4]
        fmt = lambda s: f"{s // 3600}h {s % 3600 // 60}m" if s >= 3600 else f"{s // 60}m"
        return ("Time spent: " + ", ".join(f"{c} {fmt(s)}" for c, s in cats) +
                ". Top apps: " + ", ".join(f"{a} {fmt(s)}" for a, s in apps) + ".")
