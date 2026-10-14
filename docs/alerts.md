# Alerts (Foghorn)

Foghorn sends notifications to your phone through [ntfy](https://ntfy.sh), a free push-notification service. It is off until you give it a topic.

## What it alerts on

- **A source turns active.** One notification the first time a source goes from nominal (or unknown) to active, with the source's name as the title, its one-line summary as the message, and a tap-through link to the publisher's page.
- **The brief goes missing.** One notification, "Keeper's Log is missing", when no brief has arrived within `push.brief.stale_after` (26 hours by default). Checked every 10 minutes.

What it deliberately does not do:

- No repeat reminders while a source stays active.
- No "all clear" when it goes back to nominal.
- No alert for a source that merely fails or goes unknown. Those are shown on the board and in `/v1/health`, since a flaky feed is not an emergency.
- No alert from a restart. Readings loaded from disk count as already announced.
- No alert from the ratings in your daily brief.

If a source goes active, back to nominal, and active again, you get a second alert.

## Setting it up

1. Install the ntfy app on your phone (iOS or Android).
2. Choose a topic name. It works as a password: anyone who knows it can read your alerts and send you fake ones. Make it long and random:
   ```
   python3 -c "import secrets; print('lighthouse-' + secrets.token_hex(12))"
   ```
3. In the app, subscribe to that topic on `ntfy.sh` (or on your own server).
4. Add it to `config.yaml` and restart:
   ```yaml
   foghorn:
     topic: lighthouse-3f9a1c5e8b2d4f60a1b7c9d2
   ```
   ```
   sudo systemctl restart fresnel
   ```
5. Confirm: `fresnel check` shows `alerts  ntfy on`.

You can also give the topic at install time with `fresnel init --ntfy-topic NAME`.

## Settings

| Key | Default | Meaning |
|---|---|---|
| `topic` | none | The ntfy topic. Alerts are on when it is set. |
| `url` | `https://ntfy.sh` | Change for a self-hosted ntfy server. |
| `token` | none | Access token for a protected server or topic. |
| `priority` | `high` | `min`, `low`, `default`, `high` or `max`. Higher priorities make more noise on the phone. The missing-brief alert is always `high`. |
| `enabled` | `true` | `false` mutes alerts and keeps the topic in the file. |

## Testing it

Raise a source's sensitivity temporarily, for example `usgs_quakes` with `active_mag: 1.0`, restart, and wait for a fresh fetch. Or send a test straight to the topic from any machine to prove the phone side works:

```
curl -d "test" ntfy.sh/YOUR-TOPIC
```

If that arrives but Lighthouse alerts do not, check `journalctl -u fresnel` for `ntfy send failed`. The log never contains the topic.

## Privacy

Alerts go through the ntfy server you choose, so ntfy.sh sees the topic and the text of each alert (a source name and a summary). They contain your place only as far as a source summary does. Self-host ntfy if you do not want that.
