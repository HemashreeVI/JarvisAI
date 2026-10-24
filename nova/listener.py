"""Microphone input: offline speech-to-text (faster-whisper) and the "Hey Nova" wake word."""
import queue
import re
import threading
import time

import numpy as np

SAMPLE_RATE = 16000
BLOCK = 480                  # 30 ms
SILENCE_TO_STOP = 0.9        # seconds of quiet that ends an utterance
MAX_UTTERANCE = 20.0


class Listener:
    def __init__(self, settings: dict):
        import sounddevice  # noqa: F401  (fail early if missing)
        from faster_whisper import WhisperModel

        self.cfg = settings["voice"]
        self.model = WhisperModel(self.cfg["stt_model"], device="cpu", compute_type="int8")
        self.wake_words = sorted((w.lower() for w in self.cfg["wake_words"]), key=len, reverse=True)
        self.sensitivity = float(self.cfg.get("mic_sensitivity", 1.0))
        self.noise_floor = 0.004
        self.last_peak = 0.0       # loudest sound in the last recording (for troubleshooting)
        self.last_threshold = 0.0
        self.paused = threading.Event()      # set while Nova is speaking, so she doesn't hear herself
        self._manual = threading.Event()     # "listen now" (click / hotkey)
        self.wake_word_allowed = lambda: True  # False during quiet mode

    # audio capture
    def _record_utterance(self, wait_for_speech: float | None) -> np.ndarray | None:
        """Record from the first speech until a pause. None if nobody spoke in time."""
        import sounddevice as sd

        q: queue.Queue = queue.Queue()
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK, dtype="float32",
                            callback=lambda data, *_: q.put(data.copy())):
            chunks, started, silent_for, start = [], False, 0.0, time.time()
            self.last_peak = 0.0
            pre_roll: list = []
            while True:
                block = q.get()[:, 0]
                level = float(np.sqrt(np.mean(block ** 2))) * self.sensitivity
                if not started:
                    self.noise_floor = 0.95 * self.noise_floor + 0.05 * min(level, 0.05)
                threshold = max(self.noise_floor * 2.5, 0.006)
                is_speech = level > threshold
                self.last_peak = max(self.last_peak, level)
                self.last_threshold = threshold
                if self.paused.is_set():
                    return None
                if not started:
                    pre_roll = (pre_roll + [block])[-10:]  # keep 300 ms before speech starts
                    if is_speech:
                        started, chunks = True, pre_roll[:]
                    elif wait_for_speech is not None and time.time() - start > wait_for_speech:
                        return None
                    elif self._manual.is_set() and wait_for_speech is None:
                        return None  # user clicked to talk; switch to the manual path
                    continue
                chunks.append(block)
                silent_for = 0.0 if is_speech else silent_for + BLOCK / SAMPLE_RATE
                if silent_for > SILENCE_TO_STOP or len(chunks) * BLOCK / SAMPLE_RATE > MAX_UTTERANCE:
                    audio = np.concatenate(chunks)
                    return audio if len(audio) > SAMPLE_RATE * 0.35 else None

    def diagnosis(self) -> str:
        """Why nothing was heard, in plain words."""
        peak, need = self.last_peak, self.last_threshold
        if peak < 0.001:
            return (" - the microphone sent silence. Check Windows Settings > Privacy & security > Microphone "
                    "(allow desktop apps) and that the right mic is the default input.")
        if peak < need:
            return f" - it was too quiet (loudest {peak:.3f}, needed {need:.3f}). Speak closer, or raise mic_sensitivity in settings.toml."
        return " - I heard sound but no clear words. Try again a little slower."

    @staticmethod
    def mic_name() -> str:
        try:
            import sounddevice as sd
            return sd.query_devices(kind="input")["name"]
        except Exception:
            return "unknown"

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(audio, language="en", beam_size=1, vad_filter=True)
        text = " ".join(s.text for s in segments).strip()
        # Whisper hallucinates these on silence/noise
        if re.fullmatch(r"(thanks? (you )?(for watching)?|you|bye)[.!]?", text.lower()):
            return ""
        return text

    def strip_wake_word(self, text: str) -> str | None:
        """Return the command after the wake word, '' for the wake word alone, None if absent."""
        lowered = re.sub(r"[^\w\s]", "", text.lower()).strip()
        for w in self.wake_words:
            idx = lowered.find(w)
            if idx != -1 and idx <= 12:  # must be near the start
                return lowered[idx + len(w):].strip()
        return None

    # public API
    def listen_now(self):
        """Called on click/hotkey: the next utterance is a command (no wake word needed)."""
        self._manual.set()

    def run(self, on_wake, on_command, on_state):
        """Loop forever. on_command(text, audio) for each command heard."""
        while True:
            if self.paused.is_set():
                time.sleep(0.1)
                continue
            if self._manual.is_set():
                self._manual.clear()
                on_state("listening")
                audio = self._record_utterance(wait_for_speech=7.0)
                on_state("idle")
                text = self.transcribe(audio) if audio is not None else ""
                if text:
                    on_state(f"heard:{text}")
                    on_command(text, audio)
                else:
                    on_state("missed")
                continue
            if not self.cfg.get("wake_word", True) or not self.wake_word_allowed():
                time.sleep(0.1)
                continue
            audio = self._record_utterance(wait_for_speech=None)
            if audio is None or len(audio) > SAMPLE_RATE * 8:
                continue  # long chatter without "Nova" isn't for us
            text = self.transcribe(audio)
            command = self.strip_wake_word(text) if text else None
            if command is None:
                continue
            if command:
                on_command(command, audio)
            else:
                on_wake()  # just "Hey Nova" -> listen for the actual request
                self._manual.set()
