# Troubleshooting

Start with these three; they answer most questions.

```
fresnel check                                   # does the config load, and what is on
sudo journalctl -u fresnel -n 50 --no-pager     # what the service said
curl -s http://127.0.0.1:8787/v1/health         # what the relay thinks of each source
```

If `curl` to port 8787 gives nothing, the service is not running. Everything else is detail.

## The service will not start

`systemctl status fresnel` shows `failed` or `activating (auto-restart)`. Read the last lines of the log (command above).

| Log says | Cause and fix |
|---|---|
| `Failed to load environment files` | The unit has an `EnvironmentFile=` for a file that does not exist. Remove that line from `/etc/systemd/system/fresnel.service`, then `sudo systemctl daemon-reload && sudo systemctl restart fresnel`. |
| `config file is not valid YAML` and a line number | Indentation or a stray character. Look at that line and the one above it. Nesting uses spaces, never tabs. Keys under `proxy:` or `params:` must be indented under their source, not at the same depth. |
| `Extra inputs are not permitted` or `unknown key` | A misspelled or unsupported option. The message names it. Compare with [Configuration](configuration.md). |
| `config file not found` | None of the search locations has one. Create it with `sudo fresnel init`, or pass `--config`. |
| `the tokens: section is no longer used` | Delete the `tokens:` block from the config. Create tokens with `fresnel token add`. |
| `server.board_dir has no index.html` | `board_dir` points at the wrong folder. It is relative to the config file; the installed default is `../board`. |
| `PermissionError` | The service account cannot read the config or write the data folder. `sudo chown -R lighthouse:lighthouse /opt/lighthouse/app/fresnel/data` and make sure `config.yaml` is group-readable (`chmod 640`, owner `lighthouse`). |
| Address already in use | Another program has port 8787. Change `server.port`, or find it with `sudo ss -ltnp \| grep 8787`. |

After any fix: `fresnel check`, then restart.

## Commands that fail

**`sudo: fresnel: command not found`**: the link in `/usr/local/bin` is missing, or sudo's path does not include it. Use `sudo /opt/lighthouse/app/fresnel/.venv/bin/fresnel ...`, or recreate it: `sudo ln -sf /opt/lighthouse/app/fresnel/.venv/bin/fresnel /usr/local/bin/fresnel`.

**`config file not found` when run as a normal user**: the app folder is not readable by you, so the search skipped it. Run with `sudo`, or `--config /opt/lighthouse/app/fresnel/config.yaml`.

**`su lighthouse` says "This account is currently not available"**: that is correct; the account has no login shell. To act as it, use `sudo -u lighthouse COMMAND`.

**git: `detected dubious ownership`**: git was run as root in a folder owned by `lighthouse`. Use `sudo -u lighthouse git ...`, or `sudo fresnel update`.

**git pull: `refusing to merge unrelated histories`, or a diverged branch**: the repository's history was rewritten upstream. Discard the local copy of it (your config and data are untracked and unaffected):

```
cd /opt/lighthouse/app
sudo -u lighthouse git fetch origin
sudo -u lighthouse git reset --hard origin/main
sudo systemctl restart fresnel
```

## An update seems stuck

