"""Read the latest posts from the creator / source lists (public RSS or Atom feeds only).

Lists live in research/sources.csv (oil) and research/politics/sources.csv (politics).
Rows with feed_type=manual (e.g. X/Twitter) have no free feed and are listed for you
to check by hand.
"""
import csv
import xml.etree.ElementTree as ET_XML
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
LISTS = {"oil": ROOT / "research" / "sources.csv",
         "politics": ROOT / "research" / "politics" / "sources.csv"}
ATOM = "{http://www.w3.org/2005/Atom}"


def load_sources(topic="oil", min_rating="B"):
    path = LISTS[topic]
    if not path.exists():
        return []
    order = {"A": 0, "B": 1, "C": 2}
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if order.get((r.get("rating") or "C").strip()[:1], 2) <= order[min_rating]]


def _when(text):
    if not text:
        return None
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None


def latest_posts(feed_url, since):
    """[(time, title, link)] from an RSS or Atom feed, newer than `since`."""
    r = requests.get(feed_url, timeout=20, headers={"User-Agent": "argus-research/0.1"})
    r.raise_for_status()
    root = ET_XML.fromstring(r.content)
    items = []
    for it in root.iter("item"):                       # RSS
        title = it.findtext("title") or (it.findtext("description") or "")[:200]   # Bluesky posts have no title
        items.append((_when(it.findtext("pubDate")), title, it.findtext("link") or ""))
    for it in root.iter(f"{ATOM}entry"):               # Atom (YouTube, many blogs)
        link = it.find(f"{ATOM}link")
        items.append((_when(it.findtext(f"{ATOM}published") or it.findtext(f"{ATOM}updated")),
                      it.findtext(f"{ATOM}title") or "", link.get("href") if link is not None else ""))
    out = []
    for when, title, link in items:
        if when and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when and when >= since:
            out.append((when, title.strip(), link))
    return sorted(out, reverse=True)


def gather(topic="oil", hours=48, min_rating="B"):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    posts, manual, errors = [], [], []
    for s in load_sources(topic, min_rating):
        if (s.get("feed_type") or "").strip() == "manual" or not s.get("feed_url"):
            manual.append(s)
            continue
        try:
            for when, title, link in latest_posts(s["feed_url"], since):
                posts.append({"source": s.get("name", ""), "rating": s.get("rating", ""),
                              "when": when, "title": title, "link": link})
        except Exception as e:
            errors.append((s.get("name", ""), str(e)[:80]))
    return sorted(posts, key=lambda p: p["when"], reverse=True), manual, errors
