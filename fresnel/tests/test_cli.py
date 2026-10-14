import json
import os

import pytest
import yaml

from fresnel import cli
from fresnel.config import ConfigError, load_config
from fresnel.tokens import TokenError, TokenStore

FOUND = {"county_fips": "29095", "county_name": "Jackson County", "state": "MO"}


@pytest.fixture
def no_net(monkeypatch):
    monkeypatch.setattr(cli, "lookup_county", lambda lat, lon: FOUND)


def run(*argv):
    cli.main(list(argv))


def test_init_writes_a_loadable_config(tmp_path, no_net, capsys):
    out = tmp_path / "config.yaml"
    run("init", "--yes", "--out", str(out), "--data-dir", "./data", "--lat", "39.10", "--lon", "-94.58", "--email", "me@example.org",
        "--airports", "mci, bna", "--public-url", "https://x.example/fresnel/", "--ntfy-topic", "auto")
    cfg = load_config(out)
    assert cfg.location.county_fips == "29095" and cfg.location.state == "MO"
    assert cfg.location.name == "Jackson County, MO"
    assert cfg.sources["faa_airports"].params["airports"] == ["MCI", "BNA"]
    assert cfg.server.public_url == "https://x.example/fresnel"
    assert cfg.foghorn.topic.startswith("lighthouse-")
    assert "me@example.org" in cfg.http.user_agent
    assert cfg.server.data_dir == str((tmp_path / "data").resolve())  # relative to the config file
    assert (os.stat(out).st_mode & 0o007) == 0  # not world readable


def test_init_refuses_to_overwrite(tmp_path, no_net):
    out = tmp_path / "config.yaml"
    out.write_text("x: 1")
    with pytest.raises(SystemExit):
        run("init", "--yes", "--out", str(out), "--data-dir", "./data", "--lat", "1", "--lon", "1")


def test_init_without_lookup_needs_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "lookup_county", lambda lat, lon: None)
    with pytest.raises(SystemExit):
        run("init", "--yes", "--out", str(tmp_path / "c.yaml"), "--data-dir", "./data", "--lat", "1", "--lon", "1")


def test_old_tokens_section_gets_a_clear_error(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"location": {"lat": 1, "lon": 1, "state": "MO", "county_fips": "29095"},
                                 "tokens": [{"name": "x", "secret_env": "Y"}]}))
    with pytest.raises(ConfigError, match="fresnel token add"):
        load_config(p)


def test_typo_in_config_is_reported(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"location": {"lat": 1, "lon": 1, "state": "MO", "county_fips": "29095"},
                                 "server": {"prot": 9}}))
    with pytest.raises(ConfigError, match="prot"):
        load_config(p)


def make_config(tmp_path, no_net):
    out = tmp_path / "config.yaml"
    run("init", "--yes", "--out", str(out), "--data-dir", "./data", "--lat", "39.10", "--lon", "-94.58", "--public-url", "https://x.example/fresnel")
    return out


def test_token_lifecycle(tmp_path, no_net, capsys):
    cfg = make_config(tmp_path, no_net)
    capsys.readouterr()
    run("--config", str(cfg), "token", "add", "brief-job", "--slots", "brief")
    token = capsys.readouterr().out.strip()
    assert token.startswith("lh_")
    store = TokenStore(tmp_path / "data" / "tokens.json")
    assert store.verify(token) == {"name": "brief-job", "slots": ["brief"]}
    assert store.verify("lh_wrong") is None
    stored = (tmp_path / "data" / "tokens.json").read_text()
    assert token not in stored  # only the hash is kept
    assert (os.stat(tmp_path / "data" / "tokens.json").st_mode & 0o077) == 0
    run("--config", str(cfg), "token", "list")
    assert "brief-job" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run("--config", str(cfg), "token", "add", "brief-job", "--slots", "brief")  # duplicate name
    run("--config", str(cfg), "token", "revoke", "brief-job")
    assert store.verify(token) is None


def test_token_rejects_unknown_slot(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    with pytest.raises(TokenError):
        store.add("x", ["nope"], {"brief"})
    with pytest.raises(TokenError):
        store.add("Bad Name", ["brief"], {"brief"})


def test_token_change_takes_effect_without_restart(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    reader = TokenStore(tmp_path / "t.json")  # stands in for the running relay
    assert not reader.any()
    token = store.add("a", ["*"], {"brief"})
    assert reader.verify(token)["name"] == "a"
    store.revoke("a")
    assert reader.verify(token) is None


def test_brief_step_fills_values_and_creates_token(tmp_path, no_net, capsys):
    cfg = make_config(tmp_path, no_net)
    capsys.readouterr()
    run("--config", str(cfg), "brief-step")
    out = capsys.readouterr().out
    assert "@@" not in out and "https://x.example/fresnel/v1/push/brief" in out
    token = [l.split()[-1] for l in out.splitlines() if l.strip().startswith("TOKEN:")][0]
    assert TokenStore(tmp_path / "data" / "tokens.json").verify(token)["slots"] == ["brief"]
    with pytest.raises(SystemExit):  # second run cannot reveal the existing token
        run("--config", str(cfg), "brief-step")
    run("--config", str(cfg), "brief-step", "--rotate")
    assert "lh_" in capsys.readouterr().out


def test_brief_step_needs_a_url(tmp_path, no_net):
    out = tmp_path / "config.yaml"
    run("init", "--yes", "--out", str(out), "--data-dir", "./data", "--lat", "1", "--lon", "1")
    with pytest.raises(SystemExit):
        run("--config", str(out), "brief-step")


def test_proxy_snippet_uses_configured_port(tmp_path, no_net, capsys):
    cfg = make_config(tmp_path, no_net)
    capsys.readouterr()
    run("--config", str(cfg), "proxy-snippet", "--path", "relay")
    out = capsys.readouterr().out
    assert "location /relay/ {" in out and "127.0.0.1:8787" in out


def test_check_runs(tmp_path, no_net, capsys):
    cfg = make_config(tmp_path, no_net)
    capsys.readouterr()
    run("--config", str(cfg), "check")
    out = capsys.readouterr().out
    assert "Jackson County, MO" in out and "push tokens   none" in out
    run("--config", str(cfg), "check", "--print", "port")
    assert capsys.readouterr().out.strip() == "8787"


def test_config_in_app_folder_is_found(tmp_path, monkeypatch):
    from fresnel import config as cfgmod

    app = tmp_path / "app"
    app.mkdir()
    (app / "config.yaml").write_text("location: {lat: 1.0, lon: 2.0}\n")
    monkeypatch.setattr(cfgmod, "SEARCH_PATHS", ("nowhere.yaml", str(app / "config.yaml"), "/etc/lighthouse/config.yaml"))
    monkeypatch.delenv("FRESNEL_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert cfgmod.default_path() == app / "config.yaml"


def test_unreadable_search_path_is_skipped(tmp_path, monkeypatch):
    from pathlib import Path

    from fresnel import config as cfgmod

    good = tmp_path / "config.yaml"
    good.write_text("location: {lat: 1.0, lon: 2.0, state: MO, county_fips: '29095'}\n")
    real = Path.is_file

    def flaky(self):
        if str(self) == "blocked.yaml":
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "is_file", flaky)
    monkeypatch.setattr(cfgmod, "SEARCH_PATHS", ("blocked.yaml", str(good)))
    monkeypatch.delenv("FRESNEL_CONFIG", raising=False)
    assert cfgmod.default_path() == good
