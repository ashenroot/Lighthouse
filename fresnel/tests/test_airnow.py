"""AirNow measured air quality, and the way it replaces the modeled source."""
import json

import httpx
import pytest

from conftest import AIRNOW_KEY, feeds, make_client
from fresnel.config import SourceCfg
from fresnel.keys import save_key
from fresnel.relay import Relay


@pytest.fixture()
def key(config, monkeypatch):
    monkeypatch.delenv("FRESNEL_KEY_AIRNOW", raising=False)
    save_key("airnow", AIRNOW_KEY, config.server.data_dir)


@pytest.mark.asyncio
async def test_airnow_reports_the_worst_pollutant_and_replaces_the_modeled_source(config, clock, key):
    r = Relay(config, client=make_client(), now=clock)
    snap = r.snapshot()
    assert "air_quality" not in snap["sources"] and "air_quality" not in [h["id"] for h in r.health()["sources"]]
    await r.refresh("airnow_aqi")
    await r.refresh("air_quality")  # superseded: asks nothing
    env = r.envelope("airnow_aqi")
    assert env["status"] == "nominal" and env["summary"] == "US AQI 36, good (Kansas City monitors)"
    assert [x["pollutant"] for x in env["data"]["readings"]] == ["PM2.5", "OZONE", "PM10"]  # the -999 reading is dropped
    assert env["data"]["time"] == "2026-10-14T11:00:00+00:00"
    assert r.states["air_quality"].last_success is None
    config.sources["airnow_aqi"] = SourceCfg(params={"active_aqi": 30})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("airnow_aqi")
    env = r.envelope("airnow_aqi")
    assert env["status"] == "active" and env["summary"] == "US AQI 36, good (Kansas City monitors)"


@pytest.mark.asyncio
async def test_without_the_key_the_modeled_source_stays_and_airnow_is_listed_for_setup(config, clock, monkeypatch):
    monkeypatch.delenv("FRESNEL_KEY_AIRNOW", raising=False)
    r = Relay(config, client=make_client(), now=clock)
    snap = r.snapshot()
    assert "air_quality" in snap["sources"] and "airnow_aqi" not in snap["sources"]
    assert "airnow_aqi" in [i["id"] for i in snap["setup"]]


@pytest.mark.asyncio
async def test_airnow_failures_are_visible(config, clock, key):
    def empty(request):
        return {"WebServiceError": [{"Message": "No data available"}]}
    r = Relay(config, client=make_client(data={**feeds(), "www.airnowapi.org": empty}), now=clock)
    await r.refresh("airnow_aqi")
    assert r.envelope("airnow_aqi")["fetch"]["error"] == "AirNow: No data available"
    def old(request):
        return [{"dateObserved": "2026-10-13", "hourObserved": "07:00", "localTimeZone": "EDT", "reportingAreaName": "x", "parameterName": "OZONE", "nowcastAQI": 20}]
    r = Relay(config, client=make_client(data={**feeds(), "www.airnowapi.org": old}), now=clock)
    await r.refresh("airnow_aqi")
    assert r.envelope("airnow_aqi")["fetch"]["error"].startswith("upstream data is ")
    save_key("airnow", "wrongkey12345678", config.server.data_dir)
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("airnow_aqi")
    assert r.envelope("airnow_aqi")["fetch"]["error"].startswith("AirNow rejected the key")
    assert AIRNOW_KEY not in json.dumps(r.snapshot())
