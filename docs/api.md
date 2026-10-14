# API

Fresnel serves JSON over HTTP. The board uses it, and so can anything else: a phone shortcut, a status display, a monitoring check. Everything is under `/v1`. Responses are JSON. Schemas are in [`contract/`](../contract).

All `GET` routes are open and read-only. Only `PUT /v1/push/{slot}` needs a token.

## `GET /v1/snapshot`

Everything the board needs, in one document.

```json
{
  "schema": "...",
  "generated_at": "2026-10-14T18:40:02Z",
  "location": {"name": "Kansas City, MO", "state": "MO", "county_fips": "29095",
               "approx_lat": 39.1, "approx_lon": -94.6},
  "panels": {"local": {"status": "nominal", "sources": ["nws_alerts", "openfema"]}, ...},
  "sources": {"nws_alerts": { ... }, ...},
  "push": { "brief": { ... }, "context": { ... } }
}
```

`location` is public, so the coordinates are rounded to 0.1 degree. `panels` maps each panel to its worst status and its source ids; `sources` maps each enabled source id to its envelope; `push` holds what outside producers sent.

The response has an `ETag`. Send it back as `If-None-Match` and an unchanged snapshot answers `304` with no body. The board does this on every refresh.

## Source envelope

`GET /v1/sources/{id}` (or an entry of `sources`) returns one source:

| Field | Meaning |
|---|---|
| `id`, `title`, `panel` | Which source this is. |
| `tier` | `push` for pushed slots, otherwise the source's tier. |
| `status` | `nominal`, `active`, `unknown` or `info`. |
| `summary` | One line, suitable for a notification. Starts with "Stale:" when the data is old. |
| `source_url` | The publisher's page. |
| `fetch` | `ok`, `fetched_at`, `last_attempt_at`, `stale_after` (seconds), `stale`, `error` (cleaned text or null), `consecutive_failures`. |
| `data` | The source's own rows, in the shape its board renderer expects. |

An unknown source id answers `404`.

## `GET /v1/health`

For monitoring. Always answers when the relay is running.

```json
{
  "ok": true, "degraded": false, "version": "0.1.0", "uptime_s": 86400,
  "sources": [{"id": "nws_alerts", "status": "nominal", "stale": false,
               "last_success": "...", "consecutive_failures": 0, "error": null}],
  "push": [{"slot": "brief", "received_at": "...", "stale": false}],
  "push_enabled": true,
  "mailbox": { ... },
  "brief_ntfy": { ... }
}
```

`degraded` is true if any source is stale or reporting an error. `mailbox` and `brief_ntfy` appear only when enabled. Point an uptime monitor at this and alert on `ok` missing or `degraded` staying true.

## `GET /v1/brief`

The latest brief, as a push envelope. `GET /v1/brief/YYYY-MM-DD` returns the archived brief for a date, or `404`.

## `GET /v1/push/{slot}`

The latest content for a slot (`brief` or `context`), wrapped in an envelope with `status: info`, when it was received, who sent it, and whether it is stale. An unknown slot is `404`.

## `PUT /v1/push/{slot}`

Writes a slot. Requires `Authorization: Bearer TOKEN` where the token allows that slot. The body is JSON, at most `push.SLOT.max_bytes` (default 200000).

**`brief` fields** (unknown fields are rejected):

| Field | Required | Notes |
|---|---|---|
| `generated_at` | yes | ISO 8601 time with a timezone. |
| `format` | no | `text` or `markdown` (default). Both are shown as plain text. |
| `title` | no | Up to 300 characters. |
| `model` | no | Free text naming the producer. |
| `body` | no | Up to 60000 characters. If `sections` is empty, Fresnel splits it into sections ([format](brief.md#the-brief-format)). Text before the first heading stays in `body`. |
| `sections` | no | Up to 30. Each: `body` (required, up to 20000), `title`, `number` (1 to 99), `group`, `criticality` (`quiet`, `notable`, `elevated`, `critical`), `panel`, `sources` (up to 100 `http(s)` links). |
| `source_notes` | no | Up to 50 short strings. |

**`context` fields:** `generated_at` (required) and `items`, up to 50, each with `title` (required), `summary`, `url`, `source`, `published_at`.

Control characters are stripped from every string. Content is stored as text and never interpreted as HTML.

**Responses**

| Code | Meaning |
|---|---|
| 200 | Accepted. |
| 400 | Body is not JSON. |
| 401 | Token missing or wrong. |
| 403 | Token not allowed for this slot. |
| 404 | Unknown slot. |
| 413 | Body larger than the limit. |
| 422 | A field failed validation. The response lists each problem with its location. |
| 503 | No tokens exist yet. Create one with `fresnel token add`. |

## `GET /`

If the relay is not serving the board, answers with a small JSON description. If it is, `/` is the board and everything under `/v1` still takes precedence.

## Cross-origin access

Only `GET` is allowed from other origins, and only those in `server.cors_origins`. `If-None-Match` is permitted and `ETag` is exposed so a separately hosted board can revalidate.
