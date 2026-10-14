import pytest

from conftest import make_client, feeds
from fresnel.relay import Relay, describe_error
from fresnel.sources.swpc_kp import g_scale
import httpx


@pytest.mark.asyncio
async def test_all_sources_parse(relay):
    await relay.refresh_all()
    snap = relay.snapshot()
    s = snap["sources"]
    assert s["nws_alerts"]["status"] == "active"
    assert s["nws_alerts"]["data"]["alerts"][0]["event"] == "Tornado Warning"  # sorted by severity
    assert s["swpc_kp"]["status"] == "active" and "G1" in s["swpc_kp"]["summary"]
    assert s["nhc_storms"]["status"] == "active"
    assert s["cisa_kev"]["status"] == "active" and s["cisa_kev"]["data"]["recent_count"] == 1
    assert s["openfema"]["status"] == "active" and len(s["openfema"]["data"]["declarations"]) == 1
    assert s["usgs_quakes"]["status"] == "active" and s["usgs_quakes"]["data"]["count"] == 2
    assert s["faa_airports"]["status"] == "active" and "MCI" in s["faa_airports"]["summary"]
    assert [e["airport"] for e in s["faa_airports"]["data"]["events"]] == ["MCI"]
    assert s["swpc_scales"]["status"] == "active" and s["swpc_scales"]["summary"] == "R0 S0 G1"
    assert s["drought"]["status"] == "active" and s["drought"]["data"]["map_date"] == "2026-10-06"
    g = s["usgs_gauges"]
    assert g["status"] == "active" and len(g["data"]["gauges"]) == 1  # empty series skipped
    assert g["data"]["gauges"][0]["height_ft"] == 2.5 and g["data"]["gauges"][0]["change_24h_ft"] == 1.0
    w = s["cdc_wastewater"]["data"]
    assert s["cdc_wastewater"]["status"] == "active" and w["elevated"] == ["SARS-CoV-2"] and w["pathogens"]["RSV"]["sites"] == 1
    f = s["fda_recalls"]
    assert f["status"] == "active" and [r["recall_number"] for r in f["data"]["recalls"]] == ["F-1", "F-3"]
    assert s["cpsc_recalls"]["status"] == "nominal" and s["cpsc_recalls"]["data"]["count"] == 1
    assert snap["panels"]["local"]["status"] == "active"
    assert snap["panels"]["transport"]["status"] == "active"
    assert all(e["fetch"]["ok"] and not e["fetch"]["stale"] for e in s.values())


@pytest.mark.asyncio
async def test_swpc_dict_shape_and_nominal(config, clock):
    d = feeds()
    d["services.swpc.noaa.gov"] = [{"time_tag": "2026-10-14T09:00:00", "Kp": 2.0}]
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("swpc_kp")
    assert r.envelope("swpc_kp")["status"] == "nominal"


def test_g_scale():
    assert [g_scale(x) for x in (3, 4.67, 5, 7, 9)] == [0, 1, 1, 3, 5]


@pytest.mark.asyncio
async def test_failure_is_unknown_and_sanitized(config, clock):
    r = Relay(config, client=make_client(fail=True), now=clock)
    await r.refresh_all()
    env = r.envelope("nws_alerts")
    assert env["status"] == "unknown"
    assert env["fetch"]["error"] == "connection error"
    assert env["fetch"]["consecutive_failures"] == 1
    text = str(r.snapshot())
    assert "39.10" not in text and "94.57" not in text and "point=" not in text


@pytest.mark.asyncio
async def test_stale_becomes_unknown_but_keeps_data(relay, clock):
    await relay.refresh("swpc_kp")
    clock.t = clock.t.replace(day=15)  # a day later, past stale_after
    env = relay.envelope("swpc_kp")
    assert env["status"] == "unknown" and env["fetch"]["stale"] and env["data"] is not None
    assert env["summary"].startswith("Stale:")


@pytest.mark.asyncio
async def test_last_good_survives_restart(config, clock):
    r1 = Relay(config, client=make_client(), now=clock)
    await r1.refresh("cisa_kev")
    r2 = Relay(config, client=make_client(fail=True), now=clock)
    assert r2.envelope("cisa_kev")["status"] == "active"
    await r2.refresh("cisa_kev")
    env = r2.envelope("cisa_kev")
    assert env["data"] is not None and env["fetch"]["error"] and env["status"] == "active"


@pytest.mark.asyncio
async def test_bad_format_reported(config, clock):
    d = feeds()
    d["www.cisa.gov"] = {"nope": 1}
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("cisa_kev")
    assert r.envelope("cisa_kev")["fetch"]["error"] == "unexpected response format"


def test_describe_error_http():
    req = httpx.Request("GET", "https://x/?lat=1")
    exc = httpx.HTTPStatusError("m", request=req, response=httpx.Response(503, request=req))
    assert describe_error(exc) == "HTTP 503"
    assert describe_error(httpx.ReadTimeout("t")) == "timeout"


def test_disabled_source(config, clock):
    from fresnel.config import SourceCfg
    config.sources["nhc_storms"] = SourceCfg(enabled=False)
    r = Relay(config, client=make_client(), now=clock)
    assert "nhc_storms" not in r.sources


def test_source_skipped_until_configured(config, clock):
    config.sources.pop("faa_airports")
    r = Relay(config, client=make_client(), now=clock)
    assert "faa_airports" not in r.sources


async def test_source_with_a_proxy_gets_its_own_client(config, clock):
    from fresnel.config import SourceCfg
    from fresnel.relay import Relay

    config.sources["cisa_kev"] = SourceCfg(proxy="socks5h://127.0.0.1:1080")
    r = Relay(config, client=make_client(), now=clock)
    assert r.sources["cisa_kev"].ctx.client is not r.client
    assert r.sources["swpc_kp"].ctx.client is r.client
    await r.stop()


def test_proxy_must_be_an_http_url():
    import pytest
    from pydantic import ValidationError

    from fresnel.config import SourceCfg

    with pytest.raises(ValidationError):
        SourceCfg(proxy="not a url")
    assert SourceCfg(proxy="http://127.0.0.1:3128").proxy
    assert SourceCfg(proxy="socks5h://127.0.0.1:1080").proxy


async def test_unfetched_source_still_links_to_its_publisher(relay):
    env = relay.envelope("cisa_kev")
    assert env["status"] == "unknown" and env["source_url"] == "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"
