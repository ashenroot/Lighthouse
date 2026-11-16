"""Status of major cloud and SaaS providers, from the providers' own public status feeds (no key).

Each provider is one feed in one of a few formats. A provider that cannot be reached is shown as unreachable, never as
fine. Providers whose public feed does not cover what people use (Microsoft 365 for business publishes only whether its
admin center is reachable, not Teams or Exchange health) are shown as "limited", never as operational. Only a declared
major outage turns the panel active. These feeds are the providers' own word and often lag a real outage.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from .base import Result, Source, SourceError

STATUSPAGE = "statuspage"        # Atlassian Statuspage summary.json
GOOGLE = "google_incidents"      # Google Cloud and Workspace incidents.json
SLACK = "slack"
STRIPE = "stripe"
RSS_OPEN = "rss_open"            # a feed that lists only open incidents (Azure); empty means none
RSS_RECENT = "rss_recent"        # a feed that keeps history (AWS); recent items count
M365_POSTS = "m365_posts"        # status.cloud.microsoft consumer feed
M365_ADMIN = "m365_admin"        # status.cloud.microsoft admin center feed (limited coverage)

DEFAULT_PROVIDERS = [
    {"name": "GitHub", "kind": STATUSPAGE, "url": "https://www.githubstatus.com/api/v2/summary.json", "page": "https://www.githubstatus.com"},
    {"name": "Cloudflare", "kind": STATUSPAGE, "url": "https://www.cloudflarestatus.com/api/v2/summary.json", "page": "https://www.cloudflarestatus.com"},
    {"name": "OpenAI", "kind": STATUSPAGE, "url": "https://status.openai.com/api/v2/summary.json", "page": "https://status.openai.com"},
    {"name": "Claude", "kind": STATUSPAGE, "url": "https://status.claude.com/api/v2/summary.json", "page": "https://status.claude.com"},
    {"name": "Atlassian", "kind": STATUSPAGE, "url": "https://status.atlassian.com/api/v2/summary.json", "page": "https://status.atlassian.com"},
    {"name": "Discord", "kind": STATUSPAGE, "url": "https://discordstatus.com/api/v2/summary.json", "page": "https://discordstatus.com"},
    {"name": "Dropbox", "kind": STATUSPAGE, "url": "https://status.dropbox.com/api/v2/summary.json", "page": "https://status.dropbox.com"},
    {"name": "Twilio", "kind": STATUSPAGE, "url": "https://status.twilio.com/api/v2/summary.json", "page": "https://status.twilio.com"},
    {"name": "Zoom", "kind": STATUSPAGE, "url": "https://www.zoomstatus.com/api/v2/summary.json", "page": "https://www.zoomstatus.com"},
    {"name": "Akamai", "kind": STATUSPAGE, "url": "https://www.akamaistatus.com/api/v2/summary.json", "page": "https://www.akamaistatus.com"},
    {"name": "Vercel", "kind": STATUSPAGE, "url": "https://www.vercel-status.com/api/v2/summary.json", "page": "https://www.vercel-status.com"},
    {"name": "Slack", "kind": SLACK, "url": "https://slack-status.com/api/v2.0.0/current", "page": "https://slack-status.com"},
    {"name": "Stripe", "kind": STRIPE, "url": "https://status.stripe.com/current", "page": "https://status.stripe.com"},
    {"name": "Google Cloud", "kind": GOOGLE, "url": "https://status.cloud.google.com/incidents.json", "page": "https://status.cloud.google.com"},
    {"name": "Google Workspace", "kind": GOOGLE, "url": "https://www.google.com/appsstatus/dashboard/incidents.json", "page": "https://www.google.com/appsstatus/dashboard/"},
    {"name": "AWS", "kind": RSS_RECENT, "url": "https://status.aws.amazon.com/rss/all.rss", "page": "https://health.aws.amazon.com/health/status"},
    {"name": "Azure", "kind": RSS_OPEN, "url": "https://azure.status.microsoft/en-us/status/feed/", "page": "https://azure.status.microsoft/status"},
    {"name": "Microsoft 365 (consumer)", "kind": M365_POSTS, "url": "https://status.cloud.microsoft/api/posts/m365Consumer", "page": "https://status.cloud.microsoft/"},
    {"name": "Microsoft 365 (business)", "kind": M365_ADMIN, "url": "https://status.cloud.microsoft/api/posts/mac", "page": "https://status.cloud.microsoft/"},
]
LIMITED_NOTE = "admin center only; Teams and Exchange health is not published"
OK, MINOR, MAJOR, LIMITED, UNREACHABLE = "ok", "minor", "major", "limited", "unreachable"


def _clip(text: str, n: int = 90) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _parse_statuspage(body: dict) -> tuple[str, str]:
    status = body["status"]
    level = {"none": OK, "maintenance": OK, "minor": MINOR, "major": MAJOR, "critical": MAJOR}.get(status["indicator"])
    if level is None:
        raise ValueError("unknown indicator")
    incidents = [i.get("name") for i in body.get("incidents") or [] if i.get("name")]
    return level, _clip(incidents[0] if incidents and level != OK else status.get("description") or "")


def _parse_google(body: list) -> tuple[str, str]:
    if not isinstance(body, list):
        raise ValueError("not a list")
    open_ = [i for i in body if not i.get("end")]
    if not open_:
        return OK, "No open incidents"
    worst = any(i.get("severity") == "high" or i.get("status_impact") == "SERVICE_OUTAGE" for i in open_)
    first = open_[0]
    return (MAJOR if worst else MINOR), _clip(f"{len(open_)} open: {first.get('service_name', '')} {first.get('external_desc', '')}")


def _parse_slack(body: dict) -> tuple[str, str]:
    active = body["active_incidents"]
    return (MINOR, f"{len(active)} active incident{'s' if len(active) != 1 else ''}") if active else (OK, "No active incidents")


def _parse_stripe(body: dict) -> tuple[str, str]:
    level = {"up": OK, "degraded": MINOR, "down": MAJOR}.get(body["largestatus"])
    if level is None:
        raise ValueError("unknown status")
    return level, _clip(body.get("message") or body["largestatus"])


def _rss_items(text: str) -> tuple[list[dict], datetime | None]:
    built = re.search(r"<lastBuildDate>(.*?)</lastBuildDate>", text)
    when = None
    if built:
        try:
            when = parsedate_to_datetime(built.group(1).strip().replace(" Z", " +0000"))
        except (TypeError, ValueError):
            when = None
    items = []
    for raw in re.findall(r"<item>(.*?)</item>", text, re.S):
        title = re.search(r"<title>(.*?)</title>", raw, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", raw)
        published = None
        if pub:
            try:
                published = parsedate_to_datetime(pub.group(1).strip().replace(" Z", " +0000"))
            except (TypeError, ValueError):
                published = None
        items.append({"title": re.sub(r"<!\[CDATA\[|\]\]>", "", title.group(1)).strip() if title else "", "published": published})
    return items, when


class ServiceStatus(Source):
    id = "service_status"
    title = "Cloud and service status"
    panel = "cloud"
    page = "https://status.cloud.microsoft/"
    tier = "primary"
    default_interval = 300
    # exclude: provider names to leave out. add: your own providers as {name, kind, url} (kinds: statuspage, google_incidents,
    # slack, stripe, rss_open, rss_recent). recent_hours: how recent an item in a history feed (AWS) counts. max_feed_age_hours:
    # an open-incident feed (Azure) that has not been rebuilt for this long is a failure, not an empty list.
    default_params = {"exclude": [], "add": [], "recent_hours": 6, "max_feed_age_hours": 6, "max_unreachable_pct": 50}

    def providers(self) -> list[dict]:
        skip = {str(n).lower() for n in self.params["exclude"]}
        chosen = [p for p in DEFAULT_PROVIDERS if p["name"].lower() not in skip]
        for extra in self.params["add"]:
            if not isinstance(extra, dict) or not {"name", "kind", "url"} <= set(extra):
                raise SourceError("each entry in add needs name, kind and url")
            if extra["kind"] not in (STATUSPAGE, GOOGLE, SLACK, STRIPE, RSS_OPEN, RSS_RECENT):
                raise SourceError(f"unknown kind {extra['kind']!r} for {extra['name']}")
            chosen.append({"page": extra["url"], **extra})
        if not chosen:
            raise SourceError("no providers to check")
        return chosen

    async def _one(self, p: dict, gate: asyncio.Semaphore) -> dict:
        out = {"name": p["name"], "url": p.get("page") or p["url"], "level": UNREACHABLE, "detail": "", "coverage": "full"}
        try:
            async with gate:
                resp = await self.ctx.client.get(p["url"])
            resp.raise_for_status()
            kind = p["kind"]
            if kind in (RSS_OPEN, RSS_RECENT):
                items, built = _rss_items(resp.text)
                now = self.ctx.now()
                if kind == RSS_OPEN:
                    if built is None or now - built > timedelta(hours=float(self.params["max_feed_age_hours"])):
                        raise ValueError("feed is stale")
                    level, detail = (MAJOR, _clip(items[0]["title"] or "Incident open")) if items else (OK, "No incidents declared")
                else:
                    cutoff = now - timedelta(hours=float(self.params["recent_hours"]))
                    recent = [i for i in items if i["published"] and i["published"] >= cutoff]
                    level, detail = (MINOR, _clip(f"{len(recent)} event{'s' if len(recent) != 1 else ''} in {self.params['recent_hours']} h: {recent[0]['title']}")) if recent else (OK, "No recent events")
            else:
                body = resp.json()
                if kind == M365_POSTS:
                    bad = [x for x in body if str(x.get("Status", "")).lower() != "operational"]
                    level = (MAJOR if any(re.search(r"down|outage|unavailable", str(x.get("Status", "")), re.I) for x in bad) else MINOR) if bad else OK
                    detail = _clip(", ".join(f"{x.get('ServiceDisplayName')}: {x.get('Status')}" for x in bad)) if bad else "All services operational"
                elif kind == M365_ADMIN:
                    out["coverage"] = "limited"
                    if str(body.get("Status", "")).lower() == "available":
                        level, detail = LIMITED, LIMITED_NOTE
                    else:
                        level, detail = MAJOR, _clip(f"Admin center: {body.get('Status')}")
                else:
                    level, detail = {STATUSPAGE: _parse_statuspage, GOOGLE: _parse_google, SLACK: _parse_slack, STRIPE: _parse_stripe}[kind](body)
            out.update(level=level, detail=detail)
        except httpx.HTTPStatusError as exc:
            out["detail"] = f"HTTP {exc.response.status_code}"
        except httpx.TimeoutException:
            out["detail"] = "timeout"
        except httpx.HTTPError:
            out["detail"] = "connection error"
        except (ValueError, KeyError, TypeError, AttributeError):
            out["detail"] = "unreadable feed"  # a changed or broken feed is a failure, never "fine"
        return out

    async def fetch(self) -> Result:
        gate = asyncio.Semaphore(8)
        rows = await asyncio.gather(*(self._one(p, gate) for p in self.providers()))
        down = [r for r in rows if r["level"] == UNREACHABLE]
        if len(down) * 100 > len(rows) * int(self.params["max_unreachable_pct"]):
            raise SourceError(f"{len(down)} of {len(rows)} status feeds unreachable")
        by = lambda lv: [r for r in rows if r["level"] == lv]  # noqa: E731
        major, minor, limited, ok = by(MAJOR), by(MINOR), by(LIMITED), by(OK)
        rank = {MAJOR: 0, MINOR: 1, UNREACHABLE: 2, LIMITED: 3, OK: 4}
        rows.sort(key=lambda r: (rank[r["level"]], r["name"]))
        if major:
            summary = "Major outage: " + ", ".join(r["name"] for r in major[:4])
        else:
            summary = f"No major outage declared by {len(ok) + len(minor)} of {len(rows)} providers"
        extras = [f"{len(x)} {w}" for x, w in ((minor, "degraded"), (down, "unreachable"), (limited, "with limited coverage")) if x]
        if extras:
            summary += "; " + ", ".join(extras)
        return Result(data={"providers": rows, "counts": {"major": len(major), "minor": len(minor), "ok": len(ok), "limited": len(limited), "unreachable": len(down)},
                            "checked": len(rows)},
                      summary=summary, status="active" if major else "nominal", source_url=self.page)
