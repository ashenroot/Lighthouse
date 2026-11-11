"""API keys: storage, the waiting and rejected states, and that a key never leaks."""
import io
import json
import os

import httpx
import pytest

from conftest import feeds, make_client
from fresnel import cli
from fresnel.keys import KeyFormatError, key_origin, load_key, remove_key, save_key
from fresnel.relay import Relay
from fresnel.sources import REGISTRY
from fresnel.sources.base import Result, Source

KEY = "abcd1234EFGH5678"


class Dummy(Source):
    id = "dummy_keyed"
    title = "Dummy keyed source"
    panel = "environment"
    page = "https://example.org/"
    key = "eia"
    default_interval = 600

    async def fetch(self) -> Result:
        resp = await self.ctx.client.get("https://example.org/data", params={"api_key": self.api_key()})
        self.check_key(resp)
        resp.raise_for_status()
        return Result(data={"n": 1}, summary="Dummy reading", status="nominal", source_url="https://example.org/")


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setitem(REGISTRY, "dummy_keyed", Dummy)
    for name in ("EIA", "AIRNOW"):
        monkeypatch.delenv("FRESNEL_KEY_" + name, raising=False)


def client_for(handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    client = make_client(data={**feeds(), "example.org": wrapped})
    client.calls = calls
    return client


def ok(request):
    return {"ok": True} if request.url.params.get("api_key") == KEY else httpx.Response(403, text="bad key")


def test_key_store_permissions_environment_override_and_validation(tmp_path, monkeypatch):
    monkeypatch.delenv("FRESNEL_KEY_EIA", raising=False)
    data = str(tmp_path / "data")
    os.makedirs(data)
    assert load_key("eia", data) is None and key_origin("eia", data) is None
    path = save_key("eia", "  " + KEY + "\n", data)
    assert load_key("eia", data) == KEY and key_origin("eia", data) == "file"
    assert (os.stat(path).st_mode & 0o777) == 0o600 and (os.stat(path.parent).st_mode & 0o777) == 0o700
    monkeypatch.setenv("FRESNEL_KEY_EIA", "fromenvironment123")
    assert load_key("eia", data) == "fromenvironment123" and key_origin("eia", data) == "env"
    monkeypatch.delenv("FRESNEL_KEY_EIA")
    for bad in ("short", "has space in it 123", "a&b=c-12345678", "x" * 201, ""):
        with pytest.raises(KeyFormatError) as exc:
            save_key("eia", bad, data)
        assert bad.strip() == "" or bad not in str(exc.value)  # the message never repeats the value
    with pytest.raises(KeyFormatError):
        save_key("nosuchkey", KEY, data)
    assert remove_key("eia", data) is True and remove_key("eia", data) is False and load_key("eia", data) is None


@pytest.mark.asyncio
async def test_source_without_a_key_is_listed_under_setup_and_asks_nothing(config, clock, keyed):
    client = client_for(ok)
    r = Relay(config, client=client, now=clock)
    await r.refresh("dummy_keyed")
    assert client.calls == []  # no request until there is a key
    snap = r.snapshot()
    assert "dummy_keyed" not in snap["sources"] and "dummy_keyed" not in snap["panels"].get("environment", {}).get("sources", [])
    mine = [item for item in snap["setup"] if item["id"] == "dummy_keyed"]
    assert "eia_fuel_prices" in [item["id"] for item in snap["setup"]]  # a real source that uses the same key
    assert mine == [{"id": "dummy_keyed", "title": "Dummy keyed source", "panel": "environment", "key": "eia", "label": "EIA",
                              "signup_url": "https://www.eia.gov/opendata/register.php", "command": "fresnel key set eia"}]
    env = r.envelope("dummy_keyed")
    assert env["status"] == "unknown" and env["summary"] == "Needs a free EIA key. Run: fresnel key set eia"
    assert "dummy_keyed" not in [row["id"] for row in r.health()["sources"]]


@pytest.mark.asyncio
async def test_key_set_later_is_used_without_a_restart_and_never_appears_in_output(config, clock, keyed):
    client = client_for(ok)
    r = Relay(config, client=client, now=clock)
    save_key("eia", KEY, config.server.data_dir)
    assert [i["id"] for i in r.snapshot()["setup"] if i["key"] == "eia"] == []
    await r.refresh("dummy_keyed")
    assert client.calls[0].url.params["api_key"] == KEY
    snap = r.snapshot()
    assert snap["sources"]["dummy_keyed"]["status"] == "nominal" and snap["sources"]["dummy_keyed"]["summary"] == "Dummy reading"
    assert KEY not in json.dumps(snap) + json.dumps(r.health())


@pytest.mark.asyncio
async def test_a_rejected_key_is_a_visible_failure_and_is_retried_soon(config, clock, keyed):
    client = client_for(lambda request: httpx.Response(403, text="nope"))
    r = Relay(config, client=client, now=clock)
    save_key("eia", KEY, config.server.data_dir)
    await r.refresh("dummy_keyed")
    env = r.envelope("dummy_keyed")
    message = "EIA rejected the key (HTTP 403). Replace it with: fresnel key set eia"
    assert env["status"] == "unknown" and env["summary"] == message and env["fetch"]["error"] == message
    assert r._recheck["dummy_keyed"] == 300
    assert KEY not in json.dumps(r.snapshot()) + json.dumps(r.health())
    save_key("eia", "anothergoodkey99", config.server.data_dir)
    client2 = client_for(lambda request: {"ok": True})
    r.sources["dummy_keyed"].ctx = r.sources["dummy_keyed"].ctx.__class__(client=client2, location=config.location, now=clock, keys=r.key)
    await r.refresh("dummy_keyed")
    assert r.envelope("dummy_keyed")["status"] == "nominal" and "dummy_keyed" not in r._recheck


def make_cfg(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"location: {{name: T, lat: 39.1, lon: -94.6, state: MO, county_fips: '29095'}}\nserver: {{data_dir: {tmp_path / 'data'}}}\n")
    return cfg


def test_cli_key_set_list_remove(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FRESNEL_KEY_EIA", raising=False)
    monkeypatch.delenv("FRESNEL_KEY_AIRNOW", raising=False)
    cfg = make_cfg(tmp_path)
    cli.main(["--config", str(cfg), "key", "list"])
    out = capsys.readouterr().out
    assert "eia" in out and "not set" in out and "https://www.eia.gov/opendata/register.php" in out
    monkeypatch.setattr("sys.stdin", io.StringIO(KEY + "\n"))
    cli.main(["--config", str(cfg), "key", "set", "eia"])
    capsys.readouterr()
    assert (tmp_path / "data" / "keys" / "eia").read_text().strip() == KEY
    cli.main(["--config", str(cfg), "key", "list"])
    out = capsys.readouterr().out
    assert KEY not in out and "set" in out.splitlines()[0]
    monkeypatch.setattr("sys.stdin", io.StringIO("bad key\n"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--config", str(cfg), "key", "set", "airnow"])
    assert "bad key" not in str(exc.value)
    cli.main(["--config", str(cfg), "key", "remove", "eia"])
    assert "Removed" in capsys.readouterr().out and not (tmp_path / "data" / "keys" / "eia").exists()
