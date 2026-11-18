"""Another place's view of the snapshot: visitors by ZIP code, and the owner."""
import asyncio
import json
from collections import Counter
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import feeds, make_client
from fresnel.app import create_app
from fresnel.config import Config
from fresnel.limits import SlidingWindow, allowed, client_address
from fresnel.relay import Relay

ATLANTA = "30213"  # Fulton County, GA
KC = "64111"


def counting(clock=None, fail=False):
    """A mock internet that counts requests by host."""
    hits: Counter = Counter()
    data = {}
    for key, payload in feeds().items():
        def make(key=key, payload=payload):
            def call(request):
                hits[request.url.host] += 1
                return payload(request) if callable(payload) else payload
            return call
        data[key] = make()
    return make_client(fail=fail, data=data), hits


def cfg(tmp_path, **visitors):
    return Config.model_validate({
        "location": {"name": "Kansas City, MO", "lat": 39.1012, "lon": -94.5783, "state": "MO", "county_fips": "29095"},
        "server": {"data_dir": str(tmp_path / "data")},
        "visitors": visitors,
        "sources": {"faa_airports": {"params": {"airports": ["mci"]}}},
    })


def build(tmp_path, clock, fail=False, **visitors):
    client, hits = counting(fail=fail)
    config = cfg(tmp_path, **visitors)
    relay = Relay(config, client=client, now=clock)
    return config, relay, hits


async def view(relay, zip_code=ATLANTA, admin=False, address="203.0.113.5"):
    return await relay.places.snapshot(zip_code, admin, address)


@pytest.mark.asyncio
async def test_a_visitor_sees_the_places_keyless_local_sources(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock)
    snap = await view(relay)
    assert snap["place"]["ok"] and snap["place"]["name"] == "Fulton County, GA" and snap["place"]["role"] == "visitor"
    assert snap["location"]["name"] == "Fulton County, GA"
    for sid in ("nws_alerts", "usgs_quakes", "air_quality", "drought", "openfema", "nifc_wildfires", "spc_outlook", "wpc_rainfall_outlook"):
        assert sid in snap["sources"], sid
    assert snap["sources"]["nws_alerts"]["fetch"]["ok"]
    assert "cisa_kev" in snap["sources"]  # sources that are not tied to a place are untouched
    assert "30213" not in json.dumps(snap)


@pytest.mark.asyncio
async def test_visitors_get_links_for_what_needs_a_key_or_the_owners_settings(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock)
    snap = await view(relay)
    linked = {item["id"]: item for item in snap["linked"]}
    for sid in ("eia_fuel_prices", "airnow_aqi", "usgs_gauges", "river_stages", "odin_outages", "cdc_hospital_load"):
        assert sid in linked and sid not in snap["sources"], sid
    assert linked["eia_fuel_prices"]["url"].startswith("https://")
    assert snap["setup"] == []  # the owner's key setup lines mean nothing for a visitor


@pytest.mark.asyncio
async def test_a_place_is_cached_and_nothing_is_refetched_inside_the_ttl(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock)
    await view(relay)
    first = dict(hits)
    for _ in range(5):
        await view(relay, address="203.0.113.9")
    assert dict(hits) == first
    clock.t = clock.t + timedelta(minutes=16)  # NWS alerts are reused for 15 minutes, everything else for an hour
    await view(relay)
    assert hits["api.weather.gov"] == first["api.weather.gov"] + 1
    assert hits["earthquake.usgs.gov"] == first["earthquake.usgs.gov"]  # quakes are reused for the full hour


@pytest.mark.asyncio
async def test_concurrent_requests_for_one_place_share_one_fetch(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock)
    await asyncio.gather(*(view(relay, address=f"203.0.113.{i}") for i in range(1, 8)))
    assert hits["api.weather.gov"] <= 2  # alerts, and at most the outlook's helper call; never one per visitor


