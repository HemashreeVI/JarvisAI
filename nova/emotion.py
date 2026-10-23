"""How Nova senses your mood and decides how to feel and behave in return."""
import re
from dataclasses import dataclass

from nova.voice_tone import ToneReading

USER_EMOTIONS = ["happy", "excited", "neutral", "sad", "stressed", "angry", "tired", "surprised", "affectionate"]
NOVA_EMOTIONS = ["happy", "excited", "neutral", "cute", "love", "caring", "concerned", "sad", "angry",
                 "sleepy", "surprised", "thinking"]

# How Nova responds to each of your moods: (her expression, how she should talk)
EMPATHY = {
    "happy": ("happy", "They're in a good mood - match it! Be warm, playful and a little bubbly."),
    "excited": ("excited", "They're excited - share it! Be energetic and enthusiastic, celebrate with them."),
    "neutral": ("neutral", "Be friendly, helpful and to the point."),
    "sad": ("caring", "They seem sad. Be gentle and soft: acknowledge the feeling first, comfort them, "
                      "no forced cheerfulness, no jokes. Short, kind sentences. Offer to help or just listen."),
    "stressed": ("caring", "They seem stressed. Be calm and reassuring. Break things into small steps, "
                           "keep replies short and steady, maybe suggest a short break."),
    "angry": ("concerned", "They seem frustrated or angry. Stay calm and patient - never argue, never joke. "
                           "Acknowledge the frustration briefly, then focus on fixing the problem."),
    "tired": ("sleepy", "They seem tired. Speak softly and briefly. Gently suggest rest if it fits."),
    "surprised": ("surprised", "They're surprised - react with curiosity and wonder."),
    "affectionate": ("cute", "They're being sweet to you - be touched, a little shy, and sweet back."),
}

# Fallback when the model can't be reached for classification.
LEXICON = {
    "happy": ["happy", "great", "awesome", "good", "nice", "glad", "yay", "love it", "thanks", "thank you", "haha", "lol", ":)"],
    "excited": ["excited", "can't wait", "omg", "amazing", "finally", "got selected", "got the job", "wow", "!!"],
    "sad": ["sad", "upset", "cry", "lonely", "depressed", "miss", "hurt", "bad day", "failed", "heartbroken", ":("],
    "stressed": ["stressed", "anxious", "worried", "nervous", "deadline", "exam", "pressure", "overwhelmed", "panic", "too much"],
    "angry": ["angry", "annoyed", "irritated", "hate", "stupid", "useless", "furious", "wtf", "not working", "again?!"],
    "tired": ["tired", "sleepy", "exhausted", "drained", "no energy", "yawn", "can't sleep"],
    "surprised": ["what?!", "really?", "no way", "seriously", "unbelievable"],
    "affectionate": ["love you", "you're the best", "cute", "good girl", "proud of you", "my bestie"],
}
VALENCE = {"happy": 0.7, "excited": 0.8, "neutral": 0.0, "sad": -0.7, "stressed": -0.6,
           "angry": -0.8, "tired": -0.3, "surprised": 0.1, "affectionate": 0.9}

CLASSIFY_PROMPT = (
    "You are an emotion detector. Read the user's latest message (with a little prior context) "
    "and decide how the USER feels right now. Pick one emotion from: " + ", ".join(USER_EMOTIONS) + ". "
    "intensity: 0.0 (barely) to 1.0 (very strongly). valence: -1.0 (very negative) to 1.0 (very positive). "
    "Plain requests like 'open chrome' are neutral with low intensity."
)
CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "emotion": {"type": "string", "enum": USER_EMOTIONS},
        "intensity": {"type": "number"},
        "valence": {"type": "number"},
    },
    "required": ["emotion", "intensity", "valence"],
}


@dataclass
class EmotionReading:
    emotion: str
    intensity: float
    valence: float
    source: str = "text"
    tone: ToneReading | None = None

    @property
    def nova_emotion(self) -> str:
        return EMPATHY[self.emotion][0]

    @property
    def guidance(self) -> str:
        return EMPATHY[self.emotion][1]

    def describe(self) -> str:
        level = "slightly" if self.intensity < 0.4 else "quite" if self.intensity < 0.75 else "very"
        text = f"{level} {self.emotion}" if self.emotion != "neutral" else "neutral / calm"
        if self.tone and self.tone.confidence >= 0.2:
            text += f" (voice sounds {self.tone.describe()})"
        return text


def lexicon_classify(text: str) -> EmotionReading:
    lowered = text.lower()
    scores = {emo: sum(lowered.count(w) for w in words) for emo, words in LEXICON.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return EmotionReading("neutral", 0.2, 0.0, "lexicon")
    intensity = min(1.0, 0.35 + 0.2 * scores[best] + 0.1 * len(re.findall(r"!", text)))
    return EmotionReading(best, intensity, VALENCE[best], "lexicon")


def fuse(text: EmotionReading, tone: ToneReading | None) -> EmotionReading:
    """Combine what you said (valence) with how you sounded (arousal)."""
    if tone is None or tone.confidence < 0.2:
        return text
    a, emo, intensity = tone.arousal, text.emotion, text.intensity
    if intensity < 0.45:  # words don't say much, so trust the voice more
        if a > 0.5:
            emo = "excited" if text.valence >= 0 else "stressed"
        elif a < -0.5:
            emo = "tired" if text.valence > -0.3 else "sad"
        if emo != text.emotion:
            intensity = 0.3 + 0.4 * abs(a) * tone.confidence
    else:  # words are clear; voice refines the flavour and intensity
        if emo == "happy" and a > 0.5:
            emo = "excited"
        elif emo == "excited" and a < -0.4:
            emo = "happy"
        elif emo == "sad" and a > 0.6:
            emo = "stressed"
        intensity = intensity + 0.25 * abs(a) * tone.confidence
    return EmotionReading(emo, round(min(1.0, intensity), 2), text.valence, "text+voice", tone)


class EmotionEngine:
    """Tracks your mood across messages, so one short reply doesn't erase how you feel."""

    def __init__(self, classify_fn=None):
        self.classify_fn = classify_fn  # (text, context) -> dict | None, from the brain
        self.current = EmotionReading("neutral", 0.0, 0.0)

    def classify_text(self, text: str, context: str = "") -> EmotionReading:
        if self.classify_fn:
            try:
                result = self.classify_fn(text, context)
                if result and result.get("emotion") in USER_EMOTIONS:
                    return EmotionReading(
                        result["emotion"],
                        float(max(0.0, min(1.0, result.get("intensity", 0.5)))),
                        float(max(-1.0, min(1.0, result.get("valence", 0.0)))),
                    )
            except Exception:
                pass
        return lexicon_classify(text)

    def assess(self, text: str, tone: ToneReading | None = None, context: str = "") -> EmotionReading:
        reading = fuse(self.classify_text(text, context), tone)
        prev = self.current
        # A weak neutral reading right after a strong mood probably means the mood lingers.
        if reading.emotion == "neutral" and reading.intensity < 0.4 and prev.emotion != "neutral" and prev.intensity > 0.4:
            reading = EmotionReading(prev.emotion, round(prev.intensity * 0.7, 2), prev.valence, "carried over", tone)
        self.current = reading
        return reading
