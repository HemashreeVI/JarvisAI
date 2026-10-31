"""Turn "tomorrow", "on Friday", "12 Nov" or "in 3 days" into a real date.

Relative dates go stale ("tomorrow" is wrong the day after), so Nova stores
the actual calendar date next to each memory.
"""
import re
from datetime import date, datetime, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"

_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DAY_MONTH = re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH}(?:\s+(\d{{4}}))?\b", re.I)
_MONTH_DAY = re.compile(rf"\b{_MONTH}\s+{_DAY}\b(?:,?\s+(\d{{4}}))?", re.I)
_NUMERIC = re.compile(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b")  # Indian style: day/month
_IN_DAYS = re.compile(r"\bin\s+(\d{1,2}|a|one|two|three|four|five|six|seven)\s+(day|week)s?\b", re.I)
_WEEKDAY = re.compile(r"\b(?:(this|next|coming)\s+)?(" + "|".join(WEEKDAYS) + r")\b", re.I)
_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def _build(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _future(today: date, month: int, day: int, year: str | None) -> date | None:
    """A day/month with no year means the next time it comes round."""
    if year:
        y = int(year)
        return _build(y + 2000 if y < 100 else y, month, day)
    found = _build(today.year, month, day)
    if found and found < today - timedelta(days=1):
        found = _build(today.year + 1, month, day)
    return found


def parse_date(text: str, now: datetime | None = None) -> date | None:
    """The first date mentioned in text, or None."""
    today = (now or datetime.now()).date()
    t = text.lower()

    if m := _ISO.search(t):
        return _build(int(m[1]), int(m[2]), int(m[3]))
    if m := _DAY_MONTH.search(t):
        return _future(today, MONTHS[m[2][:3]], int(m[1]), m[3])
    if m := _MONTH_DAY.search(t):
        return _future(today, MONTHS[m[1][:3]], int(m[2]), m[3])
    if m := _NUMERIC.search(t):
        day, month = int(m[1]), int(m[2])
        if 1 <= month <= 12 and 1 <= day <= 31:
            return _future(today, month, day, m[3])
    if "day after tomorrow" in t:
        return today + timedelta(days=2)
    if re.search(r"\b(tomorrow|tmrw|tmr)\b", t):
        return today + timedelta(days=1)
    if re.search(r"\b(today|tonight|this evening|this morning)\b", t):
        return today
    if m := _IN_DAYS.search(t):
        n = _WORDS.get(m[1].lower()) or int(m[1])
        return today + timedelta(days=n * (7 if m[2].lower() == "week" else 1))
    if re.search(r"\bnext week\b", t):
        return today + timedelta(days=7)
    if m := _WEEKDAY.search(t):
        ahead = (WEEKDAYS.index(m[2].lower()) - today.weekday()) % 7
        if m[1] and m[1].lower() == "next" and ahead == 0:
            ahead = 7
        return today + timedelta(days=ahead)
    return None


def describe_due(due: date, today: date | None = None) -> str:
    """'today', 'tomorrow', 'on Friday 7 Nov', '3 days ago'..."""
    today = today or date.today()
    days = (due - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    if days < 0:
        return f"{-days} days ago"
    if days < 7:
        return f"on {due:%A} {due.day} {due:%b}"
    return f"on {due.day} {due:%b %Y}"
