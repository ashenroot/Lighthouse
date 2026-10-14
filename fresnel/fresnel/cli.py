"""The `fresnel` command: run the relay, set it up, manage tokens, and update it."""
from __future__ import annotations

import argparse
import json
import os
import pwd
import secrets
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import httpx
import uvicorn

from . import __version__
from .config import APP_DIR, Config, ConfigError, default_path, load_config
from .push import SLOT_MODELS
from .sources import REGISTRY
from .tokens import TokenError, TokenStore

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE = "fresnel"


def fail(message: str) -> "None":
    sys.exit(f"fresnel: {message}")


def load(args) -> Config:
    try:
        return load_config(getattr(args, "config", None))
    except ConfigError as exc:
        fail(str(exc))


# ---------- run / check ----------

def cmd_run(args) -> None:
    from .app import create_app

    config = load(args)
    host = getattr(args, "host", None) or config.server.host
    port = getattr(args, "port", None) or config.server.port
    try:
        app = create_app(config)
    except ValueError as exc:
        fail(str(exc))
    uvicorn.run(app, host=host, port=port, log_level="info")


def cmd_check(args) -> None:
    from .mailbox import password_path

    from .relay import Relay

    config = load(args)
    if args.print:
        print({"port": config.server.port, "host": config.server.host, "data_dir": config.server.data_dir}[args.print])
        return
    relay = Relay(config)
    loc = config.location
    print(f"config        {config.config_path}")
    print(f"location      {loc.name} ({loc.state}, county {loc.county_fips}, about {loc.public_lat}, {loc.public_lon})")
    print(f"listens on    {config.server.host}:{config.server.port}")
    print(f"data          {config.server.data_dir}")
    board = config.server.board_dir
    print(f"board         {board if board else 'not served by this relay'}")
    print(f"public url    {config.server.public_url or 'not set (needed by: fresnel brief-step)'}")
    print(f"sources       {len(relay.sources)} on: {', '.join(relay.sources)}")
    skipped = sorted(set(REGISTRY) - set(relay.sources))
    if skipped:
        print(f"skipped       {', '.join(skipped)} (disabled, or needs settings such as airports)")
    tokens = relay.tokens.list()
    print(f"push tokens   {', '.join(t['name'] for t in tokens) if tokens else 'none (create one: fresnel token add brief-job --slots brief)'}")
    print(f"alerts        {'ntfy on' if relay.foghorn.enabled else 'off (set foghorn.topic to enable)'}")
    mb = relay.mailbox
    if mb.enabled:
        pw = "password saved" if password_path(mb.cfg, config.server.data_dir).is_file() else "no password yet: run fresnel mailbox set-password"
        print(f"brief source  mailbox {mb.cfg.user} / {mb.cfg.folder} ({pw})")
    elif relay.ntfy_brief.enabled:
        print("brief source  ntfy topic")
    else:
        print("brief source  push only (or none: set mailbox: or brief_ntfy: in the config)")
    import asyncio

    asyncio.run(relay.client.aclose())


# ---------- tokens ----------

def _store(args) -> TokenStore:
    config = load(args)
    return TokenStore(Path(config.server.data_dir) / "tokens.json")


def cmd_token(args) -> None:
    store = _store(args)
    if args.action == "add":
        slots = [s.strip() for s in args.slots.split(",") if s.strip()]
        try:
            token = store.add(args.name, slots, set(SLOT_MODELS))
        except TokenError as exc:
            fail(str(exc))
        print(f"Created token {args.name!r} for slots {', '.join(slots)}. It is shown once; copy it now.", file=sys.stderr)
        print(token)
    elif args.action == "list":
        rows = store.list()
        if not rows:
            print("No tokens. Create one with: fresnel token add <name> --slots brief")
        for r in rows:
            print(f"{r['name']:<16} slots: {', '.join(r['slots']):<16} created {r['created']}")
    elif args.action == "revoke":
        if not store.revoke(args.name):
            fail(f"no token named {args.name!r}")
        print(f"Revoked {args.name!r}. Anything still using it will get 401.")


