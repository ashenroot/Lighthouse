from datetime import timedelta

import pytest

from conftest import SPC_CSV, make_client, feeds
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


@pytest.mark.asyncio
async def test_air_quality_threshold_and_old_model_run(config, clock):
    from fresnel.config import SourceCfg
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("air_quality")
    env = r.envelope("air_quality")
    assert env["status"] == "active" and env["summary"] == "US AQI 112, unhealthy for sensitive groups (PM2.5 leads)"
    config.sources["air_quality"] = SourceCfg(params={"active_aqi": 150})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("air_quality")
    assert r.envelope("air_quality")["status"] == "nominal"
    clock.t = clock.t + timedelta(days=1)
    await r.refresh("air_quality")
    env = r.envelope("air_quality")
    assert env["status"] == "unknown" and env["fetch"]["error"] == "upstream data is 25 hours old"


@pytest.mark.asyncio
async def test_air_quality_without_a_value_is_a_failure(config, clock):
    d = feeds()
    d["air-quality-api.open-meteo.com"] = {"current": {"time": "2026-10-14T11:00", "us_aqi": None}}
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("air_quality")
    env = r.envelope("air_quality")
    assert env["status"] == "unknown" and env["fetch"]["error"] == "no AQI value returned"


@pytest.mark.asyncio
async def test_wildfires_skip_contained_stale_and_distant_incidents(config, clock):
    from fresnel.config import SourceCfg
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("nifc_wildfires")
    env = r.envelope("nifc_wildfires")
    assert env["status"] == "active" and env["summary"].startswith("Prairie fire, 1,200 acres, ")
    assert [f["name"] for f in env["data"]["fires"]] == ["Prairie", "Small Brush"]  # contained, 20 days stale and out of range are dropped
    config.sources["nifc_wildfires"] = SourceCfg(params={"min_acres": 5000})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("nifc_wildfires")
    env = r.envelope("nifc_wildfires")
    assert env["status"] == "nominal" and env["summary"] == "2 small uncontained fires within 100 km, none over 5,000 acres"


@pytest.mark.asyncio
async def test_wildfires_none_reported_and_upstream_error(config, clock):
    d = feeds()
    d["services3.arcgis.com"] = lambda request: {"features": []}
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("nifc_wildfires")
    env = r.envelope("nifc_wildfires")
    assert env["status"] == "nominal" and env["summary"] == "No uncontained wildfires reported within 100 km"
    d["services3.arcgis.com"] = lambda request: {"error": {"code": 400}}
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("nifc_wildfires")
    env = r.envelope("nifc_wildfires")
    assert env["fetch"]["ok"] is False and env["fetch"]["error"] == "upstream query error"


@pytest.mark.asyncio
async def test_fdic_failures_recent_failure_and_quiet_stretch(config, clock):
    from fresnel.config import SourceCfg
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("fdic_failures")
    env = r.envelope("fdic_failures")
    assert env["status"] == "active" and env["summary"] == "Example Bank (TOWN, KS) failed 2026-10-10"
    assert env["data"]["failures"][0]["assets_usd"] == 50_000_000
    config.sources["fdic_failures"] = SourceCfg(params={"active_days": 1})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("fdic_failures")
    env = r.envelope("fdic_failures")
    assert env["status"] == "nominal" and env["summary"] == "Last failure 4 days ago: Example Bank (TOWN, KS); 2 in the past 365 days"


@pytest.mark.asyncio
async def test_federal_register_new_emergency_ignores_continuations_and_old_orders(config, clock):
    from fresnel.config import SourceCfg
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("federal_register")
    env = r.envelope("federal_register")
    assert env["status"] == "active"
    assert env["summary"] == "New national emergency (2026-10-11): Declaring a National Emergency With Respect to Widgets"
    assert [e["title"] for e in env["data"]["emergencies"]] == ["Declaring a National Emergency With Respect to Widgets"]
    assert [o["number"] for o in env["data"]["orders"]] == ["14500"]  # the August order is outside the window
    config.sources["federal_register"] = SourceCfg(params={"active_days": 1})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("federal_register")
    env = r.envelope("federal_register")
    assert env["status"] == "nominal" and env["summary"] == "1 executive order in 21 days; latest 2026-10-12: Improving Widget Policy"


@pytest.mark.asyncio
async def test_storm_reports_rank_tornado_first_and_filter_by_distance(config, clock):
    from datetime import datetime, timezone
    clock.t = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("spc_storm_reports")
    env = r.envelope("spc_storm_reports")
    d = env["data"]
    assert env["status"] == "active" and env["summary"].startswith("Tornado report ") and env["summary"].endswith("60 min ago (+1 more)")
    assert (d["in_range"], d["nationwide"]) == (3, 4)  # the Iowa report is outside the radius
    assert [x["kind"] for x in d["reports"]] == ["hail", "wind", "tornado"]  # newest first
    assert d["reports"][1]["comments"] == "Trees down, power lines (EAX)"  # a quoted comma stays in one field
    assert d["reports"][0]["significant"] is False  # three-quarter inch hail is below the threshold
    from fresnel.config import SourceCfg
    config.sources["spc_storm_reports"] = SourceCfg(params={"radius_km": 1})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("spc_storm_reports")
    env = r.envelope("spc_storm_reports")
    assert env["status"] == "nominal" and env["summary"] == "No storm reports within 1 km in this report day"


