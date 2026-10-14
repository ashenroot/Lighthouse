# Contributing

Thank you for helping. Lighthouse is a community-service tool, so the bar for a change is: does it make the board more useful or more reliable for people anywhere, without hardcoding any one place?

## Ground rules

- **Nothing location-specific in code, tests or docs.** Use configuration. The examples in this repository use a neutral location on purpose.
- **Relay and board stay separate.** Fresnel fetches, judges and serves; the board only displays. Secrets live on the relay and never reach the browser.
- **Pushed and fetched text is untrusted.** Render it as text, never as HTML.
- Keep dependencies few. A new one needs a reason.

## Setup

```
cd fresnel
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

To run it locally: `fresnel init`, then `fresnel run`, and open http://127.0.0.1:8787/ (the relay serves the board when `server.board_dir` is set).

## Adding a data source

Sources are the most welcome contribution. Each one fetches a single public feed and judges its own status. Look at `fresnel/fresnel/sources/cisa_kev.py` for a complete small example.

1. Create `fresnel/fresnel/sources/<name>.py` with a class that sets `id`, `title`, `panel`, `default_interval` and `default_params`, and implements `async def fetch(self) -> Result`. Return `status="active"` only when something deserves attention.
2. Register it in `fresnel/fresnel/sources/__init__.py`.
3. Add fixture data to `tests/conftest.py` and assertions to `tests/test_sources.py`. Tests never touch the network.
4. Add a renderer for its id in `board/app.js` if it needs more than the default rows.
5. Document it in the sources table in `fresnel/README.md` and in `config.example.yaml`.

Good sources are free, need no key (or an optional one), come from an authoritative publisher, and say what they are in plain words. Mark scrapers of pages with no stable feed as `tier = "fragile"`.

## Pull requests

- Keep them small and focused, with tests.
- CI must pass: the test suite on Python 3.10 to 3.13, a syntax check of the board, and shellcheck on the installer.
- Describe what changed for a person running the relay, not only what changed in the code.
