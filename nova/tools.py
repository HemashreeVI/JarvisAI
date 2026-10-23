"""Tool registry. Every skill is a plain function decorated with @tool.

    from nova.tools import tool, ctx

    @tool("Tell a joke about a topic", topic="What the joke is about")
    def tell_joke(topic: str) -> str:
        ...

Any file you drop into nova/skills/ is loaded automatically.
"""
import importlib
import inspect
import pkgutil
import re
import traceback
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

# Shared context for skills: settings, memory and notify(text, emotion) to make Nova speak up.
ctx = SimpleNamespace(settings={}, memory=None, notify=lambda text, emotion="neutral": print(text))

_TYPES = {int: "integer", float: "number", bool: "boolean", str: "string"}


@dataclass
class Tool:
    name: str
    description: str
    func: Callable
    parameters: dict
    required: list
    dangerous: bool

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": self.parameters, "required": self.required},
            },
        }


REGISTRY: dict[str, Tool] = {}


def tool(description: str, dangerous: bool = False, **param_docs: str):
    """Register a function as a tool. Parameter types come from its type hints.

    dangerous=True tools (shutdown, sending messages...) refuse to run until the
    model calls them again with confirmed=true after the user said yes.
    """
    def wrap(func):
        sig = inspect.signature(func)
        params, required = {}, []
        for name, p in sig.parameters.items():
            params[name] = {"type": _TYPES.get(p.annotation, "string"), "description": param_docs.get(name, name)}
            if p.default is inspect.Parameter.empty:
                required.append(name)
        if dangerous:
            params["confirmed"] = {"type": "boolean",
                                   "description": "Set true ONLY after the user explicitly confirmed this action."}
        REGISTRY[func.__name__] = Tool(func.__name__, description, func, params, required, dangerous)
        return func
    return wrap


def _coerce(value, json_type: str):
    """Small local models sometimes send '5' for 5 or 'true' for True."""
    try:
        if json_type == "integer":
            return int(float(value))
        if json_type == "number":
            return float(value)
        if json_type == "boolean":
            return value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "1")
    except (TypeError, ValueError):
        pass
    return value


def call_tool(name: str, args: dict | None) -> str:
    t = REGISTRY.get(name)
    if t is None:
        return f"Error: there is no tool called {name}."
    args = dict(args or {})
    args = {k: _coerce(v, t.parameters[k]["type"]) for k, v in args.items() if k in t.parameters}
    if t.dangerous:
        if not args.pop("confirmed", False):
            return (f"CONFIRMATION NEEDED. Do not run yet. Ask the user to confirm: {name} with {args}. "
                    "Only call again with confirmed=true after they clearly say yes.")
    missing = [r for r in t.required if r not in args]
    if missing:
        return f"Error: missing {', '.join(missing)}. Ask the user for it."
    try:
        result = t.func(**args)
        return str(result) if result is not None else "Done."
    except Exception as e:  # a broken skill must never crash Nova
        traceback.print_exc()
        return f"Error while running {name}: {e}"


def load_skills():
    import nova.skills as pkg
    for mod in pkgutil.iter_modules(pkg.__path__):
        try:
            importlib.import_module(f"nova.skills.{mod.name}")
        except Exception as e:
            print(f"[nova] skill '{mod.name}' failed to load: {e}")
    return REGISTRY


def schemas(names=None) -> list[dict]:
    return [t.schema() for n, t in REGISTRY.items() if names is None or n in names]


# Words that suggest a message needs a tool. Sending only the tools a message might need keeps
# the prompt small, which makes replies MUCH faster on laptops without a graphics card.
ROUTES = {
    "get_datetime": ["time", "date", "day is it", "today", "clock", "what day"],
    "system_status": ["battery", "cpu", "ram", "memory", "storage", "disk", "charge", "charging"],
    "media_control": ["volume", "mute", "louder", "quieter", "pause", "resume", "next song", "previous song", "skip"],
    "take_screenshot": ["screenshot", "screen shot", "capture"],
    "power": ["shutdown", "shut down", "restart", "reboot", "lock", "sleep", "turn off the", "cancel shutdown"],
    "open_app": ["open", "launch", "start "],
    "close_app": ["close", "kill", "exit"],
    "open_website": ["website", ".com", ".in", ".org", "open", "go to"],
    "google_search": ["search", "google", "look up", "find out"],
    "play_youtube": ["play", "youtube", "song", "music", "video"],
    "get_weather": ["weather", "rain", "temperature", "hot", "cold", "forecast", "sunny", "umbrella"],
    "get_news": ["news", "headline", "happening"],
    "wikipedia": ["who is", "what is", "who was", "what are", "tell me about", "wiki", "explain"],
    "set_reminder": ["remind", "reminder", "alarm", "wake me", "timer"],
    "list_reminders": ["reminder", "alarm"],
    "cancel_reminder": ["cancel", "reminder", "alarm"],
    "remember": ["remember", "my name", "my birthday", "i like", "i love", "favourite", "favorite", "note that"],
    "forget": ["forget"],
    "add_note": ["note", "write down", "jot"],
    "read_notes": ["notes"],
    "find_file": ["file", "folder", "document", "pdf", "find my"],
    "send_whatsapp": ["whatsapp", "message", "text "],
    "check_email": ["email", "mail", "inbox", "gmail"],
    "send_email": ["email", "mail"],
    "perform": ["dance", "spin", "crawl", "walk", "jump", "wave", "hug", "cheer", "move"],
    "quiet_mode": ["quiet", "shut up", "silent", "stop talking", "disturb", "focus", "turn off"],
    "activity_today": ["what did i do", "screen time", "how long", "activity", "my day"],
}
YES = re.compile(r"^\s*(yes|yeah|yep|ok|okay|sure|do it|go ahead|confirm|please do|haan|ha)\b", re.I)


def route(text: str, previous: list | None = None) -> list[str]:
    """Names of the tools this message might need (often none)."""
    lowered = f" {text.lower()} "
    names = [name for name, words in ROUTES.items() if name in REGISTRY and any(w in lowered for w in words)]
    if previous and YES.match(text):  # "yes" after Nova asked to confirm something
        names += [n for n in previous if n not in names]
    names += [n for n in REGISTRY if n not in ROUTES and n.split("_")[0] in lowered]  # skills you add yourself
    return names
