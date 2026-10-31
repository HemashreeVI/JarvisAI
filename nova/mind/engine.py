"""Nova's long-term memory engine.

Every memory is a short sentence saved in a local SQLite file (data/mind.db)
with an importance score, a kind (fact, preference, goal, deadline, event,
study) and, if it has one, a real calendar date.

Finding the right memory uses two signals:
- meaning: an embedding from a small local Ollama model (nomic-embed-text),
  so "my database exam" finds "DBMS test on Friday"
- keywords: BM25 word matching, which still works if the embedding model is
  missing or Ollama is off

Memories also fade like real ones: strength drops with time unless the memory
is important, used often, pinned, or has a date that hasn't passed yet. Faded
memories are archived (hidden), never silently deleted.
"""
import math
import re
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np

from nova.mind.capture import extract
from nova.mind.dates import describe_due

KINDS = ("fact", "preference", "goal", "deadline", "event", "study", "note")
DAY = 86400.0
DUPLICATE_SIMILARITY = 0.92   # embeddings this close mean "the same memory"
ARCHIVE_BELOW = 0.05          # strength under which a memory is archived
STOP_WORDS = set("""a an the and or but if of to in on at for with from by is are was were be been am i me my you your
it its this that these those we our they them their he she his her do does did have has had will would can could
should just so very really about what when where who how which there here not no yes nova please""".split())

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    key TEXT,
    tags TEXT NOT NULL DEFAULT '',
    importance REAL NOT NULL DEFAULT 5,
    due TEXT,
    source TEXT NOT NULL DEFAULT 'chat',
    created REAL NOT NULL,
    last_used REAL NOT NULL,
    uses INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(key);
