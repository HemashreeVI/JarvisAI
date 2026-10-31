"""Tests for Nova's long-term memory. Run with:  python -m pytest tests"""
import time
import zlib
from datetime import date, datetime, timedelta

import numpy as np
import pytest

from nova.memory import Memory
from nova.mind import MemoryEngine
from nova.mind.capture import extract
from nova.mind.dates import describe_due, parse_date
from nova.mind.engine import DAY, tokenize

NOW = datetime(2026, 11, 2, 10, 0)  # a Monday
TODAY = NOW.date()

# words that mean the same thing share a slot, so this fake embedder "understands" meaning a little
SYNONYMS = {"database": "dbms", "db": "dbms", "test": "exam", "exams": "exam", "food": "eat", "dish": "eat"}


def fake_embedder(text: str):
    vec = np.zeros(256, dtype=np.float32)
    for word in tokenize(text):
        word = SYNONYMS.get(word, word)
        vec[zlib.crc32(word.encode()) % 256] += 1.0
    return vec if vec.any() else None


@pytest.fixture
def engine(tmp_path):
    e = MemoryEngine(tmp_path / "mind.db", embedder=fake_embedder)
    yield e
    e.close()


# ---------- dates ----------
@pytest.mark.parametrize("text, expected", [
    ("my exam is tomorrow", TODAY + timedelta(days=1)),
    ("it's due today", TODAY),
    ("day after tomorrow is the viva", TODAY + timedelta(days=2)),
    ("dbms exam on friday", date(2026, 11, 6)),
    ("lab next monday", date(2026, 11, 9)),
    ("lab on monday", TODAY),
    ("submission on 12 nov", date(2026, 11, 12)),
    ("submission on 12th November", date(2026, 11, 12)),
    ("hackathon on Nov 20", date(2026, 11, 20)),
    ("review on 5/1", date(2027, 1, 5)),        # day/month, already passed this year -> next year
    ("project due 2026-12-01", date(2026, 12, 1)),
    ("interview in 3 days", TODAY + timedelta(days=3)),
    ("trip in two weeks", TODAY + timedelta(days=14)),
    ("i like dosa", None),
])
def test_parse_date(text, expected):
    assert parse_date(text, NOW) == expected


def test_describe_due():
    assert describe_due(TODAY, TODAY) == "today"
    assert describe_due(TODAY + timedelta(days=1), TODAY) == "tomorrow"
    assert describe_due(date(2026, 11, 6), TODAY) == "on Friday 6 Nov"
    assert describe_due(TODAY - timedelta(days=3), TODAY) == "3 days ago"


# ---------- what gets captured ----------
def test_capture_deadline_with_date():
    [c] = extract("My DBMS exam is on Friday", NOW)
    assert c.kind == "deadline" and c.due == date(2026, 11, 6) and "exam" in c.tags


def test_capture_course_code_tag():
    [c] = extract("CSE2004 assignment is due tomorrow", NOW)
    assert "CSE2004" in c.tags and c.due == TODAY + timedelta(days=1)


@pytest.mark.parametrize("text, kind", [
    ("I love masala dosa", "preference"),
    ("My favourite subject is operating systems", "preference"),
    ("I want to learn machine learning this semester", "goal"),
    ("I study computer science at VIT", "fact"),
    ("My sister's name is Priya", "fact"),
])
def test_capture_kinds(text, kind):
    [c] = extract(text, NOW)
    assert c.kind == kind


@pytest.mark.parametrize("text", [
    "When is my exam?", "what time is it", "open chrome", "hi nova", "thanks!", "play some music",
    "The weather is nice", "ok",
])
def test_capture_skips_questions_commands_and_small_talk(text):
    assert extract(text, NOW) == []


# ---------- engine ----------
def test_add_and_search_by_keyword(tmp_path):
    e = MemoryEngine(tmp_path / "m.db")  # no embedder: keywords only
    e.add("My DBMS exam is on Friday", "deadline", 8)
    e.add("I love masala dosa", "preference", 5)
    hits = e.search("dbms exam")
    assert hits and hits[0].text.startswith("My DBMS exam")
    assert e.search("cricket") == []


def test_search_by_meaning(engine):
    engine.add("My DBMS exam is on Friday", "deadline", 8)
    engine.add("I love masala dosa", "preference", 5)
    hits = engine.search("database test")  # no shared words with the memory
    assert hits and "DBMS" in hits[0].text


def test_repeat_strengthens_instead_of_duplicating(engine):
    a = engine.add("I love masala dosa", "preference", 5)
    b = engine.add("I love masala dosa!", "preference", 6)
    assert a.id == b.id and engine.count() == 1
    assert b.uses == 1 and b.importance == 6


def test_same_key_updates_fact(engine):
    engine.add("birthday: 3 March", key="Birthday")
    engine.add("birthday: 4 March", key="birthday")
    [m] = engine.all()
    assert m.text == "birthday: 4 March"


