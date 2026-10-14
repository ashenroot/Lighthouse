"""Fresnel configuration: one YAML file, validated at startup."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .push import SLOT_MODELS


class ConfigError(Exception):
    pass


class Strict(BaseModel):
    """Unknown keys are errors, so a typo in config.yaml is reported instead of silently ignored."""

    model_config = ConfigDict(extra="forbid")


class Location(Strict):
    name: str = "Home"
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    state: str = Field(min_length=2, max_length=2)
    county_fips: str

    @field_validator("county_fips")
    @classmethod
    def _fips(cls, v: str) -> str:
        if not (len(v) == 5 and v.isdigit()):
            raise ValueError("county_fips must be 5 digits, e.g. '29095'")
        return v

    @property
    def state_fips(self) -> str:
        return self.county_fips[:2]

    @property
    def county_code(self) -> str:
        return self.county_fips[2:]

    # Anything the relay publishes (links, snapshot) uses coordinates rounded to
    # 0.1 degree (about 11 km), so a public relay does not reveal a precise spot.
    @property
    def public_lat(self) -> float:
        return round(self.lat, 1)

    @property
    def public_lon(self) -> float:
        return round(self.lon, 1)

    def public(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.upper(),
            "county_fips": self.county_fips,
            "approx_lat": self.public_lat,
            "approx_lon": self.public_lon,
        }


class Server(Strict):
    host: str = "127.0.0.1"
    port: int = 8787
    cors_origins: list[str] = Field(default_factory=list)
    data_dir: str = "./data"
    board_dir: str | None = None  # serve the Lighthouse board from this directory at /
    public_url: str | None = None  # where the outside world reaches this relay, used by `fresnel brief-step`


class Http(Strict):
    user_agent: str = "lighthouse-fresnel (set-your-contact@example.com)"
    timeout: float = 20.0


class SourceCfg(Strict):
    enabled: bool = True
    interval: int | None = Field(default=None, ge=60)
    stale_after: int | None = Field(default=None, ge=60)
    params: dict[str, Any] = Field(default_factory=dict)
    proxy: str | None = None  # fetch this source through an HTTP(S) or SOCKS5 proxy, e.g. when the publisher blocks your server's network

    @field_validator("proxy")
    @classmethod
    def _proxy_url(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^(https?|socks5h?)://[^\s/]+", v):
            raise ValueError("proxy must be an http://, https:// or socks5:// URL, e.g. socks5://127.0.0.1:1080")
        return v


class PushSlot(Strict):
    stale_after: int = Field(default=93600, ge=60)  # 26 hours
    max_bytes: int = Field(default=200_000, ge=1024)


class Foghorn(Strict):
    """ntfy notifications. Alerts are on whenever a topic is set. The topic name acts as a password."""

    enabled: bool = True
    url: str = "https://ntfy.sh"
    topic: str | None = None
    token: str | None = None  # optional bearer token for a private ntfy server
    priority: str = "high"


class Mailbox(Strict):
    """Read the daily brief from an IMAP mailbox instead of (or as well as) accepting a push."""

    enabled: bool = False
    host: str = "imap.gmail.com"
    port: int = 993
    user: str = ""
    password_file: str = "mailbox.password"  # relative paths resolve against the data directory
    folder: str = "INBOX"
    from_addrs: list[str] = Field(default_factory=list)  # optional; default is the mailbox user itself
    subject_contains: str = ""  # only messages whose subject contains this are accepted
    require_auth: bool = False  # set true for an inbox that strangers can write to: needs a passing DKIM or SPF result
    lookback_days: int = Field(default=3, ge=1, le=30)
    interval: int = Field(default=900, ge=60)  # seconds between polls


class BriefNtfy(Strict):
    """Read the daily brief from a private ntfy topic that the brief job posts to."""

    enabled: bool = True
    url: str = "https://ntfy.sh"
    topic: str | None = None  # the topic name is the secret; `fresnel ntfy-topic` makes one
    token: str | None = None  # optional access token, for a protected topic
    lookback_days: int = Field(default=2, ge=1, le=7)
    interval: int = Field(default=600, ge=60)  # seconds between checks


def _default_push() -> dict[str, PushSlot]:
    return {name: PushSlot() for name in SLOT_MODELS}


class Config(Strict):
    location: Location
    server: Server = Field(default_factory=Server)
    http: Http = Field(default_factory=Http)
    sources: dict[str, SourceCfg] = Field(default_factory=dict)
    push: dict[str, PushSlot] = Field(default_factory=_default_push)
    foghorn: Foghorn = Field(default_factory=Foghorn)
    mailbox: Mailbox = Field(default_factory=Mailbox)
    brief_ntfy: BriefNtfy = Field(default_factory=BriefNtfy)
    config_path: str | None = Field(default=None, exclude=True)  # set by load_config

    @model_validator(mode="after")
    def _check(self) -> "Config":
        unknown = set(self.push) - set(SLOT_MODELS)
        if unknown:
            raise ValueError(f"unknown push slots: {sorted(unknown)}; known: {sorted(SLOT_MODELS)}")
        return self


APP_DIR = Path(__file__).resolve().parents[1]  # the fresnel/ folder of the checkout
SEARCH_PATHS = ("config.yaml", str(APP_DIR / "config.yaml"), "/etc/lighthouse/config.yaml")


def default_path() -> Path:
    """$FRESNEL_CONFIG, else ./config.yaml, else config.yaml in the app folder, else /etc/lighthouse/config.yaml."""
    env = os.environ.get("FRESNEL_CONFIG")
    if env:
        return Path(env).expanduser()
    for candidate in SEARCH_PATHS:
        try:
            if Path(candidate).is_file():
                return Path(candidate)
        except OSError:  # an unreadable folder, such as ./ when run as another user: keep looking
            continue
    return Path(SEARCH_PATHS[0])


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path).expanduser() if path else default_path()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {p} (create one with: fresnel init)")
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {exc}")
    raw = raw or {}
    if "tokens" in raw:
        raise ConfigError(
            "the tokens: section is no longer used. Remove it from the config and create push tokens "
            "with: fresnel token add <name> --slots brief"
        )
    try:
        config = Config.model_validate(raw)
    except ValidationError as exc:
        lines = [f"  {'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise ConfigError("invalid config:\n" + "\n".join(lines))
    # Relative paths are relative to the config file, so the relay behaves the same from any directory.
    server = config.server
    server.data_dir = str((p.parent / server.data_dir).resolve())
    if server.board_dir:
        server.board_dir = str((p.parent / server.board_dir).resolve())
    config.config_path = str(p.resolve())
    return config
