"""Mailbox puller: reads the daily brief from an IMAP mailbox and stores it in the brief slot.

For setups where the job that writes the brief cannot reach the relay directly but can send
email. Fresnel logs in with its own (read-only in practice) credentials, finds the newest
message that matches the configured sender and subject, and publishes its text.

A message is accepted only if all of these hold:
  * the From address is in `from_addrs`
  * the subject contains `subject_contains`
  * (when `require_auth` is on) the receiving server recorded a passing DKIM or SPF result
"""
from __future__ import annotations

import asyncio
import email
import email.policy
import email.utils
import imaplib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .config import Mailbox
    from .relay import Relay

MAX_BODY = 60000
MAX_CANDIDATES = 10  # newest matching messages examined per poll
FETCH_LIMIT = 2_000_000  # bytes; larger messages are skipped


class MailboxError(Exception):
    """A problem with a safe message to show in health output."""


@dataclass
class Brief:
    generated_at: datetime
    title: str
    body: str
    message_id: str


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    text = "".join(parser.out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def message_text(msg: email.message.EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = html_to_text(text)
    return text.strip()


def auth_passed(msg: email.message.EmailMessage) -> bool:
    """True when a receiving server recorded dkim=pass or spf=pass for this message."""
    for header in msg.get_all("Authentication-Results") or []:
        if re.search(r"\b(dkim|spf)=pass\b", str(header), re.I):
            return True
    return False


def sender_of(msg: email.message.EmailMessage) -> str:
    return email.utils.parseaddr(str(msg.get("From", "")))[1].lower()


def evaluate(raw: bytes, cfg: "Mailbox", detail: bool = False) -> tuple[Brief | None, str]:
    """Parse one message. Returns (brief, "") or (None, reason it was skipped)."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = sender_of(msg)
    if sender not in {a.lower() for a in (cfg.from_addrs or [cfg.user])}:
        return None, f"sender not allowed (From: {sender or 'unreadable'})" if detail else "sender not allowed"
    subject = str(msg.get("Subject", "")).strip()
    if cfg.subject_contains.lower() not in subject.lower():
        return None, "subject does not match"
    if cfg.require_auth and not auth_passed(msg):
        return None, "no passing DKIM/SPF result on the message (set mailbox.require_auth: false to skip this check)"
    body = message_text(msg)
    if not body:
        return None, "no text body"
    try:
        when = email.utils.parsedate_to_datetime(str(msg.get("Date")))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None, "unreadable Date header"
    return Brief(when.astimezone(timezone.utc), subject[:200], body[:MAX_BODY], str(msg.get("Message-ID", ""))), ""


def password_path(cfg: "Mailbox", data_dir: str) -> Path:
    return Path(data_dir) / cfg.password_file  # an absolute password_file replaces the data dir


def read_password(cfg: "Mailbox", data_dir: str) -> str:
    path = password_path(cfg, data_dir)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise MailboxError(f"cannot read password file {path}; create it with: fresnel mailbox set-password")
    if not value:
        raise MailboxError(f"password file {path} is empty")
    return value


def fetch_newest(cfg: "Mailbox", data_dir: str, now: datetime, connect: Callable = imaplib.IMAP4_SSL, detail: bool = False) -> tuple[Brief | None, list[str]]:
    """Blocking IMAP work. Returns the newest acceptable brief and the reasons others were skipped."""
    password = read_password(cfg, data_dir)
    skipped: list[str] = []
    try:
        conn = connect(cfg.host, cfg.port, timeout=30)
    except Exception as exc:
        raise MailboxError(f"cannot connect to {cfg.host}:{cfg.port} ({type(exc).__name__})")
    try:
        try:
            conn.login(cfg.user, password)
        except imaplib.IMAP4.error:
            raise MailboxError("login rejected (for Gmail, use an app password with IMAP enabled)")
        folder = f'"{cfg.folder}"' if " " in cfg.folder and not cfg.folder.startswith('"') else cfg.folder
        status, _ = conn.select(folder, readonly=True)
        if status != "OK":
            raise MailboxError(f"cannot open folder {cfg.folder!r}")
        since = (now - timedelta(days=cfg.lookback_days)).strftime("%d-%b-%Y")
        status, data = conn.search(None, "SINCE", since, "SUBJECT", f'"{cfg.subject_contains}"')
        if status != "OK":
            raise MailboxError("search failed")
        ids = data[0].split()[-MAX_CANDIDATES:]
        best: Brief | None = None
        for mid in reversed(ids):
            status, parts = conn.fetch(mid, "(BODY.PEEK[])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            raw = parts[0][1]
            if len(raw) > FETCH_LIMIT:
                skipped.append("message too large")
                continue
            brief, reason = evaluate(raw, cfg, detail)
            if brief is None:
                skipped.append(reason)
            elif best is None or brief.generated_at > best.generated_at:
                best = brief
        return best, skipped
    finally:
        try:
            conn.logout()
        except Exception:
            pass


class MailboxPuller:
    def __init__(self, relay: "Relay"):
        self.relay = relay
        self.cfg = relay.config.mailbox
        self.last_poll: datetime | None = None
        self.last_ok: datetime | None = None
        self.last_message_id: str | None = None
        self.error: str | None = None
        self.skipped: list[str] = []
        self._task: asyncio.Task | None = None
        current = relay.pushed.get("brief")
        if current and current.get("token") == "mailbox":
            self.last_message_id = current.get("message_id")

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled and bool(self.cfg.user and self.cfg.subject_contains)

    async def poll(self) -> None:
        now = self.relay.now()
        self.last_poll = now
        try:
            brief, skipped = await asyncio.to_thread(fetch_newest, self.cfg, self.relay.config.server.data_dir, now)
            self.skipped = sorted(set(skipped))
            self.error = None
            self.last_ok = now
            if brief is None or brief.message_id == self.last_message_id:
                return
            current = self.relay.pushed.get("brief")
            current_at = (current or {}).get("data", {}).get("generated_at")
            if current_at and brief.generated_at.isoformat() <= current_at.replace("Z", "+00:00") and (current or {}).get("token") != "mailbox":
                return  # a direct push that is newer than this email wins
            self.relay.push(
                "brief",
                {
                    "generated_at": brief.generated_at.isoformat(),
                    "model": "email",
                    "format": "text",
                    "title": brief.title,
                    "body": brief.body,
                },
                "mailbox",
                extra={"message_id": brief.message_id},
            )
            self.last_message_id = brief.message_id
        except MailboxError as exc:
            self.error = str(exc)
        except Exception as exc:  # never take the relay down
            self.error = f"unexpected error ({type(exc).__name__})"

    async def _loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            await self.poll()
            await asyncio.sleep(self.cfg.interval)

    def start(self) -> None:
        if self.enabled:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def health(self) -> dict | None:
        if not self.enabled:
            return None
        return {
            "last_poll": self.last_poll.strftime("%Y-%m-%dT%H:%M:%SZ") if self.last_poll else None,
            "last_ok": self.last_ok.strftime("%Y-%m-%dT%H:%M:%SZ") if self.last_ok else None,
            "error": self.error,
            "skipped": self.skipped,
        }