@pytest.mark.asyncio
async def test_new_places_are_limited_per_address_but_cached_places_are_free(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock, new_zips_per_hour=2)
    assert (await view(relay, "30213"))["place"]["ok"]
    assert (await view(relay, "64111"))["place"]["ok"]
    third = await view(relay, "10001")
    assert third["place"]["ok"] is False and third["place"]["reason"] == "new_places_limit" and third["place"]["retry_after"] > 0
    assert third["location"]["name"] == "Kansas City, MO"  # the default place is shown instead
    assert (await view(relay, "30213"))["place"]["ok"]  # already cached
    assert (await view(relay, "10001", address="198.51.100.7"))["place"]["ok"]  # another address has its own allowance
    clock.t = clock.t + timedelta(hours=1, seconds=5)
    assert (await view(relay, "02134"))["place"]["ok"]


@pytest.mark.asyncio
async def test_requests_per_minute_are_limited_per_address(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock, requests_per_minute=2)
    assert (await view(relay))["place"]["ok"] and (await view(relay))["place"]["ok"]
    third = await view(relay)
    assert third["place"]["reason"] == "rate_limited" and third["place"]["retry_after"] >= 1


@pytest.mark.asyncio
async def test_unknown_zip_and_the_kill_switch(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock, enabled=False)
    assert (await view(relay, "99999"))["place"]["reason"] == "unknown_zip"
    off = await view(relay)
    assert off["place"]["reason"] == "disabled" and off["location"]["name"] == "Kansas City, MO"
    assert not hits.get("api.weather.gov")  # nothing was fetched for the visitor
    assert (await view(relay, admin=True))["place"]["ok"]  # the owner is not held to it


@pytest.mark.asyncio
async def test_allow_ips_limits_who_may_look_up_places(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock, allow_ips=["10.0.0.0/8", "2001:db8::/32"])
    assert (await view(relay, address="10.4.5.6"))["place"]["ok"]
    assert (await view(relay, address="2001:db8::1"))["place"]["ok"]
    assert (await view(relay, address="203.0.113.5"))["place"]["reason"] == "not_allowed"
    assert (await view(relay, address="203.0.113.5", admin=True))["place"]["ok"]


@pytest.mark.asyncio
async def test_the_owner_gets_every_placeable_source_with_no_limits(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock, new_zips_per_hour=1, requests_per_minute=1)
    for z in ("30213", "64111", "10001", "02134", "90210"):
        snap = await view(relay, z, admin=True)
        assert snap["place"]["ok"] and snap["place"]["role"] == "owner"
    assert "usgs_gauges" in snap["sources"] and "spc_storm_reports" in snap["sources"]
    assert "aviation_weather" in {i["id"] for i in snap["linked"]}  # the owner's airports are not the place's


@pytest.mark.asyncio
async def test_a_failing_source_shows_unknown_and_is_not_hammered(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock, fail=True)
    snap = await view(relay)
    env = snap["sources"]["nws_alerts"]
    assert env["status"] == "unknown" and "https://" not in env["summary"] and "39." not in json.dumps(snap["place"])
    before = Counter(hits)
    await view(relay)
    await view(relay)
    assert hits == before  # the retry waits (5 minutes), however often visitors ask


@pytest.mark.asyncio
async def test_least_recently_used_places_are_dropped(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock, max_places=2)
    for z in ("30213", "64111", "10001"):
        await view(relay, z, address=f"203.0.113.{z[-1]}")
    assert list(relay.places.cache) == ["64111", "10001"]


@pytest.mark.asyncio
async def test_upstream_fetches_for_visitors_are_capped_per_minute(tmp_path, clock):
    config, relay, hits = build(tmp_path, clock, max_fetches_per_minute=3)
    snap = await view(relay)
    assert sum(1 for e in snap["sources"].values() if e["panel"] and e["fetch"]["ok"] and e["id"] in relay.places.placed(False)) <= 3
    assert snap["place"]["loading"] >= 5  # the rest wait for the next minute and say so


# ---- over HTTP ----

def client(tmp_path, clock, **visitors):
    config, relay, hits = build(tmp_path, clock, **visitors)
    return config, relay, TestClient(create_app(config, relay=relay, start_background=False))


