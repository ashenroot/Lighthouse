"""FDA drug shortages: counts by drug, new listings, and a watch list."""
import pytest

from conftest import feeds, make_client
from fresnel.config import SourceCfg
from fresnel.relay import Relay


@pytest.mark.asyncio
async def test_records_group_by_drug_and_new_listings_turn_it_active(config, clock):
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("fda_drug_shortages")
    env = r.envelope("fda_drug_shortages")
    d = env["data"]
    assert env["status"] == "active" and env["summary"] == "4 drugs on the shortage list (2 with an unavailable product, 1 limited); 2 newly listed in 30 days"
    assert d["counts"] == {"Available": 1, "Limited Availability": 1, "Unavailable": 2}  # the "Unvailable" typo in the data still counts
    assert [x["name"] for x in d["new"]] == ["Amoxicillin Oral Suspension", "Ifosfamide Injection"]
    assert [x["name"] for x in d["drugs"]] == ["Amoxicillin Oral Suspension", "Ifosfamide Injection", "Lisdexamfetamine Dimesylate Capsule", "Carboplatin Injection"]
    config.sources["fda_drug_shortages"] = SourceCfg(params={"list": "none"})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("fda_drug_shortages")
    assert r.envelope("fda_drug_shortages")["data"]["drugs"] == []
    ifos = next(x for x in d["new"] if x["name"].startswith("Ifos"))
    assert ifos["availability"] == "Unavailable" and ifos["presentations"] == 3 and ifos["posted"] == "2026-09-20"


@pytest.mark.asyncio
async def test_watch_list_active_listed_and_not_listed(config, clock):
    config.sources["fda_drug_shortages"] = SourceCfg(params={"new_active": 9, "watch": ["LISDEX", "carboplatin", "insulin"]})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("fda_drug_shortages")
    env = r.envelope("fda_drug_shortages")
    w = {x["term"]: x for x in env["data"]["watch"]}
    assert w["LISDEX"]["active"] and w["carboplatin"]["listed"] and not w["carboplatin"]["active"] and not w["insulin"]["listed"]
    assert env["status"] == "active" and env["summary"].startswith("Watched drug short: LISDEX; 4 drugs on the shortage list")
    config.sources["fda_drug_shortages"] = SourceCfg(params={"new_active": 9, "watch": ["carboplatin", "insulin"]})
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("fda_drug_shortages")
    env = r.envelope("fda_drug_shortages")
    assert env["status"] == "nominal" and env["summary"].endswith("watching 2, 1 on the list (carboplatin)")


@pytest.mark.asyncio
async def test_pages_are_followed_and_bad_data_fails_visibly(config, clock, monkeypatch):
    from fresnel.sources import fda_drug_shortages as mod
    monkeypatch.setattr(mod, "PAGE_SIZE", 3)
    r = Relay(config, client=make_client(), now=clock)
    await r.refresh("fda_drug_shortages")
    assert r.envelope("fda_drug_shortages")["data"]["total"] == 4  # all three pages of records were read
    monkeypatch.setattr(mod, "PAGE_SIZE", 1000)
    old = lambda request: {"meta": {"last_updated": "2026-09-01", "results": {"total": 1}}, "results": [{"generic_name": "X", "availability": "Available"}]}
    r = Relay(config, client=make_client(data={**feeds(), "/drug/shortages.json": old}), now=clock)
    await r.refresh("fda_drug_shortages")
    assert r.envelope("fda_drug_shortages")["fetch"]["error"] == "upstream data is 43 days old"
    none = lambda request: {"meta": {"last_updated": "2026-10-13", "results": {"total": 0}}, "results": []}
    r = Relay(config, client=make_client(data={**feeds(), "/drug/shortages.json": none}), now=clock)
    await r.refresh("fda_drug_shortages")
    assert r.envelope("fda_drug_shortages")["fetch"]["error"] == "no shortage records returned"
