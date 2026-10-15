from datetime import timedelta

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
    av = s["aviation_weather"]  # airports come from the FAA feed's list, so KATL is not asked for
    assert av["status"] == "active" and av["summary"] == "MCI LIFR (ceiling 300 ft)"
    assert av["data"]["airports"][0]["ceiling_ft"] == 300 and av["data"]["airports"][0]["icao"] == "KMCI"
    o = s["odin_outages"]
    assert o["status"] == "active" and o["data"]["county"]["customers_out"] == 1200  # county_active is 1000
    assert o["data"]["national"]["customers_out"] == 11765 and o["data"]["national"]["states_reporting"] == 3
    assert o["data"]["state"]["customers_out"] == 1500 and "floor" in o["summary"]
    d = s["doe_202c"]
    assert d["status"] == "active" and d["data"]["fresh"] == ["202-26-50"]  # the plant-retention renewal is not news
    assert d["data"]["in_force"] == 2 and d["data"]["read"] == d["data"]["on_page"] == 3
    n = s["nerc_alerts"]
    assert n["status"] == "nominal" and [a["level"] for a in n["data"]["alerts"]] == [3, 2]  # webinar and off-path files skipped
    assert n["data"]["alerts"][0]["url"].startswith("https://www.nerc.com/globalassets/")
    g = s["grid_operators"]
    assert g["status"] == "nominal" and any(x["name"] == "ERCOT" for x in g["data"]["links"])
    assert snap["panels"]["grid"]["status"] == "active"
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


@pytest.mark.asyncio
async def test_odin_with_no_reports_in_state_is_nominal_and_says_so(config, clock):
    config.location.state, config.location.county_fips = "GA", "13113"
    d = feeds()
    d["ornl.opendatasoft.com"] = lambda req: {"results": []} if req.url.params.get("group_by") != "state" else {"results": [{"state": "Kansas", "out": 50, "n": 1}]}
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("odin_outages")
    env = r.envelope("odin_outages")
    assert env["status"] == "nominal" and "none reported in GA" in env["summary"] and "not an all-clear" in env["summary"]
    assert env["data"]["state"]["reporting"] is False


@pytest.mark.asyncio
async def test_doe_page_it_cannot_read_is_a_failure_not_an_empty_list(config, clock):
    d = feeds()
    d["www.energy.gov"] = "<html><body>Orders 202-26-50 and 202-26-49 are listed in a layout this reader does not know.</body></html>"
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("doe_202c")
    env = r.envelope("doe_202c")
    assert env["status"] == "unknown" and env["fetch"]["error"] == "unexpected response format"


@pytest.mark.asyncio
async def test_nerc_search_that_finds_no_alerts_is_a_failure(config, clock):
    d = feeds()
    d["www.nerc.com"] = {"items": [{"documentResult": {"title": "Something else", "modifiedDate": "May 4, 2026", "url": "/x/y.pdf"}}]}
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("nerc_alerts")
    assert r.envelope("nerc_alerts")["fetch"]["error"] == "unexpected response format"


@pytest.mark.asyncio
async def test_grid_operators_choose_and_extend(config, clock):
    from fresnel.config import SourceCfg
    config.sources["grid_operators"] = SourceCfg(params={"operators": ["pjm"], "links": [{"name": "My utility", "url": "https://example.org/outages"}]})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("grid_operators")
    assert [x["name"] for x in r.envelope("grid_operators")["data"]["links"]] == ["PJM", "My utility"]
    config.sources["grid_operators"] = SourceCfg(params={"operators": ["nope"]})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("grid_operators")
    assert r.envelope("grid_operators")["fetch"]["error"] == "unexpected response format"


@pytest.mark.asyncio
async def test_aviation_weather_threshold_stale_and_missing(config, clock):
    from fresnel.config import SourceCfg
    config.sources["aviation_weather"] = SourceCfg(params={"airports": ["MCI", "ATL", "ZZZ"], "active_min": "LIFR"})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("aviation_weather")
    env = r.envelope("aviation_weather")
    cats = {a["airport"]: a["category"] for a in env["data"]["airports"]}
    assert env["status"] == "active" and cats == {"MCI": "LIFR", "ATL": None, "ZZZ": None}  # ATL's report is over 3 hours old
    assert "no report for ATL, ZZZ" in env["summary"]
    config.sources["aviation_weather"] = SourceCfg(params={"airports": ["MCI"], "active_min": "LIFR"})
    d = feeds()
    d["aviationweather.gov"][0]["fltCat"] = "MVFR"
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("aviation_weather")
    env = r.envelope("aviation_weather")
    assert env["status"] == "nominal" and env["summary"].startswith("No LIFR or worse at MCI")