def test_the_default_snapshot_is_unchanged_and_says_nothing_about_roles(tmp_path, clock):
    config, relay, api = client(tmp_path, clock)
    with api:
        body = api.get("/v1/snapshot").json()
        assert "place" not in body and "linked" not in body and body["location"]["name"] == "Kansas City, MO"


def test_the_zip_travels_in_a_header_and_responses_vary_by_it(tmp_path, clock):
    config, relay, api = client(tmp_path, clock)
    with api:
        r = api.get("/v1/snapshot", headers={"X-Lighthouse-Zip": ATLANTA})
        assert r.status_code == 200 and r.json()["place"]["name"] == "Fulton County, GA"
        assert "X-Lighthouse-Zip" in r.headers["vary"] and "private" in r.headers["cache-control"]
        assert ATLANTA not in r.text and ATLANTA not in str(r.url)
        etag = r.headers["etag"]
        again = api.get("/v1/snapshot", headers={"X-Lighthouse-Zip": ATLANTA, "If-None-Match": etag})
        assert again.status_code in (200, 304)
        assert api.get("/v1/snapshot").json()["location"]["name"] == "Kansas City, MO"


def test_an_owner_token_unlocks_the_owner_view_and_a_push_token_does_not(tmp_path, clock):
    config, relay, api = client(tmp_path, clock, new_zips_per_hour=1)
    owner = relay.tokens.add("me", ["admin"], {"admin", "brief"})
    pusher = relay.tokens.add("job", ["brief", "*"], {"admin", "brief"})
    with api:
        ok = api.get("/v1/snapshot", headers={"X-Lighthouse-Zip": ATLANTA, "Authorization": f"Bearer {owner}"}).json()
        assert ok["place"]["role"] == "owner" and "usgs_gauges" in ok["sources"]
        no = api.get("/v1/snapshot", headers={"X-Lighthouse-Zip": KC, "Authorization": f"Bearer {pusher}"}).json()
        assert no["place"]["role"] == "visitor" and no["place"]["token_rejected"] is True and "usgs_gauges" not in no["sources"]
        junk = api.get("/v1/snapshot", headers={"Authorization": "Bearer nope"}).json()
        assert junk["place"] == {"role": "visitor", "token_rejected": True}
        assert api.get("/v1/snapshot", headers={"Authorization": f"Bearer {owner}"}).json()["place"]["role"] == "owner"


def test_a_forged_forwarding_header_is_ignored_unless_it_comes_through_the_local_proxy(tmp_path, clock):
    assert client_address("203.0.113.5", "1.2.3.4", "X-Forwarded-For") == "203.0.113.5"
    assert client_address("127.0.0.1", "9.9.9.9, 198.51.100.7", "X-Forwarded-For") == "198.51.100.7"
    assert client_address("127.0.0.1", "198.51.100.7", "CF-Connecting-IP") == "198.51.100.7"
    assert client_address("127.0.0.1", None, "X-Forwarded-For") == "127.0.0.1"


def test_health_reports_counts_and_never_a_zip(tmp_path, clock):
    config, relay, api = client(tmp_path, clock)
    with api:
        api.get("/v1/snapshot", headers={"X-Lighthouse-Zip": ATLANTA})
        health = api.get("/v1/health").json()
        assert health["visitors"] == {"enabled": True, "places": 1} and ATLANTA not in json.dumps(health)


def test_sliding_window_and_cidr_helpers():
    t = {"now": 0.0}
    w = SlidingWindow(2, 60, lambda: t["now"])
    w.hit("a"); w.hit("a")
    assert w.retry_after("a") == 60 and w.retry_after("b") == 0
    t["now"] = 61
    assert w.retry_after("a") == 0
    assert allowed("10.1.1.1", ["10.0.0.0/8"]) and not allowed("11.1.1.1", ["10.0.0.0/8"]) and not allowed("garbage", ["10.0.0.0/8"])


def test_config_rejects_a_bad_allow_ips_entry(tmp_path):
    with pytest.raises(Exception, match="not an address or CIDR"):
        cfg(tmp_path, allow_ips=["not-an-ip"])
