# Places and visitors

Lighthouse shows one place by default: the one in your config (`location`). Anyone can type a US ZIP Code into the board to see local data for another place. This page describes how that works and what keeps it from costing you, or the publishers, more than you intend.

## Two roles

- **Visitor** (the default; no sign-in). Sees your place, or any ZIP they enter. Gets keyless local data for the ZIP, cached and rate limited.
- **Owner** (a token with the `admin` slot). Sees every placeable source for any ZIP at its normal refresh interval, with none of the visitor limits.

```
sudo fresnel token add me --slots admin       # shown once
```

Open the board once as `https://your-board/#key=THE_TOKEN`. The board saves the token in that browser and removes it from the address bar; the header then shows "Owner view". Revoke it any time with `fresnel token revoke me`.

## What a visitor sees for a ZIP

Sources are sorted by how they treat another place:

| Treatment | Sources | What a visitor gets |
|---|---|---|
| **Fetched for the place** (keyless, one or two calls) | NWS alerts, SPC severe-storm outlook, WPC flash-flood outlook, USGS earthquakes, air quality (Open-Meteo), NIFC wildfires, FEMA declarations, drought | Live data for the ZIP's place |
| **Owner only** (more calls, or a key) | SPC storm reports, river stages, USGS gauges, ODIN power outages, CDC hospital load and wastewater, FDA recalls, EIA fuel prices, AirNow | A row linking to the publisher's page |
| **Cannot be derived from a ZIP** | EIA grid demand (needs a balancing authority), airport weather and forecasts (need airports) | A row linking to the publisher's page |
| **Not tied to a place** | National and global feeds: CISA KEV, FAA national view, DOE orders, FDIC, Federal Register, cloud and service status, space weather, tropical, headlines, supply chain, and so on | Unchanged |

Nothing that needs one of your API keys is fetched for a visitor, so a visitor cannot spend your EIA or AirNow quota. Your Keeper's Log and Foghorn alerts always concern your own place.

## How load is kept bounded

1. **Cache per place.** A place's data is reused for `visitors.ttl_seconds` (an hour by default, 15 minutes for NWS alerts). However many visitors ask for a ZIP, it costs one fetch per source per interval.
2. **One fetch at a time** per source and place: simultaneous requests share one fetch.
3. **Limits per visitor address:** new places per hour (`new_zips_per_hour`, 3 by default; places someone already asked for are free) and requests per minute.
4. **Limits on the whole relay:** a cap on places held (`max_places`, least recently used dropped), on simultaneous visitor fetches and on visitor fetches per minute.
5. **A circuit breaker per source:** if one source keeps failing across visitors' places, it is left alone for five minutes.
6. **Your sources are separate.** They run on their own timers and never wait on any of the above, so visitor traffic cannot slow your own board.
7. **Degrade, do not fail.** Over a limit, a visitor still gets the page: the default place, or data that is still loading, with a message that says why.

The kill switch is `visitors.enabled: false`, which makes the relay ignore visitors' ZIPs. `visitors.allow_ips` restricts lookups to a whitelist of addresses, for an instance meant for a known group.

## Privacy

A ZIP travels in a request header, so it stays out of URLs and proxy access logs. It is held in memory as the key of a cached place and never written to disk, logged, returned or published; the snapshot carries only the county and state. See [Privacy and security](privacy-security.md).

## Behind a reverse proxy

The limits are per address, so the relay must see the visitor's real address. See [Hosting](hosting.md#visitors-zip-codes-and-the-visitors-address). If you expect a burst of visitors, put a CDN in front of the board: the default snapshot has no per-visitor headers and can be cached for a few seconds.

## What is not covered

Zip codes and almost every source here are US-only. A ZIP can span counties and its point can sit miles from an address; the place is the ZIP area's interior point and the county that covers most of it. Visitors do not get EIA or AirNow data for their place, only a link to the publisher.
