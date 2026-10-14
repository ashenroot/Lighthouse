# Fresnel

The relay behind Lighthouse. Fresnel fetches public feeds on a schedule, decides each one's status, caches the last good reading on disk, and serves everything as JSON. It renders nothing; the dashboard does that.

## Install

On a Linux server with systemd (Ubuntu, Debian and similar), as root:

```
git clone https://github.com/ashenroot/Lighthouse.git
cd Lighthouse
sudo ./install.sh
```

The installer creates a service account, puts the code in `/opt/lighthouse`, asks a few questions (your coordinates, an optional airport code and contact email), writes `config.yaml` into the app folder (`/opt/lighthouse/app/fresnel/`, next to its `data/`), and starts a `fresnel` service. County and state come from your coordinates, so there is no FIPS code to look up. It is safe to run again; it keeps your config and tokens.

Without systemd, or to try it on a laptop:

```
cd fresnel && pip install -e .
fresnel init          # asks the same questions, writes ./config.yaml
fresnel run
```

Then open `http://127.0.0.1:8787/` for the dashboard.

## Commands

| Command | Does |
|---|---|
| `fresnel run` | Run the relay (also what plain `fresnel` does). |
| `fresnel init` | Create a config by answering questions. `--yes` with flags skips the prompts. |
| `fresnel check` | Validate the config and show what is enabled and what is not. |
| `fresnel token add NAME --slots brief` | Create a push token. It is shown once. |
| `fresnel token list` / `revoke NAME` | See or remove tokens. Changes apply immediately, no restart. |
| `fresnel mailbox set-password` / `test` | store the mailbox password; check the mailbox connection |
| `fresnel brief-step` | Print the push step for a briefing job with the URL and a new token filled in. |
| `fresnel proxy-snippet` | Print an nginx block that publishes the relay under a path. |
| `fresnel update` | Pull the latest version, reinstall, and restart the service. `--check` only lists changes. |

The config file is found from `--config`, `$FRESNEL_CONFIG`, `./config.yaml`, `config.yaml` in the app folder (`fresnel/config.yaml` of the checkout, which keeps everything self-contained), or `/etc/lighthouse/config.yaml`, in that order. Relative paths inside it are relative to the config file. [`config.example.yaml`](config.example.yaml) documents every option. Unknown keys are errors, so a typo is reported instead of ignored.

## Updating

```
sudo fresnel update
```

New options always have defaults, so your config keeps working across updates.

## Publishing it

Fresnel listens on `127.0.0.1`. To reach it from outside, put a reverse proxy with HTTPS in front. `fresnel proxy-snippet` prints a ready nginx block. Set `server.public_url` in the config to the resulting address.

## API

| Route | Purpose |
|---|---|
| `GET /v1/snapshot` | Everything the dashboard needs. Supports `ETag` / `If-None-Match` (304). |
| `GET /v1/sources/{id}` | One envelope. |
| `GET /v1/brief`, `/v1/brief/{YYYY-MM-DD}` | Latest and archived Keeper's Log. |
| `GET /v1/push/{slot}` | Latest pushed content for a slot. |
| `PUT /v1/push/{slot}` | Producer write. `Authorization: Bearer <token>`. |
| `GET /v1/health` | Per-source freshness and failures, for monitoring. |

Envelope and push schemas are in [`../contract`](../contract).

## Status rules

`nominal` / `active` come from each source's own thresholds. Stale, failed or never-fetched data is `unknown`. Pushed content is `info` and never drives status. A panel shows its worst reading: active, then unknown, then nominal.

## Sources

No API keys. Every location-specific value comes from `location:` or the `sources:` section of the config. Each source can be disabled or tuned there.

| Id | Panel | Feed | Goes active when |
|---|---|---|---|
| `nws_alerts` | local | NWS active alerts at your point | an alert at or above `active_min_severity` |
| `openfema` | local | FEMA declarations for your county | a declaration within `active_days` |
| `usgs_quakes` | local | USGS earthquakes within `radius_km` | M`active_mag`+ within `active_hours` |
| `usgs_gauges` | environment | nearest stream gauges, or your `sites` | a gauge at or above its `flood_stage_ft` (never, if none set) |
| `drought` | environment | US Drought Monitor, your county | `active_pct` of the county in `active_category` or worse |
| `faa_airports` | transport | FAA ground stops, delays, closures | a delay or ground program at a listed airport. Needs `airports`; skipped until set |
| `cdc_wastewater` | health | CDC wastewater activity, your state | `active_share` of sites High or above for a pathogen |
| `fda_recalls` | recalls | FDA enforcement reports reaching your state | a Class I recall within `active_days` |
| `cpsc_recalls` | recalls | CPSC consumer product recalls | never; informational |
| `cisa_kev` | cyber | CISA Known Exploited Vulnerabilities | additions within `active_days` |
| `swpc_kp` | space | planetary K-index | G`active_g` or higher |
| `swpc_scales` | space | NOAA R/S/G scales | R2+, S1+ or G1+ (tunable) |
| `nhc_storms` | tropical | active tropical cyclones | a storm in a watched basin |