CREATE INDEX IF NOT EXISTS idx_memories_due ON memories(due);
"""


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in words:
        if w in STOP_WORDS or len(w) < 2:
            continue
        for suffix in ("ing", "ies", "es", "s"):  # tiny stemmer: exams -> exam, studying -> study
            if len(w) > len(suffix) + 3 and w.endswith(suffix):
                w = w[: -len(suffix)] + ("y" if suffix == "ies" else "")
                break
        out.append(w)
    return out


@dataclass
class MemoryItem:
    id: int
    text: str
    kind: str
    importance: float
    created: float
    last_used: float
    uses: int = 0
    key: str | None = None
    tags: list = field(default_factory=list)
    due: date | None = None
    source: str = "chat"
    pinned: bool = False
    archived: bool = False
    score: float = 0.0  # set by search

    def strength(self, now: float | None = None, fade_days: float = 30.0) -> float:
        """0-1: how alive this memory is. Important, used, pinned and upcoming memories stay strong."""
        if self.pinned:
            return 1.0
        now = now or time.time()
        base = self.importance / 10.0
        if self.due:
            days_left = (self.due - datetime.fromtimestamp(now).date()).days
            if days_left >= 0:
                return max(base, 0.9)
            # a passed date fades fast: after about a week it's mostly gone
            return base * 0.5 ** (-days_left / 3.0)
        half_life = fade_days * (1 + 0.5 * min(self.uses, 10)) * (0.5 + base)
        age_days = max(0.0, now - self.last_used) / DAY
        return base * 0.5 ** (age_days / half_life)

    def describe(self, today: date | None = None) -> str:
        """The memory as Nova should read it, with stale words like 'tomorrow' replaced by the real date."""
        if self.due:
            return f"{self.text} (that's {describe_due(self.due, today)}, {self.due:%d %b})"
        return self.text


class MemoryEngine:
    def __init__(self, path: str | Path, embedder=None, fade_days: float = 30.0):
        """embedder(text) -> list[float] | None. Without one, search uses keywords only."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self.fade_days = fade_days
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    # ---------- helpers ----------
    def _embed(self, text: str):
        if not self.embedder:
            return None
        try:
            vec = self.embedder(text)
        except Exception as e:  # embedding is a bonus; memory must keep working without it
            print("[memory] embedding failed:", e)
            return None
        if vec is None or len(vec) == 0:
            return None
        arr = np.asarray(vec, dtype=np.float32)
        norm = float(np.linalg.norm(arr))
        return arr / norm if norm else None

    @staticmethod
    def _row_to_item(row) -> MemoryItem:
        return MemoryItem(
            id=row["id"], text=row["text"], kind=row["kind"], importance=row["importance"],
            created=row["created"], last_used=row["last_used"], uses=row["uses"], key=row["key"],
            tags=[t for t in row["tags"].split(",") if t], source=row["source"],
            due=date.fromisoformat(row["due"]) if row["due"] else None,
            pinned=bool(row["pinned"]), archived=bool(row["archived"]),
        )

    def _rows(self, where: str = "archived = 0", params: tuple = ()):
        with self._lock:
            return self._db.execute(f"SELECT * FROM memories WHERE {where}", params).fetchall()

    # ---------- writing ----------
    def add(self, text: str, kind: str = "fact", importance: float = 5, key: str | None = None,
            due: date | None = None, tags=(), source: str = "chat", pinned: bool = False) -> MemoryItem:
        """Save a memory. A repeat of something already known strengthens it instead of duplicating it."""
        text = " ".join(text.split())
        if not text:
            raise ValueError("empty memory")
        kind = kind if kind in KINDS else "fact"
        importance = float(min(10, max(1, importance)))
        key = key.strip().lower() if key else None
        now = time.time()
        vec = self._embed(text)
        with self._lock:
            existing = self._find_duplicate(text, key, vec)
            if existing:
                merged_tags = sorted(set(existing.tags) | set(tags))
                self._db.execute(
                    "UPDATE memories SET text = ?, importance = ?, due = COALESCE(?, due), tags = ?, "
                    "last_used = ?, uses = uses + 1, archived = 0, pinned = MAX(pinned, ?), "
                    "embedding = COALESCE(?, embedding) WHERE id = ?",
                    (text, max(existing.importance, importance), due.isoformat() if due else None,
                     ",".join(merged_tags), now, int(pinned), vec.tobytes() if vec is not None else None,
                     existing.id))
                self._db.commit()
                return self.get(existing.id)
            cur = self._db.execute(
                "INSERT INTO memories (text, kind, key, tags, importance, due, source, created, last_used, "
                "pinned, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (text, kind, key, ",".join(sorted(set(tags))), importance, due.isoformat() if due else None,
                 source, now, now, int(pinned), vec.tobytes() if vec is not None else None))
            self._db.commit()
            return self.get(cur.lastrowid)

    def _find_duplicate(self, text: str, key: str | None, vec) -> MemoryItem | None:
        if key:
            rows = self._rows("key = ?", (key,))
            if rows:
                return self._row_to_item(rows[0])
        wanted = " ".join(tokenize(text))
        best, best_sim = None, 0.0
        for row in self._rows("archived = 0"):
            if wanted and " ".join(tokenize(row["text"])) == wanted:
                return self._row_to_item(row)
            if vec is not None and row["embedding"] is not None:
                other = np.frombuffer(row["embedding"], dtype=np.float32)
                if other.shape == vec.shape:
                    sim = float(other @ vec)
                    if sim > best_sim:
                        best, best_sim = row, sim
        if best is not None and best_sim >= DUPLICATE_SIMILARITY:
            return self._row_to_item(best)
        return None

    def capture(self, user_text: str, now: datetime | None = None) -> list[MemoryItem]:
        """Save anything worth remembering from one thing the user said."""
        saved = []
        for c in extract(user_text, now):
            try:
                saved.append(self.add(c.text, c.kind, c.importance, due=c.due, tags=c.tags, source="auto"))
            except ValueError:
                pass
        return saved

    def update(self, memory_id: int, **changes) -> MemoryItem | None:
        allowed = {"text", "kind", "importance", "pinned", "archived", "due", "tags"}
        sets, params = [], []
        for name, value in changes.items():
            if name not in allowed:
                raise ValueError(f"can't change {name}")
            if name == "due":
                value = value.isoformat() if value else None
            elif name == "tags":
                value = ",".join(sorted(set(value)))
            elif name in ("pinned", "archived"):
                value = int(bool(value))
            sets.append(f"{name} = ?")
            params.append(value)
        if "text" in changes:
            vec = self._embed(changes["text"])
            sets.append("embedding = ?")
            params.append(vec.tobytes() if vec is not None else None)
        if not sets:
            return self.get(memory_id)
        with self._lock:
            self._db.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", (*params, memory_id))
            self._db.commit()
        return self.get(memory_id)

    def archive(self, memory_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE memories SET archived = 1 WHERE id = ? AND archived = 0", (memory_id,))
            self._db.commit()
            return cur.rowcount > 0

    def archive_key(self, key: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE memories SET archived = 1 WHERE key = ? AND archived = 0",
                                   (key.strip().lower(),))
            self._db.commit()
            return cur.rowcount > 0

    def delete(self, memory_id: int) -> bool:
        """Gone for good (the memory screen's delete button)."""
        with self._lock:
            cur = self._db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._db.commit()
            return cur.rowcount > 0

    def touch(self, ids):
        """Using a memory makes it stronger, like recalling something helps you keep it."""
        ids = list(ids)
        if not ids:
            return
        with self._lock:
            self._db.executemany("UPDATE memories SET uses = uses + 1, last_used = ? WHERE id = ?",
                                 [(time.time(), i) for i in ids])
            self._db.commit()

    def sweep(self, now: float | None = None) -> int:
        """Archive memories that have faded away. Returns how many."""
        now = now or time.time()
        faded = [m.id for m in self.all() if m.strength(now, self.fade_days) < ARCHIVE_BELOW]
        with self._lock:
            self._db.executemany("UPDATE memories SET archived = 1 WHERE id = ?", [(i,) for i in faded])
            self._db.commit()
        return len(faded)

    # ---------- reading ----------
    def get(self, memory_id: int) -> MemoryItem | None:
        rows = self._rows("id = ?", (memory_id,))
        return self._row_to_item(rows[0]) if rows else None

    def all(self, include_archived: bool = False) -> list[MemoryItem]:
        rows = self._rows("1 = 1" if include_archived else "archived = 0")
        return [self._row_to_item(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM memories WHERE archived = 0").fetchone()[0]

    def upcoming(self, days: int = 7, today: date | None = None) -> list[MemoryItem]:
        """Dated memories (exams, deadlines, birthdays) from today up to `days` ahead, soonest first."""
        today = today or date.today()
        items = [m for m in self.all() if m.due and 0 <= (m.due - today).days <= days]
        return sorted(items, key=lambda m: (m.due, -m.importance))

    def search(self, query: str, limit: int = 5, min_score: float = 0.15, touch: bool = True) -> list[MemoryItem]:
        """The memories most related to query, best first."""
        rows = self._rows("archived = 0")
        if not rows or not query.strip():
            return []
        q_tokens = tokenize(query)
        docs = [tokenize(r["text"] + " " + r["tags"].replace(",", " ")) for r in rows]
        keyword = _bm25(q_tokens, docs)
        top_kw = max(keyword) if keyword and max(keyword) > 0 else 1.0
        q_vec = self._embed(query)
        now = time.time()

        results = []
        for row, kw in zip(rows, keyword):
            kw_score = kw / top_kw
            sem = None
            if q_vec is not None and row["embedding"] is not None:
                other = np.frombuffer(row["embedding"], dtype=np.float32)
                if other.shape == q_vec.shape:
                    # nomic-style embeddings rarely go below ~0.3 for unrelated text, so rescale
                    sem = max(0.0, (float(other @ q_vec) - 0.35) / 0.65)
            relevance = kw_score if sem is None else 0.65 * sem + 0.35 * kw_score
            if kw == 0 and (sem is None or sem < 0.35):
                continue  # no shared words and not close in meaning
            item = self._row_to_item(row)
            item.score = relevance * (0.7 + 0.3 * item.strength(now, self.fade_days))
            if item.score >= min_score:
                results.append(item)
        results.sort(key=lambda m: m.score, reverse=True)
        results = results[:limit]
        if touch:
            self.touch(m.id for m in results)
        return results

    def context_for(self, message: str, limit: int = 4, today: date | None = None) -> str:
        """A short note for the brain: what Nova remembers that matters for this message."""
        lines, seen = [], set()
        for m in self.upcoming(3, today):
            lines.append(m.describe(today))
            seen.add(m.id)
        for m in self.search(message, limit=limit):
            if m.id not in seen:
                lines.append(m.describe(today))
                seen.add(m.id)
        return "; ".join(lines[: limit + 2])

    def import_facts(self, facts: dict) -> int:
        """Bring old key: value facts from memory.json into the engine (runs once; repeats are no-ops)."""
        added = 0
        for k, v in facts.items():
            if not self._rows("key = ?", (k.strip().lower(),)):
                self.add(f"{k}: {v}", "fact", 7, key=k, source="imported")
                added += 1
        return added


def _bm25(query: list[str], docs: list[list[str]], k1: float = 1.4, b: float = 0.75) -> list[float]:
    if not query or not docs:
        return [0.0] * len(docs)
    n = len(docs)
    avg_len = sum(len(d) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in set(d))
    scores = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for t in set(query):
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg_len))
        scores.append(s)
    return scores


class OllamaEmbedder:
    """Embeddings from a local Ollama model. If it's missing or Ollama is off, it pauses for a few
    minutes (keyword search still works) and tries again, printing the setup tip only once."""

    RETRY_AFTER = 300.0

    def __init__(self, host: str, model: str = "nomic-embed-text", client=None):
        self.model = model
        self.enabled = bool(model)
        self._client = client
        self._host = host
        self._cache: dict[str, list] = {}
        self._paused_until = 0.0
        self._warned = False

    def __call__(self, text: str):
        if not self.enabled or time.time() < self._paused_until:
            return None
        if text in self._cache:
            return self._cache[text]
        try:
            if self._client is None:
                from ollama import Client
                self._client = Client(host=self._host, timeout=20)
            vec = self._client.embed(model=self.model, input=text)["embeddings"][0]
        except Exception as e:
            self._paused_until = time.time() + self.RETRY_AFTER
            if not self._warned:
                self._warned = True
                print(f"[memory] smart search paused ({e}). For it, run:  ollama pull {self.model}")
            return None
        if len(self._cache) > 256:
            self._cache.clear()
        self._cache[text] = vec
        return vec
