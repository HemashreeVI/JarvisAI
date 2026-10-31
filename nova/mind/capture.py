"""Decides what is worth remembering from what you say, without an extra AI call.

Nova keeps statements about you ("my DBMS exam is on Friday", "I love dosa"),
and skips questions, commands and small talk. Being picky keeps her memory
useful instead of a pile of every sentence.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime

from nova.mind.dates import parse_date

DEADLINE_WORDS = (
    "exam", "test", "quiz", "cat1", "cat2", "cat 1", "cat 2", "fat", "midterm", "mid sem", "end sem", "viva",
    "assignment", "deadline", "submission", "submit", "due", "project review", "review", "presentation",
    "interview", "lab", "hackathon", "meeting", "appointment", "birthday", "anniversary", "trip", "flight",
)
GOAL_STARTS = ("i want to", "i wanna", "i'd like to", "i would like to", "my goal", "i'm planning to",
               "i am planning to", "i plan to", "i need to", "i have to", "i'm going to", "i am going to")
LIKE_STARTS = ("i like", "i love", "i hate", "i prefer", "i enjoy", "i don't like", "i dont like",
               "my favourite", "my favorite")
ABOUT_ME = ("i am", "i'm", "im ", "my ", "i study", "i work", "i live", "i have", "i've got", "i got",
            "i finished", "i completed", "i scored", "i failed", "i passed", "i started")
QUESTION_STARTS = ("what", "when", "where", "who", "why", "how", "which", "can", "could", "would", "will",
                   "should", "do ", "does", "did", "is ", "are ", "am i", "have you", "has ")
COMMAND_STARTS = ("open", "close", "play", "search", "google", "set ", "remind", "tell me", "show", "turn",
                  "stop", "start", "launch", "send", "find", "take", "lock", "shut", "restart", "volume",
                  "mute", "dance", "spin", "jump", "wave", "hug", "crawl", "walk", "please", "nova,")
SMALL_TALK = re.compile(r"^(hi|hey|hello|ok|okay|thanks|thank you|bye|good (morning|night|evening)|lol|haha)\b")


@dataclass
class Candidate:
    text: str
    kind: str          # fact, preference, goal, deadline, event
    importance: float  # 1-10
    due: date | None = None
    tags: tuple = ()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _subject_tags(sentence: str) -> tuple:
    """Course codes like CSE2004 and the deadline word, so search and the memory screen can group them."""
    tags = {w for w in DEADLINE_WORDS if re.search(rf"\b{re.escape(w)}\b", sentence)}
    tags |= {c.upper() for c in re.findall(r"\b[a-z]{3,4}\s?\d{3,4}[a-z]?\b", sentence)}
    return tuple(sorted(tags))


def extract(text: str, now: datetime | None = None) -> list[Candidate]:
    """Things worth remembering in one message from the user."""
    found = []
    for sentence in _sentences(text):
        low = sentence.lower().strip(" \"'")
        if len(low) < 8 or len(low) > 300:
            continue
        if low.endswith("?") or low.startswith(QUESTION_STARTS) or SMALL_TALK.match(low):
            continue
        if low.startswith(COMMAND_STARTS):
            continue
        personal = re.search(r"\b(i|i'm|im|my|me|we|our)\b", low)
        clean = sentence.strip().rstrip(".!")
        tags = _subject_tags(low)
        due = parse_date(low, now)

        if due and any(re.search(rf"\b{re.escape(w)}\b", low) for w in DEADLINE_WORDS):
            found.append(Candidate(clean, "deadline", 8, due, tags))
        elif not personal:
            continue
        elif low.startswith(GOAL_STARTS) or "my goal" in low:
            found.append(Candidate(clean, "goal", 6, due, tags))
        elif low.startswith(LIKE_STARTS) or re.search(r"\bmy fav(ou)?rite\b", low):
            found.append(Candidate(clean, "preference", 5, None, tags))
        elif due:
            found.append(Candidate(clean, "event", 6, due, tags))
        elif low.startswith(ABOUT_ME) or " my " in f" {low} ":
            found.append(Candidate(clean, "fact", 5, None, tags))
    return found