@pytest.mark.asyncio
async def test_storm_reports_stuck_file_and_changed_format_are_failures(config, clock):
    import httpx
    from datetime import datetime, timezone
    clock.t = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
    d = feeds()
    d["www.spc.noaa.gov"] = httpx.Response(200, text=SPC_CSV, headers={"last-modified": "Sun, 11 Oct 2026 15:00:00 GMT"})
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("spc_storm_reports")
    assert r.envelope("spc_storm_reports")["fetch"]["error"] == "report file is 72 hours old"
    d["www.spc.noaa.gov"] = "<html>moved</html>"
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("spc_storm_reports")
    assert r.envelope("spc_storm_reports")["fetch"]["error"] == "unexpected report file format"


@pytest.mark.asyncio
async def test_rss_headlines_reports_a_stale_feed_instead_of_trusting_it(config, clock):
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("rss_headlines")
    env = r.envelope("rss_headlines")
    assert env["status"] == "nominal"
    assert [h["title"] for h in env["data"]["headlines"]] == ["Flood warnings across the north", "Talks resume in Geneva"]
    assert env["summary"] == "2 headlines from 1 feed; NPR: stale (newest item 2023-05-01)"
    assert {f["name"]: f["ok"] for f in env["data"]["feeds"]} == {"BBC World": True, "NPR": False}


@pytest.mark.asyncio
async def test_rss_headlines_keywords_and_all_feeds_dead(config, clock):
    from fresnel.config import SourceCfg
    config.sources["rss_headlines"] = SourceCfg(params={"feeds": [
        {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/world/rss.xml", "keywords": ["flood"]}]})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("rss_headlines")
    assert [h["title"] for h in r.envelope("rss_headlines")["data"]["headlines"]] == ["Flood warnings across the north"]
    config.sources["rss_headlines"] = SourceCfg(params={"feeds": [{"name": "NPR", "url": "https://feeds.npr.org/1001/rss.xml"}]})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("rss_headlines")
    env = r.envelope("rss_headlines")
    assert env["fetch"]["ok"] is False and env["fetch"]["error"] == "no feed is current: NPR stale (newest item 2023-05-01)"


@pytest.mark.asyncio
async def test_rss_headlines_refuses_feeds_with_a_dtd(config, clock):
    from fresnel.config import SourceCfg
    from conftest import rss
    d = feeds()
    d["feeds.bbci.co.uk"] = rss(("x", "Wed, 14 Oct 2026 10:00:00 GMT"), doctype='<!DOCTYPE r [<!ENTITY a "aaaa">]>')
    config.sources["rss_headlines"] = SourceCfg(params={"feeds": [{"name": "BBC", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"}]})
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("rss_headlines")
    assert r.envelope("rss_headlines")["fetch"]["error"] == "no feed is current: BBC feed uses a DTD"


@pytest.mark.asyncio
async def test_rss_headlines_per_feed_limits_and_rss1_feeds(config, clock):
    from fresnel.config import SourceCfg
    from fresnel.sources.rss_headlines import parse_feed
    config.sources["rss_headlines"] = SourceCfg(params={"feeds": [
        {"name": "Slow", "url": "https://feeds.npr.org/1001/rss.xml", "stale_days": 100000, "max_age_hours": 10_000_000}]})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("rss_headlines")
    env = r.envelope("rss_headlines")
    assert [h["title"] for h in env["data"]["headlines"]] == ["Top story from 2023"]  # a slow feed can keep its own limits
    rdf = ('<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" '
           'xmlns:dc="http://purl.org/dc/elements/1.1/"><item><title>Berlin item</title><link>https://example.org/b</link>'
           '<dc:date>2026-10-14T09:00:00Z</dc:date></item></rdf:RDF>')
    item = parse_feed(rdf)[0]
    assert item["title"] == "Berlin item" and item["time"].year == 2026


@pytest.mark.asyncio
async def test_rss_headlines_says_why_a_feed_failed(config, clock):
    import httpx
    from fresnel.config import SourceCfg
    d = feeds()
    d["www.statnews.com"] = httpx.Response(403, text="blocked")
    d["example.org"] = "this is not xml <<<"
    config.sources["rss_headlines"] = SourceCfg(params={"feeds": [
        {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
        {"name": "STAT", "url": "https://www.statnews.com/feed/"},
        {"name": "Page", "url": "https://example.org/feed"}]})
    r = Relay(config, client=make_client(data=d), now=clock)
    await r.refresh("rss_headlines")
    env = r.envelope("rss_headlines")
    assert {f["name"]: f["problem"] for f in env["data"]["feeds"]} == {"BBC": None, "STAT": "HTTP 403", "Page": "not a readable feed"}
    assert env["summary"] == "2 headlines from 1 feed; STAT: HTTP 403, Page: not a readable feed"
