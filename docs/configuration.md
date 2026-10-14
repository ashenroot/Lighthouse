# Configuration

Everything is set in one YAML file, `config.yaml`. The installer and `fresnel init` write a minimal one; this page documents every option. [`fresnel/config.example.yaml`](../fresnel/config.example.yaml) shows them all in one place.

```
fresnel check        # validate the file and show what is enabled
sudo systemctl restart fresnel   # apply changes
```

## Rules for the file

- **Typos are errors.** An unknown key stops the relay from starting and the message names it. This is deliberate, so a misspelled setting is never silently ignored.
- **Indentation is meaning.** YAML nests by indentation with spaces. A tab, or a line indented to the wrong depth, makes the file unreadable. The error gives a line number; look one line above it too.
- **Everything except `location` is optional.** Every setting has a default.
- **Relative paths** (`data_dir`, `board_dir`, `mailbox.password_file`) are relative to the config file, or for the mailbox password, to the data folder.
- **Secrets live here.** The ntfy topic, tokens and any proxy credentials sit in this file in plain text. Keep it readable only by the service account and root (`chmod 640`, owned by the service user). `fresnel init` does this.

## Where the file is found

First match wins:

1. `--config PATH` on the command line
2. the `FRESNEL_CONFIG` environment variable
3. `./config.yaml` in the current directory
4. `config.yaml` in the app folder (`/opt/lighthouse/app/fresnel/` for an installed copy)
5. `/etc/lighthouse/config.yaml`

A folder the current user cannot read is skipped. `fresnel check` prints which file it used.

## `location`

The place to watch. This is the only required section.

```yaml
location:
  name: Kansas City, MO
  lat: 39.10
  lon: -94.58
  state: MO
  county_fips: "29095"
```

| Key | Meaning |
|---|---|
| `name` | The label shown in the board's header. Free text. Default `Home`. |
| `lat`, `lon` | Decimal degrees. North and east are positive. Used to ask the weather and earthquake services about your point, and to find the nearest stream gauges. |
| `state` | Two-letter state code. Used for state-level feeds (wastewater, recalls) and drought. |
| `county_fips` | Five digits, state plus county, quoted so YAML keeps leading zeros. Used for FEMA declarations and the drought map. `fresnel init` looks it up; or find yours in the Census FIPS list. |

The relay uses your exact coordinates only when asking feeds that need a point. Everything it publishes (the JSON, links) rounds them to 0.1 degree, about 11 km. See [Privacy](privacy-security.md).

## `server`

```yaml
server:
  host: 127.0.0.1
  port: 8787
  data_dir: ./data
  board_dir: ../board
  cors_origins: []
  public_url: https://example.org/fresnel
```

| Key | Default | Meaning |
|---|---|---|
| `host` | `127.0.0.1` | Address to listen on. Leave it on loopback and put a reverse proxy in front ([Hosting](hosting.md)). Use `0.0.0.0` only to reach it directly on a trusted network, since the relay itself serves plain HTTP. |
| `port` | `8787` | Port to listen on. |
| `data_dir` | `./data` | Where the relay stores its cache, brief archive, tokens and mailbox password. The service account must be able to write it. |
| `board_dir` | none | A folder containing the board's `index.html`. When set, the relay serves the board at `/`. Leave it out to run the relay alone and host the board elsewhere. The relay refuses to start if the folder has no `index.html`. |
| `cors_origins` | `[]` | Web origins allowed to read the API from a browser, for a board hosted on a different address. Write each as scheme, host and port with no path, such as `https://board.example.org`. Empty means same-origin only. |
| `public_url` | none | The address the outside world uses to reach the relay. Only the `fresnel brief-step` command reads it, to fill in the push step for your briefing job. |

When `board_dir` is set, every response from the board carries a strict Content-Security-Policy (`default-src 'self'`), so the page can load nothing from other sites.

## `http`

```yaml
http:
  user_agent: "lighthouse-fresnel (you@example.org)"
  timeout: 20
```

| Key | Default | Meaning |
|---|---|---|
| `user_agent` | `lighthouse-fresnel (set-your-contact@example.com)` | Sent with every feed request. The National Weather Service requires one that identifies you with a contact address. Put a real email here. |
| `timeout` | `20` | Seconds to wait on a feed before giving up on that attempt. |

## `sources`

Every source is on by default. Override only what you need. Each entry is the source's id:

