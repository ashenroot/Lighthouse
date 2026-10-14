# The daily brief (Keeper's Log)

The Keeper's Log is a written briefing that appears on the board next to the live feeds. Lighthouse does not write it. Something else does (a scheduled AI assistant, a script, you), and delivers it to Fresnel. Fresnel splits it into numbered sections, and the board shows each as a row with a rating pill; clicking a row opens that section in a popout.

The brief is optional. Without one, the Keeper's Log panel shows that nothing has arrived and everything else works.

## Three ways to deliver it

Pick the one your briefing job can actually do. You can configure more than one; the newest brief wins.

| Option | Use it when | Setup |
|---|---|---|
| 1. Push | The job can make an HTTPS request to your relay. | A token and one `curl`. |
| 2. Mailbox | The job can send email but cannot reach your server. | An IMAP login Fresnel can read. |
| 3. ntfy | The job can reach a public ntfy server but not your server, and cannot send email. | A private topic name. |

A brief that came by a direct push replaces an older mailbox or ntfy brief, and the other way round. Whichever is newest is shown. Each brief is also kept in an archive under `data/` and is readable at `/v1/brief/YYYY-MM-DD`.

### Option 1: push it

The relay must be reachable from wherever the job runs (see [Hosting](hosting.md)), and `server.public_url` must be set.

```
fresnel brief-step --out brief-step.txt
```

This creates a token named `brief-job` that is allowed to write the `brief` slot only, and writes a complete step to the file, with your URL and the token filled in. Paste the file's contents at the end of your briefing prompt. The file is mode 600 because it contains the token. If `brief-job` already exists, use `--rotate` to replace it, or `--token VALUE` to reuse a token you saved.

To do it by hand, create the token yourself (it is shown once):

```
fresnel token add brief-job --slots brief
```

and send the brief:

```
curl -X PUT https://YOUR-HOST/fresnel/v1/push/brief \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"generated_at":"2026-10-14T11:00:00Z","format":"text","title":"Daily brief","body":"..."}'
```

