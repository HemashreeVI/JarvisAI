"""Reads emotional *arousal* (calm <-> energetic) from how you sound.

Words tell Nova whether you feel good or bad; your voice tells her how intense it is.
Features are compared against YOUR normal voice, which Nova learns over time
(saved in data/voice_baseline.json), so a naturally loud or soft voice isn't
mistaken for a mood.
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Starting guesses for an average speaker, replaced by your own baseline as Nova learns.
POPULATION = {
    "energy_db": (-28.0, 6.0),   # loudness of voiced speech (dBFS)
    "pitch_hz": (180.0, 45.0),   # median pitch
    "pitch_var": (25.0, 12.0),   # pitch movement (monotone vs expressive)
    "rate_wps": (2.6, 0.7),      # words per second
}
WEIGHTS = {"energy_db": 0.4, "pitch_hz": 0.25, "pitch_var": 0.2, "rate_wps": 0.15}
LEARN_RATE = 0.1


@dataclass
class ToneReading:
    arousal: float        # -1 (flat, tired, low) .. +1 (energetic, agitated)
    confidence: float     # 0..1, low until Nova knows your voice
    features: dict

    def describe(self) -> str:
        if self.confidence < 0.2:
            return "unclear"
        if self.arousal > 0.5:
            return "very energetic / tense"
        if self.arousal > 0.2:
            return "lively"
        if self.arousal < -0.5:
            return "very flat / low energy"
        if self.arousal < -0.2:
            return "quiet / subdued"
        return "normal"


def _frame_pitch(frame: np.ndarray, sr: int) -> float | None:
    """Autocorrelation pitch estimate for one frame, or None if unvoiced."""
    frame = frame - frame.mean()
    if not frame.any():
        return None
    corr = np.correlate(frame, frame, mode="full")[len(frame) - 1:]
    lo, hi = int(sr / 400), int(sr / 75)  # search 75-400 Hz
    if hi >= len(corr):
        return None
    lag = lo + int(np.argmax(corr[lo:hi]))
    if corr[lag] < 0.3 * corr[0]:  # weak periodicity: noise or unvoiced sound
        return None
    return sr / lag


def extract_features(audio: np.ndarray, sr: int, transcript: str) -> dict | None:
    audio = np.asarray(audio, dtype=np.float32).flatten()
    if len(audio) < sr * 0.4:
        return None
    frame_len = int(sr * 0.04)
    frames = [audio[i:i + frame_len] for i in range(0, len(audio) - frame_len, frame_len)]
    rms = np.array([math.sqrt(float(np.mean(f ** 2)) + 1e-12) for f in frames])
    voiced_mask = rms > max(rms.max() * 0.1, 1e-4)
    voiced = [f for f, v in zip(frames, voiced_mask) if v]
    if len(voiced) < 5:
        return None

    pitches = [p for p in (_frame_pitch(f, sr) for f in voiced) if p]
    speech_seconds = max(len(voiced) * 0.04, 0.5)
    words = len(transcript.split())
    return {
        "energy_db": float(20 * np.log10(np.mean(rms[voiced_mask]) + 1e-9)),
        "pitch_hz": float(np.median(pitches)) if len(pitches) >= 3 else None,
        "pitch_var": float(np.std(pitches)) if len(pitches) >= 3 else None,
        "rate_wps": words / speech_seconds if words else None,
    }


class VoiceToneAnalyzer:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "voice_baseline.json"
        self.baseline = {k: {"mean": m, "std": s} for k, (m, s) in POPULATION.items()}
        self.samples = 0
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                self.baseline.update(saved["baseline"])
                self.samples = saved["samples"]
            except (OSError, KeyError, json.JSONDecodeError):
                pass

    def analyze(self, audio: np.ndarray, sr: int, transcript: str) -> ToneReading | None:
        feats = extract_features(audio, sr, transcript)
        if feats is None:
            return None
        score, weight = 0.0, 0.0
        for key, value in feats.items():
            if value is None:
                continue
            b = self.baseline[key]
            z = (value - b["mean"]) / max(b["std"], 1e-6)
            score += WEIGHTS[key] * max(-3.0, min(3.0, z))
            weight += WEIGHTS[key]
        arousal = math.tanh(score / weight) if weight else 0.0
        # Confidence: how well Nova knows your voice x how much of the signal we got.
        confidence = min(1.0, 0.3 + self.samples / 15) * weight
        self._learn(feats)
        return ToneReading(arousal=arousal, confidence=round(confidence, 2), features=feats)

    def _learn(self, feats: dict):
        """Slowly adapt the baseline toward your voice (exponential moving average)."""
        for key, value in feats.items():
            if value is None:
                continue
            b = self.baseline[key]
            diff = value - b["mean"]
            b["mean"] += LEARN_RATE * diff
            b["std"] = max(math.sqrt((1 - LEARN_RATE) * (b["std"] ** 2 + LEARN_RATE * diff ** 2)),
                           POPULATION[key][1] * 0.3)
        self.samples += 1
        try:
            self.path.write_text(json.dumps({"baseline": self.baseline, "samples": self.samples}, indent=2))
        except OSError:
            pass
