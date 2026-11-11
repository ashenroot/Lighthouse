"""The two EIA sources, which share one free key."""
import pytest

from conftest import EIA_KEY, feeds, make_client
from fresnel.config import SourceCfg
from fresnel.keys import save_key
from fresnel.relay import Relay


@pytest.fixture(autouse=True)
def eia_key(config, monkeypatch):
    monkeypatch.delenv("FRESNEL_KEY_EIA", raising=False)
    save_key("eia", EIA_KEY, config.server.data_dir)


@pytest.mark.asyncio
async def test_fuel_prices_active_on_a_regional_rise_and_nominal_when_the_bar_is_higher(config, clock):
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("eia_fuel_prices")
    env = r.envelope("eia_fuel_prices")
    assert env["status"] == "active" and env["summary"] == "Regular gasoline up 13% in 4 weeks to $3.82/gal (PADD 2)"  # Missouri is in the Midwest
    data = env["data"]
    gas, diesel = data["products"]
    assert data["region"] == {"code": "R20", "name": "PADD 2"} and data["week"] == "2026-10-12"
    assert gas["active"] is True and gas["national"]["price"] == 3.50 and gas["vs_year_pct"] > 5
    assert diesel["active"] is False and diesel["change_weeks_pct"] == 0.0 and diesel["national"]["price"] == 6.10
    config.sources["eia_fuel_prices"] = SourceCfg(params={"rise_pct": 20})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("eia_fuel_prices")
    env = r.envelope("eia_fuel_prices")
    assert env["status"] == "nominal" and env["summary"] == "PADD 2: regular gasoline $3.82 (+13% in 4 wk), diesel $5.00 (+0% in 4 wk)"


@pytest.mark.asyncio
async def test_fuel_prices_region_override_unknown_region_and_old_data(config, clock):
    from datetime import timedelta
    config.sources["eia_fuel_prices"] = SourceCfg(params={"region": "r1z"})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("eia_fuel_prices")
    assert r.envelope("eia_fuel_prices")["data"]["region"]["name"] == "PADD 1C"
    config.sources["eia_fuel_prices"] = SourceCfg(params={"region": "R9Z"})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("eia_fuel_prices")
    assert r.envelope("eia_fuel_prices")["fetch"]["error"] == "no price data for region R9Z"
    config.sources["eia_fuel_prices"] = SourceCfg(params={})
    r = Relay(config, client=make_client(), now=clock)
    clock.t = clock.t + timedelta(days=40)
    await r.refresh("eia_fuel_prices")
    assert r.envelope("eia_fuel_prices")["fetch"]["error"].startswith("upstream data is ")


def test_every_state_has_a_price_region():
    from fresnel.sources.eia_fuel_prices import REGION_OF_STATE
    states = "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
    assert [s for s in states if s not in REGION_OF_STATE] == []


@pytest.mark.asyncio
async def test_grid_demand_active_near_the_peak_and_the_peak_is_robust_to_a_bad_spike(config, clock):
    config.sources["eia_grid_demand"] = SourceCfg(params={"balancing_authority": "soco"})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("eia_grid_demand")
    env = r.envelope("eia_grid_demand")
    assert env["status"] == "active"
    assert env["summary"] == "SOCO demand at 92% of its one-year peak; SOCO day-ahead forecast reaches 93% of peak around 18Z"
    data = env["data"]
    assert data["peak_mwh"] == 30000  # the 90,000 spike does not set the peak, and both pages of history were read
    assert data["demand_mwh"] == 27500 and data["age_hours"] == 2.0 and data["name"].startswith("Southern Company")
    config.sources["eia_grid_demand"] = SourceCfg(params={"balancing_authority": "SOCO", "peak_pct": 99})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("eia_grid_demand")
    env = r.envelope("eia_grid_demand")
    assert env["status"] == "nominal" and env["summary"] == "SOCO demand 27.5 GW (92% of its one-year peak); forecast peak 28.0 GW (93%)"


@pytest.mark.asyncio
async def test_grid_demand_fetches_the_year_once_a_day_and_fails_visibly(config, clock):
    from datetime import timedelta
    calls = []

    def counting(request):
        calls.append(request)
        from conftest import _eia
        return _eia(request)

    config.sources["eia_grid_demand"] = SourceCfg(params={"balancing_authority": "SOCO"})
    r = Relay(config, client=make_client(data={**feeds(), "api.eia.gov": counting}), now=clock)
    await r.refresh("eia_grid_demand")
    await r.refresh("eia_grid_demand")
    history = [c for c in calls if c.url.params.get("length") == "5000"]
    assert len(history) == 2  # two pages, fetched on the first refresh only
    clock.t = clock.t + timedelta(days=1)  # the latest reading is now more than 12 hours old
    await r.refresh("eia_grid_demand")
    assert r.envelope("eia_grid_demand")["fetch"]["error"] == "latest demand reading is 26 hours old"
    config.sources["eia_grid_demand"] = SourceCfg(params={"balancing_authority": "XXXX"})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("eia_grid_demand")
    assert r.envelope("eia_grid_demand")["fetch"]["error"] == "no data for balancing authority XXXX (check the code)"


@pytest.mark.asyncio
async def test_grid_demand_is_off_until_a_balancing_authority_is_set(config, clock):
    r = Relay(config, client=make_client(), now=clock)
    assert "eia_grid_demand" not in r.sources


@pytest.mark.asyncio
async def test_eia_key_is_never_in_output_and_a_refused_key_says_so(config, clock):
    import json
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("eia_fuel_prices")
    assert EIA_KEY not in json.dumps(r.snapshot()) + json.dumps(r.health())
    save_key("eia", "wrongkey123456", config.server.data_dir)
    await r.refresh("eia_fuel_prices")
    env = r.envelope("eia_fuel_prices")
    assert env["fetch"]["error"] == "EIA rejected the key (HTTP 403). Replace it with: fresnel key set eia"
