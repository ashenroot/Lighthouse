import pytest

from conftest import brief_payload, make_client
from fresnel.relay import Relay


@pytest.fixture
def loud(config, clock):
    config.foghorn.topic = "secret-topic"
    client = make_client()
    return Relay(config, client=client, now=clock), client


async def test_alert_once_per_transition(loud):
    relay, client = loud
    await relay.refresh("swpc_kp")  # Kp 5.0 -> G1 -> active
    assert len(client.sent) == 1
    req = client.sent[0]
    assert req.url.path == "/secret-topic" and req.headers["title"].endswith("active")
    await relay.refresh("swpc_kp")  # still active, silent
    assert len(client.sent) == 1


async def test_restart_is_quiet(loud, config, clock, monkeypatch):
    relay, _ = loud
    await relay.refresh("cisa_kev")
    client2 = make_client()
    r2 = Relay(config, client=client2, now=clock)
    await r2.refresh("cisa_kev")
    assert client2.sent == []


async def test_missing_brief_alarm(loud, clock):
    relay, client = loud
    relay.foghorn.seen["brief"] = "ok"
    await relay.foghorn.check_brief()
    await relay.foghorn.check_brief()
    assert len(client.sent) == 1 and "missing" in client.sent[0].headers["title"]


async def test_disabled_without_topic(config, clock):
    client = make_client()
    relay = Relay(config, client=client, now=clock)
    await relay.refresh("swpc_kp")
    assert client.sent == []
