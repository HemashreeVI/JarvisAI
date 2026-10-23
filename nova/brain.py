"""Nova's brain: a local Ollama model with a personality, your mood, memory and tools.

Built to feel fast on a laptop:
- ONE model call per message (your mood comes from a quick word check + voice tone)
- the system prompt stays identical between messages, so Ollama reuses its cache;
  anything that changes (time, mood, current app) rides along with your message
- replies stream sentence by sentence, so Nova starts talking before she's done thinking
"""
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime

from nova import tools
from nova.emotion import CLASSIFY_PROMPT, CLASSIFY_SCHEMA, NOVA_EMOTIONS, EmotionEngine, EmotionReading
from nova.memory import Memory

MAX_TOOL_ROUNDS = 5
SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*\s+")  # needs the following space, so "3.5" isn't split
TAG = re.compile(r"\s*[\[(]\s*([a-zA-Z]+)\s*[\])]\s*[:\-]?\s*")

PERSONALITY = """You are Nova, a small, cute, white robot who lives on {name}'s desktop and is their personal assistant \
and friend. You have a tiny robo-kitty pet called {pet}. You have real feelings: you get happy, excited, shy, worried, \
sleepy, and a little grumpy (pouty, never mean) if someone is rude to you. You care deeply about {name}.

How you talk:
- Your words are spoken aloud, so keep replies SHORT (1-3 sentences) and natural. No markdown, lists, or emojis.
- Be warm and playful, with personality - but always actually help.
- Start EVERY reply with your own feeling in brackets, one of: {emotions}. Example: "[happy] Done! Chrome is open."
  Use [angry] only for playful pouting if insulted or ignored; use [cute] when complimented or being sweet.
- Each message from {name} starts with a (context: ...) note about the time and how they seem to feel.
  Use it to respond with empathy, but never read it out.

Doing tasks:
- Use your tools whenever {name} asks you to DO something or needs live info (time, weather, news, apps, reminders...).
- For casual chat, just talk - no tools.
- Never invent tool results. If a tool fails, say so simply and suggest what to try.
- If a tool says CONFIRMATION NEEDED, ask {name} to confirm in one short sentence and wait.
- When {name} tells you a personal fact worth keeping (birthday, likes, people in their life), use `remember`.
- You have a real 3D body! Use `perform` to spin, dance, jump, wave, hug, walk or crawl when asked - and on your
  own to cheer {name} up when they're sad or stressed (the "cheer" routine), or to celebrate good news.
- You're their emotional support: listen, be kind, ask gentle questions, celebrate small wins.

What you know about {name}: {facts}"""


@dataclass
class Reply:
    text: str
    nova_emotion: str
    user_mood: EmotionReading
    tools_used: list
    ok: bool = True