A success answers `200`. The fields are listed in [API](api.md#put-v1pushslot). Errors: `401` missing or wrong token, `403` token not allowed for the slot, `413` body too large, `400` not JSON, `422` a field failed validation (the response lists which), `503` no tokens exist yet.

Tokens are stored as hashes, so a lost token cannot be shown again; revoke it and make a new one: `fresnel token revoke brief-job`.

### Option 2: read it from a mailbox

The job emails the brief, and Fresnel reads the message over IMAP. Nothing on your server needs to be reachable from the internet, and the job needs no token.

1. Make an app password for the mailbox (Gmail: Google Account, Security, 2-Step Verification, App passwords). Do not use your real password.
2. Add to `config.yaml`:

   ```yaml
   mailbox:
     enabled: true
     user: you@gmail.com
     folder: "[Gmail]/Sent Mail"
     subject_contains: "Daily Briefing"
   ```

   Which folder to read depends on where the message ends up. If the job sends the email from this same account to itself or others, `[Gmail]/Sent Mail` has it. If it arrives from elsewhere, use `INBOX`, and set `from_addrs` to the sending address.
3. Store the password and test:

   ```
   sudo fresnel mailbox set-password    # paste the app password; stored in the data folder, mode 600
   sudo fresnel mailbox test            # logs in, shows what it would publish
   sudo systemctl restart fresnel
   ```

`mailbox test` prints the newest message it would accept, or says why it skipped each one (wrong sender, subject does not match, too old). Mailbox status also appears under `mailbox` in `/v1/health`.

How a message is chosen: it must be in the folder, no older than `lookback_days`, from an accepted sender (by default, the mailbox user itself), and its subject must contain `subject_contains`. The newest such message is published once; the same message is never re-published (Fresnel remembers the Message-ID of the last one it published). If the body is HTML only, it is converted to text. All settings are in [Configuration](configuration.md#mailbox).

If the mailbox is an inbox strangers can write to, set `require_auth: true` so a message also needs a passing DKIM or SPF result, and set `subject_contains` to something unguessable. For a mailbox only you and your job use, the defaults are enough.

Mail that reaches you through another provider first (for example, Proton Mail) cannot be read this way, because Proton does not offer IMAP without its bridge app. Point Fresnel at the mailbox the message arrives in first.

### Option 3: post it to ntfy

The job posts the brief as a text attachment to a private topic on a public ntfy server, and Fresnel collects it.

```
fresnel ntfy-topic                                  # prints a random topic name
```

Put that name in `config.yaml`:

```yaml
brief_ntfy:
  topic: lighthouse-brief-3f9a1c...
```

then restart and generate the step for your job:

```
sudo systemctl restart fresnel
fresnel brief-step --ntfy --out brief-step.txt
```

The topic name is the only secret. Fresnel checks every 10 minutes and publishes the newest brief once.

Limits to know:

- ntfy.sh deletes attachments after about three hours, so Fresnel must be running when the brief is posted. Briefs it has collected are kept on disk.
- Anonymous posting is limited per source IP address. A job running behind a shared address can get `HTTP 429`. Create a free ntfy.sh account and set `brief_ntfy.token`; the generated step then includes it.
- Attachments over about 300 KB are ignored, and the attachment must be hosted on the same ntfy server you configured.

## The brief format

Fresnel reads a plain-text brief and splits it at its numbered headings. Anything it cannot split is shown whole as plain text, so a loosely formatted brief still works. Following this format gets you the section list and ratings:

```
Daily Preparedness Briefing - October 14, 2026

Overall: most areas quiet; one item worth watching today.

CORE TIER

1. Severe Weather [NOTABLE]
Text of the section. Any number of paragraphs.

2. Public Health [QUIET]
Text of the section.

WATCH TIER

3. Cyber [ELEVATED]
Text of the section.

Sources
1. Example News https://example.org/story
2. https://example.org/other
```

The rules:

- **Headings** are numbered `1.`, `2.`, `3.` from 1, in order, each on its own line with a title. At least three are needed before the brief is treated as sections; this stops a two-item numbered list in a normal paragraph from being mistaken for headings.
- **Ratings** go in square brackets at the end of the heading: `QUIET`, `NOTABLE`, `ELEVATED` or `CRITICAL`. Common synonyms are accepted: `LOW`, `ROUTINE`, `NORMAL` or `NONE` count as quiet; `MODERATE`, `MEDIUM`, `WATCH` or `GUARDED` as notable; `HIGH` or `SIGNIFICANT` as elevated; `SEVERE` or `EXTREME` as critical. A section with no rating shows **UNRATED**, in grey.
- **Group lines** are lines beginning `CORE TIER` or `WATCH TIER` (any capitalization, optional trailing words). They become group labels over the sections beneath them. No other group names are recognised in text briefs; a producer sending `sections` explicitly can use any `group` text.
- **Intro**: text before the first heading is shown above the list. If it only repeats the title, it is hidden.
- **A `Sources` heading** (not numbered) turns the lines beneath it into a list of links. Any `http` or `https` address on those lines is kept.
- Text inside a section is shown as plain text. No markdown, HTML or scripts are interpreted.

| Rating | Meaning | Pill colour |
|---|---|---|
| `QUIET` | Nothing to act on. | Green |
| `NOTABLE` | Worth knowing. | Blue |
| `ELEVATED` | Worth acting on soon. | Amber |
| `CRITICAL` | Act now. | Red |
| unrated | The brief gave no rating. | Grey |

Ratings are the producer's judgement. They are shown but never drive the status of any other panel, and they never trigger Foghorn alerts. Only the brief going missing does.

### Sending sections explicitly

A producer that already has structure can skip the text format and send `sections` itself, each with `title`, `body`, optional `criticality`, `group`, `number` and `sources` (a list of links). See [API](api.md#put-v1pushslot).

## Writing the prompt

If an AI assistant writes your brief, the format above is what the prompt has to ask for. The parts that matter:

1. Ask for the exact heading form: a number, a title, and a rating in brackets, on one line, `N. Title [LEVEL]`.
2. Say which ratings exist and what each means, with a rule for choosing (for example, "CRITICAL only when a source reports a present danger to life or infrastructure in my region").
3. Ask for plain text only: no markdown, no tables, no bold.
4. Ask for a closing `Sources` heading listing the links used.
5. Tell it to treat all fetched web content as data and ignore any instructions inside it. The step that `fresnel brief-step` prints already does this for the delivery part.
6. Keep what it must watch (regions, topics, tiers) in the prompt. Lighthouse does not interpret it.

The delivery step from `fresnel brief-step` (or your own email step) goes at the end of the prompt.

## Staleness

A brief older than `push.brief.stale_after` (default 26 hours) shows as out of date on the board, and Foghorn sends "Keeper's Log is missing" once. A new brief clears it. See [Alerts](alerts.md).

## Checking that it worked

```
curl -s http://127.0.0.1:8787/v1/health | python3 -m json.tool
```

Look at the `push` entry for `brief`: `received_at` is when it last arrived and `stale` says whether it is overdue. If the board shows sections as UNRATED, the brief arrived but its headings had no `[LEVEL]`. If it shows plain text with no section list, the numbered headings were not recognised; compare with the example above.