```yaml
sources:
  nws_alerts:
    interval: 300
    stale_after: 900
    params:
      active_min_severity: Severe
  cpsc_recalls:
    enabled: false
  faa_airports:
    params:
      airports: [MCI, STL]
    proxy: socks5h://127.0.0.1:1080
```

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Set `false` to turn the source off. It then does not appear on the board. |
| `interval` | per source | Seconds between fetches. Minimum 60. Be kind to the publishers; the defaults are already frequent enough. |
| `stale_after` | three intervals, at least 900 | Seconds after which a reading counts as old. An old reading shows as unknown. |
| `params` | per source | The source's own thresholds and choices. See [Sources](sources.md) for each. |
| `proxy` | none | Fetch this one source through a proxy (`http://`, `https://`, `socks5://` or `socks5h://`). For feeds that block your server's network. See [Sources](sources.md#when-a-feed-blocks-your-server). |

A source name that does not exist stops the relay from starting, and the message lists the valid ones.

## `push`

How long each pushed slot can go without an update before the board calls it stale, and its size limit.

```yaml
push:
  brief:
    stale_after: 93600
    max_bytes: 200000
  context:
    stale_after: 93600
```

| Key | Default | Meaning |
|---|---|---|
| `stale_after` | `93600` (26 hours) | Seconds. A daily brief that is older than this shows as out of date, and Foghorn raises "Keeper's Log is missing". |
| `max_bytes` | `200000` | Largest request body accepted. Larger gets HTTP 413. |

Slots are `brief` and `context`. See [The daily brief](brief.md). Tokens are not set here; create them with `fresnel token add`.

## `foghorn`

Phone notifications. On whenever a `topic` is set. See [Alerts](alerts.md).

```yaml
foghorn:
  topic: some-long-random-name
  url: https://ntfy.sh
  token: tk_xxxxx
  priority: high
  enabled: true
```

| Key | Default | Meaning |
|---|---|---|
| `topic` | none | The ntfy topic to publish to. It acts as a password, so make it long and random. |
| `url` | `https://ntfy.sh` | An ntfy server. Change it for a self-hosted one. |
| `token` | none | An access token, for a protected topic or server. |
| `priority` | `high` | ntfy priority for source alerts (`min`, `low`, `default`, `high`, `max`). |
| `enabled` | `true` | Set `false` to silence alerts while keeping the topic in the file. |

## `mailbox`

Read the daily brief from an email account. See [The daily brief](brief.md#option-2-read-it-from-a-mailbox).

```yaml
mailbox:
  enabled: true
  user: you@gmail.com
  folder: "[Gmail]/Sent Mail"
  subject_contains: "Daily Briefing"
```

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Turn the mailbox reader on. |
| `host` | `imap.gmail.com` | IMAP server, reached over TLS. |
| `port` | `993` | IMAP port. |
| `user` | none | Login name, usually the full address. Required. |
| `password_file` | `mailbox.password` | File holding the password, relative to the data folder. Create it with `fresnel mailbox set-password`. |
| `folder` | `INBOX` | Folder to read. For mail you sent from a Gmail account, `[Gmail]/Sent Mail`. |
| `from_addrs` | the user | Accepted senders. By default only mail from `user` itself. |
| `subject_contains` | none | Only messages whose subject contains this text (any capitalization) are used. Required. |
| `require_auth` | `false` | When `true`, a message also needs a passing DKIM or SPF result recorded by your mail server. Turn it on for an inbox that strangers can write to. |
| `lookback_days` | `3` | How far back to look (1 to 30). |
| `interval` | `900` | Seconds between checks. Minimum 60. |

## `brief_ntfy`

Read the daily brief from a private ntfy topic. See [The daily brief](brief.md#option-3-post-it-to-ntfy).

```yaml
brief_ntfy:
  topic: lighthouse-brief-3f9a1c...
```

| Key | Default | Meaning |
|---|---|---|
| `topic` | none | The topic the job posts to. The name is the secret; `fresnel ntfy-topic` generates one. The reader is off until this is set. |
| `url` | `https://ntfy.sh` | The ntfy server. |
| `token` | none | Access token, for a free ntfy.sh account or a protected topic. |
| `enabled` | `true` | Set `false` to turn it off while keeping the topic. |
| `lookback_days` | `2` | How far back to look (1 to 7). |
| `interval` | `600` | Seconds between checks. Minimum 60. |

## A complete example

```yaml
location:
  name: Kansas City, MO
  lat: 39.10
  lon: -94.58
  state: MO
  county_fips: "29095"

server:
  port: 8787
  data_dir: ./data
  board_dir: ../board
  public_url: https://lighthouse.example.org

http:
  user_agent: "lighthouse-fresnel (you@example.org)"

sources:
  faa_airports:
    params:
      airports: [MCI]
  nws_alerts:
    params:
      active_min_severity: Severe

foghorn:
  topic: a-long-random-topic-name

mailbox:
  enabled: true
  user: you@gmail.com
  folder: "[Gmail]/Sent Mail"
  subject_contains: "Daily Briefing"
```