# ---------- mailbox ----------

def cmd_mailbox(args) -> None:
    import getpass
    from datetime import datetime, timezone

    from .mailbox import MailboxError, fetch_newest, password_path

    config = load(args)
    cfg, data_dir = config.mailbox, config.server.data_dir
    if args.action == "set-password":
        value = getpass.getpass("Mailbox password (app password): ") if sys.stdin.isatty() else sys.stdin.readline()
        value = value.strip().replace(" ", "")  # Google shows app passwords in groups of four
        if not value:
            fail("empty password")
        path = password_path(cfg, data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(value + "\n")
        os.chmod(path, 0o600)
        if os.geteuid() == 0:
            owner = Path(data_dir).stat()
            os.chown(path, owner.st_uid, owner.st_gid)
        print(f"Saved to {path} (mode 600). It is only read by the relay.")
        return
    problems = [n for n, v in (("mailbox.user", cfg.user), ("mailbox.subject_contains", cfg.subject_contains)) if not v]
    if problems:
        fail("set " + ", ".join(problems) + " in the config first")
    try:
        brief, skipped = fetch_newest(cfg, data_dir, datetime.now(timezone.utc), detail=True)
    except MailboxError as exc:
        fail(str(exc))
    print(f"Logged in to {cfg.host} as {cfg.user}, folder {cfg.folder}.")
    for reason in sorted(set(skipped)):
        print(f"  skipped a message: {reason}")
    if brief is None:
        print(f"No acceptable message from {', '.join(cfg.from_addrs or [cfg.user])} with subject containing {cfg.subject_contains!r} in the last {cfg.lookback_days} days.")
    else:
        print(f"Newest acceptable brief: {brief.title!r}, {brief.generated_at:%Y-%m-%d %H:%M} UTC, {len(brief.body)} characters.")
        print("The running relay will publish it on its next poll.")


# ---------- init ----------

def lookup_county(lat: float, lon: float) -> dict | None:
    """Resolve coordinates to state and county with the FCC's free Census area API."""
    try:
        r = httpx.get("https://geo.fcc.gov/api/census/area", params={"lat": lat, "lon": lon, "format": "json"}, timeout=15)
        r.raise_for_status()
        hit = r.json()["results"][0]
        return {"county_fips": hit["county_fips"], "county_name": hit["county_name"], "state": hit["state_code"]}
    except Exception:
        return None


def _ask(prompt: str, default: str = "", interactive: bool = True) -> str:
    if not interactive:
        return default
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _q(value) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON scalars and lists are valid YAML


def render_config(c: dict) -> str:
    lines = [
        "# Fresnel configuration, written by `fresnel init`. Edit freely, then run `fresnel check`.",
        "# Every option and its default is documented in config.example.yaml in the repository.",
        "",
        "location:",
        f"  name: {_q(c['name'])}",
        f"  lat: {c['lat']}",
        f"  lon: {c['lon']}",
        f"  state: {_q(c['state'])}",
        f"  county_fips: {_q(c['county_fips'])}",
        "",
        "server:",
        "  host: 127.0.0.1",
        f"  port: {c['port']}",
        f"  data_dir: {_q(c['data_dir'])}",
    ]
    lines.append(f"  board_dir: {_q(c['board_dir'])}   # the dashboard, served at /" if c["board_dir"] else "  # board_dir: /path/to/board   # serve the dashboard from this relay")
    lines.append(f"  public_url: {_q(c['public_url'])}" if c["public_url"] else "  # public_url: https://example.org/fresnel   # used by `fresnel brief-step`")
    lines += ["  cors_origins: []   # add the board's origin here only if it is hosted elsewhere", "", "http:",
              f"  user_agent: {_q(c['user_agent'])}   # NWS asks for a contact address", ""]
    if c["airports"]:
        lines += ["sources:", "  faa_airports:", "    params:", f"      airports: {_q(c['airports'])}", ""]
    else:
        lines += ["# sources:", "#   faa_airports:", "#     params:", "#       airports: [MCI]   # FAA status stays off until airports are listed", ""]
    if c["ntfy_topic"]:
        lines += ["foghorn:", f"  topic: {_q(c['ntfy_topic'])}   # alerts go to this ntfy topic; treat it like a password", ""]
    else:
        lines += ["# foghorn:", "#   topic: your-private-ntfy-topic   # set a topic to turn alerts on", ""]
    return "\n".join(lines)


def cmd_init(args) -> None:
    interactive = sys.stdin.isatty() and not args.yes
    out = Path(args.out) if args.out else (APP_DIR / "config.yaml" if os.geteuid() == 0 else Path("config.yaml"))
    if out.exists() and not args.force:
        fail(f"{out} already exists. Edit it, or pass --force to replace it.")

    def number(label: str, given: float | None) -> float:
        if given is not None:
            return given
        raw = _ask(label, interactive=interactive)
        try:
            return float(raw)
        except ValueError:
            fail(f"{label} is required (a decimal number)")

    if interactive:
        print("Lighthouse setup. Press Enter to accept a [default].\n")
    lat = number("Latitude (decimal degrees, e.g. 39.10)", args.lat)
    lon = number("Longitude (e.g. -94.58)", args.lon)
    found = lookup_county(lat, lon)
    if found:
        print(f"Found: {found['county_name']}, {found['state']} (FIPS {found['county_fips']})")
    else:
        print("Could not look up the county automatically.")
        state = _ask("State abbreviation (e.g. MO)", interactive=interactive).upper()
        fips = _ask("5-digit county FIPS (state + county, e.g. 29095)", interactive=interactive)
        if not state or not fips:
            fail("state and county FIPS are required when the lookup is unavailable")
        found = {"county_fips": fips, "county_name": "", "state": state}
    default_name = args.name or (f"{found['county_name']}, {found['state']}" if found["county_name"] else found["state"])
    name = _ask("Name shown on the board", default_name, interactive)
    email = args.email if args.email is not None else _ask("Contact email for the User-Agent (NWS asks for one)", "", interactive)
    airports = args.airports if args.airports is not None else _ask("Airports to watch, FAA codes (e.g. MCI, blank to skip)", "", interactive)
    public_url = args.public_url if args.public_url is not None else _ask("Public address of this relay, if known (e.g. https://example.org/fresnel)", "", interactive)
    topic = args.ntfy_topic if args.ntfy_topic is not None else _ask("ntfy topic for alerts (blank to skip, 'auto' to generate one)", "", interactive)
    if topic == "auto":
        topic = "lighthouse-" + secrets.token_hex(8)
    board = REPO_ROOT / "board"
    board_dir = str(board) if (board / "index.html").is_file() and not args.no_board else None
    data_dir = args.data_dir or "./data"  # relative to the config file

    text = render_config({
        "name": name, "lat": lat, "lon": lon, "state": found["state"], "county_fips": found["county_fips"],
        "port": args.port, "data_dir": data_dir, "board_dir": board_dir, "public_url": public_url.rstrip("/"),
        "user_agent": f"lighthouse-fresnel ({email})" if email else "lighthouse-fresnel (set-your-contact@example.com)",
        "airports": [a.strip().upper() for a in airports.split(",") if a.strip()],
        "ntfy_topic": topic,
    })
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    data_path = (out.parent / data_dir).resolve()
    data_path.mkdir(parents=True, exist_ok=True)
    if args.owner:
        user = pwd.getpwnam(args.owner)
        for p in (out, data_path):
            os.chown(p, user.pw_uid, user.pw_gid)
    print(f"\nWrote {out}")
    print("Next: fresnel check    (validate), then fresnel run    (start), or use the installer's service.")


# ---------- proxy snippet ----------

def cmd_proxy(args) -> None:
    config = load(args)
    path = "/" + args.path.strip("/")
    print(f"""# Add inside your existing HTTPS server block (nginx):
location = {path} {{ return 301 {path}/; }}

location {path}/ {{
    proxy_pass http://127.0.0.1:{config.server.port}/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 256k;
}}
# Then: nginx -t && systemctl reload nginx
# The relay is then reached at https://YOUR-HOST{path}  (set it as server.public_url)""")


# ---------- brief step ----------

def cmd_ntfy_topic(args) -> None:
    print("lighthouse-brief-" + secrets.token_hex(12))


def cmd_brief_step(args) -> None:
    config = load(args)
    if args.ntfy:
        cfg = config.brief_ntfy
        if not cfg.topic:
            fail("set brief_ntfy.topic in the config first. Here is a new random topic you can use:  " + "lighthouse-brief-" + secrets.token_hex(12))
        auth = f'-H "Authorization: Bearer {cfg.token}" ' if cfg.token else ""
        template = (Path(__file__).parent / "templates" / "brief_step_ntfy.txt").read_text(encoding="utf-8")
        text = (template.replace("@@NTFY_URL@@", cfg.url.rstrip("/")).replace("@@NTFY_TOPIC@@", cfg.topic)
                .replace("@@TODAY@@", date.today().strftime("%B %-d, %Y")).replace("@@AUTH@@", auth))
        if args.out:
            fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"Wrote {args.out} (contains the topic name; mode 600). Paste it at the end of your briefing prompt.", file=sys.stderr)
        else:
            print(text)
        return
    url = (args.url or config.server.public_url or "").rstrip("/")
    if not url:
        fail("no relay URL. Pass --url https://example.org/fresnel or set server.public_url in the config")
    token = args.token
    if not token:
        store = TokenStore(Path(config.server.data_dir) / "tokens.json")
        if args.rotate:
            store.revoke("brief-job")
        try:
            token = store.add("brief-job", ["brief"], set(SLOT_MODELS))
        except TokenError:
            fail("a token named 'brief-job' already exists and its value cannot be shown again. Use --rotate to replace it, or --token VALUE")
        print("Created a new token 'brief-job' for the brief slot.", file=sys.stderr)
    template = (Path(__file__).parent / "templates" / "brief_step.txt").read_text(encoding="utf-8")
    today = date.today().strftime("%B %-d, %Y")
    text = template.replace("@@RELAY_URL@@", url).replace("@@BRIEF_TOKEN@@", token).replace("@@TODAY@@", today)
    if args.out:
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Wrote {args.out} (contains the token; mode 600). Paste it at the end of your briefing prompt.", file=sys.stderr)
    else:
        print(text)


