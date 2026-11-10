"""Headlines from RSS or Atom feeds you choose (publisher feeds, no key).

Headlines are shown as headlines and never drive any panel's status. A feed whose newest item is older than
`stale_days` is reported as stale, because some publishers leave a dead feed answering normally.
"""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from .base import Result, Source, SourceError

PAGE = "https://www.rssboard.org/rss-specification"
ATOM = "{http://www.w3.org/2005/Atom}"
RSS1 = "{http://purl.org/rss/1.0/}"  # RSS 1.0 (RDF) feeds, such as Deutsche Welle's
DEFAULT_FEEDS = [
    {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "NPR", "url": "https://feeds.npr.org/1001/rss.xml"},
]
_TAGS = re.compile(r"<[^>]+>")


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        t = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            t = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def parse_feed(xml: str) -> list[dict]:
    if re.search(r"<!(DOCTYPE|ENTITY)", xml, re.I):
        raise SourceError("feed uses a DTD")  # refuses entity-expansion tricks in untrusted XML
    root = ET.fromstring(xml)
    items = []
    for it in root.iter("item"):
        items.append({"title": (it.findtext("title") or "").strip(), "url": (it.findtext("link") or "").strip(),
                      "time": _when(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")),
                      "text": _TAGS.sub(" ", it.findtext("description") or "")})
    for it in root.iter(f"{RSS1}item"):
        items.append({"title": (it.findtext(f"{RSS1}title") or "").strip(), "url": (it.findtext(f"{RSS1}link") or "").strip(),
                      "time": _when(it.findtext("{http://purl.org/dc/elements/1.1/}date")),
                      "text": _TAGS.sub(" ", it.findtext(f"{RSS1}description") or "")})
    for it in root.iter(f"{ATOM}entry"):
        link = next((l.get("href") for l in it.findall(f"{ATOM}link") if l.get("rel") in (None, "alternate")), "")
        items.append({"title": (it.findtext(f"{ATOM}title") or "").strip(), "url": link or "",
                      "time": _when(it.findtext(f"{ATOM}updated") or it.findtext(f"{ATOM}published")),
                      "text": _TAGS.sub(" ", it.findtext(f"{ATOM}summary") or "")})
    return items


class RssHeadlines(Source):
    id = "rss_headlines"
    title = "Headlines (RSS)"
    panel = "headlines"
    page = PAGE
    tier = "fragile"
    default_interval = 900
    # feeds: a list of {name, url, keywords, max_items, stale_days, max_age_hours}, the last four optional. keywords keep only
    # items whose title or summary contains one of them. stale_days: a feed whose newest item is older than this is shown as
    # stale. A slow feed (a weekly blog, say) can set its own stale_days and max_age_hours.
    default_params = {"feeds": DEFAULT_FEEDS, "stale_days": 3, "max_items": 12, "max_age_hours": 48}

    async def _one(self, spec: dict) -> dict:
        name, url = spec.get("name"), spec.get("url")
        if not name or not str(url).startswith(("http://", "https://")):
            raise SourceError("each feed needs a name and an http(s) url")
        try:
            resp = await self.ctx.client.get(url, follow_redirects=True)
            resp.raise_for_status()
            items = parse_feed(resp.text)
        except SourceError as exc:  # a feed that cannot be parsed safely is one bad feed
            return {"name": name, "ok": False, "problem": str(exc), "items": []}
        except httpx.HTTPStatusError as exc:
            return {"name": name, "ok": False, "problem": f"HTTP {exc.response.status_code}", "items": []}
        except httpx.TimeoutException:
            return {"name": name, "ok": False, "problem": "timed out", "items": []}
        except ET.ParseError:
            return {"name": name, "ok": False, "problem": "not a readable feed", "items": []}
        except Exception:
            return {"name": name, "ok": False, "problem": "fetch failed", "items": []}
        dated = [i for i in items if i["time"]]
        if not dated:
            return {"name": name, "ok": False, "problem": "no dated items", "items": []}
        newest = max(i["time"] for i in dated)
        now = self.ctx.now()
        if now - newest > timedelta(days=float(spec.get("stale_days", self.params["stale_days"]))):
            return {"name": name, "ok": False, "problem": f"stale (newest item {newest.date().isoformat()})", "items": []}
        words = [w.lower() for w in spec.get("keywords") or []]
        keep = [i for i in dated if not words or any(w in (i["title"] + " " + i["text"]).lower() for w in words)]
        cutoff = now - timedelta(hours=float(spec.get("max_age_hours", self.params["max_age_hours"])))
        keep = [i for i in keep if i["time"] >= cutoff and i["title"]]
        keep.sort(key=lambda i: i["time"], reverse=True)
        out = [{"outlet": name, "title": i["title"][:200], "url": i["url"], "time": i["time"].isoformat()} for i in keep[: int(spec.get("max_items", 5))]]
        return {"name": name, "ok": True, "problem": None, "items": out}

    async def fetch(self) -> Result:
        feeds = self.params["feeds"]
        if not feeds:
            raise SourceError("no feeds configured")
        results = await asyncio.gather(*(self._one(f) for f in feeds))
        good = [r for r in results if r["ok"]]
        if not good:
            raise SourceError("no feed is current: " + "; ".join(f"{r['name']} {r['problem']}" for r in results)[:160])
        items = sorted((i for r in good for i in r["items"]), key=lambda i: i["time"], reverse=True)[: int(self.params["max_items"])]
        bad = [r for r in results if not r["ok"]]
        summary = f"{len(items)} headlines from {len(good)} feed{'s' if len(good) > 1 else ''}"
        if bad:
            summary += "; " + ", ".join(f"{r['name']}: {r['problem']}" for r in bad)
        return Result(
            data={"headlines": items, "feeds": [{"name": r["name"], "ok": r["ok"], "problem": r["problem"]} for r in results]},
            summary=summary, status="nominal", source_url=(items[0]["url"] if items else PAGE),
        )
