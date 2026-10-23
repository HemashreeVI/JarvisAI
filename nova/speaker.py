"""Nova's voice. Pitch, speed and loudness follow her mood.

engine = "neural": Microsoft Edge neural voices (natural and cute, free, needs internet)
engine = "system": your PC's built-in voice (offline, more robotic)
If the neural voice can't be reached, Nova switches to the system voice by herself.
"""
import asyncio
import queue
import re
import threading

# Natural voices to choose from ("Change my voice" in Nova's menu cycles through them).
VOICES = [
    ("en-US-AvaNeural", "Ava"),             # warm, very natural (default)
    ("en-US-EmmaNeural", "Emma"),           # bright and friendly
    ("en-IN-NeerjaExpressiveNeural", "Neerja"),  # Indian English, expressive
    ("en-US-JennyNeural", "Jenny"),         # clear and cheerful
    ("en-GB-SoniaNeural", "Sonia"),         # British
    ("en-US-AnaNeural", "Ana"),             # a child's voice
]

# per emotion: (rate %, volume %). No pitch shifting: it makes neural voices sound robotic.
NEURAL_STYLE = {
    "excited": (+10, +5), "happy": (+5, 0), "cute": (0, -5), "love": (-4, -8),
    "neutral": (0, 0), "caring": (-8, -12), "concerned": (-5, -5), "sad": (-10, -15),
    "angry": (+4, +8), "sleepy": (-14, -20), "surprised": (+8, +5), "thinking": (-4, -5),
}

# per emotion: (rate change in wpm, volume 0-1) for the system voice
SYSTEM_STYLE = {
    "excited": (+30, 1.0), "happy": (+12, 1.0), "cute": (+5, 0.95), "love": (-5, 0.9),
    "neutral": (0, 0.95), "caring": (-25, 0.8), "concerned": (-12, 0.9), "sad": (-30, 0.75),
    "angry": (+8, 1.0), "sleepy": (-40, 0.7), "surprised": (+20, 1.0), "thinking": (-5, 0.9),
}


def clean_for_speech(text: str) -> str:
    text = re.sub(r"\[[a-zA-Z]+\]", "", text)  # feeling tags like [happy]
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[\U0001F000-\U0001FFFF☀-➿]", "", text)  # emoji
    return re.sub(r"[*_#`>\[\]]", "", text).strip()


class Speaker:
    def __init__(self, settings: dict):
        self.cfg = settings["voice"]
        self.engine = self.cfg.get("engine", "neural")
        self.voice = self.cfg.get("neural_voice", VOICES[0][0])
        self.muted = False
        self._q: queue.Queue = queue.Queue()
        self._system = None
        self._stop = threading.Event()
        threading.Thread(target=self._worker, daemon=True, name="speaker").start()

    def say(self, text: str, emotion: str = "neutral", on_start=None, on_end=None):
        if self.muted or not text.strip():
            if on_end:
                on_end()
            return
        self._q.put((clean_for_speech(text), emotion, on_start, on_end))

    def next_voice(self) -> str:
        """Switch to the next natural voice; returns its friendly name."""
        ids = [v for v, _ in VOICES]
        i = (ids.index(self.voice) + 1) % len(VOICES) if self.voice in ids else 0
        self.voice = VOICES[i][0]
        self.engine = "neural"  # give the neural voice another try
        return VOICES[i][1]

    def voice_name(self) -> str:
        return dict(VOICES).get(self.voice, self.voice) if self.engine == "neural" else "offline system voice"

    def stop(self):
        while not self._q.empty():
            self._q.get_nowait()
        self._stop.set()
        if self._system:
            self._system.stop()

    def _worker(self):
        while True:
            text, emotion, on_start, on_end = self._q.get()
            self._stop.clear()
            try:
                if self.engine == "neural":
                    try:
                        audio, rate = self._neural_audio(text, emotion)
                    except Exception as e:
                        print(f"[voice] neural voice unavailable ({e}); using the system voice")
                        self.engine = "system"
                    else:
                        if on_start:
                            on_start()
                        self._play(audio, rate)
                        continue
                if on_start:
                    on_start()
                self._system_say(text, emotion)
            except Exception as e:
                print("[voice]", e)
            finally:
                if on_end:
                    on_end()

    # neural (edge-tts)
    def _neural_audio(self, text: str, emotion: str):
        import edge_tts
        import miniaudio
        import numpy as np

        rate, volume = NEURAL_STYLE.get(emotion, (0, 0))
        rate += int(self.cfg.get("voice_speed", 0))
        pitch = int(self.cfg.get("voice_pitch", 0))

        async def synth():
            comm = edge_tts.Communicate(text, self.voice,
                                        rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz", volume=f"{volume:+d}%")
            data = bytearray()
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    data += chunk["data"]
            return bytes(data)

        mp3 = asyncio.run(asyncio.wait_for(synth(), timeout=15))
        decoded = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1, sample_rate=24000)
        return np.frombuffer(decoded.samples, dtype=np.float32), decoded.sample_rate

    def _play(self, audio, rate):
        import sounddevice as sd

        sd.play(audio, rate)
        duration = len(audio) / rate
        # wait in small steps so stop() can cut her off
        waited = 0.0
        while waited < duration + 0.1:
            if self._stop.wait(0.05):
                sd.stop()
                return
            waited += 0.05
        sd.wait()

    # system voice (pyttsx3)
    def _system_say(self, text: str, emotion: str):
        if self._system is None:
            import pyttsx3  # the engine must live on the thread that uses it
            self._system = pyttsx3.init()
            wanted = self.cfg.get("tts_voice", "").lower()
            if wanted:
                for v in self._system.getProperty("voices"):
                    if wanted in v.name.lower():
                        self._system.setProperty("voice", v.id)
                        break
        rate, volume = SYSTEM_STYLE.get(emotion, (0, 0.95))
        self._system.setProperty("rate", int(self.cfg.get("tts_rate", 180)) + rate)
        self._system.setProperty("volume", volume)
        self._system.say(text)
        self._system.runAndWait()
