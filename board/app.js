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
    brief: "Keeper's Log", local: "Local", environment: "Environment", transport: "Transport", grid: "Power grid", supply: "Supply chain",
    space: "Space weather", tropical: "Tropical", cyber: "Cyber", health: "Health",
    recalls: "Recalls", geo: "Context headlines",
  };
  const DEFAULT_ORDER = ["brief", "local", "environment", "transport", "grid", "supply", "space", "tropical", "cyber", "health", "recalls", "geo"];
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
    const base = { interval: 5, hidden: [], order: [] };
    try {
      const p = JSON.parse(localStorage.getItem(PREF_KEY) || "null");
      if (!p) return base;
      if (INTERVALS.some(([m]) => m === p.interval)) base.interval = p.interval;
      if (Array.isArray(p.hidden)) base.hidden = p.hidden.filter((x) => typeof x === "string");
      if (Array.isArray(p.order)) base.order = p.order.filter((x) => typeof x === "string");
    } catch (_) { /* storage blocked or corrupt: use defaults */ }
    return base;
  }
  const prefs = loadPrefs();
  function savePrefs() {
    try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch (_) { /* ignore */ }
  }

  // ---------- state ----------
  const state = { snap: null, etag: null, lastOk: null, error: null, nextAt: null, loading: false };
  let dragId = null;

  // ---------- row renderers, one per source ----------
  function row(label, value, o) {
    o = o || {};
    const url = o.url ? safeUrl(o.url) : null;
    const cls = "row" + (o.tone ? " t-" + o.tone : "") + (o.stale ? " stale" : "");
    const kids = [h("span", { class: "k" }, label), h("span", { class: "v" }, value)];
    return url ? h("a", { class: cls, href: url, target: "_blank", rel: "noopener" }, kids) : h("div", { class: cls }, kids);
  }

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
      const rows = d.gauges.slice(0, 4).map((g) => {
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
    drought(e, d) {
      const p = d.percent_area;
      return [
        row("Drought", e.summary, { url: e.source_url, tone: e.status === "active" ? "active" : null }),
        row("Area (cumulative)", `D0 ${p.d0.toFixed(0)}% · D1 ${p.d1.toFixed(0)}% · D2 ${p.d2.toFixed(0)}% · D3 ${p.d3.toFixed(0)}%`, { url: e.source_url }),
        row("Map date", d.map_date, { url: e.source_url }),
      ];
    },
    faa_airports(e, d) {
      if (!d.events.length) return [row(d.airports.join(", "), "No delays", { url: e.source_url, tone: "ok" })];
      return d.events.map((x) => {
        const closure = x.kind.toLowerCase().includes("closure");
        return row(x.airport, `${x.kind}${x.reason && !closure ? ": " + clip(x.reason, 40) : ""}`, { url: e.source_url, tone: closure ? null : "active" });
      });
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
      body = h("div", { class: "rows" }, m.envs.flatMap(sourceRows));
      foot = sourceFooter(m.envs);
    }
    const mv = (dir, label) => h("button", { type: "button", class: "mv", "aria-label": `Move ${title} ${label}`, onclick: () => move(m.id, dir) }, dir < 0 ? "◀" : "▶");
    const header = h("header", { draggable: "true", ondragstart: (ev) => {
      dragId = m.id; ev.dataTransfer.effectAllowed = "move"; ev.dataTransfer.setData("text/plain", m.id);
      ev.dataTransfer.setDragImage(el, 20, 20); el.classList.add("dragging");
    }, ondragend: () => { dragId = null; document.querySelectorAll(".panel").forEach((p) => p.classList.remove("dragging", "over")); } },
      h("h2", {}, title),
      h("div", { class: "hd-right" }, mv(-1, "earlier"), mv(1, "later"), h("span", { class: "badge " + m.status }, STATE_TEXT[m.status] || m.status)));
    const el = h("section", { class: "panel" + (m.id === "brief" ? " wide" : ""), id: "panel-" + m.id, "aria-label": title,
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
    const ids = orderedIds(buildModel(state.snap)).filter((x) => !prefs.hidden.includes(x) || x === id);
    const i = ids.indexOf(id), j = i + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    const rest = orderedIds(buildModel(state.snap)).filter((x) => !ids.includes(x));
    prefs.order = ids.concat(rest); savePrefs(); render();
  }

  function toggle(id) {
    const i = prefs.hidden.indexOf(id);
    if (i >= 0) prefs.hidden.splice(i, 1); else prefs.hidden.push(id);
    savePrefs(); render();
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
    $("panels").replaceChildren(...ids.map((id, i) => panelEl(model[id], snap, i)));
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