Add a source by subclassing `Source` and registering it in `fresnel/sources/__init__.py`.

### When a feed blocks your server

Some publishers sit behind a CDN that rejects hosting-provider address ranges, and the source then shows `HTTP 403`. A different User-Agent does not help. Either turn the source off (`sources: {faa_airports: {enabled: false}}`) or fetch just that source through a proxy on a network the publisher accepts:

```yaml
sources:
  faa_airports:
    proxy: http://user:pass@proxy.example.org:3128
```

`http://`, `https://` and `socks5://` (use `socks5h://` to let the proxy resolve names) are accepted. Only that source uses the proxy. If the URL carries credentials, keep the config file private (mode 640).

A proxy does not need extra software if you have a machine on a network the publisher accepts, such as a home connection. Run this on that machine, as a service so it restarts:

```
ssh -N -R 127.0.0.1:1080 tunnel@your-server
```

The server now listens on `127.0.0.1:1080` as a SOCKS proxy whose traffic exits from the home machine, and the config line is `proxy: socks5h://127.0.0.1:1080`. Use a dedicated SSH user with no shell for the tunnel. If the tunnel is down the source goes stale and shows "unknown", and nothing else is affected.

## Push

Slots are `brief` and `context`. A producer sends JSON with `PUT /v1/push/{slot}` and a bearer token. Create one token per producer, limited to its slot:

```
fresnel token add brief-job --slots brief
```

The command prints the token once and stores only a hash. Revoke it with `fresnel token revoke brief-job`. `fresnel brief-step` prints a complete push step for a briefing job, including the URL and a new token.

```
curl -X PUT https://YOUR-HOST/fresnel/v1/push/brief \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"generated_at":"2026-10-14T11:00:00Z","model":"claude","format":"text","title":"Daily brief","body":"..."}'
```

Responses: 401 missing or bad token, 403 token not allowed for the slot, 413 too large, 400 not JSON, 422 schema failure (unknown fields are rejected), 503 no tokens exist yet.

### Brief over ntfy

If the job can reach a public ntfy server but not the relay, it can post the brief to a private ntfy topic and Fresnel reads it from there. The topic name is the only secret, so use a long random one:

```
fresnel ntfy-topic                       # prints a random topic name
# config.yaml:  brief_ntfy: {topic: <that name>}
sudo systemctl restart fresnel
fresnel brief-step --ntfy --out brief-step.txt   # the step to paste into the briefing prompt
```

The brief is posted as a text attachment; Fresnel reads it every 10 minutes and publishes the newest one once. Anonymous posting to ntfy.sh is limited per source IP, so if the job runs behind a shared IP and gets HTTP 429, create a free ntfy.sh account and set `brief_ntfy.token` (it is added to the job's request). ntfy.sh keeps attachments for a few hours, so the relay must be running to collect each brief; stored briefs are kept on disk. Status appears under `brief_ntfy` in `/v1/health`.

### Brief from a mailbox

If the job that writes the brief cannot reach the relay but can send email, Fresnel can read the brief from a mailbox instead. Configure the `mailbox:` section (see `config.example.yaml`), then:

```
fresnel mailbox set-password   # paste the app password; stored in the data dir, mode 600
fresnel mailbox test           # logs in and shows what it would publish
sudo systemctl restart fresnel
```

A message is accepted when the sender is the mailbox user (or one of `from_addrs`) and the subject contains `subject_contains`. For an inbox that strangers can write to, set `require_auth: true` so a message also needs a passing DKIM or SPF result. The newest accepted message from the last three days is published once. A direct push that is newer wins. Mailbox status appears under `mailbox` in `/v1/health`. For a brief sent from the same Gmail account, read its Sent Mail with `folder: "[Gmail]/Sent Mail"`. Proton Mail cannot be read this way; use the mailbox the message arrives in first.

### Sections and ratings

A plain-text brief is split into sections when it arrives, and the board shows them as a list with a rating pill on each; clicking a row opens that section. Number the section headings 1, 2, 3 in order and end each with a rating in brackets: `QUIET`, `NOTABLE`, `ELEVATED` or `CRITICAL`. Lines such as `CORE TIER` and `WATCH TIER` become group labels, and a heading named `Sources` becomes a list of links. Briefs with no recognizable headings are shown as plain text, and sections without a rating show as UNRATED. Producers can instead send `sections` explicitly, each with `title`, `body`, optional `criticality`, `group`, `number` and `sources`.

## Alerts (Foghorn)

Set `foghorn.topic` in the config. Fresnel posts to ntfy once each time a source turns active, and once when the brief goes stale. Readings cached before a restart do not re-alert. The topic name works as a password, so keep the config file private; `fresnel init` writes it readable only by the service account.

## Privacy

Published coordinates are rounded to 0.1 degree, and error strings never include request URLs.

## Tests

```
pip install -e '.[dev]' && pytest
```