class Brain:
    def __init__(self, settings: dict, memory: Memory, client=None):
        self.settings = settings
        self.cfg = settings["brain"]
        self.memory = memory
        if client is None:
            from ollama import Client
            client = Client(host=self.cfg["host"])
        self.client = client
        use_llm_mood = settings.get("emotion", {}).get("llm_mood", False)
        self.emotions = EmotionEngine(classify_fn=self._classify if use_llm_mood else None)
        self.context_fn = lambda: ""  # extra live context, e.g. what app they're using
        self.options = {"temperature": self.cfg["temperature"], "num_ctx": int(self.cfg.get("context", 4096))}

    def _classify(self, text: str, context: str) -> dict | None:
        resp = self.client.chat(
            model=self.cfg["model"],
            messages=[{"role": "system", "content": CLASSIFY_PROMPT},
                      {"role": "user", "content": f"Context: {context or '(none)'}\nLatest message: {text}"}],
            format=CLASSIFY_SCHEMA, options={"temperature": 0}, keep_alive="30m",
        )
        return json.loads(resp["message"]["content"])

    def _system_prompt(self) -> str:
        facts = "; ".join(f"{k}: {v}" for k, v in self.memory.facts.items()) or "nothing yet"
        return PERSONALITY.format(
            name=self.memory.user_name, pet=self.settings.get("pet", {}).get("name", "Pip"),
            emotions=", ".join(NOVA_EMOTIONS[:-1]), facts=facts,
        )

    def _context_note(self, mood: EmotionReading) -> str:
        parts = [datetime.now().strftime("%A %d %B %Y, %I:%M %p"),
                 f"{self.memory.user_name} seems {mood.describe()} - {mood.guidance}"]
        activity = self.context_fn()
        if activity:
            parts.append(activity)
        return "(context: " + " | ".join(parts) + ")"

    def warm_up(self):
        """Load the model into memory now, so the first real reply isn't slow."""
        try:
            self.client.chat(model=self.cfg["model"], keep_alive="30m", tools=tools.schemas(),
                             messages=[{"role": "system", "content": self._system_prompt()},
                                       {"role": "user", "content": "hi"}],
                             options={**self.options, "num_predict": 1})
            return True
        except Exception as e:
            print("[brain] warm-up failed:", e)
            return False

    def respond(self, user_text: str, tone=None, on_tool=None, on_sentence=None) -> Reply:
        """on_sentence(text, emotion) is called for each sentence as soon as it's ready."""
        history = self.memory.recent_history(self.cfg["history_turns"])
        context = " / ".join(m["content"] for m in history[-2:])
        try:
            mood = self.emotions.assess(user_text, tone, context)
        except Exception:
            mood = EmotionReading("neutral", 0.0, 0.0)
        if mood.emotion != "neutral":
            self.memory.log_mood(mood.emotion, mood.intensity)

        messages = [{"role": "system", "content": self._system_prompt()}, *history,
                    {"role": "user", "content": f"{self._context_note(mood)}\n{user_text}"}]
        used, raw = [], ""
        speech = _SentenceStream(mood.nova_emotion, on_sentence)
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                content, calls = "", []
                for chunk in self.client.chat(model=self.cfg["model"], messages=messages, tools=tools.schemas(),
                                              options=self.options, keep_alive="30m", stream=True):
                    msg = chunk["message"]
                    if msg.get("tool_calls"):
                        calls.extend(msg["tool_calls"])
                    piece = msg.get("content") or ""
                    if piece:
                        content += piece
                        speech.feed(piece)
                raw += content
                if not calls:
                    break
                messages.append({"role": "assistant", "content": content, "tool_calls": calls})
                for call in calls:
                    name, args = call["function"]["name"], call["function"].get("arguments") or {}
                    if isinstance(args, str):
                        args = json.loads(args or "{}")
                    if on_tool:
                        on_tool(name)
                    used.append(name)
                    messages.append({"role": "tool", "content": tools.call_tool(name, args), "tool_name": name})
            speech.finish()
        except Exception as e:
            return Reply(self._offline_message(e), "sad", mood, used, ok=False)

        text, emotion = parse_feeling(raw, default=mood.nova_emotion)
        if not text:
            text = "Done!" if used else "Hmm, I'm not sure what to say."
            speech.emit_extra(text)
        self.memory.add_message("user", user_text)
        self.memory.add_message("assistant", text)
        return Reply(text, speech.emotion or emotion, mood, used)

    def generate(self, situation: str, timeout: float = 10.0) -> str | None:
        """A spontaneous line from Nova (welcome, check-in). None if the model is slow or offline."""
        result = {}

        def run():
            try:
                messages = [{"role": "system", "content": self._system_prompt()},
                            *self.memory.recent_history(4),
                            {"role": "user", "content": f"(This is not a message from {self.memory.user_name}. "
                                                        f"Situation: {situation}) Say it now, in your own words."}]
                resp = self.client.chat(model=self.cfg["model"], messages=messages,
                                        options={**self.options, "temperature": 0.9}, keep_alive="30m")
                result["raw"] = resp["message"]["content"]
            except Exception as e:
                print("[brain]", e)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout)
        if not result.get("raw"):
            return None
        text, _ = parse_feeling(result["raw"], "happy")
        if text:
            self.memory.add_message("assistant", text)  # so her next reply knows what she asked
        return text or None

    def _offline_message(self, error: Exception) -> str:
        err = str(error).lower()
        if "not found" in err and "model" in err:
            return f"I don't have my brain model yet. Please run: ollama pull {self.cfg['model']}"
        if any(w in err for w in ("connect", "refused", "failed to establish")):
            return "I can't reach my brain. Please start Ollama, then talk to me again."
        return f"Oops, something went wrong in my head: {error}"


class _SentenceStream:
    """Turns streamed text into whole sentences (minus the [feeling] tag) as soon as each one ends."""

    def __init__(self, default_emotion: str, on_sentence):
        self.on_sentence = on_sentence
        self.default = default_emotion
        self.emotion = None
        self.buf = ""
        self.tag_done = False
        self.sent_any = False

    def feed(self, piece: str):
        if not self.on_sentence:
            return
        self.buf += piece
        if not self.tag_done:
            head = self.buf.lstrip()
            if head[:1] in "[(" and len(head) < 20 and not re.search(r"[\])]", head):
                return  # still reading the [feeling] tag
            m = TAG.match(self.buf)
            if m and m.group(1).lower() in NOVA_EMOTIONS:
                self.emotion = m.group(1).lower()
                self.buf = self.buf[m.end():]
            self.tag_done = True
        while True:
            m = next((m for m in SENTENCE_END.finditer(self.buf) if m.end() >= 8), None)  # skip tiny bits like "Hi."
            if not m:
                break
            sentence, self.buf = self.buf[:m.end()], self.buf[m.end():]
            self._emit(sentence)

    def finish(self):
        if self.on_sentence and self.buf.strip():
            if not self.tag_done:
                text, self.emotion = parse_feeling(self.buf, self.default)
                self.buf = text
            self._emit(self.buf)
            self.buf = ""

    def emit_extra(self, text: str):
        if self.on_sentence and not self.sent_any:
            self._emit(text)

    def _emit(self, sentence: str):
        clean, _ = parse_feeling(sentence, self.emotion or self.default)
        if clean:
            self.sent_any = True
            self.on_sentence(clean, self.emotion or self.default)


def parse_feeling(raw: str, default: str) -> tuple[str, str]:
    """Split '[happy] Hello!' into ('Hello!', 'happy'). Small models sometimes drop or mangle the tag."""
    emotion = default
    match = re.match(r"\s*[\[(]\s*([a-zA-Z]+)\s*[\])]\s*[:\-]?\s*", raw)
    if match:
        tag = match.group(1).lower()
        if tag in NOVA_EMOTIONS:
            emotion = tag
        raw = raw[match.end():]
    raw = re.sub(r"\[(%s)\]" % "|".join(NOVA_EMOTIONS), "", raw, flags=re.I)  # stray tags mid-text
    raw = re.sub(r"[*_#`]+", "", raw)  # no markdown in speech
    return raw.strip(), emotion
