# Privacy and security

Lighthouse is meant to run on a small server you control. This page says what leaves it, what it stores, and what protects the few secrets involved.

## What leaves your server

**To public feeds**, from the relay, over HTTPS:

| Host | For | What it receives about you |
|---|---|---|
| `api.weather.gov` | NWS alerts | Your exact coordinates (a `point=` query), and your User-Agent with your contact email |
| `earthquake.usgs.gov` | Earthquakes | Your exact coordinates and the search radius |
| `waterservices.usgs.gov` | Stream gauges | A box around your exact coordinates |
| `www.fema.gov` | Disaster declarations | Your county code |
| `usdmdataservices.unl.edu` | Drought | Your county code |
| `data.cdc.gov` | Wastewater | Your state |
| `api.fda.gov` | FDA recalls | Your state, in the filter |
| `www.saferproducts.gov` | CPSC recalls | Nothing about you |
| `nasstatus.faa.gov` | Airport status | Nothing about you (the airport list is applied locally) |
| `services.swpc.noaa.gov`, `www.nhc.noaa.gov` | Space weather, tropical storms | Nothing about you |
| `www.cisa.gov` | Known exploited vulnerabilities | Nothing about you |
| `ornl.opendatasoft.com` | Power outages (ODIN) | Your state's FIPS prefix and county code, as a filter |
| `www.energy.gov`, `www.nerc.com` | DOE emergency orders, NERC alerts | Nothing about you |

Every request also carries the IP address of your server, as any web request does. The installer, and `fresnel init`, send your coordinates once to `geo.fcc.gov` to look up your state and county.

Nothing is sent to any analytics, advertising, font or CDN service. The board loads nothing from other sites; its content security policy forbids it.

**To ntfy**, only if you turn on alerts or use the ntfy brief option: the alert text (a source name and one-line summary), under your topic name. See [Alerts](alerts.md#privacy).

**To your mail provider**, only if you use the mailbox option: Fresnel logs in to read.

**Through a proxy**, for any source where you set `proxy:`: that source's requests, with the exact coordinates where the table above says so, go through the proxy host. Use one you control.

## What is published

The board and API show your location's name and state, a county code, and coordinates rounded to 0.1 degree (about 11 km). They do not show your exact point, your email address, your tokens or any password. Error text names the kind of failure, never a URL.

The board and API have no login. Anyone who can reach the address can read them, including your configured airports and your daily brief. See [Hosting](hosting.md#who-can-see-it) to restrict that.

Exact coordinates are not hidden from you or the server's administrators: they are in `config.yaml`.

## What is stored

All under the data folder (`/opt/lighthouse/app/fresnel/data` by default):

| What | Notes |
|---|---|
| Last good reading for each source | JSON, replaced on every success. |
| Pushed content | The latest brief and context, and one archived brief per day. |
| `tokens.json` | Push tokens as SHA-256 hashes, with names, slots and creation times. Mode 600. |
| `mailbox.password` | The mailbox app password, if you use that option. Mode 600. |
| Last message ID | Of the mailbox or ntfy message last published, so it is not published twice. |

`config.yaml` holds your coordinates, the Foghorn topic, any ntfy token and any proxy credentials. `fresnel init` writes it mode 640, owned by the service account. There is no database, and no logs of visitors are kept by Fresnel itself (nginx keeps its own).

## Secrets and how they are handled

- **Push tokens** look like `lh_` plus 48 hex characters. Fresnel keeps only a hash, so a leaked data folder does not reveal usable tokens. Tokens are limited to named slots, so the brief token cannot write anything else, and it can be revoked instantly. Comparisons are constant-time.
- **The ntfy topic** (alerts, or brief delivery) acts as a password for that channel. Use a long random one. Anyone who knows it can read alerts or post to it.
- **The mailbox password** should be an app password, never your real one. It is read only by the relay.
- **The service** runs as an unprivileged `lighthouse` account with `NoNewPrivileges`, a private `/tmp`, a read-only system, no access to home folders, and write access to its data folder only.
- **Nothing secret reaches the board.** The board has no tokens, no keys and no logic. Even a compromised page can only read what any visitor can.

## Pushed and fetched text is untrusted

Everything a feed or a producer sends is treated as hostile text. Control characters are stripped, lengths and counts are capped, only `http` and `https` links are kept, and the board inserts content as text, never as HTML. A malicious feed entry or a prompt-injected briefing cannot run script on the board.

If an AI assistant writes your brief, remember that it reads the web, and that web pages can contain text written to manipulate it. The prompt step Fresnel generates tells it to ignore instructions found in fetched content and to treat the token as secret. The token still gives the job (and anything that hijacks it) the ability to write a brief, which is why it can write nothing else.

## Hardening checklist

- Keep Fresnel on `127.0.0.1` behind HTTPS.
- Keep `config.yaml` readable only by the service account and root.
- One token per producer, `--slots brief` only; revoke tokens you no longer use.
- Keep the system updated, and Lighthouse too (`sudo fresnel update`).
- Open only the ports you need, in both the machine's and the provider's firewall.
- Add basic auth or an IP allow-list at nginx if the board should be private.
- Use a long random ntfy topic, or a self-hosted ntfy.
- Use an app password for the mailbox, and `require_auth: true` if strangers can email that inbox.

## Reporting a problem

Lighthouse is maintained by one person and does not take outside contributions. If you find a security problem in the code, you are welcome to email the maintainer through the contact on the repository owner's GitHub profile rather than open a public issue with details.
