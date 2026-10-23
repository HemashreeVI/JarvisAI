"""Browser, Google, YouTube, weather, news and Wikipedia. No API keys needed."""
import json
import re
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET

from nova.tools import ctx, tool

HEADERS = {"User-Agent": "Mozilla/5.0 (NovaAssistant)"}


def _get(url: str, timeout: int = 8) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


@tool("Open a website in the browser.", url="Website address or name, e.g. youtube.com")
def open_website(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + (url if "." in url else url + ".com")
    webbrowser.open(url)
    return f"Opened {url}."


@tool("Search Google in the browser.", query="What to search for")
def google_search(query: str) -> str:
    webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
    return f"Here are Google results for {query}."


@tool("Play a song or video on YouTube.", query="Song, video or channel to play")
def play_youtube(query: str) -> str:
    search = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    try:
        match = re.search(r'"videoId":"([\w-]{11})"', _get(search))
        if match:
            webbrowser.open(f"https://www.youtube.com/watch?v={match.group(1)}")
            return f"Playing {query} on YouTube."
    except OSError:
        pass
    webbrowser.open(search)
    return f"Opened YouTube results for {query}."


@tool("Get the current weather and today's forecast.", city="City name; leave empty for the user's city")
def get_weather(city: str = "") -> str:
    city = city or ctx.settings.get("user", {}).get("city", "Chennai")
    data = json.loads(_get(f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"))
    now = data["current_condition"][0]
    today = data["weather"][0]
    rain = max(int(h.get("chanceofrain", 0)) for h in today["hourly"])
    return (f"{city}: {now['weatherDesc'][0]['value']}, {now['temp_C']}°C (feels like {now['FeelsLikeC']}°C), "
            f"humidity {now['humidity']}%. Today {today['mintempC']}-{today['maxtempC']}°C, "
            f"{rain}% chance of rain.")


@tool("Get the latest news headlines, optionally about a topic.", topic="Topic, or empty for top headlines")
def get_news(topic: str = "") -> str:
    region = ctx.settings.get("user", {}).get("news_region", "IN")
    params = f"hl=en-{region}&gl={region}&ceid={region}:en"
    url = (f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(topic)}&{params}" if topic
           else f"https://news.google.com/rss?{params}")
    items = ET.fromstring(_get(url)).findall(".//item")[:5]
    if not items:
        return "I couldn't find any news right now."
    return "Headlines: " + " | ".join(i.findtext("title", "").rsplit(" - ", 1)[0] for i in items)


@tool("Look up a fact, person, place or concept on Wikipedia.", topic="What to look up")
def wikipedia(topic: str) -> str:
    try:
        title = json.loads(_get("https://en.wikipedia.org/w/api.php?action=opensearch&limit=1&format=json&search="
                                + urllib.parse.quote(topic)))[1][0]
        summary = json.loads(_get("https://en.wikipedia.org/api/rest_v1/page/summary/"
                                  + urllib.parse.quote(title.replace(" ", "_"))))
        return summary.get("extract", "No summary found.")[:1200]
    except (IndexError, OSError, ValueError):
        return f"I couldn't find {topic} on Wikipedia."