# ---------- update ----------

def _git(args_list, repo: Path, as_owner: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(as_owner + ["git", "-C", str(repo)] + args_list, capture_output=True, text=True)


def cmd_update(args) -> None:
    repo = REPO_ROOT
    if not (repo / ".git").exists():
        fail(f"{repo} is not a git checkout, so it cannot update itself. Reinstall from the repository.")
    owner = pwd.getpwuid(repo.stat().st_uid).pw_name
    as_owner = ["runuser", "-u", owner, "--"] if os.geteuid() == 0 and repo.stat().st_uid != 0 else []

    print("Checking for updates...", flush=True)
    fetched = _git(["fetch", "--quiet"], repo, as_owner)
    if fetched.returncode:
        fail("could not reach the repository: " + fetched.stderr.strip())
    behind = _git(["log", "--oneline", "HEAD..@{u}"], repo, as_owner).stdout.strip()
    if not behind:
        print(f"Already up to date (version {__version__}).")
        return
    print("Changes available:\n" + behind)
    if args.check:
        return
    print("Pulling the new code...", flush=True)
    pulled = _git(["pull", "--ff-only", "--quiet"], repo, as_owner)
    if pulled.returncode:
        fail("git pull failed (local changes in the checkout?):\n" + pulled.stderr.strip())
    print("Installing dependencies (up to a minute; it prints errors if it fails)...", flush=True)
    pip = subprocess.run(as_owner + [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--timeout", "30", "-e", str(repo / "fresnel")], capture_output=True, text=True, cwd=repo / "fresnel")  # not the caller's cwd: the owner may not be able to read it
    if pip.returncode:
        fail("dependency install failed:\n" + pip.stderr.strip())
    print("Updated.")
    if shutil.which("systemctl") and subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], stderr=subprocess.DEVNULL).returncode == 0:
        if os.geteuid() == 0:
            subprocess.run(["systemctl", "restart", SERVICE], check=False)
            print("Restarted the fresnel service.")
        else:
            print(f"Restart it to apply: sudo systemctl restart {SERVICE}")
    else:
        print("Restart the relay to apply the update.")


# ---------- argument parsing ----------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="config file (default: $FRESNEL_CONFIG, ./config.yaml, config.yaml in the app folder, /etc/lighthouse/config.yaml)")
    parser = argparse.ArgumentParser(prog="fresnel", description="Fresnel, the Lighthouse relay", parents=[common])
    parser.add_argument("--version", action="version", version=f"fresnel {__version__}")
    sub = parser.add_subparsers(dest="cmd", metavar="command")

    p = sub.add_parser("run", parents=[common], help="run the relay (the default)")
    p.add_argument("--host"); p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("check", parents=[common], help="validate the config and show what is enabled")
    p.add_argument("--print", choices=["port", "host", "data_dir"], help="print one value only")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("init", parents=[common], help="create a config file by answering a few questions")
    p.add_argument("--out", help="where to write it (default: config.yaml in the app folder as root, else ./config.yaml)")
    p.add_argument("--force", action="store_true"); p.add_argument("--yes", action="store_true", help="never prompt; use flags and defaults")
    p.add_argument("--lat", type=float); p.add_argument("--lon", type=float); p.add_argument("--name")
    p.add_argument("--email"); p.add_argument("--airports"); p.add_argument("--public-url", dest="public_url")
    p.add_argument("--ntfy-topic", dest="ntfy_topic"); p.add_argument("--port", type=int, default=8787)
    p.add_argument("--data-dir", dest="data_dir"); p.add_argument("--no-board", action="store_true")
    p.add_argument("--owner", help="account that runs the relay; it will own the config and data")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("token", help="manage push tokens")
    ts = p.add_subparsers(dest="action", required=True)
    a = ts.add_parser("add", parents=[common], help="create a token (shown once)")
    a.add_argument("name"); a.add_argument("--slots", required=True, help="comma-separated slots, e.g. brief, or * for all")
    ts.add_parser("list", parents=[common], help="list tokens")
    r = ts.add_parser("revoke", parents=[common], help="delete a token"); r.add_argument("name")
    p.set_defaults(func=cmd_token)

    p = sub.add_parser("mailbox", parents=[common], help="read the daily brief from a mailbox: set the password, test the connection")
    p.add_argument("action", choices=["set-password", "test"])
    p.set_defaults(func=cmd_mailbox)

    p = sub.add_parser("brief-step", parents=[common], help="print the push step to add to your briefing job, with URL and token filled in")
    p.add_argument("--url"); p.add_argument("--token"); p.add_argument("--rotate", action="store_true"); p.add_argument("--out")
    p.add_argument("--ntfy", action="store_true", help="print the step that posts the brief to your ntfy topic instead")
    p.set_defaults(func=cmd_brief_step)

    p = sub.add_parser("ntfy-topic", help="print a new random topic name for brief_ntfy.topic")
    p.set_defaults(func=cmd_ntfy_topic)

    p = sub.add_parser("proxy-snippet", parents=[common], help="print an nginx block that publishes the relay under a path")
    p.add_argument("--path", default="/fresnel")
    p.set_defaults(func=cmd_proxy)

    p = sub.add_parser("update", parents=[common], help="pull the latest version, reinstall and restart")
    p.add_argument("--check", action="store_true", help="only list available changes")
    p.set_defaults(func=cmd_update)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args.func = cmd_run  # bare `fresnel` runs the relay
    try:
        args.func(args)
    except KeyboardInterrupt:
        pass
