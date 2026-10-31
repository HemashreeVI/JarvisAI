"""Nova's long-term memory: facts about you, chat history and a mood diary."""
import json
import threading
from datetime import datetime
from pathlib import Path

MAX_HISTORY = 60
MAX_MOODS = 300


class Memory:
    def __init__(self, data_dir: Path, user_name: str = "friend", mind=None):
        """mind: an optional nova.mind.MemoryEngine for long-term, searchable memories."""
        self.path = Path(data_dir) / "memory.json"
        self.mind = mind
        self._lock = threading.Lock()
        self.data = {"user_name": user_name, "facts": {}, "history": [], "moods": []}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass  # corrupted file: start fresh rather than crash
        self.data["user_name"] = user_name or self.data["user_name"]
        if self.mind is not None and self.data["facts"]:
            self.mind.import_facts(self.data["facts"])

    def save(self):
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    @property
    def user_name(self) -> str:
        return self.data["user_name"]

    # facts
    def remember(self, key: str, value: str):
        self.data["facts"][key.strip().lower()] = value.strip()
        self.save()
        if self.mind is not None:
            self.mind.add(f"{key.strip()}: {value.strip()}", "fact", 7, key=key, source="told")

    def recall(self, key: str):
        return self.data["facts"].get(key.strip().lower())

    def forget(self, key: str) -> bool:
        removed = self.data["facts"].pop(key.strip().lower(), None) is not None
        self.save()
        if self.mind is not None:
            removed = self.mind.archive_key(key) or removed
        return removed

    @property
    def facts(self) -> dict:
        return dict(self.data["facts"])

    # conversation
    def add_message(self, role: str, content: str):
        self.data["history"].append(
            {"role": role, "content": content, "time": datetime.now().isoformat(timespec="seconds")}
        )
        self.data["history"] = self.data["history"][-MAX_HISTORY:]
        self.save()

    def recent_history(self, turns: int) -> list[dict]:
        return [{"role": m["role"], "content": m["content"]} for m in self.data["history"][-turns:]]

    # mood diary
    def log_mood(self, emotion: str, intensity: float):
        self.data["moods"].append(
            {"emotion": emotion, "intensity": round(intensity, 2),
             "time": datetime.now().isoformat(timespec="seconds")}
        )
        self.data["moods"] = self.data["moods"][-MAX_MOODS:]
        self.save()

    def last_mood(self):
        return self.data["moods"][-1] if self.data["moods"] else None