@pytest.mark.asyncio
async def test_aviation_weather_with_no_current_reports_is_a_failure(config, clock):
    d = feeds()
    d["aviationweather.gov"] = []
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("aviation_weather")
    env = r.envelope("aviation_weather")
    assert env["status"] == "unknown" and env["fetch"]["error"] == "unexpected response format"


@pytest.mark.asyncio
async def test_river_stages_flags_observed_and_forecast_and_counts_silent_gauges(config, clock):
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("river_stages")
    env = r.envelope("river_stages")
    assert env["status"] == "active"
    assert env["summary"].startswith("minor flooding at Missouri River at Kansas City")
    d = env["data"]
    assert (d["total"], d["rated"], d["not_reporting"]) == (5, 3, 1)  # the far gauge is outside the radius
    names = [g["name"] for g in d["gauges"]]
    assert names[0] == "Missouri River at Kansas City" and "Far River" not in names
    assert env["source_url"] == "https://water.noaa.gov/gauges/MCIM7"


@pytest.mark.asyncio
async def test_river_stages_threshold_and_forecast_only(config, clock):
    from fresnel.config import SourceCfg
    config.sources["river_stages"] = SourceCfg(params={"active_min": "major"})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("river_stages")
    env = r.envelope("river_stages")
    assert env["status"] == "nominal" and env["summary"].startswith("No gauge at major flooding or worse among 3 rated within 50 km")
    assert "1 not reporting" in env["summary"]
    config.sources["river_stages"] = SourceCfg(params={"active_min": "moderate"})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("river_stages")
    env = r.envelope("river_stages")
    assert env["status"] == "active" and env["summary"] == "moderate flooding forecast at Missouri River at Kansas City"


@pytest.mark.asyncio
async def test_river_stages_with_no_reporting_gauges_is_a_failure(config, clock):
    from conftest import GAUGES
    d = feeds()
    d["api.water.noaa.gov"] = lambda request: {"gauges": [g for g in GAUGES if g["lid"] == "LBLM7"]}
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("river_stages")
    env = r.envelope("river_stages")
    assert env["status"] == "unknown" and env["fetch"]["error"] == "no gauges reporting"


@pytest.mark.asyncio
async def test_river_stages_with_no_gauges_nearby_says_so(config, clock):
    d = feeds()
    d["api.water.noaa.gov"] = lambda request: {"gauges": []}
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("river_stages")
    env = r.envelope("river_stages")
    assert env["status"] == "nominal" and env["summary"] == "No NWS river forecast gauges within 50 km"


@pytest.mark.asyncio
async def test_chokepoints_judges_against_the_year_as_well_as_the_quarter(config, clock):
    from fresnel.config import SourceCfg
    config.sources["chokepoints"] = SourceCfg(params={"names": ["Panama Canal", "Strait of Hormuz", "Suez Canal"]})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("chokepoints")
    env = r.envelope("chokepoints")
    rows = {c["name"]: c for c in env["data"]["chokepoints"]}
    assert env["status"] == "active"
    assert env["summary"].startswith("Strait of Hormuz -") and "vs 1-year normal" in env["summary"]
    assert "(data through 2026-10-11)" in env["summary"]
    # Hormuz fell in March, so the 90-day baseline has absorbed it and only the year shows the drop.
    assert rows["Strait of Hormuz"]["change_pct"] == 0.0 and rows["Strait of Hormuz"]["year_change_pct"] < -30
    assert rows["Panama Canal"]["active"] is False and rows["Panama Canal"]["worst_pct"] == 0.0


@pytest.mark.asyncio
async def test_chokepoints_steady_traffic_is_nominal_and_old_data_is_a_failure(config, clock):
    from fresnel.config import SourceCfg
    config.sources["chokepoints"] = SourceCfg(params={"names": ["Panama Canal", "Suez Canal"]})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("chokepoints")
    env = r.envelope("chokepoints")
    assert env["status"] == "nominal" and env["summary"].startswith("All 2 rated chokepoints within 30% of their normal")
    clock.t = clock.t + timedelta(days=30)
    await r.refresh("chokepoints")
    env = r.envelope("chokepoints")
    assert env["status"] == "unknown" and env["fetch"]["error"] == "upstream data is 33 days old"
