"""Nova's body: a floating desktop robot with expressive glowing eyes, and her pet robo-kitty.

Everything is drawn on one tkinter canvas at ~30 fps. Other threads never touch tkinter
directly; they call the public methods, which queue work for the UI thread.

Eyes carry the emotion:
  neutral    soft cyan ovals that blink and follow your mouse
  happy      ^ ^ arcs, mint glow, blush
  excited    big bright eyes with sparkles, bouncing
  cute       huge glossy pink eyes with twinkling highlights, blush, cat smile
  love       beating pink hearts
  angry      RED narrowed eyes under slanted lids, red-tinted visor, anger mark, shaking, steam
  caring     soft blue eyes with gently drooping lids
  sad        droopy blue eyes with a falling tear
  concerned  amber, half-lidded, wobbly mouth
  sleepy     heavy lids that slowly droop, floating Zzz
  surprised  wide ring eyes that pop
  thinking   lavender eyes glancing up, one brow lowered, "..." dots
"""
import math
import platform
import queue
import random
import time
import tkinter as tk

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

KEY = "#ff00fe"          # this exact colour becomes see-through on Windows
FALLBACK_BG = "#f6c7cd"  # Linux has no per-pixel transparency: soft pink like Nova's photo
WHITE, SHADE, DARK = "#f7f4f6", "#dccbd0", "#1b1e2a"
VISOR, VISOR_ANGRY, VISOR_EDGE = "#0e111d", "#22090d", "#2a3042"
BLUSH = "#ff8fb0"

EYE_COLORS = {
    "neutral": "#5ef0ff", "happy": "#5fffd0", "excited": "#8cfcff", "cute": "#ff9fe3",
    "love": "#ff4f9a", "caring": "#8cc4ff", "sad": "#6b97ff", "concerned": "#ffc161",
    "angry": "#ff2a2a", "sleepy": "#4b9fbc", "surprised": "#cbfdff", "thinking": "#aab5ff",
}
# (bob height, bob speed) per expression
MOTION = {
    "neutral": (3, 2.0), "happy": (4, 4.0), "excited": (7, 7.5), "cute": (3, 3.0), "love": (3, 2.6),
    "caring": (2, 1.3), "sad": (1.5, 1.0), "concerned": (2, 2.0), "angry": (1.5, 2.5),
    "sleepy": (1.5, 0.8), "surprised": (2, 3.0), "thinking": (2, 1.5),
}
GLOW = [(6.0, 0.16), (3.0, 0.38), (0.0, 1.0)]  # (extra size, strength) layers for the LED glow
BLINKING = {"neutral", "excited", "cute", "caring", "sad", "concerned", "angry", "surprised", "thinking"}
IDLE_SLEEP_AFTER = 240


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def mix(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(ra, rb))


def heart_points(cx, cy, size, steps=28):
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts += [cx + x * size / 17, cy - y * size / 17]
    return pts


def star_points(cx, cy, r):
    pts = []
    for i in range(8):
        rr = r if i % 2 == 0 else r * 0.32
        a = math.pi / 4 * i - math.pi / 2
        pts += [cx + rr * math.cos(a), cy + rr * math.sin(a)]
    return pts


