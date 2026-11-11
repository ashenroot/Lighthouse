"""CDC hospital load: bed occupancy and respiratory admissions for the home state."""
import pytest

from conftest import feeds, make_client
from fresnel.config import SourceCfg
from fresnel.relay import Relay


@pytest.mark.asyncio
async def test_a_sharp_rise_in_flu_patients_turns_it_active(config, clock):
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("cdc_hospital_load")
    env = r.envelope("cdc_hospital_load")
    assert env["status"] == "active" and env["summary"] == "MO: Influenza patients up 100% in 2 weeks to 90"
    flu = next(p for p in env["data"]["pathogens"] if p["pathogen"] == "Influenza")
    covid = next(p for p in env["data"]["pathogens"] if p["pathogen"] == "COVID-19")
    rsv = next(p for p in env["data"]["pathogens"] if p["pathogen"] == "RSV")
    assert flu["rising"] and not covid["rising"] and covid["change_pct"] == -9.1
    assert not rsv["rising"] and rsv["change_pct"] == 25.0  # under the bar, and under the minimum count
    assert env["data"]["week_end"] == "2026-10-10" and env["data"]["beds_pct"] == 80.2


@pytest.mark.asyncio
async def test_nominal_summary_and_full_beds(config, clock):
    config.sources["cdc_hospital_load"] = SourceCfg(params={"rise_pct": 500})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("cdc_hospital_load")
    env = r.envelope("cdc_hospital_load")
    assert env["status"] == "nominal" and env["summary"] == "MO hospitals 80% full, ICU 70%; in hospital: COVID-19 100, Influenza 90, RSV 25"
    config.sources["cdc_hospital_load"] = SourceCfg(params={"rise_pct": 500, "occupancy_pct": 80, "icu_pct": 70})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("cdc_hospital_load")
    env = r.envelope("cdc_hospital_load")
    assert env["status"] == "active" and env["summary"] == "MO: inpatient beds 80% full; ICU beds 70% full"  # occupancy is rounded for display


@pytest.mark.asyncio
async def test_old_empty_and_unknown_state_fail_visibly(config, clock):
    from datetime import timedelta
    clock.t = clock.t + timedelta(days=30)
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("cdc_hospital_load")
    assert r.envelope("cdc_hospital_load")["fetch"]["error"] == "upstream data is 34 days old"
    clock.t = clock.t - timedelta(days=30)
    r = Relay(config, client=make_client(data={**feeds(), "/resource/ua7e-t2fy.json": lambda request: []}), now=clock)
    await r.refresh("cdc_hospital_load")
    assert r.envelope("cdc_hospital_load")["fetch"]["error"] == "no hospital data for MO"
