"""Push tokens: created and revoked with the `fresnel token` command, stored hashed.

The relay keeps only a SHA-256 hash of each token, so the file on disk cannot be used to push.
The token itself is shown once, when it is created. Tokens are random and long, which is why a
plain hash is enough. The relay re-reads the file when it changes, so no restart is needed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

PREFIX = "lh_"
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class TokenError(Exception):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TokenStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._mtime: float | None = None
        self._cache: dict = {}

    def _load(self) -> dict:
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            self._mtime, self._cache = None, {}
            return self._cache
        if mtime != self._mtime:
            try:
                self._cache = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                self._cache = {}
            self._mtime = mtime
        return self._cache

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.path)
        # When root runs the command, keep the file readable by the account that runs the relay.
        if os.geteuid() == 0:
            st = self.path.parent.stat()
            os.chown(self.path, st.st_uid, st.st_gid)
        self._mtime = None

    # management

    def add(self, name: str, slots: list[str], known_slots: set[str]) -> str:
        if not _NAME.match(name):
            raise TokenError("name must be lowercase letters, digits, - or _, starting with a letter")
        bad = [s for s in slots if s != "*" and s not in known_slots]
        if bad or not slots:
            raise TokenError(f"unknown slot(s) {bad or slots}; known: {sorted(known_slots)} or *")
        data = dict(self._load())
        if name in data:
            raise TokenError(f"a token named {name!r} already exists; revoke it first or pick another name")
        token = PREFIX + secrets.token_hex(24)
        data[name] = {
            "hash": _hash(token),
            "slots": sorted(set(slots)),
            "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        self._save(data)
        return token

    def revoke(self, name: str) -> bool:
        data = dict(self._load())
        if name not in data:
            return False
        del data[name]
        self._save(data)
        return True

    def list(self) -> list[dict]:
        return [{"name": n, "slots": v["slots"], "created": v["created"]} for n, v in sorted(self._load().items())]

    # checking

    def any(self) -> bool:
        return bool(self._load())

    def verify(self, token: str) -> dict | None:
        """Return {"name", "slots"} for a valid token, else None."""
        digest = _hash(token)
        found = None
        for name, rec in self._load().items():  # no early exit: constant work per request
            if hmac.compare_digest(rec["hash"], digest):
                found = {"name": name, "slots": rec["slots"]}
        return found
