import json

import httpx
import pytest

from fresnel.config import BriefNtfy
from fresnel.ntfy_brief import NtfyBrief

TOPIC = "lighthouse-brief-test"


def feed(messages):
    return "\n".join(json.dumps(m) for m in messages)


def build(relay, handler):
    relay.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    relay.config.brief_ntfy = BriefNtfy(topic=TOPIC)
    relay.ntfy_brief = NtfyBrief(relay)
    return relay.ntfy_brief


async def test_attachment_message_is_published_once(relay):
    msgs = [
        {"id": "a1", "time": 1760000000, "event": "open", "topic": TOPIC},
        {"id": "m1", "time": 1760000100, "event": "message", "title": "Daily Briefing - Oct 14",
         "message": "You received a file: briefing.txt",
         "attachment": {"name": "briefing.txt", "url": "https://ntfy.sh/file/abc.txt"}},
    ]
    hits = []

    def handler(req: httpx.Request):
        hits.append(str(req.url))
        if req.url.path.endswith("/json"):
            return httpx.Response(200, text=feed(msgs))
        return httpx.Response(200, text="All quiet.\nSources: https://example.com")

    nb = build(relay, handler)
    await nb.poll()
    assert nb.error is None
    env = relay.push_envelope("brief")
    assert env["data"]["title"] == "Daily Briefing - Oct 14" and env["data"]["body"].startswith("All quiet")
    n = len(hits)
    await nb.poll()
    assert len([h for h in hits if "/file/" in h]) == 1 and len(hits) == n + 1  # re-polled, not re-fetched
    assert relay.health()["brief_ntfy"]["error"] is None


async def test_inline_message(relay):
    msgs = [{"id": "m2", "time": 1760000100, "event": "message", "message": "short brief"}]
    nb = build(relay, lambda req: httpx.Response(200, text=feed(msgs)))
    await nb.poll()
    assert relay.push_envelope("brief")["data"]["body"] == "short brief"


async def test_attachment_on_another_host_is_refused(relay):
    msgs = [{"id": "m3", "time": 1, "event": "message", "attachment": {"url": "https://evil.example/x"}}]
    nb = build(relay, lambda req: httpx.Response(200, text=feed(msgs)))
    await nb.poll()
    assert "not hosted" in nb.error and relay.pushed["brief"] is None


async def test_server_error_is_reported(relay):
    nb = build(relay, lambda req: httpx.Response(429, text="quota"))
    await nb.poll()
    assert nb.error == "ntfy server answered HTTP 429"


async def test_empty_topic_is_quiet(relay):
    nb = build(relay, lambda req: httpx.Response(200, text=""))
    await nb.poll()
    assert nb.error is None and relay.pushed["brief"] is None