`fresnel update` prints each step. The slow one is installing dependencies, which can take a couple of minutes on a small server. If the machine froze or rebooted during it, it ran out of memory; add swap ([Small servers](install.md#small-servers)) and run it again. It is safe to repeat.

## Installing fails

- `python3 -m venv` fails: `sudo apt install python3-venv`.
- "Python 3.10 or newer is required": install a newer Python, or pick a newer distribution.
- Questions never appear when you run the installer through a pipe: download the script and run it as a file ([Install](install.md#the-installer)).
- Installation hangs or the server reboots: memory. See above.

## A source shows "unknown"

Open `/v1/health` and look at the source's `error`.

| Error | Meaning and fix |
|---|---|
| `HTTP 403` | The publisher refuses your server's address. See [When a feed blocks your server](sources.md#when-a-feed-blocks-your-server). A different User-Agent will not help. |
| `HTTP 429` | You are being rate limited. Raise `interval`. |
| `HTTP 5xx` | The publisher is down. It recovers by itself; the last good data keeps showing, greyed. |
| `ConnectError`, `ConnectTimeout` | The server cannot reach the host. Check DNS and outbound firewall: `curl -sS -m 10 https://api.weather.gov`. If the source uses a `proxy:`, is the proxy up? |
| `ReadTimeout` | Slow publisher. Raise `http.timeout`. |
| `ProxyError`, `socks` errors | The configured proxy is down or refused. Check the tunnel and test it with curl ([Sources](sources.md#when-a-feed-blocks-your-server)). |
| `Waiting for first successful fetch` | Fresnel just started; sources are staggered. Wait a few minutes. |
| `Stale: ...` | A fetch has not succeeded within `stale_after`. Look for the error above. |

`degraded: true` in the health output only means one or more sources are stale or failing; the relay itself is running.

A source missing from the board entirely is disabled, or (for `faa_airports`) has no airports. `fresnel check` lists skipped sources.

The National Weather Service rejects requests that do not identify you. Make sure `http.user_agent` contains a real email address.

## The board

- **Blank page or "relay unreachable" banner**: open `/v1/snapshot` directly in the browser. If it loads, the board's `config.js` points at the wrong address; if not, see the next section.
- **Board on a separate host shows CORS errors in the browser console**: its origin is not in `server.cors_origins` (exact scheme, host and port, no trailing slash), or Fresnel was not restarted after adding it.
- **Mixed-content errors**: the board is on HTTPS and the relay is on HTTP. Put the relay behind HTTPS.
- **Old data after an update**: hard-refresh (Shift+Reload); the browser may hold the old script.
- **Layout, hidden panels, refresh interval are wrong**: those are stored in the browser (`localStorage`, key `lighthouse.prefs`). Clear site data for the page to reset.

## Cannot reach it from outside

Work from the server outwards:

1. `curl -s http://127.0.0.1:8787/v1/health` works? If not, the service is the problem.
2. `curl -sk https://127.0.0.1/fresnel/v1/health` (or your port) works? If not, nginx: `sudo nginx -t`, then check the `location` block and the `proxy_pass`.
3. From another machine, `curl -sI https://your-domain/` works? If it times out, a firewall is blocking: the machine's, or the hosting provider's. Both must allow the port.
4. Watch the nginx access log while you request it. If nothing appears, the traffic never arrived: DNS, firewall or provider rules. If `502` appears, nginx cannot reach Fresnel. If `404`, the path in `location` is wrong.
5. `sudo certbot certificates` shows the certificate and its expiry.

See [Hosting](hosting.md).

## The daily brief

| Symptom | Fix |
|---|---|
| Never arrives | `curl -s localhost:8787/v1/health` and look at the `push` entry for `brief`, and `mailbox` or `brief_ntfy`. Then follow the matching section below. |
| Push gets `401` | Wrong or revoked token. `fresnel token list` shows names only; make a new one with `fresnel brief-step --rotate`. |
| Push gets `403` | The token was made for other slots. Make one with `--slots brief`. |
| Push gets `413` | Body over `push.brief.max_bytes`, or nginx's `client_max_body_size`. |
| Push gets `422` | The response lists the field. Often an unknown field in the payload, or `generated_at` without a timezone. |
| Push gets `503` | No tokens exist. `fresnel token add brief-job --slots brief`. |
| Push never reaches the server | The job's network cannot reach your server (some sandboxes only reach a limited set of addresses). Use the mailbox or ntfy delivery instead. |
| Mailbox: `LOGIN failed` / authentication error | Use an app password, with 2-step verification on. Re-run `fresnel mailbox set-password`. |
| Mailbox: `sender not allowed` | The message's From address is not the mailbox user or in `from_addrs`. Check for a typo in `user`. `fresnel mailbox test` names the sender it rejected. |
| Mailbox: no acceptable message | Wrong `folder` (a sent message is in `[Gmail]/Sent Mail`, not `INBOX`), a subject that does not contain `subject_contains`, or older than `lookback_days`. |
| Mailbox: folder error | Folder names with spaces or brackets must be quoted in YAML: `"[Gmail]/Sent Mail"`. |
| ntfy: `HTTP 429` from the job | Anonymous quota exhausted on a shared IP. Set a `brief_ntfy.token` from a free account. |
| ntfy: brief never collected | ntfy.sh keeps attachments only a few hours, and Fresnel must have been running to collect it. |
| Sections all show UNRATED | The headings have no `[LEVEL]`. Fix the prompt ([format](brief.md#the-brief-format)). |
| One block of plain text, no sections | Headings are not numbered `1.`, `2.`, `3.` in order, or fewer than three were found. |
| "Out of date" on the board | The brief is older than `push.brief.stale_after` (26 hours). Check the job ran. |

## Alerts do not arrive

`fresnel check` must say `alerts  ntfy on`. Then send a test directly: `curl -d test ntfy.sh/YOUR-TOPIC`. If that does not reach the phone, the problem is the ntfy app or its subscription. If it does, remember Foghorn alerts only when a source *becomes* active, not while it already is, and never for unknown or failed sources. See [Alerts](alerts.md).

## Starting over

Your config is the only thing you need to keep. To reset the data (readings, brief archive and tokens), stop the service, delete the `data/` folder contents, and start it again. Tokens will need to be recreated.

For a clean reinstall: [Uninstalling](install.md#uninstalling), then run the installer again.
