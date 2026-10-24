"""Loads settings.toml and merges it over safe defaults."""
import copy
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SETTINGS_FILE = ROOT / "settings.toml"

DEFAULTS = {
    "user": {"name": "friend", "city": "Chennai", "news_region": "IN"},
    "brain": {
        "model": "qwen2.5:7b",
        "host": "http://localhost:11434",
        "temperature": 0.7,
        "history_turns": 10,
    },
    "voice": {
        "enabled": True,
        "stt_model": "base.en",
        "wake_word": True,
        "wake_words": ["nova", "hey nova"],
        "engine": "neural",
        "neural_voice": "en-US-AvaNeural",
        "voice_pitch": 0,
        "voice_speed": 0,
        "tts_rate": 180,
        "tts_voice": "",
        "mic_sensitivity": 1.0,
        "hotkey": "<ctrl>+<alt>+n",
    },
    "pet": {"name": "Pip"},
    "emotion": {"use_voice_tone": True},
    "companion": {},
    "buddy": {"style": "3d", "size": 70, "corner": "bottom-right"},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings(path: Path = SETTINGS_FILE) -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    with open(path, "rb") as f:
        return _merge(DEFAULTS, tomllib.load(f))