class Buddy:
    def __init__(self, settings: dict):
        self.s = s = settings["buddy"]["size"] / 190
        self.W, self.H = int(300 * s), int(252 * s)
        self.robot_x, self.head_y = 192 * s, 96 * s
        self.ground = 238 * s

        self.root = tk.Tk()
        self.root.title("Nova")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        if IS_WINDOWS:
            self.bg = KEY
            self.root.attributes("-transparentcolor", KEY)
        elif IS_MAC:
            self.bg = "systemTransparent"
            self.root.attributes("-transparent", True)
        else:
            self.bg = FALLBACK_BG
        self.root.configure(bg=self.bg)
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H, bg=self.bg, highlightthickness=0, bd=0)
        self.cv.pack()
        self.corner = settings["buddy"].get("corner", "bottom-right")
        self._place(self.corner)

        # callbacks wired up by the app
        self.on_click = lambda: None
        self.on_text = lambda text: None
        self.on_pet = lambda: None
        self.on_toggle_mute = lambda: False
        self.on_quiet = lambda minutes: None
        self.on_quit = self.root.destroy
        self.on_ready = lambda: None

        self._q: queue.Queue = queue.Queue()
        self.emotion, self.emotion_until = "neutral", 0.0
        self.activity = "idle"            # idle | listening | thinking | speaking
        self.expr = "neutral"
        self.eye_color = EYE_COLORS["neutral"]
        self.t = 0.0
        self._last = time.time()
        self.last_interaction = time.time()
        self.next_blink, self.blink_start = time.time() + 2, 0.0
        self.expr_since = time.time()
        self.gaze = [0.0, 0.0]
        self.particles: list[dict] = []
        self._spawn_clock = 0.0
        self.pet_x, self.pet_y = 62 * s, 200 * s
        self.pet_happy_until = 0.0

        self.bubble = SpeechBubble(self.root, s)
        self.entry = InputBox(self.root, s, lambda text: self.on_text(text))
        self._bind_mouse()
        self.root.after(33, self._tick)

    # thread-safe public API
    def set_emotion(self, emotion: str, hold: float = 40.0):
        self._q.put(lambda: self._set_emotion(emotion, hold))

    def set_activity(self, activity: str):
        self._q.put(lambda: setattr(self, "activity", activity))

    def say(self, text: str, emotion: str | None = None, seconds: float | None = None):
        def show():
            if emotion:
                self._set_emotion(emotion, 40.0)
            secs = seconds or min(14.0, 3.0 + len(text) / 14)
            self.bubble.show(text, EYE_COLORS.get(emotion or self.emotion, EYE_COLORS["neutral"]),
                             self._anchor(), secs)
        self._q.put(show)

    def wake(self):
        self._q.put(self._touch)

    def perform(self, action: str):
        # the 2D body can't spin or crawl, so it just gets excited
        self.set_emotion("excited", 6)

    def home(self):
        self._q.put(lambda: self._place(self.corner))

    def after(self, ms: int, fn):
        self._q.put(lambda: self.root.after(ms, fn))

    def show(self):
        self._q.put(self.root.deiconify)

    def hide(self):
        self._q.put(self.root.withdraw)

    def quit(self):
        self._q.put(self.root.destroy)

    def run(self):
        self.root.after(700, self.on_ready)
        self.root.mainloop()

    # internals
    def _set_emotion(self, emotion, hold):
        self.emotion = emotion if emotion in EYE_COLORS else "neutral"
        self.emotion_until = time.time() + hold
        self._touch()

    def _touch(self):
        self.last_interaction = time.time()

    def _place(self, corner):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = sw - self.W - 24 if "right" in corner else 24
        y = sh - self.H - 64 if "bottom" in corner else 40
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

    def _anchor(self):
        """Screen point just above Nova's head, for the speech bubble."""
        return (self.root.winfo_x() + int(self.robot_x), self.root.winfo_y() + int(self.head_y - 58 * self.s))

    def _bind_mouse(self):
        self._press = None
        self._click_job = None
        self.cv.bind("<ButtonPress-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.cv.bind("<Double-Button-1>", self._on_double)
        self.cv.bind("<Button-3>", self._on_menu)
        if IS_MAC:
            self.cv.bind("<Button-2>", self._on_menu)

    def _on_press(self, e):
        self._press = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y(), e.x, e.y)

    def _on_drag(self, e):
        if self._press:
            px, py, wx, wy, *_ = self._press
            self.root.geometry(f"+{wx + e.x_root - px}+{wy + e.y_root - py}")

    def _on_release(self, e):
        if not self._press:
            return
        px, py, _, _, cx, cy = self._press
        self._press = None
        if abs(e.x_root - px) + abs(e.y_root - py) > 5:
            return  # it was a drag
        if math.hypot(cx - self.pet_x, cy - self.pet_y) < 34 * self.s:
            self._pet_pip()
            return
        # wait a moment in case this is a double-click (which means "type")
        self._click_job = self.root.after(260, self._single_click)

    def _single_click(self):
        self._click_job = None
        self._touch()
        self.on_click()

    def _on_double(self, _e):
        if self._click_job:
            self.root.after_cancel(self._click_job)
            self._click_job = None
        self._touch()
        self.entry.show(self.root.winfo_x() + self.W // 2, self.root.winfo_y() + self.H)

    def _on_menu(self, e):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Talk to Nova", command=self.on_click)
        menu.add_command(label="Type a message", command=lambda: self._on_double(None))
        menu.add_command(label="Pet the kitty", command=self._pet_pip)
        menu.add_separator()
        menu.add_command(label="Mute / unmute voice", command=lambda: self.say(
            "Okay, I'll stay quiet." if self.on_toggle_mute() else "Yay, I can talk again!"))
        menu.add_command(label="Quit", command=self.on_quit)
        menu.tk_popup(e.x_root, e.y_root)

    def _pet_pip(self):
        self.pet_happy_until = time.time() + 2.5
        for _ in range(4):
            self._spawn("heart", self.pet_x + random.uniform(-14, 14) * self.s, self.pet_y - 26 * self.s,
                        size=random.uniform(5, 8))
        self._touch()
        self.on_pet()

    # animation loop
    def _tick(self):
        now = time.time()
        dt, self._last = min(0.1, now - self._last), now
        self.t += dt
        while not self._q.empty():
            try:
                self._q.get_nowait()()
            except Exception as e:
                print("[buddy]", e)

        if now > self.emotion_until and self.emotion != "neutral" and self.activity == "idle":
            self.emotion = "neutral"
        expr = self.emotion
        if self.activity == "thinking":
            expr = "thinking"
        elif (self.activity == "idle" and expr == "neutral" and not self.bubble.visible
              and now - self.last_interaction > IDLE_SLEEP_AFTER):
            expr = "sleepy"
        if expr != self.expr:
            self._on_expression_change(self.expr, expr, now)
        self.expr = expr
        self.eye_color = mix(self.eye_color, EYE_COLORS[expr], min(1.0, dt * 8))

        self._update_gaze(dt)
        self._update_particles(dt, now)
        self.cv.delete("all")
        self._draw_pet(now)
        self._draw_robot(now)
        self._draw_particles()
        self.root.after(33, self._tick)

    def _on_expression_change(self, old, new, now):
        self.expr_since = now
        self.blink_start = now  # a blink hides the shape change, like a real double-take
        if new == "surprised":
            self._spawn("bang", self.robot_x + 52 * self.s, self.head_y - 70 * self.s)
        if old == "sleepy":
            self._spawn("bang", self.robot_x + 52 * self.s, self.head_y - 70 * self.s)

    def _openness(self, now):
        if now > self.next_blink and self.expr in BLINKING:
            self.blink_start = now
            self.next_blink = now + random.uniform(2.2, 6.0)
            if random.random() < 0.2:  # sometimes a double blink
                self.next_blink = now + 0.35
        phase = (now - self.blink_start) / 0.17
        return 1.0 if phase >= 1 else max(0.08, 1 - math.sin(math.pi * phase))

    def _update_gaze(self, dt):
        s = self.s
        if self.expr == "thinking":
            target = (7 * s, -6 * s)
        elif self.expr in ("sleepy", "sad"):
            target = (0, 3 * s)
        else:
            px, py = self.root.winfo_pointerxy()
            dx = px - (self.root.winfo_rootx() + self.robot_x)
            dy = py - (self.root.winfo_rooty() + self.head_y)
            dist = math.hypot(dx, dy) or 1
            pull = min(1.0, dist / 260)
            target = (dx / dist * 7 * s * pull, dy / dist * 5 * s * pull)
        k = min(1.0, dt * 10)
        self.gaze[0] += (target[0] - self.gaze[0]) * k
        self.gaze[1] += (target[1] - self.gaze[1]) * k

    # particles: hearts, sparkles, zzz, tears, steam, ! and ?
    def _spawn(self, kind, x, y, **kw):
        p = {"kind": kind, "x": x, "y": y, "vx": 0.0, "vy": -18 * self.s, "born": time.time(), "life": 1.6}
        p.update(kw)
        self.particles.append(p)

    def _update_particles(self, dt, now):
        s, e = self.s, self.expr
        self._spawn_clock += dt
        hx, hy = self.robot_x, self.head_y
        if self._spawn_clock > 0.45:
            self._spawn_clock = 0.0
            r = random.random()
            if e == "love" and r < 0.6:
                self._spawn("heart", hx + random.uniform(-60, 60) * s, hy - 50 * s, size=random.uniform(6, 10),
                            vx=random.uniform(-6, 6) * s)
            elif e == "cute" and r < 0.35:
                self._spawn("heart", hx + random.choice((-70, 70)) * s, hy - 30 * s, size=6)
            elif e == "excited" and r < 0.7:
                self._spawn("sparkle", hx + random.uniform(-85, 85) * s, hy + random.uniform(-65, 20) * s,
                            vy=0, life=0.8, size=random.uniform(5, 9))
            elif e == "sleepy" and r < 0.35:
                self._spawn("zzz", hx + 60 * s, hy - 55 * s, vx=10 * s, vy=-14 * s, life=2.6,
                            size=random.choice((9, 12)))
            elif e == "sad" and r < 0.22:
                self._spawn("tear", hx + 36 * s, hy + 16 * s, vy=6 * s, life=1.2)
            elif e == "angry" and r < 0.4:
                side = random.choice((-1, 1))
                self._spawn("steam", hx + side * 82 * s, hy - 18 * s, vx=side * 12 * s, vy=-26 * s, life=1.0,
                            size=random.uniform(4, 7))
            elif e == "thinking" and r < 0.15:
                self._spawn("question", self.pet_x + 18 * s, self.pet_y - 40 * s, life=1.4, vy=-8 * s)
        alive = []
        for p in self.particles:
            age = now - p["born"]
            if age > p["life"]:
                continue
            if p["kind"] == "tear":
                p["vy"] += 90 * s * dt
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            alive.append(p)
        self.particles = alive[-60:]

    def _draw_particles(self):
        cv, s, now = self.cv, self.s, time.time()
        for p in self.particles:
            k = (now - p["born"]) / p["life"]
            x, y, kind = p["x"], p["y"], p["kind"]
            size = p.get("size", 6) * s
            if kind == "heart":
                cv.create_polygon(heart_points(x, y, size * (1 - 0.3 * k)), fill="#ff5c9d", outline="")
            elif kind == "sparkle":
                r = size * math.sin(math.pi * k)
                cv.create_polygon(star_points(x, y, r), fill="#fff6a6", outline="")
            elif kind == "zzz":
                cv.create_text(x, y, text="z", fill="#8fc3e0", font=("Arial", int(size), "bold"))
            elif kind == "tear":
                cv.create_oval(x - 3 * s, y - 2 * s, x + 3 * s, y + 6 * s, fill="#8fd6ff", outline="")
                cv.create_polygon(x - 3 * s, y + 1 * s, x, y - 6 * s, x + 3 * s, y + 1 * s, fill="#8fd6ff")
            elif kind == "steam":
                r = size * (0.6 + k)
                cv.create_oval(x - r, y - r, x + r, y + r, fill="#e7e2e4", outline="")
            elif kind == "bang":
                cv.create_text(x, y - 8 * s * k, text="!", fill="#ffcf3f", font=("Arial", int(22 * s), "bold"))
            elif kind == "question":
                cv.create_text(x, y, text="?", fill="#aab5ff", font=("Arial", int(15 * s), "bold"))

    # robot
    def _draw_robot(self, now):
        cv, s, e = self.cv, self.s, self.expr
        amp, speed = MOTION[e]
        dy = -abs(math.sin(self.t * speed)) * amp * s if e in ("excited", "happy") else math.sin(self.t * speed) * amp * s
        dx = 0.0
        if e == "angry" and math.sin(self.t * 1.7) > 0.3:
            dx = random.uniform(-1.6, 1.6) * s  # trembling with rage (cute rage)
        if e == "surprised":
            dy -= max(0.0, 1 - (now - self.expr_since) / 0.35) * 14 * s  # jump
        cx, hy = self.robot_x + dx, self.head_y + dy

        # shadow on the "floor"
        sw = 46 * s * (1 + dy / (60 * s))
        cv.create_oval(self.robot_x - sw, self.ground - 4 * s, self.robot_x + sw, self.ground + 4 * s,
                       fill="#3b3640" if self.bg != FALLBACK_BG else "#e3aeb6", outline="")

        self._draw_body(cx, hy, e)
        self._draw_head(cx, hy, e)
        self._draw_face(cx, hy, e, now)
        if self.activity == "listening":
            self._draw_sound_waves(cx, hy)

    def _rrect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.cv.create_polygon(pts, smooth=True, splinesteps=20, **kw)

    def _arm_targets(self, cx, hy, e):
        s, t = self.s, self.t
        down_l, down_r = (cx - 46 * s, hy + 104 * s), (cx + 46 * s, hy + 104 * s)
        if e in ("happy", "excited"):
            wave = math.sin(t * (9 if e == "excited" else 6)) * 10 * s
            right = (cx + 62 * s + wave * 0.4, hy + 44 * s + wave)
            left = (cx - 60 * s, hy + 50 * s - wave) if e == "excited" else down_l
            return left, right
        if e in ("cute", "love"):
            return (cx - 10 * s, hy + 95 * s), (cx + 10 * s, hy + 95 * s)  # hands clasped, shy
        if e == "angry":
            shake = math.sin(t * 22) * 2 * s
            return (cx - 24 * s, hy + 74 * s + shake), (cx + 24 * s, hy + 74 * s - shake)  # little fists
        if e == "surprised":
            return (cx - 66 * s, hy + 46 * s), (cx + 66 * s, hy + 46 * s)
        if e == "thinking":
            return down_l, (cx + 20 * s, hy + 62 * s)  # hand on chin
        if e in ("sad", "sleepy"):
            return (cx - 40 * s, hy + 108 * s), (cx + 40 * s, hy + 108 * s)
        if e == "caring":
            return (cx - 44 * s, hy + 86 * s), (cx + 44 * s, hy + 86 * s)
        return down_l, down_r

    def _draw_body(self, cx, hy, e):
        cv, s = self.cv, self.s
        top, bottom = hy + 60 * s, hy + 102 * s
        for side in (-1, 1):  # legs
            x = cx + side * 15 * s
            self._rrect(x - 10 * s, bottom - 8 * s, x + 10 * s, bottom + 26 * s, 8 * s, fill=WHITE, outline=SHADE,
                        width=max(1, int(1.5 * s)))
            cv.create_rectangle(x - 9 * s, bottom + 4 * s, x + 9 * s, bottom + 8 * s, fill=DARK, outline="")
        cv.create_rectangle(cx - 12 * s, hy + 48 * s, cx + 12 * s, top + 6 * s, fill=DARK, outline="")  # neck
        self._rrect(cx - 34 * s, top, cx + 34 * s, bottom, 16 * s, fill=WHITE, outline=SHADE,
                    width=max(1, int(1.5 * s)))
        self._rrect(cx - 15 * s, top + 8 * s, cx + 15 * s, bottom - 10 * s, 8 * s, fill=DARK, outline="")
        pulse = 0.55 + 0.45 * math.sin(self.t * (6 if e in ("angry", "excited") else 2.5))
        r = 4.5 * s
        cy = (top + bottom) / 2 - 1 * s
        cv.create_oval(cx - r - 2 * s, cy - r - 2 * s, cx + r + 2 * s, cy + r + 2 * s,
                       fill=mix(DARK, self.eye_color, 0.35 * pulse), outline="")
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=mix(DARK, self.eye_color, 0.4 + 0.6 * pulse), outline="")
        left, right = self._arm_targets(cx, hy, e)
        for side, hand in ((-1, left), (1, right)):  # arms
            sx, sy = cx + side * 33 * s, top + 10 * s
            cv.create_line(sx, sy, *hand, width=15 * s, fill=SHADE, capstyle="round")
            cv.create_line(sx, sy, *hand, width=12 * s, fill=WHITE, capstyle="round")
            hx, hy2 = hand
            cv.create_oval(hx - 6 * s, hy2 - 6 * s, hx + 6 * s, hy2 + 6 * s, fill=DARK, outline="")

    def _draw_head(self, cx, hy, e):
        cv, s = self.cv, self.s
        for side in (-1, 1):  # ear pods
            ex = cx + side * 79 * s
            ey = hy + 2 * s
            cv.create_oval(ex - 21 * s, ey - 21 * s, ex + 21 * s, ey + 21 * s, fill=WHITE, outline=SHADE,
                           width=max(1, int(1.5 * s)))
            cv.create_oval(ex - 13 * s, ey - 13 * s, ex + 13 * s, ey + 13 * s, fill=DARK, outline="")
            cv.create_oval(ex - 6 * s, ey - 6 * s, ex + 6 * s, ey + 6 * s, fill=mix(DARK, self.eye_color, 0.55),
                           outline="")
        self._rrect(cx - 77 * s, hy - 60 * s, cx + 77 * s, hy + 56 * s, 50 * s, fill=WHITE, outline=SHADE,
                    width=max(1, int(2 * s)))
        cv.create_line(cx + 26 * s, hy - 52 * s, cx + 46 * s, hy - 47 * s, fill=SHADE, width=3 * s, capstyle="round")
        cv.create_line(cx + 30 * s, hy - 45 * s, cx + 48 * s, hy - 40 * s, fill=SHADE, width=3 * s, capstyle="round")
        visor = VISOR_ANGRY if e == "angry" else VISOR
        self._visor = visor
        self._rrect(cx - 61 * s, hy - 34 * s, cx + 61 * s, hy + 40 * s, 32 * s, fill=visor, outline=VISOR_EDGE,
                    width=max(1, int(2 * s)))
        cv.create_arc(cx - 52 * s, hy - 28 * s, cx + 20 * s, hy + 20 * s, start=110, extent=50, style="arc",
                      outline=mix(visor, "#ffffff", 0.18), width=2 * s)  # glossy reflection
        if e == "angry":
            self._anger_mark(cx + 56 * s, hy - 48 * s)

    def _anger_mark(self, x, y):
        """The classic anime anger symbol: four curved corners pulsing."""
        s, cv = self.s, self.cv
        k = 1 + 0.15 * math.sin(self.t * 10)
        d = 5 * s * k
        for sx in (-1, 1):
            for sy in (-1, 1):
                ox, oy = x + sx * d * 1.7, y + sy * d * 1.7
                facing = math.degrees(math.atan2(sy, -sx))  # tk angles: toward the centre
                cv.create_arc(ox - d * 1.2, oy - d * 1.2, ox + d * 1.2, oy + d * 1.2, start=facing - 45, extent=90,
                              style="arc", outline="#ff2a2a", width=3 * s)

    # eyes
    def _glow_oval(self, x, y, rx, ry, color):
        for extra, strength in GLOW:
            g = extra * self.s
            self.cv.create_oval(x - rx - g, y - ry - g, x + rx + g, y + ry + g,
                                fill=mix(self._visor, color, strength), outline="")

    def _glow_arc(self, x0, y0, x1, y1, start, extent, width, color):
        for extra, strength in GLOW:
            self.cv.create_arc(x0, y0, x1, y1, start=start, extent=extent, style="arc",
                               outline=mix(self._visor, color, strength), width=width + extra * self.s)

    def _glow_poly(self, pts, color, cx, cy):
        for extra, strength in GLOW:
            k = 1 + extra * self.s / 14
            scaled = [cx + (pts[i] - cx) * k if i % 2 == 0 else cy + (pts[i] - cy) * k for i in range(len(pts))]
            self.cv.create_polygon(scaled, fill=mix(self._visor, color, strength), outline="", smooth=True)

    def _lid(self, x, y, rx, ry, inner_drop, outer_drop, side):
        """Cover the top of an eye with visor colour. drops: 0 = top of eye, 1 = middle."""
        s = self.s
        top = y - ry - 12 * s
        ix, ox = x - side * (rx + 8 * s), x + side * (rx + 8 * s)
        iy, oy = y - ry + inner_drop * ry, y - ry + outer_drop * ry
        self.cv.create_polygon(ix, top, ox, top, ox, oy, ix, iy, fill=self._visor, outline="")
        return ix, iy, ox, oy

    def _highlight(self, x, y, r):
        self.cv.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", outline="")

    def _draw_face(self, cx, hy, e, now):
        s, c, cv = self.s, self.eye_color, self.cv
        op = self._openness(now)
        gx, gy = self.gaze
        ey = hy + 2 * s + gy
        for side in (-1, 1):
            x = cx + side * 26 * s + gx
            if e == "happy":
                self._glow_arc(x - 13 * s, ey - 6 * s, x + 13 * s, ey + 20 * s, 0, 180, 6 * s, c)
            elif e == "love":
                beat = 1 + 0.14 * max(0.0, math.sin(self.t * 7)) ** 4
                pts = heart_points(x, ey, 15 * s * beat)
                self._glow_poly(pts, c, x, ey)
                self._highlight(x - 5 * s, ey - 4 * s, 2.5 * s)
            elif e == "cute":
                r = 17 * s
                self._glow_oval(x, ey, r, r * op, c)
                if op > 0.5:
                    tw = 1 + 0.18 * math.sin(self.t * 5 + side)
                    self._highlight(x - 6 * s, ey - 6 * s, 6 * s * tw)
                    self._highlight(x + 6 * s, ey + 6 * s, 3 * s * tw)
                    self._highlight(x + 8 * s, ey - 9 * s, 1.6 * s)
            elif e == "excited":
                self._glow_oval(x, ey, 15 * s, 19 * s * op, c)
                if op > 0.5:
                    self._highlight(x - 5 * s, ey - 8 * s, 4.5 * s)
                    self._highlight(x + 5 * s, ey + 6 * s, 2 * s)
            elif e == "surprised":
                pop = 1 + max(0.0, 1 - (now - self.expr_since) / 0.3) * 0.35
                r = 18 * s * pop
                self._glow_oval(x, ey, r, r * op, c)
                ri = r * 0.42
                cv.create_oval(x - ri, ey - ri * op, x + ri, ey + ri * op, fill=self._visor, outline="")
            elif e == "angry":
                rx, ry = 14 * s, 15 * s * op
                self._glow_oval(x, ey, rx, ry, c)
                ix, iy, ox, oy = self._lid(x, ey, rx, ry, 0.95, 0.05, side)
                cv.create_line(ix, iy - 3 * s, ox, oy - 3 * s, fill=c, width=4 * s, capstyle="round")  # red brow
            elif e in ("caring", "sad"):
                rx, ry = 12 * s, (15 if e == "caring" else 14) * s * op
                self._glow_oval(x, ey, rx, ry, c)
                self._lid(x, ey, rx, ry, 0.05, 0.6 if e == "caring" else 0.9, side)
                if op > 0.5:
                    self._highlight(x - 4 * s, ey - 2 * s, 2.5 * s)
            elif e == "concerned":
                rx, ry = 12 * s, 15 * s * op
                self._glow_oval(x, ey, rx, ry, c)
                self._lid(x, ey, rx, ry, 0.5 if side < 0 else 0.25, 0.25 if side < 0 else 0.5, side)
            elif e == "sleepy":
                droop = 0.4 + 0.35 * (0.5 + 0.5 * math.sin(self.t * 0.9))  # slowly nodding off
                rx, ry = 13 * s, 14 * s
                self._glow_oval(x, ey + 4 * s, rx, ry, c)
                self._lid(x, ey + 4 * s, rx, ry, droop * 2, droop * 2, side)
            elif e == "thinking":
                rx, ry = 11 * s, 14 * s * op
                self._glow_oval(x, ey, rx, ry, c)
                if side > 0:
                    self._lid(x, ey, rx, ry, 0.45, 0.2, side)
            else:  # neutral
                ry = (17 if self.activity == "listening" else 16) * s * op
                self._glow_oval(x, ey, 12 * s, ry, c)
                if op > 0.5:
                    self._highlight(x - 4 * s, ey - 7 * s, 3 * s)

        if e in ("happy", "cute", "love", "caring", "excited"):  # blush
            for side in (-1, 1):
                bx, by = cx + side * 40 * s, hy + 26 * s
                cv.create_oval(bx - 9 * s, by - 3.5 * s, bx + 9 * s, by + 3.5 * s,
                               fill=mix(self._visor, BLUSH, 0.6 if e != "caring" else 0.35), outline="")
        self._draw_mouth(cx, hy + 27 * s, e)
        if e == "thinking":
            for i in range(3):
                on = int(self.t * 3) % 4 > i
                r = 3.5 * s
                x = cx + (62 + i * 12) * s
                y = hy - (58 + i * 7) * s
                if on:
                    cv.create_oval(x - r, y - r, x + r, y + r, fill="#aab5ff", outline="")

    def _draw_mouth(self, cx, my, e):
        s, c, cv = self.s, self.eye_color, self.cv
        if self.activity == "speaking":  # little equalizer while talking
            for i, off in enumerate((-8, 0, 8)):
                h = (3 + 5 * abs(math.sin(self.t * (11 + i * 3) + i))) * s
                cv.create_line(cx + off * s, my - h, cx + off * s, my + h, fill=c, width=4 * s, capstyle="round")
            return
        if e in ("happy", "excited", "love"):
            cv.create_arc(cx - 9 * s, my - 9 * s, cx + 9 * s, my + 5 * s, start=200, extent=140, style="arc",
                          outline=c, width=3 * s)
        elif e == "cute":  # cat mouth  ω
            for side in (-1, 1):
                cv.create_arc(cx + side * 5 * s - 5 * s, my - 6 * s, cx + side * 5 * s + 5 * s, my + 3 * s,
                              start=180, extent=180, style="arc", outline=c, width=2.5 * s)
        elif e in ("sad", "angry"):
            cv.create_arc(cx - 8 * s, my - 1 * s, cx + 8 * s, my + 11 * s, start=20, extent=140, style="arc",
                          outline=c, width=3 * s)
        elif e == "concerned":
            pts = [cx - 9 * s, my + 2 * s, cx - 4 * s, my - 1 * s, cx, my + 2 * s, cx + 4 * s, my - 1 * s,
                   cx + 9 * s, my + 2 * s]
            cv.create_line(pts, fill=c, width=2.5 * s, smooth=True)
        elif e == "surprised":
            cv.create_oval(cx - 4 * s, my - 3 * s, cx + 4 * s, my + 5 * s, outline=c, width=2.5 * s)
        elif e == "caring":
            cv.create_arc(cx - 6 * s, my - 6 * s, cx + 6 * s, my + 3 * s, start=200, extent=140, style="arc",
                          outline=c, width=2.5 * s)

    def _draw_sound_waves(self, cx, hy):
        s = self.s
        for side in (-1, 1):
            for i in range(3):
                k = (self.t * 1.6 + i / 3) % 1
                r = (24 + 20 * k) * s
                ex = cx + side * 79 * s
                self.cv.create_arc(ex - r, hy - r, ex + r, hy + r, start=-35 if side > 0 else 145, extent=70,
                                   style="arc", outline=self.eye_color, width=max(1, (3 - 2.5 * k) * s))

    # the pet: a tiny robo-kitty
    def _pet_mood(self, now):
        if now < self.pet_happy_until:
            return "petted"
        return {"happy": "happy", "excited": "happy", "cute": "love", "love": "love", "angry": "scared",
                "sad": "sad", "caring": "sad", "sleepy": "sleepy", "surprised": "surprised",
                "thinking": "curious", "concerned": "sad"}.get(self.expr, "idle")

    def _draw_pet(self, now):
        cv, s = self.cv, self.s
        mood = self._pet_mood(now)
        t = self.t
        target_x = {"scared": 40 * s, "sad": 92 * s}.get(mood, 64 * s)
        base_y = 214 * s if mood == "sleepy" else 196 * s
        self.pet_x += (target_x - self.pet_x) * 0.08
        hop = 0.0
        if mood in ("happy", "petted"):
            hop = -abs(math.sin(t * (7 if mood == "happy" else 9))) * 12 * s
        elif mood == "surprised":
            hop = -max(0.0, 1 - (now - self.expr_since) / 0.4) * 20 * s
        elif mood != "sleepy":
            hop = math.sin(t * 2.4 + 1.3) * 3 * s  # hovering
        jitter = random.uniform(-1.2, 1.2) * s if mood == "scared" else 0.0
        x, y = self.pet_x + jitter, base_y + hop
        self.pet_y = y
        sh = 22 * s * (1 + hop / (50 * s))
        cv.create_oval(self.pet_x - sh, self.ground - 3 * s, self.pet_x + sh, self.ground + 3 * s,
                       fill="#3b3640" if self.bg != FALLBACK_BG else "#e3aeb6", outline="")

        wag = math.sin(t * (8 if mood in ("happy", "petted", "love") else 2)) * 6 * s
        cv.create_line(x - 18 * s, y + 10 * s, x - 32 * s, y + 2 * s, x - 34 * s + wag, y - 14 * s,
                       smooth=True, width=6 * s, fill=SHADE, capstyle="round")
        cv.create_line(x - 18 * s, y + 10 * s, x - 32 * s, y + 2 * s, x - 34 * s + wag, y - 14 * s,
                       smooth=True, width=4 * s, fill=WHITE, capstyle="round")
        ear_flat = 6 * s if mood in ("scared", "sad", "sleepy") else 0
        for side in (-1, 1):
            ex = x + side * 14 * s
            cv.create_polygon(ex - 9 * s, y - 14 * s, ex + side * 5 * s, y - 32 * s + ear_flat, ex + 9 * s,
                              y - 12 * s, fill=WHITE, outline=SHADE)
            cv.create_polygon(ex - 4 * s, y - 16 * s, ex + side * 3 * s, y - 26 * s + ear_flat, ex + 4 * s,
                              y - 15 * s, fill="#ffb3c8", outline="")
        cv.create_oval(x - 24 * s, y - 22 * s, x + 24 * s, y + 20 * s, fill=WHITE, outline=SHADE,
                       width=max(1, int(1.5 * s)))
        for side in (-1, 1):
            cv.create_oval(x + side * 10 * s - 6 * s, y + 15 * s, x + side * 10 * s + 6 * s, y + 23 * s,
                           fill=WHITE, outline=SHADE)
        visor = VISOR
        self._rrect(x - 17 * s, y - 12 * s, x + 17 * s, y + 8 * s, 9 * s, fill=visor, outline="")
        color = {"love": "#ff4f9a", "scared": "#ffd36b", "sad": "#8cc4ff", "sleepy": "#4b9fbc"}.get(mood, "#5ef0ff")
        look = 3 * s if mood == "idle" and math.sin(t * 0.5) > 0.6 else 0  # glances at Nova
        if mood == "curious":
            look = 2 * s
        for side in (-1, 1):
            px, py = x + side * 7 * s + look, y - 2 * s - (2 * s if mood == "curious" else 0)
            if mood in ("happy", "petted", "love"):
                cv.create_arc(px - 4 * s, py - 3 * s, px + 4 * s, py + 5 * s, start=0, extent=180, style="arc",
                              outline=color, width=2.5 * s)
            elif mood == "scared":  # > <
                d = -side
                cv.create_line(px - d * 3.5 * s, py - 3.5 * s, px + d * 3.5 * s, py, px - d * 3.5 * s, py + 3.5 * s,
                               fill=color, width=2.2 * s)
            elif mood == "sleepy":
                cv.create_arc(px - 4 * s, py - 4 * s, px + 4 * s, py + 3 * s, start=180, extent=180, style="arc",
                              outline=color, width=2 * s)
            else:
                blink = 0.15 if (t + side * 0.01) % 4.3 < 0.12 else 1
                h = (3 if mood == "sad" else 4.2) * s * blink
                cv.create_oval(px - 3.2 * s, py - h, px + 3.2 * s, py + h, fill=color, outline="")
        if mood in ("love", "petted", "happy"):
            for side in (-1, 1):
                bx = x + side * 15 * s
                cv.create_oval(bx - 4 * s, y + 4 * s, bx + 4 * s, y + 8 * s, fill="#ffc0d2", outline="")
        if mood == "scared":  # sweat drop
            cv.create_oval(x + 20 * s, y - 18 * s, x + 26 * s, y - 10 * s, fill="#8fd6ff", outline="")
        if mood == "sleepy" and int(t * 1.5) % 2 == 0:
            cv.create_text(x + 26 * s, y - 28 * s, text="z", fill="#8fc3e0", font=("Arial", int(9 * s), "bold"))


