# Sources

A source is one public feed. Fresnel fetches each on its own schedule, judges it, and keeps the last good reading. None needs an API key. This page explains how status works, then lists every source with its settings.

## How status works

Every source is always in one of four states:

| Status | Colour on the board | Meaning |
|---|---|---|
| `nominal` | green | Fresh data, nothing needs attention. |
| `active` | amber | Fresh data, and it crossed the source's threshold. This is the state Foghorn alerts on. |
| `unknown` | grey, dashed | The data is old, or has never loaded. Treat it as "no information", not as "all clear". |
| `info` | blue | Content you pushed (the Keeper's Log). It never counts as an alert. |

A reading is **old** when it is more than `stale_after` seconds behind (three intervals, at least 15 minutes, unless you set it). Old readings still show, greyed out, with the last known values, so a short outage does not blank the board. A **panel** shows its worst source: active, then unknown, then nominal.

How fetching behaves:

- Each source runs on its own timer, started a little apart from the others so they do not all hit the network at once. Each wait varies by about 10% for the same reason.
- When a source fails, the relay keeps the last good data and tries again later, waiting longer after each failure (up to six times the normal interval). It resets as soon as one succeeds.
- Each source's last good reading is saved to disk, so a restart shows data immediately instead of an empty board. A restart does not re-send alerts for conditions that were already active.
- Error text shown on the board and in `/v1/health` is cleaned: it names the kind of failure (`HTTP 403`, `ConnectError`) and never contains a URL.

Every row on the board links to the publisher's page, even for a source that has not loaded yet.

Check live state any time with `curl -s http://127.0.0.1:8787/v1/health`.

## Setting a source's options

```yaml
sources:
  nws_alerts:
    interval: 300          # seconds between fetches (minimum 60)
    stale_after: 900       # seconds until a reading counts as old
    enabled: true
    params:
      active_min_severity: Severe
    proxy: socks5h://127.0.0.1:1080
```

`interval`, `stale_after`, `enabled` and `proxy` work for every source. `params` are listed below. See [Configuration](configuration.md#sources).

## The sources

### `nws_alerts` — National Weather Service alerts (panel: Local)

Active watches, warnings and advisories for your exact point, from `api.weather.gov`. Shown most severe first, with the event name, severity, headline and when it expires.

Goes **active** when any alert is at or above `active_min_severity`. Default interval 5 minutes.

| Param | Default | Meaning |
|---|---|---|
| `active_min_severity` | `Moderate` | `Minor`, `Moderate`, `Severe` or `Extreme`. Raise it to `Severe` to stop moderate alerts (such as a wind advisory) from turning the panel amber. |
| `max_items` | `20` | Most alerts listed. |

NWS requires your contact address in the User-Agent. Set `http.user_agent`.

### `openfema` — Disaster declarations (panel: Local)

FEMA disaster and emergency declarations that include your county, from OpenFEMA. Default interval 30 minutes.

Goes **active** when a declaration was made within `active_days`.

| Param | Default | Meaning |
|---|---|---|
| `active_days` | `30` | How recent a declaration must be to count. |
| `lookback_days` | `365` | How far back declarations are listed. |
| `max_items` | `10` | Most listed. |

### `usgs_quakes` — Earthquakes (panel: Local)

Recent earthquakes within a radius of your point, from the USGS. Default interval 10 minutes.

Goes **active** when an earthquake of at least `active_mag` happened within `active_hours`.

| Param | Default | Meaning |
|---|---|---|
| `radius_km` | `300` | Search radius around your point. |
| `lookback_days` | `7` | How far back quakes are listed. |
| `min_mag` | `2.5` | Smallest magnitude listed. |
| `active_mag` | `4.0` | Magnitude that can turn the panel active. |
| `active_hours` | `24` | How recent that quake must be. |
| `max_items` | `10` | Most listed. |

### `usgs_gauges` — Stream gauges (panel: Environment)

Water height at USGS gauges near you, with the change over 24 hours. By default Fresnel picks the nearest gauges itself. Default interval 15 minutes.

Goes **active** only if you give a gauge a flood stage and it reaches it. With no stages set, the source shows readings and never turns amber, because "high" is different for every river.

| Param | Default | Meaning |
|---|---|---|
| `sites` | `[]` | USGS site numbers to show, as quoted strings. Empty means the nearest gauges. Find numbers at waterdata.usgs.gov. |
| `radius_deg` | `0.5` | How far, in degrees, to look for gauges when `sites` is empty. |
| `max_sites` | `4` | How many gauges to show when choosing automatically. |
| `flood_stage_ft` | `{}` | Flood stage per site, in feet: `{"02204130": 8.0}`. A gauge at or above its stage turns the panel active. |

```yaml
sources:
  usgs_gauges:
    params:
      sites: ["06893000"]
      flood_stage_ft: {"06893000": 21.0}
```

### `drought` — Drought Monitor (panel: Environment)

The share of your county in each drought category, from the US Drought Monitor. The map updates weekly, so the default interval is 6 hours.

Goes **active** when at least `active_pct` percent of the county is in `active_category` or a worse one.

| Param | Default | Meaning |
|---|---|---|
| `active_category` | `d2` | `d0` abnormally dry, `d1` moderate, `d2` severe, `d3` extreme, `d4` exceptional. |
| `active_pct` | `50` | Percent of the county that must be in that category or worse. |

### `faa_airports` — Airport status (panel: Transport)

Ground stops, ground delay programs, delays and closures at the airports you list, from the FAA's NAS status service. Default interval 5 minutes. **Off until you list airports.**

Goes **active** for a delay or ground program. Closure notices are listed but do not turn it active, because most are partial restrictions.

| Param | Default | Meaning |
|---|---|---|
| `airports` | `[]` | FAA codes, such as `[MCI, STL]`. |

The FAA's content network rejects many hosting providers' address ranges. See [When a feed blocks your server](#when-a-feed-blocks-your-server).

### `aviation_weather` — Airport weather (panel: Transport)

The current METAR flight category at your airports, from the Aviation Weather Center: VFR (clear), MVFR (marginal), IFR (low visibility or cloud) or LIFR (very low), with visibility and the lowest cloud ceiling. Fog and low cloud cause delays before the FAA posts them, so this sits next to the FAA feed. Default interval 10 minutes. **Off until airports are set.**

It uses the same airports as `faa_airports` unless you give it its own list. Three-letter FAA codes in the contiguous US are looked up as their ICAO code (`MCI` becomes `KMCI`). For Alaska, Hawaii and anything else, give the four-letter ICAO code (`PANC`, `PHNL`). An airport with no report in the last `max_age_minutes` shows "No recent report" and is never counted as clear. If none of your airports returns a current report, the source fails (grey), not nominal.

Goes **active** when an airport is at `active_min` or worse. The default, IFR, is routine at some coastal airports in the morning; set `LIFR` if that is too noisy.

| Param | Default | Meaning |
|---|---|---|
| `airports` | the `faa_airports` list | FAA or ICAO codes. |
| `active_min` | `IFR` | `MVFR`, `IFR` or `LIFR`: the category at which the airport counts as active. |
| `max_age_minutes` | `180` | Reports older than this are ignored. |

### `cdc_wastewater` — Wastewater (panel: Health)

Viral activity in your state's wastewater, from the CDC: SARS-CoV-2, influenza A and RSV. The data updates weekly, so the default interval is 6 hours.

Goes **active** when the share of the state's sites reporting High or Very High for any one pathogen reaches `active_share`.

| Param | Default | Meaning |
|---|---|---|
| `active_share` | `0.5` | A fraction from 0 to 1. |

### `fda_recalls` — FDA recalls (panel: Recalls)

Recalls from the FDA enforcement reports whose distribution is nationwide or includes your state, one row per recall event. Default interval 2 hours.

Goes **active** when a Class I recall (the most serious class) was reported within `active_days`.

| Param | Default | Meaning |
|---|---|---|
| `categories` | `[food]` | Any of `food`, `drug`, `device`. |
| `lookback_days` | `30` | How far back recalls are listed. |
| `active_days` | `3` | How recent a Class I recall must be. |
| `max_items` | `10` | Most listed. |

### `cpsc_recalls` — Consumer product recalls (panel: Recalls)

Recent recalls from the Consumer Product Safety Commission. The CPSC does not grade severity, so this source is informational and stays nominal. Default interval 2 hours.

| Param | Default | Meaning |
|---|---|---|
| `lookback_days` | `14` | How far back recalls are listed. |
| `max_items` | `10` | Most listed. |

### `odin_outages` — Customer power outages (panel: Power grid)

Customers without power, as reported to ODIN, the Outage Data Initiative Nationwide run by Oak Ridge National Laboratory for the Department of Energy. Shows the total for your state, your county, and the country. Default interval 15 minutes. Uses about 200 of the portal's 5,000 daily calls.

**Utilities report to ODIN voluntarily**, so every number is a floor. A state or county with no record has no *reported* outage, which is not the same as no outage. The panel says so in its own words ("reported", "floor, not an all-clear"), and a state with no records shows "None reported" and stays nominal.

Goes **active** when customers out in your county reach `county_active`, or in your state reach `state_active`.

| Param | Default | Meaning |
|---|---|---|
| `county_active` | `1000` | Reported customers out in your county. |
| `state_active` | `25000` | Reported customers out in your state. |
| `max_items` | `5` | Most counties kept in the data. |

### `doe_202c` — DOE emergency orders (panel: Power grid)

Orders the Department of Energy issues under section 202(c) of the Federal Power Act, which let grid operators run plants past normal limits during an emergency, or keep a retiring plant running. Read from DOE's yearly register page, so this is the most fragile source: it parses a web page, and DOE can change the layout. It counts the orders it can read against the order numbers on the page and treats a page it cannot make sense of as a failure ("unexpected response format"), never as "no orders". Default interval 6 hours.

Each order is classified as event-driven (dispatching units, relaxing permit limits, emergency authorizations), a plant-retention order (keeping a plant open), or capacity. Plant-retention orders are renewed every 90 days and are never news.

Goes **active** when a new order that is not a plant-retention renewal appeared within `active_days`. An order it cannot classify counts as news, because an unknown order should not be assumed routine.

| Param | Default | Meaning |
|---|---|---|
| `active_days` | `14` | How long a new order counts as active. |
| `max_items` | `5` | Most orders listed. |

### `nerc_alerts` — NERC alerts (panel: Power grid)

The latest alerts from the North American Electric Reliability Corporation, such as Level 2 and Level 3 alerts. NERC builds its list in the browser, so Fresnel reads the JSON behind the site's own search. The dates are NERC's document dates, so this source is informational: it lists the latest alerts with links and stays nominal. If the search stops returning alert documents, that is reported as a failure. Default interval 6 hours.

| Param | Default | Meaning |
|---|---|---|
| `max_items` | `5` | Most alerts listed. |
| `pages` | `2` | Search result pages read per query. |

### `grid_operators` — Regional grid dashboards (panel: Power grid)

Links to the dashboards that each regional operator maintains: ERCOT, CAISO, PJM, MISO, SPP, NYISO, ISO-NE, and the EIA Grid Monitor, which covers every region including utilities outside the ISO markets (most of the Southeast and West). Nothing is fetched. Operators publish their emergency alerts in different formats or not at all, and some require accounts, so there is no reliable way to read them all. The panel links to the source of truth instead.

| Param | Default | Meaning |
|---|---|---|
| `operators` | all eight | Any of `ercot`, `caiso`, `pjm`, `miso`, `spp`, `nyiso`, `isone`, `eia`. Trim the list to your region. |
| `links` | `[]` | Your own links, as `{name: "My utility", url: "https://..."}`, such as your utility's outage map. |

```yaml
sources:
  grid_operators:
    params:
      operators: [eia]
      links:
        - {name: "Georgia Power outages", url: "https://outagemap.georgiapower.com"}
```

### `cisa_kev` — Known exploited vulnerabilities (panel: Cyber)

Vulnerabilities the US Cybersecurity and Infrastructure Security Agency has confirmed are being exploited. This is a national feed, not tied to your location. Default interval 30 minutes.

Goes **active** when entries were added within `active_days`.

| Param | Default | Meaning |
|---|---|---|
| `active_days` | `1` | How recent an addition must be. |
| `window_days` | `7` | How far back entries are listed. |
| `max_items` | `25` | Most listed. |

### `swpc_kp` — Geomagnetic activity (panel: Space)

The planetary K-index from NOAA's Space Weather Prediction Center, translated into the G scale (Kp 5 is G1, up to Kp 9 as G5). Relevant to HF radio and aurora. Default interval 5 minutes.

Goes **active** at `active_g` or higher.

| Param | Default | Meaning |
|---|---|---|
| `active_g` | `1` | G level (1 to 5). |
| `recent` | `8` | How many recent readings to include. |

### `swpc_scales` — Space weather scales (panel: Space)

NOAA's current radio blackout (R), solar radiation storm (S) and geomagnetic storm (G) levels. Default interval 5 minutes.

Goes **active** when any of the three reaches its threshold.

| Param | Default | Meaning |
|---|---|---|
| `active_r` | `2` | Radio blackout level. R1 flares are routine, so the default starts at R2. |
| `active_s` | `1` | Solar radiation storm level. |
| `active_g` | `1` | Geomagnetic storm level. |

### `nhc_storms` — Tropical storms (panel: Tropical)

Active tropical cyclones from the National Hurricane Center, with their category and position. Default interval 15 minutes.

Goes **active** when a storm is in a basin you watch. Storms elsewhere are listed but leave the panel nominal.

| Param | Default | Meaning |
|---|---|---|
| `basins` | `[al]` | `al` Atlantic, `ep` eastern Pacific, `cp` central Pacific. |

## When a feed blocks your server

Some publishers sit behind a content network that refuses addresses belonging to hosting providers. The source then shows **`HTTP 403`** in `/v1/health` and its panel row says unknown. Confirm it with:

```
curl -sS -i -m 20 https://THE-FEED-URL | head -20
```

An "Access Denied" page from a content network (for example `AkamaiGHost`) means the block is on your server's address. Changing the User-Agent does not help. You have three options.

**Turn it off.** The panel stops showing the source:

```yaml
sources:
  faa_airports:
    enabled: false
```

**Accept "unknown".** Leave it on. The row shows unknown with a link to the publisher's page, so one click gets you the live data. The relay is reported as `degraded` in `/v1/health` while a source is failing.

**Fetch it through another network.** Only that source goes through the proxy:

```yaml
sources:
  faa_airports:
    proxy: socks5h://127.0.0.1:1080
```

Proxies can be `http://`, `https://`, `socks5://` or `socks5h://` (the `h` makes the proxy look up the name, which is what you want). If you have a machine on a network the publisher accepts, such as a home computer, you do not need any proxy software. Run this on that machine:

```
ssh -N -R 127.0.0.1:1080 tunnel@your-server
```

The `-R` with only a port is a built-in SOCKS proxy (OpenSSH 7.6 or newer). The server then listens on `127.0.0.1:1080`, and requests exit from the home machine's connection. Test it on the server before putting it in the config:

```
curl -sS -m 20 -x socks5h://127.0.0.1:1080 -o /dev/null -w "%{http_code}\n" https://THE-FEED-URL
```

A `200` means it works. Use a dedicated SSH account with no shell for the tunnel, and run it as a service on the home machine so it restarts after a reboot or a dropped connection. If the tunnel is down, the source goes unknown and nothing else is affected. If the proxy URL carries a username and password, keep the config file private.

## Adding a source

Sources are small: one file in `fresnel/fresnel/sources/` that sets an `id`, `title`, `panel`, `page` (the publisher's page) and `default_interval`, and implements `async def fetch()` returning a `Result` with the data, a one-line summary, `nominal` or `active`, and a link. Register it in `fresnel/fresnel/sources/__init__.py`, give it fixture data in the tests, and add a renderer in `board/app.js` if the default rows do not suit it. `sources/cisa_kev.py` is a complete short example. This is for people running their own fork.
