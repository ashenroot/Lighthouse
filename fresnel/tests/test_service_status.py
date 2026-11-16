"""Cloud and service status: official provider feeds, with unreachable and limited-coverage providers kept visible."""
import httpx
import pytest

from conftest import make_client, status_feeds
from fresnel.config import SourceCfg
from fresnel.relay import Relay
from fresnel.sources.service_status import DEFAULT_PROVIDERS


def sp(indicator="none", description="All Systems Operational", incidents=()):
    return {"status": {"indicator": indicator, "description": description}, "incidents": [{"name": n} for n in incidents]}


RSS = "<rss><channel><title>x</title><lastBuildDate>{built}</lastBuildDate>{items}</channel></rss>"


def feeds(**over):
    d = status_feeds()
    d.update(over)
    return d


async def run(config, clock, data, **params):
    if params:
        config.sources["service_status"] = SourceCfg(params=params)
    r = Relay(config, client=make_client(data=data), now=clock)
    await r.refresh("service_status")
    return r.envelope("service_status")


def row(env, name):
    return next(p for p in env["data"]["providers"] if p["name"] == name)


@pytest.mark.asyncio
async def test_quiet_world_is_nominal_and_microsoft_business_is_limited_never_operational(config, clock):
    env = await run(config, clock, feeds())
    assert env["status"] == "nominal"
    assert env["summary"] == "No major outage declared by 18 of 19 providers; 1 with limited coverage"
    ms = row(env, "Microsoft 365 (business)")
    assert ms["level"] == "limited" and "Teams and Exchange" in ms["detail"]
    assert row(env, "Azure")["level"] == "ok"


@pytest.mark.asyncio
async def test_a_major_outage_turns_it_active_and_a_minor_one_does_not(config, clock):
    d = feeds(**{"www.githubstatus.com": sp("major", "Major System Outage", ["Git operations failing"]),
                 "www.cloudflarestatus.com": sp("minor", "Partially Degraded Service", ["Workers delay"])})
    env = await run(config, clock, d)
    assert env["status"] == "active" and env["summary"].startswith("Major outage: GitHub")
    assert row(env, "GitHub")["detail"] == "Git operations failing"
    assert row(env, "Cloudflare")["level"] == "minor"
    env = await run(config, clock, feeds(**{"www.cloudflarestatus.com": sp("minor")}))
    assert env["status"] == "nominal" and "1 degraded" in env["summary"]


@pytest.mark.asyncio
async def test_other_feed_formats(config, clock):
    items = ("<item><title><![CDATA[Service disruption: Increased Error Rates]]></title><pubDate>Wed, 14 Oct 2026 04:30:00 PDT</pubDate></item>"
             "<item><title>old</title><pubDate>Mon, 01 Jun 2026 04:30:00 PDT</pubDate></item>")
    d = feeds(**{"status.aws.amazon.com": RSS.format(built="Wed, 14 Oct 2026 11:58:00 Z", items=items),
                 "azure.status.microsoft": RSS.format(built="Wed, 14 Oct 2026 11:58:00 Z", items="<item><title>Azure Front Door</title></item>"),
                 "status.cloud.google.com": [{"end": None, "severity": "high", "service_name": "Compute", "external_desc": "Elevated errors"},
                                             {"end": "2026-10-01T00:00:00+00:00", "severity": "low"}],
                 "slack-status.com": {"status": "active", "active_incidents": [{"id": 1}]},
                 "status.stripe.com": {"largestatus": "down", "message": "Payments down"}})
    env = await run(config, clock, d)
    assert row(env, "AWS")["level"] == "minor" and "1 event" in row(env, "AWS")["detail"]
    assert row(env, "Azure")["level"] == "major" and row(env, "Google Cloud")["level"] == "major"
    assert row(env, "Slack")["level"] == "minor" and row(env, "Stripe")["level"] == "major"
    assert env["status"] == "active"


@pytest.mark.asyncio
async def test_an_unreachable_or_broken_feed_is_shown_not_hidden(config, clock):
    d = feeds(**{"www.githubstatus.com": httpx.Response(503), "discordstatus.com": {"unexpected": True},
                 "azure.status.microsoft": RSS.format(built="Mon, 12 Oct 2026 11:58:00 Z", items="")})  # stale build date
    env = await run(config, clock, d)
    assert row(env, "GitHub")["level"] == "unreachable" and row(env, "GitHub")["detail"] == "HTTP 503"
    assert row(env, "Discord")["level"] == "unreachable" and row(env, "Discord")["detail"] == "unreadable feed"
    assert row(env, "Azure")["level"] == "unreachable"  # an empty list from a feed that stopped updating is not "no incidents"
    assert "3 unreachable" in env["summary"]


@pytest.mark.asyncio
async def test_most_providers_unreachable_is_a_failure(config, clock):
    r = Relay(config, client=make_client(fail=True), now=clock)
    await r.refresh("service_status")
    env = r.envelope("service_status")
    assert env["fetch"]["ok"] is False and "unreachable" in env["fetch"]["error"]


@pytest.mark.asyncio
async def test_exclude_and_add(config, clock):
    d = feeds(**{"status.example.org": sp("major", "Down")})
    env = await run(config, clock, d, exclude=["Microsoft 365 (business)", "azure"],
                    add=[{"name": "Example", "kind": "statuspage", "url": "https://status.example.org/api/v2/summary.json"}])
    names = [p["name"] for p in env["data"]["providers"]]
    assert "Azure" not in names and "Microsoft 365 (business)" not in names and "Example" in names
    assert env["status"] == "active" and env["data"]["checked"] == 18
    env = await run(config, clock, d, add=[{"name": "Bad", "kind": "nope", "url": "https://x.example/"}])
    assert env["fetch"]["ok"] is False and "unknown kind" in env["fetch"]["error"]