class SpeechBubble:
    def __init__(self, root, s):
        self.s = s
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        bg = KEY if IS_WINDOWS else FALLBACK_BG
        if IS_WINDOWS:
            self.win.attributes("-transparentcolor", KEY)
        self.win.configure(bg=bg)
        self.cv = tk.Canvas(self.win, bg=bg, highlightthickness=0, bd=0)
        self.cv.pack()
        self.win.withdraw()
        self.visible = False
        self._job = None

    def show(self, text, accent, anchor, seconds):
        s, cv = self.s, self.cv
        cv.delete("all")
        font = ("Segoe UI", max(9, int(10.5 * s)))
        item = cv.create_text(0, 0, text=text, font=font, width=int(250 * s), anchor="nw", fill="#1b1e2a")
        x0, y0, x1, y1 = cv.bbox(item)
        pad, tail = int(12 * s), int(12 * s)
        w, h = x1 - x0 + pad * 2, y1 - y0 + pad * 2
        cv.config(width=w + 4, height=h + tail + 4)
        r = 14 * s
        pts = [2 + r, 2, w - r, 2, w, 2, w, 2 + r, w, h - r, w, h, w - r, h, 2 + r, h, 2, h, 2, h - r, 2, 2 + r, 2, 2]
        bubble = cv.create_polygon(pts, smooth=True, fill="#ffffff", outline=accent, width=2)
        cx = w / 2
        cv.create_polygon(cx - 9 * s, h - 1, cx + 9 * s, h - 1, cx, h + tail, fill="#ffffff", outline="")
        cv.create_line(cx - 9 * s, h, cx, h + tail, cx + 9 * s, h, fill=accent, width=2)
        cv.tag_raise(item)
        cv.move(item, pad, pad)
        cv.tag_lower(bubble)
        ax, ay = anchor
        x = max(4, min(ax - w // 2, self.win.winfo_screenwidth() - w - 4))
        y = max(4, ay - h - tail)
        self.win.geometry(f"{w + 4}x{h + tail + 4}+{int(x)}+{int(y)}")
        self.win.deiconify()
        self.win.lift()
        self.visible = True
        if self._job:
            self.win.after_cancel(self._job)
        self._job = self.win.after(int(seconds * 1000), self.hide)

    def hide(self):
        self.win.withdraw()
        self.visible = False
        self._job = None


class InputBox:
    def __init__(self, root, s, on_submit):
        self.on_submit = on_submit
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#5ef0ff")
        self.var = tk.StringVar()
        self.entry = tk.Entry(self.win, textvariable=self.var, font=("Segoe UI", max(9, int(11 * s))),
                              width=32, bd=0, bg="#0e111d", fg="#e8fbff", insertbackground="#5ef0ff")
        self.entry.pack(padx=2, pady=2, ipady=6, ipadx=6)
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Escape>", lambda _e: self.win.withdraw())
        self.win.withdraw()

    def show(self, x_center, y):
        self.win.update_idletasks()
        w = self.win.winfo_reqwidth()
        self.win.geometry(f"+{int(x_center - w / 2)}+{int(min(y, self.win.winfo_screenheight() - 60))}")
        self.win.deiconify()
        self.win.lift()
        self.entry.focus_force()

    def _submit(self, _e):
        text = self.var.get().strip()
        self.var.set("")
        self.win.withdraw()
        if text:
            self.on_submit(text)
