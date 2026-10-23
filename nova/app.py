"""Wires Nova together: ears -> emotions -> brain -> voice + body, plus the companion that
watches over you (welcomes, goodbyes, check-ins) and the tray icon to turn her off."""
import queue
import random
import threading
import time

from nova import tools
from nova.brain import Brain
from nova.config import DATA_DIR
from nova.memory import Memory

PET_LINES = [
    ("Aww, {pet} loves head pats!", "cute"),
    ("{pet} is purring so loudly right now.", "love"),
    ("Hehe, {pet} says thank you!", "happy"),
    ("Careful, {pet} might never let you stop.", "cute"),
]
WAKE_LINES = ["Yes?", "I'm listening!", "Hmm?", "Here!"]
CHEER_COOLDOWN = 10 * 60


class NovaApp:
    def __init__(self, settings: dict, gui: bool = True):
        self.settings = settings
        self.pet = settings.get("pet", {}).get("name", "Pip")
        self.memory = Memory(DATA_DIR, settings["user"]["name"])
        tools.ctx.settings, tools.ctx.memory, tools.ctx.notify, tools.ctx.app = settings, self.memory, self.notify, self
        tools.load_skills()
        self.brain = Brain(settings, self.memory)
        self.body = None
        self.speaker = self.listener = self.tone = None
        self.companion = None
        self.tray = None
        self._jobs: queue.Queue = queue.Queue()
        self._quiet_until = 0.0
        self._busy_until = 0.0
        self._brain_ok = True
        self._last_cheer = 0.0
        self._thinking = False
        self._fast = False  # set once the brain proves quick; slow laptops get instant ready-made lines

        if settings["voice"]["enabled"]:
            self._init_voice()
        if gui:
            self.body = self._make_body()
            self._wire_body()
            from nova.companion import Companion
            self.companion = Companion(self, settings)
            self.brain.context_fn = self.companion.describe_now
        threading.Thread(target=self._worker, daemon=True, name="brain").start()

    def _wire_body(self):
        self.body.on_click = self.listen_now
        self.body.on_text = lambda text: self.submit(text)
        self.body.on_pet = self._petted
        self.body.on_toggle_mute = self._toggle_mute
        self.body.on_quiet = self.set_quiet
        self.body.on_next_voice = self.next_voice
        self.body.on_hide = self.hide
        self.body.on_quit = self.shutdown
        self.body.on_ready = lambda: self.companion.welcome("start") if self.companion else None

    def _make_body(self):
        buddy = self.settings["buddy"]
        if buddy.get("style", "3d") == "3d":
            renderer = buddy.get("renderer", "auto")
            if renderer in ("auto", "electron"):
                try:
                    from nova.body_electron import BodyElectron
                    return BodyElectron(self.settings)
                except Exception as e:
                    print(f"[nova] transparent window unavailable: {e}")
            try:
                from nova.body3d import Body3D
                return Body3D(self.settings)
            except Exception as e:
                print(f"[nova] 3D window unavailable ({e}); using the 2D body.")
        from nova.buddy import Buddy
        return Buddy(self.settings)

    def _init_voice(self):
        try:
            from nova.speaker import Speaker
            self.speaker = Speaker(self.settings)
            if self.memory.data.get("voice"):
                self.speaker.voice = self.memory.data["voice"]
        except Exception as e:
            print(f"[nova] voice output off ({e})")
        try:
            from nova.listener import Listener
            self.listener = Listener(self.settings)
            # don't run speech recognition for "Hey Nova" while the brain needs the processor
            self.listener.wake_word_allowed = lambda: not self.quiet() and not self._thinking
            if self.settings["emotion"]["use_voice_tone"]:
                from nova.voice_tone import VoiceToneAnalyzer
                self.tone = VoiceToneAnalyzer(DATA_DIR)
        except Exception as e:
            print(f"[nova] microphone off ({e}). You can still type to Nova.")

    # helpers used by skills and the companion
    def body_do(self, method: str, *args):
        if self.body:
            getattr(self.body, method)(*args)

    def show_emotion(self, emotion: str):
        self.body_do("set_emotion", emotion)

    def quiet(self) -> bool:
        return time.time() < self._quiet_until

    def busy(self) -> bool:
        """Don't interrupt: we're mid-conversation."""
        return time.time() < self._busy_until or not self._jobs.empty()

    def brain_ready(self) -> bool:
        """OK to ask the brain for spontaneous lines (welcomes, check-ins) without making you wait?"""
        return self._brain_ok and self._fast and not self._thinking

    def set_quiet(self, minutes: int):
        self._quiet_until = time.time() + minutes * 60 if minutes > 0 else 0.0
        if minutes > 0:
            self.express("Okay, I'll be quiet. Click me if you need me!", "sleepy")
        else:
            self.express("I'm back! Did you miss me?", "happy")

    def toggle_visible(self):
        if not self.body:
            return
        if getattr(self.body, "visible", True):
            self.body.hide()
        else:
            self.body.show()
            self.express("Peekaboo!", "excited")

    def hide(self):
        if self.body:
            self.express("Okay, hiding! Click the tray icon or press Ctrl+Alt+N to bring me back.", "cute")
            self.body.after(2500, self.body.hide)

    def next_voice(self):
        if not self.speaker:
            return self.express("My voice is turned off in settings.", "concerned")
        name = self.speaker.next_voice()
        self.memory.data["voice"] = self.speaker.voice
        self.memory.save()
        self.express(f"Hi, I'm Nova with {name}'s voice. Do you like this one?", "happy")

    def listen_after_speaking(self):
        """After Nova asks a question, listen for the answer without needing the wake word."""
        if self.listener:
            self.listener.listen_now()

    # input
    def submit(self, text: str, audio=None):
        self._jobs.put((text, audio))

    def listen_now(self):
        if self.body and not getattr(self.body, "visible", True):
            self.body.show()
            self.express("I'm back!", "excited")
        if self.listener:
            self.listener.listen_now()
            self._show("listening")
        elif self.body:
            self.body.say("I can't hear you (no microphone set up). Double-click me to type!", "concerned")

    def _worker(self):
        while True:
            text, audio = self._jobs.get()
            try:
                self.handle(text, audio)
            except Exception as e:
                print("[nova] error:", e)

    def handle(self, text: str, audio=None):
        self._busy_until = time.time() + 120
        started = time.time()
        print(f"\nYou: {text}")
        print("[nova] thinking...", flush=True)
        if self.body:
            self.body.wake()
            self.body.set_activity("thinking")
        tone = None
        if audio is not None and self.tone:
            from nova.listener import SAMPLE_RATE
            tone = self.tone.analyze(audio, SAMPLE_RATE, text)

        said = []
        self._thinking = True

        def still_thinking(msg):
            if not said and self.body:
                self.body.say(msg, "thinking", 30)

        timers = [threading.Timer(2.5, still_thinking, ["Hmm, let me think..."]),
                  threading.Timer(25, still_thinking, ["Still thinking... my brain is a bit slow on this laptop!"])]
        for t in timers:
            t.start()

        def on_sentence(sentence, emotion):
            # speak each sentence as soon as it's ready; the bubble shows it the moment she says it
            if not said:
                for t in timers:
                    t.cancel()
                first = time.time() - started
                self._fast = first < 6
                print(f"[nova] first words after {first:.1f}s", flush=True)
            said.append(sentence)
            self.express(" ".join(said), emotion, speak=sentence)

        try:
            reply = self.brain.respond(text, tone=tone, on_sentence=on_sentence)
        finally:
            self._thinking = False
            for t in timers:
                t.cancel()
        took = time.time() - started
        if took > 15 and self.settings["brain"]["model"] != "qwen2.5:3b":
            print(f"[nova] tip: that took {took:.0f}s. For faster replies run  ollama pull qwen2.5:3b  "
                  'and set model = "qwen2.5:3b" in settings.toml')
        self._brain_ok = reply.ok
        mood = reply.user_mood
        print(f"Nova [{reply.nova_emotion}] ({mood.describe()} detected, {time.time() - started:.1f}s): {reply.text}"
              + (f"   (used: {', '.join(reply.tools_used)})" if reply.tools_used else ""))
        if not said:  # nothing streamed (e.g. the brain is offline)
            self.express(reply.text, reply.nova_emotion)
        elif self.body:
            self.body.set_activity("idle")
        self._react_with_body(mood, reply)
        return reply

    def _react_with_body(self, mood, reply):
        """Moves on her own: a cheer-up routine when you're down, a happy spin for good news."""
        if "perform" in reply.tools_used or not self.body:
            return
        if mood.emotion in ("sad", "stressed", "tired") and mood.intensity >= 0.55:
            if time.time() - self._last_cheer > CHEER_COOLDOWN:
                self._last_cheer = time.time()
                self.body.after(1500, lambda: self.body.perform("cheer"))
        elif mood.emotion in ("excited", "happy") and mood.intensity >= 0.7:
            self.body.perform(random.choice(["spin", "jump", "dance"]))
        elif mood.emotion == "affectionate":
            self.body.perform("hug")

    # output
    def express(self, text: str, emotion: str, speak: str | None = None):
        """Show `text` in the bubble and say `speak` (default: all of it), in sync."""
        speak = text if speak is None else speak
        voice_on = self.speaker and not self.speaker.muted
        if self.body and not voice_on:
            self.body.set_activity("idle")
            self.body.say(text, emotion)
        if voice_on:
            def started():
                if self.body:
                    self.body.say(text, emotion)  # bubble appears exactly when she starts talking
                self._speaking_started()
            self.speaker.say(speak, emotion, on_start=started, on_end=self._speaking_ended)

    def notify(self, text: str, emotion: str = "neutral"):
        """For skills (reminders) to make Nova speak up on her own."""
        print(f"\nNova [{emotion}]: {text}")
        self.express(text, emotion)

    def _speaking_started(self):
        if self.listener:
            self.listener.paused.set()
        self._show("speaking")

    def _speaking_ended(self):
        if self.speaker and not self.speaker.idle:
            return  # more sentences coming
        if self.listener:
            threading.Timer(0.4, self.listener.paused.clear).start()  # let the room echo fade
        self._show("idle")

    def _show(self, activity: str):
        if self.body:
            self.body.set_activity(activity)

    def _ears_state(self, state: str):
        if state == "missed":
            print(f"[ears] didn't catch anything{self.listener.diagnosis() if self.listener else ''}")
            self.express("Hmm, I didn't catch that. Try again, or double-click me to type!", "concerned")
        elif state.startswith("heard:"):
            print(f"[ears] heard: {state[6:]}")
        else:
            self._show(state)

    def _petted(self):
        line, emotion = random.choice(PET_LINES)
        self.express(line.format(pet=self.pet), emotion)

    def _toggle_mute(self) -> bool:
        if not self.speaker:
            return True
        self.speaker.muted = not self.speaker.muted
        if self.speaker.muted:
            self.speaker.stop()
        return self.speaker.muted

    def shutdown(self):
        """Turn Nova off (tray, menu or voice), saying goodbye first."""
        if self.companion:
            self.companion.goodbye("quit")
        if self.tray:
            self.tray.stop()
        if self.body:
            self.body.after(2500, self.body.quit)

    def greeting(self) -> tuple[str, str]:
        from datetime import datetime
        name = self.memory.user_name
        hour = datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
        last = self.memory.last_mood()
        if last and last["emotion"] in ("sad", "stressed", "tired", "angry"):
            return f"Good {part}, {name}. Last time you seemed {last['emotion']}. Feeling any better?", "caring"
        return f"Good {part}, {name}! {self.pet} and I are ready. What shall we do?", "happy"

    def check_brain(self) -> str:
        """Is Ollama running and is the model downloaded?"""
        from ollama import Client
        model = self.settings["brain"]["model"]
        try:
            names = [m.model for m in Client(host=self.settings["brain"]["host"], timeout=4).list().models]
        except Exception:
            self._brain_ok = False
            return "NOT RUNNING - install/start Ollama (https://ollama.com), then restart Nova"
        if not any(n == model or n.split(":")[0] == model or n == f"{model}:latest" for n in names):
            self._brain_ok = False
            return f"model missing - run:  ollama pull {model}"
        return f"ok ({model})"

    def startup_report(self):
        body = type(self.body).__name__ if self.body else "terminal"
        body = {"BodyElectron": "3D robot (transparent window)", "Body3D": "3D robot (basic window)",
                "Buddy": "2D robot"}.get(body, body)
        lines = [
            ("Brain", self.check_brain()),
            ("Body", f"{body} - look at the {self.settings['buddy']['corner']} of your screen" if self.body else body),
            ("Voice", f"{self.speaker.voice_name()} (click Nova > Change my voice)" if self.speaker else "off"),
            ("Ears", (("listening for 'Hey Nova'" if self.settings["voice"]["wake_word"] else "click/hotkey only")
                      + f" (mic: {self.listener.mic_name()})")
             if self.listener else "off (no microphone) - double-click Nova to type"),
            ("Pet", self.pet),
        ]
        print("\n  ✦ Nova is awake ✦")
        for k, v in lines:
            print(f"  {k:<6} {v}")
        if self.body:
            print("\n  Click her for options (talk, moves, voice, quiet, turn off). Double-click to type.")
            print("  Turn her off from the tray icon, the right-click menu, or by closing this window.\n")

    # run
    def _warm_up(self):
        t = time.time()
        if self.brain.warm_up():
            print(f"[nova] brain loaded in {time.time() - t:.0f}s - I'm ready to chat!")

    def _start_hotkey(self):
        try:
            from pynput import keyboard
            keyboard.GlobalHotKeys({self.settings["voice"].get("hotkey", "<ctrl>+<alt>+n"): self.listen_now}).start()
        except Exception:
            pass  # optional

    def _start_listener(self):
        if not self.listener:
            return
        threading.Thread(
            target=self.listener.run, daemon=True, name="ears",
            kwargs=dict(on_wake=lambda: self.express(random.choice(WAKE_LINES), "happy"),
                        on_command=lambda text, audio: self.submit(text, audio),
                        on_state=self._ears_state),
        ).start()

    def run_gui(self):
        self._start_listener()
        self._start_hotkey()
        self.companion.start()
        try:
            from nova.tray import start_tray
            self.tray = start_tray(self)
        except Exception as e:
            print(f"[nova] tray icon off ({e})")
        if self._brain_ok:
            threading.Thread(target=self._warm_up, daemon=True).start()
        self.body.run()
        if getattr(self.body, "failed", False):  # transparent window couldn't start: use the basic one
            print("[nova] using the basic window instead")
            from nova.body3d import Body3D
            self.body = Body3D(self.settings)
            self._wire_body()
            self.body.run()
        import os
        os._exit(0)  # window closed: stop the mic, voice and tray threads too

    def run_terminal(self):
        self._start_listener()
        text, emotion = self.greeting()
        self.notify(text, emotion)
        print("(type to chat, 'quit' to exit)")
        while True:
            try:
                line = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if line.lower() in ("quit", "exit", "bye"):
                self.notify(f"Bye {self.memory.user_name}! {self.pet} will miss you.", "cute")
                break
            if line:
                self.handle(line)
