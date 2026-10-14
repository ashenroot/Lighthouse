import imaplib
from datetime import datetime, timezone
from email.message import EmailMessage

import pytest

from fresnel import mailbox
from fresnel.config import Mailbox

real_fetch = mailbox.fetch_newest


def make(**over) -> bytes:
    msg = EmailMessage()
    msg["From"] = over.pop("sender", "Job <job@example.com>")
    msg["To"] = "box@example.com"
    msg["Subject"] = over.pop("subject", "Daily Briefing - Oct 14")
    msg["Date"] = over.pop("date", "Wed, 14 Oct 2026 07:00:00 -0400")
    msg["Message-ID"] = over.pop("mid", "<a@x>")
    if over.pop("auth", True):
        msg["Authentication-Results"] = "mx.google.com; dkim=pass header.i=@example.com"
    msg.set_content(over.pop("body", "All quiet.\nhttps://example.com/x"))
    return msg.as_bytes()


@pytest.fixture
def cfg():
    return Mailbox(enabled=True, user="box@example.com", from_addrs=["job@example.com"], subject_contains="Daily Briefing", require_auth=True)


def test_accepts_matching_message(cfg):
    brief, reason = mailbox.evaluate(make(), cfg)
    assert reason == "" and brief.title == "Daily Briefing - Oct 14"
    assert brief.generated_at == datetime(2026, 10, 14, 11, 0, tzinfo=timezone.utc)
    assert "All quiet." in brief.body


@pytest.mark.parametrize(
    "over,why",
    [
        ({"sender": "evil@example.net"}, "sender"),
        ({"subject": "Weekly report"}, "subject"),
        ({"auth": False}, "DKIM"),
    ],
)
def test_rejects(cfg, over, why):
    brief, reason = mailbox.evaluate(make(**over), cfg)
    assert brief is None and why in reason


def test_defaults_are_minimal():
    c = Mailbox(user="Job@Example.com", subject_contains="Daily")
    assert c.require_auth is False
    assert mailbox.evaluate(make(auth=False), c)[0] is not None  # sender defaults to the user


def test_auth_check_can_be_disabled(cfg):
    cfg.require_auth = False
    assert mailbox.evaluate(make(auth=False), cfg)[0] is not None


def test_html_only_message_is_converted(cfg):
    msg = EmailMessage()
    msg["From"] = "job@example.com"; msg["Subject"] = "Daily Briefing"; msg["Date"] = "Wed, 14 Oct 2026 07:00:00 +0000"
    msg["Authentication-Results"] = "x; spf=pass"
    msg.set_content("<p>Hello</p><script>alert(1)</script><p>World</p>", subtype="html")
    brief, _ = mailbox.evaluate(msg.as_bytes(), cfg)
    assert "Hello" in brief.body and "World" in brief.body and "alert" not in brief.body


class FakeImap:
    messages: list[bytes] = []
    reject_login = False

    def __init__(self, host, port, timeout=None):
        pass

    def login(self, user, password):
        if self.reject_login:
            raise imaplib.IMAP4.error("nope")

    def select(self, folder, readonly=False):
        return "OK", [b"1"]

    def search(self, *args):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]

    def fetch(self, mid, spec):
        raw = self.messages[int(mid) - 1]
        return "OK", [(b"1 (BODY[] {%d}" % len(raw), raw), b")"]

    def logout(self):
        pass


@pytest.fixture
def pw(tmp_path, cfg):
    (tmp_path / cfg.password_file).write_text("secret\n")
    return str(tmp_path)


def test_fetch_picks_newest_acceptable(cfg, pw):
    FakeImap.messages = [
        make(mid="<old>", date="Tue, 13 Oct 2026 07:00:00 -0400"),
        make(mid="<new>", date="Wed, 14 Oct 2026 07:00:00 -0400"),
        make(mid="<fake>", sender="evil@example.net", date="Thu, 15 Oct 2026 07:00:00 -0400"),
    ]
    FakeImap.reject_login = False
    brief, skipped = mailbox.fetch_newest(cfg, pw, datetime(2026, 10, 14, tzinfo=timezone.utc), connect=FakeImap)
    assert brief.message_id == "<new>" and skipped == ["sender not allowed"]


def test_login_failure_and_missing_password(cfg, pw, tmp_path):
    FakeImap.reject_login = True
    with pytest.raises(mailbox.MailboxError, match="login rejected"):
        mailbox.fetch_newest(cfg, pw, datetime.now(timezone.utc), connect=FakeImap)
    with pytest.raises(mailbox.MailboxError, match="set-password"):
        mailbox.fetch_newest(cfg, str(tmp_path / "empty"), datetime.now(timezone.utc), connect=FakeImap)


async def test_puller_publishes_once(config, relay, pw, monkeypatch, cfg):
    relay.config.mailbox = cfg
    relay.config.server.data_dir = pw
    relay.mailbox = mailbox.MailboxPuller(relay)
    FakeImap.messages = [make()]
    FakeImap.reject_login = False
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", FakeImap)
    monkeypatch.setattr(mailbox, "fetch_newest", lambda c, d, n: real_fetch(c, d, n, FakeImap))
    await relay.mailbox.poll()
    assert relay.mailbox.error is None
    env = relay.push_envelope("brief")
    assert env["data"]["body"].startswith("All quiet") and env["status"] == "info"
    first = relay.pushed["brief"]["received_at"]
    await relay.mailbox.poll()
    assert relay.pushed["brief"]["received_at"] == first  # same message is not re-published
    assert relay.health()["mailbox"]["error"] is None



def test_detail_names_the_sender_only_when_asked(cfg):
    assert mailbox.evaluate(make(sender="x@example.net"), cfg)[1] == "sender not allowed"
    assert "x@example.net" in mailbox.evaluate(make(sender="x@example.net"), cfg, detail=True)[1]
