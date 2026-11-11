"""API keys for the sources that need one.

Keys never go in config.yaml, the repository or the board. `fresnel key set <name>` stores one in a file under
the data directory (mode 600), the way the mailbox password is stored, and a source reads it when it fetches,
so a new key takes effect without a restart. An environment variable, FRESNEL_KEY_<NAME>, overrides the file
for container setups. A source that needs a key and has none is not an error: it is listed under Setup on the
board, and its panel is not marked unknown on its account.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KeySpec:
    name: str
    label: str  # how the board and the messages name the provider
    signup_url: str


KEYS = {
    "eia": KeySpec("eia", "EIA", "https://www.eia.gov/opendata/register.php"),
    "airnow": KeySpec("airnow", "AirNow", "https://docs.airnowapi.org/account/request/"),
}

# A key goes into a URL query string, so only the characters real keys use are accepted.
_VALID = re.compile(r"[A-Za-z0-9_.~-]{8,200}")


class KeyFormatError(ValueError):
    """The key value is not acceptable. The message never contains the value."""


def env_name(name: str) -> str:
    return "FRESNEL_KEY_" + name.upper()


def key_path(name: str, data_dir: str) -> Path:
    if name not in KEYS:
        raise KeyFormatError(f"unknown key {name!r}; known: {', '.join(sorted(KEYS))}")
    return Path(data_dir) / "keys" / name


def load_key(name: str, data_dir: str) -> str | None:
    value = os.environ.get(env_name(name), "").strip()
    if not value:
        try:
            value = key_path(name, data_dir).read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return value if _VALID.fullmatch(value) else None


def key_origin(name: str, data_dir: str) -> str | None:
    """Where the usable key comes from: 'env', 'file', or None."""
    if _VALID.fullmatch(os.environ.get(env_name(name), "").strip()):
        return "env"
    return "file" if load_key(name, data_dir) else None


def clean(value: str) -> str:
    value = value.strip()
    if not _VALID.fullmatch(value):
        raise KeyFormatError("that does not look like an API key (8 to 200 letters, digits, dashes or underscores, no spaces)")
    return value


def save_key(name: str, value: str, data_dir: str) -> Path:
    value = clean(value)
    path = key_path(name, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value + "\n")
    os.chmod(path, 0o600)
    if os.geteuid() == 0:  # keep the relay's own user able to read what root just wrote
        owner = Path(data_dir).stat()
        for p in (path.parent, path):
            os.chown(p, owner.st_uid, owner.st_gid)
    return path


def remove_key(name: str, data_dir: str) -> bool:
    try:
        key_path(name, data_dir).unlink()
        return True
    except FileNotFoundError:
        return False
