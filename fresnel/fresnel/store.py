"""Small on-disk store so a restart serves last-good data instead of a blank board."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _write(self, path: Path, obj: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)

    def _read(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    def _checked(self, name: str) -> str:
        if not _NAME.match(name):
            raise ValueError(f"bad name: {name!r}")
        return name

    # pulled sources
    def load_state(self, source_id: str) -> dict | None:
        return self._read(self.root / "cache" / f"{self._checked(source_id)}.json")

    def save_state(self, source_id: str, state: dict) -> None:
        self._write(self.root / "cache" / f"{self._checked(source_id)}.json", state)

    # pushed slots: latest plus one archive file per day
    def load_push(self, slot: str) -> dict | None:
        return self._read(self.root / "push" / self._checked(slot) / "latest.json")

    def save_push(self, slot: str, record: dict, day: str) -> None:
        if not _DAY.match(day):
            raise ValueError(f"bad day: {day!r}")
        base = self.root / "push" / self._checked(slot)
        self._write(base / "latest.json", record)
        self._write(base / "archive" / f"{day}.json", record)

    def load_archive(self, slot: str, day: str) -> dict | None:
        if not _DAY.match(day):
            return None
        return self._read(self.root / "push" / self._checked(slot) / "archive" / f"{day}.json")