def test_upcoming_sorted_and_windowed(engine):
    engine.add("OS quiz", "deadline", 8, due=TODAY + timedelta(days=5))
    engine.add("DBMS exam", "deadline", 8, due=TODAY + timedelta(days=1))
    engine.add("old lab", "deadline", 8, due=TODAY - timedelta(days=1))
    engine.add("far trip", "event", 6, due=TODAY + timedelta(days=30))
    assert [m.text for m in engine.upcoming(7, TODAY)] == ["DBMS exam", "OS quiz"]


def test_fading_and_sweep(engine):
    weak = engine.add("I watched a movie", "fact", 2)
    strong = engine.add("My name is Hema", "fact", 9)
    pinned = engine.add("Allergic to peanuts", "fact", 2, pinned=True)
    soon = engine.add("Exam", "deadline", 3, due=date.today() + timedelta(days=2))
    later = time.time() + 120 * DAY
    assert weak.strength(later) < 0.05
    assert pinned.strength(later) == 1.0
    assert soon.strength() >= 0.9
    engine._db.execute("UPDATE memories SET last_used = last_used - ? WHERE id IN (?, ?)",
                       (120 * DAY, weak.id, strong.id))
    assert engine.sweep() == 1
    remaining = {m.id for m in engine.all()}
    assert weak.id not in remaining and {strong.id, pinned.id, soon.id} <= remaining
    assert engine.get(weak.id).archived  # archived, not deleted


def test_passed_deadline_fades():
    from nova.mind.engine import MemoryItem
    m = MemoryItem(1, "exam", "deadline", 8, time.time(), time.time(), due=date.today() - timedelta(days=10))
    assert m.strength() < 0.1


def test_using_a_memory_strengthens_it(engine):
    m = engine.add("I love masala dosa", "preference", 5)
    engine.search("dosa")
    assert engine.get(m.id).uses == 1


def test_capture_saves_with_real_date(engine):
    [m] = engine.capture("My DBMS exam is tomorrow", NOW)
    assert m.due == TODAY + timedelta(days=1)
    assert "(that's tomorrow" in m.describe(TODAY)


def test_context_for_includes_upcoming_and_related(engine):
    engine.add("DBMS exam", "deadline", 8, due=TODAY + timedelta(days=1))
    engine.add("I love masala dosa", "preference", 5)
    note = engine.context_for("what should I eat for dinner, maybe dosa?", today=TODAY)
    assert "DBMS exam" in note and "dosa" in note


def test_archive_update_delete(engine):
    m = engine.add("I love masala dosa", "preference", 5)
    assert engine.update(m.id, importance=9, pinned=True).pinned
    assert engine.archive(m.id) and engine.count() == 0
    assert engine.delete(m.id) and engine.get(m.id) is None


def test_embedder_failure_falls_back_to_keywords(tmp_path):
    def broken(text):
        raise ConnectionError("ollama is off")
    e = MemoryEngine(tmp_path / "m.db", embedder=broken)
    e.add("My DBMS exam is on Friday", "deadline", 8)
    assert e.search("DBMS")


def test_survives_restart(tmp_path):
    e = MemoryEngine(tmp_path / "m.db")
    e.add("I love masala dosa", "preference", 5)
    e.close()
    assert MemoryEngine(tmp_path / "m.db").search("dosa")


# ---------- the old Memory class ----------
def test_memory_imports_old_facts_once(tmp_path):
    Memory(tmp_path, "Hema").remember("birthday", "3 March")  # old style, no engine
    mind = MemoryEngine(tmp_path / "mind.db")
    Memory(tmp_path, "Hema", mind=mind)
    Memory(tmp_path, "Hema", mind=mind)
    assert [m.text for m in mind.all()] == ["birthday: 3 March"]


def test_memory_remember_and_forget_sync(tmp_path):
    mind = MemoryEngine(tmp_path / "mind.db")
    mem = Memory(tmp_path, "Hema", mind=mind)
    mem.remember("favourite food", "dosa")
    assert mind.search("food")
    assert mem.forget("favourite food")
    assert mind.count() == 0


# ---------- the brain uses it ----------
class FakeClient:
    """Pretends to be Ollama: records what it was sent and replies with a fixed line."""

    def __init__(self):
        self.sent = []

    def chat(self, model, messages, stream=False, **kwargs):
        self.sent.append(messages)
        reply = {"message": {"content": "[happy] Good luck on your exam!"}}
        return iter([reply]) if stream else reply


def test_brain_recalls_and_captures(tmp_path):
    from nova.brain import Brain
    from nova.config import DEFAULTS
    mind = MemoryEngine(tmp_path / "mind.db")
    mind.add("I love masala dosa", "preference", 5)
    mem = Memory(tmp_path, "Hema", mind=mind)
    client = FakeClient()
    brain = Brain(DEFAULTS, mem, client=client)

    brain.respond("I'm craving dosa tonight")
    assert "you remember: I love masala dosa" in client.sent[-1][-1]["content"]

    brain.respond("My DBMS exam is tomorrow")
    deadline = None
    for _ in range(50):  # capture runs in the background
        deadline = next((m for m in mind.all() if m.kind == "deadline"), None)
        if deadline:
            break
        time.sleep(0.02)
    assert deadline and deadline.due == date.today() + timedelta(days=1)
