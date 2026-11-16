"""CBP border wait times: commercial truck waits at land crossings."""
import pytest

from conftest import _cbp_xml, feeds, make_client
from fresnel.config import SourceCfg
from fresnel.relay import Relay


async def run(config, clock, xml=None, **params):
    if params:
        config.sources["cbp_border_waits"] = SourceCfg(params=params)
    data = feeds()
    if xml is not None:
        data["bwt.cbp.gov"] = lambda request: xml
    r = Relay(config, client=make_client(data=data), now=clock)
    await r.refresh("cbp_border_waits")
    return r.envelope("cbp_border_waits")


@pytest.mark.asyncio
async def test_a_long_truck_wait_turns_it_active(config, clock):
    env = await run(config, clock, delay_min=50)
    assert env["status"] == "active"
    assert env["summary"] == "longest truck wait 56 min at Pembina"
    assert [c["port"] for c in env["data"]["listed"]][:2] == ["Pembina", "Detroit"]
    assert env["data"]["pending"] == 1 and env["data"]["reporting"] == 4  # the Update Pending crossing is counted, not ranked


@pytest.mark.asyncio
async def test_short_waits_are_nominal_and_name_the_longest(config, clock):
    env = await run(config, clock)  # default bar is 60 minutes
    assert env["status"] == "nominal"
    assert env["summary"] == "No truck wait over 60 min; longest 56 min at Pembina"


@pytest.mark.asyncio
async def test_a_closed_port_turns_it_active(config, clock):
    env = await run(config, clock, xml=_cbp_xml(port_status="Closed"))
    assert env["status"] == "active" and "port closed: Sweetgrass" in env["summary"]
    assert env["data"]["closed"] == ["Sweetgrass"]


@pytest.mark.asyncio
async def test_border_and_port_filters(config, clock):
    env = await run(config, clock, borders=["Mexican Border"])
    assert [c["port"] for c in env["data"]["listed"]] == ["Laredo"]
    env = await run(config, clock, borders=[], ports=["detroit"])
    assert [c["port"] for c in env["data"]["listed"]] == ["Detroit"]


@pytest.mark.asyncio
async def test_a_stale_or_broken_feed_is_a_failure_not_calm(config, clock):
    env = await run(config, clock, xml=_cbp_xml(updated="2026-10-13 07:40:00"))
    assert env["fetch"]["ok"] is False and "old" in env["fetch"]["error"]
    env = await run(config, clock, xml="<html>maintenance</html>")
    assert env["fetch"]["ok"] is False
    env = await run(config, clock, xml="<border_wait_time><last_updated_date>2026-10-14</last_updated_date>"
                                       "<last_updated_time>07:40:00</last_updated_time></border_wait_time>")
    assert env["fetch"]["ok"] is False and "no crossings" in env["fetch"]["error"]
