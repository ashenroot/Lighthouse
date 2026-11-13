/* Lighthouse board. Reads Fresnel's /v1/snapshot and displays it. No status logic lives here:
   every status shown was computed by the relay. All content is inserted as text, never HTML. */
(function () {
  "use strict";

  const RELAY = (window.LIGHTHOUSE_RELAY || "").replace(/\/+$/, "");
  // With no relay URL set, the snapshot is fetched relative to this page, so the board works at / or under
  // a reverse-proxy prefix such as /fresnel/ without editing anything.
  const SNAPSHOT_URL = RELAY ? RELAY + "/v1/snapshot" : "v1/snapshot";
  const PREF_KEY = "lighthouse.prefs";
  const INTERVALS = [[0, "Off"], [1, "1m"], [5, "5m"], [15, "15m"], [30, "30m"], [60, "1h"]];
  const TITLES = {
    brief: "Keeper's Log", local: "Local", water: "Water", airfire: "Air & fire", transport: "Transport", grid: "Power grid", supply: "Supply chain", finance: "Finance", federal: "Federal actions", headlines: "Headlines",
    space: "Space weather", tropical: "Tropical", cyber: "Cyber", health: "Health",
    recalls: "Recalls", geo: "Internet outages",
  };
  const DEFAULT_ORDER = ["brief", "local", "water", "airfire", "transport", "grid", "supply", "finance", "federal", "space", "tropical", "cyber", "health", "recalls", "geo", "headlines"];
  // Tabs group the panels by theme. A panel that is in no group still appears, under "Other", so a new source is never hidden.
  // The Keeper's Log (brief) is pinned above the tabs. The "All" tab is the whole board in one page.
  const GROUPS = [
    { id: "now", label: "Now", panels: ["local", "water", "airfire", "transport", "tropical"] },
    { id: "infra", label: "Infrastructure", panels: ["grid", "supply", "finance", "federal", "cyber", "space"] },
    { id: "health", label: "Health", panels: ["health", "recalls"] },
    { id: "world", label: "World", panels: ["geo", "headlines"] },
  ];
  const STATE_TEXT = { nominal: "● NOMINAL", active: "▲ ACTIVE", unknown: "? UNKNOWN", info: "ℹ INFO" };
  const SEVERITY = { Unknown: 0, Minor: 1, Moderate: 2, Severe: 3, Extreme: 4 };

  // ---------- small helpers ----------
  const $ = (id) => document.getElementById(id);

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  function safeUrl(u) {
    try {
      const x = new URL(u);
      return x.protocol === "https:" || x.protocol === "http:" ? x.href : null;
    } catch (_) {
      return null;
    }
  }

  const clip = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");
  const hhmm = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—");
  const within = (dateStr, days) => Date.now() - Date.parse(dateStr) < days * 864e5;

  // ---------- preferences (this browser only) ----------
  function loadPrefs() {
    const base = { interval: 5, hidden: [], order: [], tab: "" };
    try {
      const p = JSON.parse(localStorage.getItem(PREF_KEY) || "null");
      if (!p) return base;
      if (INTERVALS.some(([m]) => m === p.interval)) base.interval = p.interval;
      if (Array.isArray(p.hidden)) base.hidden = p.hidden.filter((x) => typeof x === "string");
      if (typeof p.tab === "string") base.tab = p.tab;
      if (Array.isArray(p.order)) base.order = p.order.filter((x) => typeof x === "string");
    } catch (_) { /* storage blocked or corrupt: use defaults */ }
    return base;
  }
  const prefs = loadPrefs();
  function savePrefs() {
    try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch (_) { /* ignore */ }
  }

  // ---------- state ----------
  const state = { snap: null, etag: null, lastOk: null, error: null, nextAt: null, loading: false, visible: [], refocus: null, setupOpen: false };
  let dragId = null;

  // ---------- row renderers, one per source ----------
  function row(label, value, o) {
    o = o || {};
    const url = o.url ? safeUrl(o.url) : null;
    const cls = "row" + (o.tone ? " t-" + o.tone : "") + (o.stale ? " stale" : "") + (o.stack ? " stack" : "");
    const kids = [h("span", { class: "k" }, label), h("span", { class: "v" }, value)];
    return url ? h("a", { class: cls, href: url, target: "_blank", rel: "noopener" }, kids) : h("div", { class: cls }, kids);
  }

  const outlookRows = (e, d, label) => d.days.every((day) => !day.unchecked && !day.category)
    ? [row(label, `No risk area · next ${d.days.length} days`, { url: e.source_url, tone: "ok" })]
    : d.days.map((day) => {
    const name = `${label} · ${day.when}`;
    if (day.unchecked) return row(name, "Not available", { url: e.source_url, tone: "unknown" });
    if (!day.category) return row(name, "No risk area", { url: e.source_url, tone: "ok" });
    const text = day.category.charAt(0).toUpperCase() + day.category.slice(1);
    return row(name, day.rank ? `${text} risk` : text, { url: e.source_url, tone: day.active ? "active" : null });
  });

  const RENDER = {
    nws_alerts(e, d) {
      if (!d.alerts.length) return [row("NWS alerts", "None active", { url: e.source_url, tone: "ok" })];
      return d.alerts.slice(0, 6).map((a) =>
        row(a.event, clip(a.area, 60), { url: e.source_url, tone: (SEVERITY[a.severity] || 0) >= 2 ? "active" : null }));
    },
    openfema(e, d) {
      if (!d.declarations.length) return [row("FEMA declarations", "None this year", { url: e.source_url, tone: "ok" })];
      return d.declarations.slice(0, 4).map((x) =>
        row(`${x.type_label} ${x.number}`, `${clip(x.title, 40)} · ${x.declared}`, { url: x.url, tone: within(x.declared, 30) ? "active" : null }));
    },
    usgs_quakes(e, d) {
      if (!d.quakes.length) return [row("Earthquakes", e.summary, { url: e.source_url, tone: "ok" })];
      return d.quakes.slice(0, 5).map((q) => row(`M${q.mag}`, clip(q.place, 50), { url: q.url }));
    },
    usgs_gauges(e, d) {
      if (!d.gauges.length) return [row("Stream gauges", e.summary, { url: e.source_url })];
      return d.gauges.map((g) => {
        const c = g.change_24h_ft;
        return row(clip(g.name, 42), `${g.height_ft.toFixed(2)} ft (${c >= 0 ? "+" : ""}${c.toFixed(2)}/24h)`,
          { url: g.url, tone: g.above_flood_stage ? "active" : null });
      });
    },
    river_stages(e, d) {
      if (!d.gauges.length) return [row("River gauges", e.summary, { url: e.source_url })];
      const WORDS = { no_flooding: "no flooding", low_threshold: "low water", action: "action stage", minor: "minor flooding",
        moderate: "moderate flooding", major: "major flooding", not_defined: "no flood stage set" };
      const RANK = { action: 1, minor: 2, moderate: 3, major: 4 };
      const floor = RANK[d.active_min] || 2;
      const rows = d.gauges.map((g) => {
        if (!g.reporting) return row(clip(g.name, 40), "Not reporting", { url: g.url, tone: "unknown" });
        const bits = [WORDS[g.observed.category] || g.observed.category];
        if (g.observed.stage != null && g.observed.stage > -900) bits.push(`${g.observed.stage} ${g.observed.unit || "ft"}`);
        if (g.forecast.category) bits.push(`forecast ${g.forecast.category}`);
        return row(clip(g.name, 40), bits.join(" · "), { url: g.url, tone: g.rank >= floor ? "active" : g.rank >= 1 ? null : "ok" });
      });
      rows.push(row("Gauges reporting", `${d.total - d.not_reporting} of ${d.total} within ${d.radius_km} km`,
        { url: e.source_url, tone: d.not_reporting ? "unknown" : null }));
      return rows;
    },
    chokepoints(e, d) {
      const pct = (v) => { const n = Math.round(v); return `${n > 0 ? "+" : ""}${n === 0 ? 0 : n}%`; };
      const rows = d.chokepoints.map((c) => {
        if (!c.rated) return row(c.name, `Not rated (${c.reason})`, { url: e.source_url, tone: "unknown" });
        const bits = [`${Math.round(c.recent_avg)}/day`, `${pct(c.change_pct)} ${d.baseline_days}d`];
        if (c.year_change_pct != null) bits.push(`${pct(c.year_change_pct)} 1y`);
        return row(c.name, bits.join(" · "), { url: e.source_url, tone: c.active ? "active" : "ok" });
      });
      rows.push(row("Data through", `${d.through} (${d.age_days} days ago)`, { url: e.source_url }));
      return rows;
    },
    ioda_outages(e, d) {
      const span = (s) => { const m = Math.floor(s / 60); return m < 90 ? `${m} min` : m < 2880 ? `${Math.floor(m / 60)} h` : `${Math.floor(m / 1440)} days`; };
      return d.countries.map((c) => {
        const more = c.more ? ` (+${c.more} more)` : "";
        if (c.state === "clear") return row(c.name, "No outage", { url: e.source_url, tone: "ok" });
        if (c.state === "unchecked") return row(c.name, "Not checked", { url: e.source_url, tone: "unknown" });
        if (c.state === "ended") return row(c.name, `Outage ended ${span(c.ended_ago_s)} ago (lasted ${span(c.duration_s)})${more}`, { url: e.source_url });
        const brief = c.state === "running" ? " (under the alert minimum)" : "";
        return row(c.name, `Outage running ${span(c.duration_s)} · ${c.source}${brief}${more}`, { url: e.source_url, tone: c.state === "outage" ? "active" : null });
      });
    },
    airnow_aqi(e, d) {
      const rows = [row("US AQI", `${d.aqi} · ${d.category} · ${d.dominant}`, { url: e.source_url, tone: e.status === "active" ? "active" : d.aqi <= 50 ? "ok" : null })];
      d.readings.filter((x) => x.pollutant !== d.dominant).forEach((x) => rows.push(row(x.pollutant, `${x.aqi} · ${x.category}`, { url: e.source_url })));
      return rows.concat([row("Monitors", `${d.area} area`, { url: e.source_url })]);
    },
    air_quality(e, d) {
      const rows = [row("US AQI (modeled)", `${d.aqi} · ${d.category}`,
        { url: e.source_url, tone: e.status === "active" ? "active" : d.aqi <= 50 ? "ok" : null })];
      if (d.pm2_5 != null) rows.push(row("PM2.5", `${d.pm2_5} µg/m³`, { url: e.source_url }));
      return rows;
    },
    nifc_wildfires(e, d) {
      if (!d.fires.length) return [row("Wildfires", e.summary, { url: e.source_url })];
      return d.fires.map((f) => {
        const size = f.acres != null ? `${Math.round(f.acres).toLocaleString()} ac` : "size ?";
        const held = f.percent_contained != null ? `${Math.round(f.percent_contained)}%` : "? %";
        return row(clip(`${f.name}, ${f.county || ""} ${f.state}`.trim(), 40), `${size} · ${held} · ${Math.round(f.distance_km)} km`,
          { url: f.url, tone: f.big ? "active" : null });
      });
    },
    fdic_failures(e, d) {
      const money = (v) => (v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${Math.round(v / 1e6)}M`);
      const rows = d.failures.slice(0, 4).map((f) => row(clip(`${f.name}, ${f.place || ""}`, 40),
        `${f.date}${f.assets_usd ? " · " + money(f.assets_usd) : ""}`, { url: e.source_url, tone: f.days_ago <= d.active_days ? "active" : null }));
      rows.push(row("Since last failure", `${d.days_since_last} days · ${d.window_count} in ${d.window_days} days`, { url: e.source_url }));
      return rows;
    },
    federal_register(e, d) {
      const rows = [
        ...d.emergencies.map((x) => row(`Emergency ${x.date}`, clip(x.title, 56), { url: x.url, tone: "active" })),
        ...d.orders.map((x) => row(`EO ${x.number || ""} · ${x.date}`.trim(), clip(x.title, 56), { url: x.url })),
      ];
      return rows.length ? rows : [row("Presidential docs", e.summary, { url: e.source_url })];
    },
    spc_storm_reports(e, d) {
      if (!d.reports.length) return [row("Storm reports", e.summary, { url: e.source_url })];
      const ago = (m) => (m < 60 ? `${m} min ago` : `${Math.floor(m / 60)} h ago`);
      const rows = d.reports.slice(0, 5).map((r) => row(r.detail.charAt(0).toUpperCase() + r.detail.slice(1),
        `${Math.round(r.distance_km)} km · ${ago(r.age_min)}`, { url: e.source_url, tone: r.significant && r.age_min <= d.active_hours * 60 ? "active" : null }));
      rows.push(row("Reports in range", `${d.in_range} of ${d.nationwide} nationwide`, { url: e.source_url }));
      return rows;
    },
    rss_headlines(e, d) {
      const rows = d.headlines.map((x) => row(x.outlet, clip(x.title, 120), { url: x.url, stack: true }));
      for (const f of d.feeds) if (!f.ok) rows.push(row(f.name, f.problem, { tone: "unknown", stack: true }));
      return rows.length ? rows : [row("Headlines", e.summary, { url: e.source_url })];
    },
    drought(e, d) {
      const p = d.percent_area;
      return [
        row("Drought", e.summary, { url: e.source_url, tone: e.status === "active" ? "active" : null }),
        row("Area (cumulative)", `D0 ${p.d0.toFixed(0)}% · D1 ${p.d1.toFixed(0)}% · D2 ${p.d2.toFixed(0)}% · D3 ${p.d3.toFixed(0)}%`, { url: e.source_url }),
        row("Map date", d.map_date, { url: e.source_url }),
      ];
    },
    faa_airports(e, d) {
      const own = !d.airports.length ? [] : !d.events.length ? [row(d.airports.join(", "), "No delays", { url: e.source_url, tone: "ok" })] : d.events.map((x) => {
        const closure = x.kind.toLowerCase().includes("closure");
        return row(x.airport, `${x.kind}${x.reason && !closure ? ": " + clip(x.reason, 40) : ""}`, { url: e.source_url, tone: closure ? null : "active" });
      });
      const n = d.national, c = n.counts, hm = (m) => (m >= 60 ? `${Math.floor(m / 60)}h${m % 60 ? String(m % 60).padStart(2, "0") : ""}` : `${m}m`);
      const rows = [row("Nationwide", `${c.stops} ground stop${c.stops === 1 ? "" : "s"} · ${c.ground_delay_programs} delay programs · ${c.long_delays} long delays`, { url: e.source_url, tone: n.active ? "active" : null })];
      if (n.equipment.length) rows.push(row("Equipment", n.equipment.map((x) => x.airport).join(", "), { url: e.source_url, tone: n.equipment.length >= n.thresholds.equip_active ? "active" : null }));
      if (n.stops.length) rows.push(row("Ground stops", clip(n.stops.map((x) => x.airport).join(", "), 90), { url: e.source_url, tone: c.stops >= n.thresholds.stops_active ? "active" : null }));
      if (n.long_delays.length) rows.push(row("Longest delays", clip(n.long_delays.map((x) => `${x.airport} ${hm(x.minutes)}${x.trend ? " " + x.trend.toLowerCase() : ""}`).join(", "), 90), { url: e.source_url, stack: true }));
      return own.concat(rows);
    },
    aviation_weather(e, d) {
      return d.airports.map((a) => {
        if (!a.category) return row(a.airport, "No recent report", { url: e.source_url, tone: "unknown" });
        const parts = [a.category];
        if (a.visibility_sm != null) parts.push(`vis ${a.visibility_sm} mi`);
        if (a.ceiling_ft != null) parts.push(`ceiling ${a.ceiling_ft.toLocaleString()} ft`);
        return row(a.airport, parts.join(" · "), { url: e.source_url, tone: a.active ? "active" : a.category === "VFR" ? "ok" : null });
      });
    },
    spc_outlook(e, d) { return outlookRows(e, d, "Severe storms"); },
    wpc_rainfall_outlook(e, d) { return outlookRows(e, d, "Flash flooding"); },
    eia_fuel_prices(e, d) {
      const sgn = (n) => (n == null ? "n/a" : `${n >= 0 ? "+" : ""}${n.toFixed(0)}%`);
      const rows = d.products.map((p) => p.rated
        ? row(`${p.label} (${d.region.name})`, `$${p.price.toFixed(2)} · ${sgn(p.change_weeks_pct)} in ${d.weeks} wk · ${sgn(p.vs_year_pct)} vs year` + (p.national ? ` · US $${p.national.price.toFixed(2)}` : ""), { url: e.source_url, tone: p.active ? "active" : null })
        : row(p.label, "No recent data", { url: e.source_url, tone: "unknown" }));
      return rows.concat([row("Week of", d.week, { url: e.source_url })]);
    },
    eia_grid_demand(e, d) {
      const gw = (mwh) => `${(mwh / 1000).toFixed(1)} GW`;
      const rows = [row(`${d.ba} demand`, `${gw(d.demand_mwh)} · ${d.pct_of_peak.toFixed(0)}% of one-year peak`, { url: e.source_url, tone: d.pct_of_peak >= d.peak_pct ? "active" : null })];
      if (d.forecast) rows.push(row("Day-ahead forecast", `peak ${gw(d.forecast.max_mwh)} (${d.forecast.pct_of_peak.toFixed(0)}%)`, { url: e.source_url, tone: d.forecast.pct_of_peak >= d.peak_pct ? "active" : null }));
      return rows.concat([row("Operator", d.name || d.ba, { url: e.source_url })]);
    },
    aviation_forecast(e, d) {
      const hhz = (ts) => new Date(ts * 1000).toISOString().slice(11, 13) + "Z";
      return d.airports.map((a) => {
        if (!a.forecast) return row(a.airport, "No forecast", { url: e.source_url, tone: "unknown" });
        const maybe = a.possible ? ` · ${a.possible} possible (TEMPO)` : "";
        if (a.active) return row(a.airport, `${a.bad_category} forecast from ${hhz(a.bad_from)}${maybe}`, { url: e.source_url, tone: "active" });
        return row(a.airport, `${a.worst} through ${a.hours} h${maybe}`, { url: e.source_url, tone: a.worst === "VFR" && !a.possible ? "ok" : null });
      });
    },
    cdc_hospital_load(e, d) {
      const rows = [];
      if (d.beds_pct != null) rows.push(row("Inpatient beds", `${d.beds_pct.toFixed(0)}% occupied`, { url: e.source_url, tone: d.beds_active ? "active" : null }));
      if (d.icu_pct != null) rows.push(row("ICU beds", `${d.icu_pct.toFixed(0)}% occupied`, { url: e.source_url, tone: d.icu_active ? "active" : null }));
      d.pathogens.forEach((p) => rows.push(p.patients == null
        ? row(p.pathogen, "No data", { url: e.source_url, tone: "unknown" })
        : row(p.pathogen, `${p.patients.toLocaleString()} in hospital` + (p.change_pct == null ? "" : ` · ${p.change_pct >= 0 ? "+" : ""}${p.change_pct.toFixed(0)}% in ${d.weeks} wk`), { url: e.source_url, tone: p.rising ? "active" : null })));
      return rows.concat([row("Week ending", d.week_end, { url: e.source_url })]);
    },
    fda_drug_shortages(e, d) {
      const tone = (a) => (a === "Unavailable" ? "active" : a === "Limited Availability" ? "active" : "ok");
      const rows = [row("On shortage list", `${d.total} drugs · ${d.counts.Unavailable} with an unavailable product · ${d.counts["Limited Availability"]} limited`, { url: e.source_url })];
      const quiet = d.watch.filter((w) => !w.listed);
      d.watch.filter((w) => w.listed).forEach((w) => rows.push(
        row(`Watching ${w.term}`, clip(w.matches.map((m) => `${m.name}: ${m.availability.toLowerCase()}`).join("; "), 90), { url: e.source_url, tone: tone(w.matches[0].availability) })));
      if (quiet.length) rows.push(row(`Watching ${quiet.length} drug${quiet.length === 1 ? "" : "s"}`, `Not on the shortage list: ${quiet.map((w) => w.term).join(", ")}`, { url: e.source_url, tone: "ok", stack: true }));
      const names = (list) => list.map((m) => m.name.replace(/, /g, " + ")).join(", ");
      [["Unavailable", "Product unavailable"], ["Limited Availability", "Limited"], ["Available", "Available"]].forEach(([key, label]) => {
        const hit = d.drugs.filter((m) => m.availability === key);
        if (hit.length) rows.push(row(`${label} (${hit.length})`, names(hit), { url: e.source_url, stack: true }));
      });
      if (!d.new_total) rows.push(row(`New in ${d.new_days} days`, "None", { url: e.source_url }));
      d.new.forEach((m) => rows.push(row(`New ${m.posted}`, clip(`${m.name} · ${m.availability.toLowerCase()}`, 80), { url: e.source_url, tone: d.new_active ? "active" : null })));
      return rows;
    },
    cdc_wastewater(e, d) {
      const names = Object.keys(d.pathogens);
      return names.map((n) =>
        row(n, `${d.pathogens[n].median_level} · ${d.pathogens[n].sites} sites`,
          { url: e.source_url, tone: d.elevated.includes(n) ? "active" : null }))
        .concat([row("Week ending", d.week_end, { url: e.source_url })]);
    },
    fda_recalls(e, d) {
      if (!d.recalls.length) return [row("FDA recalls", e.summary, { url: e.source_url, tone: "ok" })];
      return d.recalls.slice(0, 5).map((r) =>
        row(r.class || "Recall", clip(`${r.firm}: ${r.product}`, 80), { url: r.url, tone: r.class === "Class I" ? "active" : null }));
    },
    cpsc_recalls(e, d) {
      if (!d.recalls.length) return [row("CPSC recalls", e.summary, { url: e.source_url, tone: "ok" })];
      return d.recalls.slice(0, 5).map((r) => row(r.date, clip(r.title, 80), { url: r.url }));
    },
    cisa_kev(e, d) {
      if (!d.recent.length) return [row("CISA KEV", e.summary, { url: e.source_url, tone: "ok" })];
      return d.recent.slice(0, 6).map((v, i) =>
        row(v.cve, clip(`${v.vendor} ${v.product}`, 50), { url: v.url, tone: i < d.newest_count ? "active" : null }));
    },
    odin_outages(e, d) {
      const url = e.source_url, hot = e.status === "active" ? "active" : null;
      const where = d.state.reporting
        ? `${d.state.customers_out.toLocaleString()} out` + (d.county.reporting ? ` · ${d.county.customers_out.toLocaleString()} in county` : "")
        : "None reported";
      return [
        row(`${d.state.abbr} customers (reported)`, where, { url, tone: hot }),
        row("US (reported)", `${d.national.customers_out.toLocaleString()} · ${d.national.states_reporting} states`, { url }),
        row("Coverage", "Voluntary. A floor, not an all-clear.", { url }),
      ];
    },
    doe_202c(e, d) {
      const rows = [row("Orders in force", String(d.in_force), { url: e.source_url })];
      const KIND = { "retirement deferral": "plant kept open", "event dispatch": "event", "capacity assurance": "capacity" };
      d.orders.slice(0, 3).forEach((o) => {
        const fresh = d.fresh.includes(o.id);
        rows.push(row(o.date, clip(`${o.recipient || o.id} · ${KIND[o.character] || "unclassified"}`, 60), { url: e.source_url, tone: fresh ? "active" : null }));
      });
      if (d.read < d.on_page) rows.push(row("Orders read", `${d.read} of ${d.on_page} on the page`, { url: e.source_url }));
      return rows;
    },
    nerc_alerts(e, d) {
      return d.alerts.slice(0, 3).map((a) => row(a.date, clip(a.title, 60), { url: a.url }));
    },
    grid_operators(e, d) {
      const chips = d.links.map((l) => {
        const u = safeUrl(l.url);
        return u ? h("a", { href: u, target: "_blank", rel: "noopener", title: l.region || l.name }, l.name) : null;
      });
      return [h("div", { class: "chips" }, h("span", { class: "k" }, "Regional dashboards"), h("div", {}, chips))];
    },
    swpc_kp(e) {
      return [row("Planetary Kp", e.summary, { url: e.source_url, tone: e.status === "active" ? "active" : null })];
    },
    swpc_scales(e, d) {
      const f = d.forecast[0];
      const out = [row("R / S / G now", e.summary, { url: e.source_url, tone: e.status === "active" ? "active" : null })];
      if (f && f.date) out.push(row("Next day odds", `R1+ ${f.r_minor_prob ?? "—"}% · R3+ ${f.r_major_prob ?? "—"}% · S1+ ${f.s_prob ?? "—"}%`, { url: e.source_url }));
      return out;
    },
    nhc_storms(e, d) {
      if (!d.storms.length) return [row("Tropical cyclones", "None active", { url: e.source_url, tone: "ok" })];
      return d.storms.slice(0, 6).map((s) =>
        row(`${s.classification || "TC"} ${s.name || s.id}`, `${s.basin.toUpperCase()} · ${s.intensity_kt ?? "?"} kt`,
          { url: s.advisory_url || e.source_url, tone: d.watched_basins.includes(s.basin) ? "active" : null }));
    },
  };

  function sourceRows(e) {
    const fallback = () => [row(e.title, e.summary, { url: e.source_url })];
    if (e.status === "unknown" && !e.data) return [row(e.title, e.summary, { url: e.source_url, tone: "unknown" })];
    let rows;
    try {
      rows = (RENDER[e.id] || fallback)(e, e.data || {});
    } catch (_) {
      rows = [row(e.title, "Unreadable data from relay", { tone: "unknown" })];
    }
    if (e.status === "unknown") {
      rows.forEach((r) => r.classList.add("stale"));
      rows.unshift(row(e.title, e.summary, { tone: "unknown" }));
    }
    return rows;
  }

  // ---------- keeper's log ----------
  function inline(s) {
    const out = [];
    const re = /\*\*(.+?)\*\*|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>"'\)]+)/g;
    let last = 0, m;
    while ((m = re.exec(s))) {
      if (m.index > last) out.push(s.slice(last, m.index));
      if (m[1]) out.push(h("strong", {}, m[1]));
      else if (m[4]) {
        const u = safeUrl(m[4]);
        out.push(u ? h("a", { href: u, target: "_blank", rel: "noopener" }, clip(m[4], 80)) : m[4]);
      } else {
        const u = safeUrl(m[3]);
        out.push(u ? h("a", { href: u, target: "_blank", rel: "noopener" }, m[2]) : m[0]);
      }
      last = re.lastIndex;
    }
    if (last < s.length) out.push(s.slice(last));
    return out;
  }

  // Plain-text briefs arrive as long paragraphs. Make them scannable: join hard-wrapped lines,
  // turn bullet runs into lists, bold a leading "Label:", and break long paragraphs at sentence ends.
  const ABBR = /(?:\b[A-Za-z]|\bU\.S|\bMr|\bMrs|\bMs|\bDr|\bSt|\bNo|\bvs|\bInc|\bCo|\bCorp|\bGen|\bSen|\bRep|\bGov|\bJan|\bFeb|\bAug|\bSept?|\bOct|\bNov|\bDec)\.$/;
  const BULLET = /^\s*(?:[-*\u2022\u2013]|\d{1,2}[.)])\s+/;
  function sentences(p) {
    const parts = p.split(/(?<=[.!?])\s+(?=[A-Z0-9"\u201c(])/), out = [];
    for (const part of parts) {
      if (out.length && ABBR.test(out[out.length - 1])) out[out.length - 1] += " " + part;
      else out.push(part);
    }
    return out;
  }
  function chunks(p) {
    if (p.length <= 420) return [p];
    const out = []; let cur = "";
    for (const sent of sentences(p)) {
      if (cur && (cur + " " + sent).length > 340) { out.push(cur); cur = sent; }
      else cur = cur ? cur + " " + sent : sent;
    }
    if (cur) out.push(cur);
    return out;
  }
  function labelled(p) {
    const m = /^([A-Z][A-Za-z0-9 \/&'\u2019().-]{1,40}):\s+(\S[\s\S]*)$/.exec(p);
    if (!m || /https?$/i.test(m[1])) return inline(p);
    return [h("strong", { class: "lbl" }, m[1] + ": "), ...inline(m[2])];
  }
  function readable(text) {
    const out = [];
    for (const block of text.split(/\n{2,}/)) {
      let para = [], list = [];
      const flushPara = () => {
        if (!para.length) return;
        for (const c of chunks(para.join(" ").replace(/\s+/g, " ").trim())) out.push(h("p", {}, labelled(c)));
        para = [];
      };
      const flushList = () => {
        if (list.length) out.push(h("ul", { class: "pts" }, list.map((l) => h("li", {}, labelled(l)))));
        list = [];
      };
      for (const line of block.split("\n")) {
        if (!line.trim()) continue;
        if (BULLET.test(line)) { flushPara(); list.push(line.replace(BULLET, "").trim()); }
        else if (list.length && /^\s{2,}\S/.test(line)) list[list.length - 1] += " " + line.trim();
        else { flushList(); para.push(line.trim()); }
      }
      flushPara(); flushList();
    }
    return out;
  }

  function markdown(text, format) {
    if (format === "text") return readable(text);
    return text.split(/\n{2,}/).map((block) => {
      const lines = block.split("\n").filter(Boolean);
      if (lines.length && lines.every((l) => /^\s*[-*]\s+/.test(l)))
        return h("ul", {}, lines.map((l) => h("li", {}, inline(l.replace(/^\s*[-*]\s+/, "")))));
      if (/^#{1,6}\s/.test(block)) return h("h3", {}, inline(block.replace(/^#{1,6}\s+/, "")));
      return h("p", {}, inline(lines.join(" ")));
    });
  }

  // ---------- brief sections: a list with a rating pill, each opening in a popout ----------
  const LEVELS = ["critical", "elevated", "notable", "quiet"];
  const pillText = (c) => (c ? c.toUpperCase() : "UNRATED");

  let popout = null;
  function openSection(sec, format) {
    if (!popout) {
      popout = h("dialog", { class: "pop", "aria-labelledby": "pop-title" });
      popout.addEventListener("click", (ev) => { if (ev.target === popout) popout.close(); });
      document.body.append(popout);
    }
    popout.replaceChildren(
      h("div", { class: "pop-head" },
        h("h2", { id: "pop-title" }, sec.title || "Section"),
        sec.criticality || !sec.sources.length ? h("span", { class: "pill " + (sec.criticality || "unrated") }, pillText(sec.criticality)) : null,
        h("button", { class: "btn mv", type: "button", "aria-label": "Close", onclick: () => popout.close() }, "✕")),
      h("div", { class: "pop-body" },
        sec.sources.length
          ? h("ul", { class: "links" }, sec.sources.map((u) => { const su = safeUrl(u); return su ? h("li", {}, h("a", { href: su, target: "_blank", rel: "noopener" }, clip(su, 110))) : null; }))
          : markdown(sec.body || "", format)));
    popout.showModal();
  }

  function sectionList(sections, format) {
    const counts = {};
    for (const s of sections) if (s.criticality) counts[s.criticality] = (counts[s.criticality] || 0) + 1;
    const summary = LEVELS.filter((l) => counts[l]).map((l) => `${counts[l]} ${l}`).join(" · ");
    const out = [];
    if (summary) out.push(h("div", { class: "sub mono" }, summary));
    let group = null;
    const list = h("div", { class: "secs" });
    for (const s of sections) {
      if (s.group && s.group !== group) list.append(h("div", { class: "eyebrow mono grp" }, s.group));
      group = s.group || group;
      const sec = { ...s, sources: s.sources || [] };
      list.append(h("button", { class: "sec", type: "button", onclick: () => openSection(sec, format) },
        h("span", { class: "num mono" }, s.number != null ? String(s.number) : ""),
        h("span", { class: "ttl" }, s.title || "Section"),
        s.criticality || s.number != null
          ? h("span", { class: "pill " + (s.criticality || "unrated") }, pillText(s.criticality))
          : h("span", { class: "sub" }, `${sec.sources.length} links`),
        h("span", { class: "go", "aria-hidden": "true" }, "›")));
    }
    out.push(list);
    return out;
  }

  function briefPanel(env, snap) {
    const d = env && env.data;
    const main = h("div", { class: "main" });
    if (!d) {
      main.append(h("p", { class: "muted" }, "No brief has been received yet."));
    } else {
      main.append(h("div", { class: "sub mono" }, `${d.title} · generated ${new Date(d.generated_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`));
      if (env.fetch.stale) main.append(h("p", { class: "warn" }, "This brief is out of date. The producer may have stopped."));
      if (d.body && d.body.trim() !== (d.title || "").trim()) main.append(...markdown(d.body, d.format));
      const secs = d.sections || [];
      if (secs.length) main.append(...sectionList(secs, d.format));
      if ((d.source_notes || []).length) main.append(h("div", { class: "sub" }, "Sources: " + d.source_notes.join(" · ")));
    }
    const live = Object.values(snap.sources).filter((s) => s.status === "active");
    const side = h("div", { class: "side" },
      h("div", { class: "eyebrow mono flush" }, "Live now"),
      live.length
        ? live.map((s) => h("div", {}, h("span", { class: "muted" }, s.title + ": "), s.summary))
        : h("div", { class: "muted" }, "No live source is active."));
    return { body: h("div", { class: "log" }, main, side), foot: null };
  }

  function contextPanel(env) {
    const items = (env && env.data && env.data.items) || [];
    if (!items.length) return h("div", { class: "empty" }, env && env.fetch.stale ? "Headlines are out of date." : "No headlines pushed yet.");
    return h("div", { class: "rows" }, items.slice(0, 8).map((i) =>
      row(clip(i.source || "", 24), clip(i.title, 90), { url: i.url, stale: env.fetch.stale })));
  }

  // ---------- assembling panels ----------
  function buildModel(snap) {
    const model = {};
    for (const [id, p] of Object.entries(snap.panels || {}))
      model[id] = { id, status: p.status, envs: p.sources.map((s) => snap.sources[s]).filter(Boolean) };
    for (const env of Object.values(snap.push || {})) {
      const m = model[env.panel] || (model[env.panel] = { id: env.panel, status: env.status, envs: [] });
      m.push = (m.push || []).concat(env);
      m.status = m.envs.length ? m.status : env.status;
    }
    return model;
  }

  function tileDetail(m) {
    const live = m.envs.find((e) => e.status === "active") || m.envs[0] || (m.push && m.push[0]);
    return live ? live.summary : "";
  }

  function sourceFooter(envs) {
    return h("div", { class: "foot-src" }, envs.map((e) => {
      const f = e.fetch;
      const bad = f.error || f.stale;
      const url = safeUrl(e.source_url || "");
      const name = url ? h("a", { href: url, target: "_blank", rel: "noopener" }, e.title) : e.title;
      return h("span", {}, name, " · ", h("span", { class: bad ? "bad" : "" }, f.error ? "error: " + f.error : f.stale ? "stale" : "ok " + hhmm(f.fetched_at)));
    }));
  }

  function orderedIds(model) {
    const known = new Set(Object.keys(model));
    const out = [];
    for (const id of [...prefs.order, ...DEFAULT_ORDER, ...Object.keys(model)])
      if (known.has(id) && !out.includes(id)) out.push(id);
    return out;
  }

  function panelEl(m, snap, index) {
    const title = TITLES[m.id] || m.id.charAt(0).toUpperCase() + m.id.slice(1);
    let body, foot;
    if (m.id === "brief") {
      const r = briefPanel((m.push || []).find((e) => e.id === "brief"), snap);
      body = r.body; foot = null;
    } else if (!m.envs.length && m.push) {
      body = contextPanel(m.push[0]); foot = null;
    } else {
      body = h("div", { class: "rows" + (m.id === "headlines" ? " multi" : "") }, m.envs.flatMap(sourceRows));
      foot = sourceFooter(m.envs);
    }
    const mv = (dir, label) => h("button", { type: "button", class: "mv", "aria-label": `Move ${title} ${label}`, onclick: () => move(m.id, dir) }, dir < 0 ? "◀" : "▶");
    const header = h("header", { draggable: "true", ondragstart: (ev) => {
      dragId = m.id; ev.dataTransfer.effectAllowed = "move"; ev.dataTransfer.setData("text/plain", m.id);
      ev.dataTransfer.setDragImage(el, 20, 20); el.classList.add("dragging");
    }, ondragend: () => { dragId = null; document.querySelectorAll(".panel").forEach((p) => p.classList.remove("dragging", "over")); } },
      h("h2", {}, title),
      h("div", { class: "hd-right" }, mv(-1, "earlier"), mv(1, "later"), h("span", { class: "badge " + m.status }, STATE_TEXT[m.status] || m.status)));
    const el = h("section", { class: "panel" + (m.id === "brief" || m.id === "headlines" ? " wide" : ""), id: "panel-" + m.id, "aria-label": title,
      ondragover: (ev) => { if (dragId && dragId !== m.id) { ev.preventDefault(); el.classList.add("over"); } },
      ondragleave: () => el.classList.remove("over"),
      ondrop: (ev) => { ev.preventDefault(); el.classList.remove("over"); if (dragId) reorder(dragId, m.id); } },
      header, body, foot);
    el.style.order = String(index);
    el.hidden = prefs.hidden.includes(m.id);
    return el;
  }

  function reorder(from, to) {
    const ids = orderedIds(buildModel(state.snap));
    ids.splice(ids.indexOf(from), 1);
    ids.splice(ids.indexOf(to), 0, from);
    prefs.order = ids; savePrefs(); render();
  }

  function move(id, dir) {
    const vis = state.visible;
    const i = vis.indexOf(id), j = i + dir;
    if (i < 0 || j < 0 || j >= vis.length) return;
    const all = orderedIds(buildModel(state.snap));
    const x = all.indexOf(vis[i]), y = all.indexOf(vis[j]);
    [all[x], all[y]] = [all[y], all[x]];
    prefs.order = all; savePrefs(); render();
  }

  function toggle(id) {
    const i = prefs.hidden.indexOf(id);
    if (i >= 0) prefs.hidden.splice(i, 1); else prefs.hidden.push(id);
    savePrefs(); render();
  }

  // ---------- tabs ----------
  const WORST = ["nominal", "info", "unknown", "active"];

  function tabList(model) {
    const ids = Object.keys(model).filter((id) => id !== "brief");
    const grouped = new Set(GROUPS.flatMap((g) => g.panels));
    const tabs = GROUPS.map((g) => ({ id: g.id, label: g.label, panels: g.panels.filter((id) => ids.includes(id)) }));
    const other = ids.filter((id) => !grouped.has(id));
    if (other.length) tabs.push({ id: "other", label: "Other", panels: other });
    const shown = tabs.filter((t) => t.panels.length);
    shown.push({ id: "all", label: "All", panels: ids });
    return shown;
  }

  function tabBadge(t, model) {
    const live = t.panels.filter((id) => !prefs.hidden.includes(id)).map((id) => model[id].status);
    const n = (s) => live.filter((x) => x === s).length;
    if (n("active")) return { cls: "active", text: `${n("active")} active` };
    if (n("unknown")) return { cls: "unknown", text: `${n("unknown")} unknown` };
    return { cls: live.some((x) => x === "info") ? "info" : "nominal", text: live.length ? "●" : "–" };
  }

  function selectTab(id) { prefs.tab = id; savePrefs(); render(); }

  function renderTabs(tabs, current, model) {
    const bar = $("tabs");
    const held = document.activeElement && document.activeElement.id && document.activeElement.id.startsWith("tab-") ? document.activeElement.id.slice(4) : null;
    if (held && !state.refocus) state.refocus = held;  // a redraw must not drop keyboard focus from the tab bar
    bar.replaceChildren(...tabs.map((t, i) => {
      const badge = tabBadge(t, model);
      const on = t.id === current;
      return h("button", {
        type: "button", role: "tab", id: "tab-" + t.id, class: "tab", "aria-selected": String(on), tabindex: on ? "0" : "-1",
        "aria-controls": "panels", "aria-label": `${t.label}, ${badge.cls === "active" || badge.cls === "unknown" ? badge.text : "all nominal"}`,
        onclick: () => { state.refocus = t.id; selectTab(t.id); },
        onkeydown: (ev) => {
          const step = ev.key === "ArrowRight" ? 1 : ev.key === "ArrowLeft" ? -1 : 0;
          if (!step) return;
          ev.preventDefault();
          const next = tabs[(i + step + tabs.length) % tabs.length];
          state.refocus = next.id; selectTab(next.id);
        },
      }, h("span", {}, t.label), h("span", { class: "tab-badge " + badge.cls, "aria-hidden": "true" }, badge.text));
    }), h("span", { class: "tab-hint mono" }, "drag a panel, or use ◀ ▶, to rearrange"));
    if (state.refocus) { const el = $("tab-" + state.refocus); if (el) el.focus(); state.refocus = null; }
  }

  // Sources that need a free API key are listed here, once, instead of as empty panels.
  function renderSetup(items) {
    const box = $("setup");
    if (!items.length) { box.hidden = true; box.replaceChildren(); return; }
    box.hidden = false;
    const n = items.length;
    const det = h("details", { class: "setup-box" },
      h("summary", { class: "mono" }, `Setup · ${n} source${n === 1 ? " is" : "s are"} waiting for a free API key`),
      h("div", { class: "setup-list" }, items.map((it) => {
        const url = safeUrl(it.signup_url || "");
        return h("div", { class: "setup-row" },
          h("span", { class: "setup-t" }, it.title),
          h("code", {}, it.command),
          url ? h("a", { href: url, target: "_blank", rel: "noopener" }, `Get a free key (${it.label})`) : null);
      })));
    if (state.setupOpen) det.setAttribute("open", "");
    det.addEventListener("toggle", () => { state.setupOpen = det.open; });
    box.replaceChildren(det);
  }

  // ---------- rendering ----------
  function renderHeader() {
    const s = state.snap;
    if (s) $("where").textContent = `${s.location.name} · county ${s.location.county_fips}`;
    const now = new Date();
    $("when").textContent = now.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }) +
      (state.lastOk ? ` · updated ${hhmm(state.lastOk)}` : "");
    const banner = $("banner");
    if (state.error) {
      banner.hidden = false;
      banner.textContent = `Cannot reach the relay (${state.error}). ` + (s ? "Showing the last data received, which may be out of date." : "Nothing to show yet.");
    } else banner.hidden = true;
  }

  function renderIntervals() {
    const box = $("intervals");
    box.replaceChildren(...INTERVALS.map(([m, label]) =>
      h("button", { type: "button", "aria-pressed": String(prefs.interval === m), onclick: () => { prefs.interval = m; savePrefs(); schedule(); renderIntervals(); tick(); } }, label)));
  }

  function render() {
    renderHeader();
    const snap = state.snap;
    if (!snap) return;
    const model = buildModel(snap);
    const ids = orderedIds(model);
    $("tiles").replaceChildren(...ids.map((id) => {
      const m = model[id];
      const on = !prefs.hidden.includes(id);
      return h("button", { type: "button", class: "tile " + m.status, "aria-pressed": String(on), onclick: () => toggle(id) },
        h("div", { class: "st" }, STATE_TEXT[m.status] || m.status),
        h("div", { class: "lb" }, TITLES[id] || id),
        h("div", { class: "dt" }, clip(tileDetail(m), 60)));
    }));
    const tabs = tabList(model);
    const current = (tabs.find((t) => t.id === prefs.tab) || tabs[0]).id;
    renderTabs(tabs, current, model);
    const inTab = new Set(tabs.find((t) => t.id === current).panels);
    const mine = ids.filter((id) => inTab.has(id));
    state.visible = mine.filter((id) => !prefs.hidden.includes(id));
    $("pinned").replaceChildren(...(model.brief ? [panelEl(model.brief, snap, 0)] : []));
    $("panels").setAttribute("aria-labelledby", "tab-" + current);
    $("panels").replaceChildren(...(mine.length && !state.visible.length
      ? [h("div", { class: "empty wide-note" }, "Every panel in this tab is hidden. Click its tile above to show it again.")]
      : mine.map((id, i) => panelEl(model[id], snap, i))));
    renderSetup(snap.setup || []);
    $("relay-info").textContent = `Fresnel relay · schema ${snap.schema} · ${Object.keys(snap.sources).length} sources`;
  }

  // ---------- loading ----------
  async function load() {
    if (state.loading) return;
    state.loading = true;
    try {
      const headers = state.etag ? { "If-None-Match": state.etag } : {};
      const r = await fetch(SNAPSHOT_URL, { headers, cache: "no-store" });
      if (r.status === 304) {
        state.lastOk = new Date(); state.error = null;
      } else if (r.ok) {
        state.snap = await r.json();
        state.etag = r.headers.get("ETag");
        state.lastOk = new Date(); state.error = null;
      } else throw new Error("HTTP " + r.status);
    } catch (err) {
      state.error = err && err.message ? err.message : "network error";
    } finally {
      state.loading = false;
      schedule();
      render();
    }
  }

  function schedule() {
    state.nextAt = prefs.interval > 0 ? Date.now() + prefs.interval * 60000 : null;
  }

  function tick() {
    const next = $("next");
    if (state.nextAt) {
      const left = Math.max(0, Math.round((state.nextAt - Date.now()) / 1000));
      next.textContent = `next in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
      if (left === 0) load();
    } else next.textContent = "manual";
  }

  $("refresh-now").addEventListener("click", load);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.nextAt && Date.now() >= state.nextAt) load();
  });
  renderIntervals();
  setInterval(tick, 1000);
  setInterval(renderHeader, 30000);
  load();
})();
