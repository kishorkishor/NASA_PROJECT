"use strict";
/* Burn Before the Rain - demo page. All data comes from the local API; untrusted strings go in via textContent. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const NS = "http://www.w3.org/2000/svg";
const tip = $("#tooltip");
const state = { region: "hills", kind: "observed", sel: null, summary: null, annual: {}, monthly: null, redraws: [] };

const PRIO = {
  "inspect first": { cls: "p-first", icon: "◆", label: "Inspect first", color: "--critical", fill: 0.9 },
  "inspect": { cls: "p-inspect", icon: "▲", label: "Inspect", color: "--serious", fill: 0.8 },
  "monitor": { cls: "p-monitor", icon: "●", label: "Monitor", color: "--monitor", fill: 0.28 },
  "none": { cls: "p-monitor", icon: "·", label: "No fire detected", color: "--monitor", fill: 0 },
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
const num = (v, d = 0) => (v == null || Number.isNaN(v)) ? "–"
  : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (v, d = 0) => (v == null || Number.isNaN(v)) ? "–" : `${(v * 100).toFixed(d)}%`;
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function h(tag, attrs = {}, parent, text) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") e.className = v; else e.setAttribute(k, v);
  }
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function sv(tag, attrs = {}, parent, text) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function showState(root, msg, retry) {
  root.replaceChildren();
  const box = h("div", { class: "state" }, root);
  const inner = h("div", {}, box);
  h("div", {}, inner, msg);
  if (retry) h("button", { type: "button" }, inner, "Try again").addEventListener("click", retry);
}
async function guarded(root, load) {
  try {
    await load();
  } catch (err) {
    console.error(err);
    showState(root, `Could not load this part (${err.message}).`, () => {
      showState(root, "Loading…");
      guarded(root, load);
    });
  }
}
function niceTicks(max, count = 4) {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(st => st >= raw);
  const ticks = [];
  for (let v = 0; v < max + step; v += step) ticks.push(+v.toFixed(6));
  return ticks;
}
function responsive(root, draw) {
  let width = 0;
  const run = w => { width = w; draw(w); };
  new ResizeObserver(entries => {
    const w = Math.round(entries[0].contentRect.width);
    if (w && Math.abs(w - width) > 2) run(w);
  }).observe(root);
  const redraw = () => { if (width) draw(width); };
  state.redraws.push(redraw);
  return redraw;
}

function tipShow(title, rows, x, y) {
  tip.replaceChildren();
  h("div", { class: "tt-title" }, tip, title);
  for (const r of rows) {
    const row = h("div", { class: "tt-row" }, tip);
    const key = h("span", {}, row);
    if (r.color) { const k = h("span", { class: "key-line" }, key); k.style.background = `var(${r.color})`; }
    h("strong", {}, row, r.value);
    h("span", {}, row, r.label);
  }
  tip.classList.add("on");
  const pad = 12, w = tip.offsetWidth, hgt = tip.offsetHeight;
  let left = x + pad, top = y - hgt - pad;
  if (left + w > window.scrollX + document.documentElement.clientWidth - 8) left = x - w - pad;
  if (top < window.scrollY + 8) top = y + pad;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${top}px`;
}
const tipHide = () => tip.classList.remove("on");

function lineChart(root, cfg) {
  return responsive(root, width => {
    root.replaceChildren();
    const height = cfg.height ?? (width < 520 ? 240 : 300);
    const m = { t: 22, r: width < 520 ? 70 : 104, b: 30, l: 52 };
    const iw = width - m.l - m.r, ih = height - m.t - m.b;
    const years = cfg.years, x0 = years[0], x1 = years[years.length - 1];
    const X = yr => m.l + (yr - x0) / (x1 - x0) * iw;
    let ymax = 0;
    for (const se of cfg.series) {
      for (const v of se.values.values()) if (v != null) ymax = Math.max(ymax, v);
      if (se.band) for (const [, hi] of se.band.values()) ymax = Math.max(ymax, hi);
    }
    const ticks = niceTicks(ymax * 1.05);
    const ytop = ticks[ticks.length - 1];
    const Y = v => m.t + ih - v / ytop * ih;
    const svg = sv("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", tabindex: 0,
      "aria-label": `${cfg.label} Use the left and right arrow keys to read values; the table below has every number.` }, root);

    const gShade = sv("g", { class: "annot" }, svg);
    for (const sh of cfg.shades || []) {
      const xa = X(Math.max(sh.from - 0.5, x0 - 0.3)), xb = X(Math.min(sh.to + 0.5, x1 + 0.3));
      sv("rect", { class: "shade", x: xa, y: m.t, width: Math.max(0, xb - xa), height: ih }, gShade);
      if (sh.label && xb - xa > 40) sv("text", { x: xa + 4, y: m.t - 6 }, gShade, sh.label);
    }
    const gAxis = sv("g", { class: "axis" }, svg);
    for (const t of ticks) {
      sv("line", { class: t === 0 ? "baseline" : "gridline", x1: m.l, x2: m.l + iw, y1: Y(t), y2: Y(t) }, gAxis);
      sv("text", { x: m.l - 8, y: Y(t) + 4, "text-anchor": "end" }, gAxis, num(t));
    }
    const every = width < 520 ? 5 : width < 860 ? 3 : 2;
    for (const yr of years) {
      if ((yr - x0) % every === 0) sv("text", { x: X(yr), y: height - 8, "text-anchor": "middle" }, gAxis, String(yr));
    }
    const gRule = sv("g", { class: "annot" }, svg);
    for (const r of cfg.rules || []) {
      const xr = X(r.x);
      sv("line", { class: "rule", x1: xr, x2: xr, y1: m.t, y2: m.t + ih }, gRule);
      sv("text", { x: xr + 6, y: m.t + 12 }, gRule, r.label);
    }
    for (const se of cfg.series) {
      if (se.band) {
        const bys = years.filter(y => se.band.has(y));
        if (bys.length > 1) {
          const top = bys.map(y => `${X(y)},${Y(se.band.get(y)[1])}`);
          const bottom = bys.slice().reverse().map(y => `${X(y)},${Y(se.band.get(y)[0])}`);
          const p = sv("path", { d: `M${top.join("L")}L${bottom.join("L")}Z` }, svg);
          p.style.fill = "var(--band-1)";
        }
      }
    }
    const ends = [];
    for (const se of cfg.series) {
      const pts = years.filter(y => se.values.get(y) != null);
      if (!pts.length) continue;
      const p = sv("path", { class: "series", d: `M${pts.map(y => `${X(y)},${Y(se.values.get(y))}`).join("L")}` }, svg);
      p.style.stroke = `var(${se.color})`;
      const last = pts[pts.length - 1];
      const dot = sv("circle", { class: "end-dot", cx: X(last), cy: Y(se.values.get(last)), r: 4 }, svg);
      dot.style.fill = `var(${se.color})`;
      if (se.endLabel) ends.push({ se, y: Y(se.values.get(last)), x: X(last) });
    }
    // Direct end-labels only where they don't collide; the legend and table carry the rest.
    ends.sort((a, b) => a.y - b.y);
    let lastY = -Infinity;
    for (const e of ends) {
      if (e.y - lastY < 30) continue;
      sv("text", { class: "dlabel", x: e.x + 10, y: e.y + 1 }, svg, e.se.endLabel);
      sv("text", { class: "dlabel sub", x: e.x + 10, y: e.y + 16 }, svg, num(e.se.values.get(years.filter(y => e.se.values.get(y) != null).pop())));
      lastY = e.y;
    }

    for (const mk of cfg.marks || []) {
      const se = cfg.series.find(x => x.key === mk.series);
      const v = se && se.values.get(mk.x);
      if (v == null) continue;
      const g = sv("g", { class: "annot" }, svg);
      const ring = sv("circle", { cx: X(mk.x), cy: Y(v), r: 5, fill: "none", "stroke-width": 1.5 }, g);
      ring.style.stroke = "var(--ink-2)";
      sv("text", { x: X(mk.x), y: Math.min(m.t + ih - 4, Y(v) + 18), "text-anchor": "middle" }, g, mk.label);
    }
    const hair = sv("line", { class: "crosshair", y1: m.t, y2: m.t + ih, visibility: "hidden" }, svg);
    const hit = sv("rect", { x: m.l - 8, y: m.t, width: iw + 16, height: ih, fill: "transparent" }, svg);
    let cur = null;
    const show = yr => {
      cur = Math.min(x1, Math.max(x0, yr));
      hair.setAttribute("x1", X(cur)); hair.setAttribute("x2", X(cur)); hair.setAttribute("visibility", "visible");
      const rows = [];
      for (const se of cfg.series) {
        const v = se.values.get(cur);
        rows.push({ color: se.color, value: num(v), label: se.label });
        if (se.band && se.band.has(cur)) {
          const [lo, hi] = se.band.get(cur);
          rows.push({ color: null, value: `${num(lo)}–${num(hi)}`, label: "95% calibration uncertainty" });
        }
      }
      const note = cfg.note ? cfg.note(cur) : "";
      const box = svg.getBoundingClientRect();
      const scale = box.width / width;
      tipShow(`${cur}${note ? ` · ${note}` : ""}`, rows, box.left + window.scrollX + X(cur) * scale, box.top + window.scrollY + m.t * scale + 20);
    };
    const hide = () => { hair.setAttribute("visibility", "hidden"); tipHide(); };
    hit.addEventListener("pointermove", e => {
      const box = svg.getBoundingClientRect();
      const px = (e.clientX - box.left) * (width / box.width);
      show(Math.round(x0 + (px - m.l) / iw * (x1 - x0)));
    });
    hit.addEventListener("pointerleave", hide);
    svg.addEventListener("focus", () => show(cur ?? x1));
    svg.addEventListener("blur", hide);
    svg.addEventListener("keydown", e => {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        show((cur ?? x1) + (e.key === "ArrowLeft" ? -1 : 1));
      } else if (e.key === "Escape") hide();
    });
  });
}

function dataTable(root, headers, rows) {
  root.replaceChildren();
  const t = h("table", { class: "data" }, root);
  const tr = h("tr", {}, h("thead", {}, t));
  for (const hd of headers) h("th", { scope: "col" }, tr, hd);
  const tb = h("tbody", {}, t);
  for (const r of rows) {
    const row = h("tr", {}, tb);
    r.forEach((c, i) => h(i === 0 ? "th" : "td", i === 0 ? { scope: "row" } : {}, row, c));
  }
  return t;
}

function completeYears(rows) { return rows.filter(r => r.months === 12).map(r => r.year); }

function drawNaive() {
  const rows = state.annual.all;
  const years = completeYears(rows);
  const byYear = new Map(rows.map(r => [r.year, r]));
  lineChart($("#chart-naive"), {
    label: "Line chart: vegetation fire detections per year in Bangladesh when MODIS and VIIRS are joined as-is; the count jumps from about 3,000 to about 10,000 in 2012 when VIIRS starts.",
    years,
    series: [{ key: "naive", label: "Joined as-is", color: "--series-2", endLabel: "Joined as-is",
      values: new Map(years.map(y => [y, byYear.get(y).naive_stitched])) }],
    rules: [{ x: 2011.5, label: "VIIRS joins" }],
    note: y => (y < 2012 ? "MODIS only" : "VIIRS from 2012"),
  });
  dataTable($("#table-naive"), ["Year", "Joined as-is", "Source"],
    years.map(y => [String(y), num(byYear.get(y).naive_stitched), y < 2012 ? "MODIS Aqua" : "VIIRS S-NPP"]));
}

function drawFixed() {
  const rows = state.annual[state.region];
  const years = completeYears(rows);
  const byYear = new Map(rows.map(r => [r.year, r]));
  const drift = state.summary.aqua_drift.drift_years;
  const outage = state.summary.aqua_outage_months.map(m => +m.slice(0, 4));
  const shades = drift.length ? [{ from: Math.min(...drift), to: Math.max(...drift), label: "MODIS drifting" }] : [];
  const regionName = state.region === "hills" ? "the hill districts" : "all of Bangladesh";
  lineChart($("#chart-fixed"), {
    label: `Line chart: corrected burning activity per year for ${regionName}, with a 95% interval, and MODIS alone as a reference. There is no jump in 2012 and the long-term trend is down.`,
    years,
    series: [
      { key: "harmonized", label: "Corrected", color: "--series-1", endLabel: "Corrected",
        values: new Map(years.map(y => [y, byYear.get(y).harmonized])),
        band: new Map(years.filter(y => y >= 2012).map(y => [y, [byYear.get(y).harmonized_lo, byYear.get(y).harmonized_hi]])) },
      { key: "modis", label: "MODIS alone", color: "--series-3", endLabel: "MODIS alone",
        values: new Map(years.map(y => [y, byYear.get(y).modis])) },
    ],
    shades,
    marks: outage.filter(y => years.includes(y)).map(y => ({ series: "modis", x: y, label: "MODIS outage" })),
    note: y => (drift.includes(y) ? "MODIS drifted: reference unreliable" : outage.includes(y) ? "MODIS outage in April" : y < 2012 ? "MODIS era" : ""),
  });
  dataTable($("#table-fixed"), ["Year", "Corrected", "95% low", "95% high", "MODIS alone", "Joined as-is", "MODIS status"],
    years.map(y => {
      const r = byYear.get(y);
      return [String(y), num(r.harmonized), y >= 2012 ? num(r.harmonized_lo) : "–", y >= 2012 ? num(r.harmonized_hi) : "–",
        num(r.modis), num(r.naive_stitched), r.modis_drifted ? "orbit drift" : r.modis_gap ? "outage month" : "ok"];
    }));
}

function drawCalendar() {
  const rows = state.monthly;
  const byKey = new Map(rows.map(r => [`${r.year}-${r.month}`, r]));
  const years = [...new Set(rows.map(r => r.year))].sort((a, b) => a - b);
  const BINS = [1, 10, 50, 150, 400, 800];
  const LABELS = ["under 1", "1–10", "10–50", "50–150", "150–400", "400–800", "800+"];
  const cls = v => BINS.filter(b => v >= b).length;
  const legend = $("#cal-legend");
  legend.replaceChildren();
  LABELS.forEach((l, i) => {
    const s = h("span", {}, legend);
    const sw = h("span", { class: "swatch" }, s); sw.style.background = `var(--seq-${i})`;
    s.append(l);
  });
  const nd = h("span", {}, legend);
  const sw = h("span", { class: "swatch" }, nd); sw.style.cssText = "background:transparent;border:1px dashed var(--baseline)";
  nd.append("no data yet");

  responsive($("#calendar"), width => {
    const root = $("#calendar");
    root.replaceChildren();
    const lw = 40, top = 20, gap = 2;
    const cw = (width - lw) / 12, ch = width < 520 ? 14 : 16;
    const height = top + years.length * (ch + gap);
    const svg = sv("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img",
      "aria-label": "Heatmap of corrected burning activity by month (columns) and year (rows), 2003 to 2026. Activity is concentrated in March and April every year. The table below lists every value." }, root);
    const g = sv("g", { class: "axis" }, svg);
    MONTHS.forEach((mo, i) => sv("text", { x: lw + i * cw + cw / 2, y: 12, "text-anchor": "middle" }, g, width < 520 ? mo[0] : mo));
    years.forEach((y, j) => {
      const yy = top + j * (ch + gap);
      if (j % (width < 520 ? 2 : 1) === 0 || y === years[years.length - 1]) sv("text", { x: lw - 6, y: yy + ch - 3, "text-anchor": "end" }, g, String(y));
      for (let mo = 1; mo <= 12; mo++) {
        const r = byKey.get(`${y}-${mo}`);
        const rect = sv("rect", { x: lw + (mo - 1) * cw + gap / 2, y: yy, width: cw - gap, height: ch, rx: 2, class: "cal-cell" }, svg);
        if (!r) {
          rect.style.cssText = "fill:transparent;stroke:var(--baseline);stroke-dasharray:2 2";
          continue;
        }
        rect.style.fill = `var(--seq-${cls(r.harmonized)})`;
        rect.addEventListener("pointermove", e => {
          const rows_ = [{ color: "--series-1", value: num(r.harmonized), label: "corrected" }];
          if (y >= 2012) rows_.push({ value: `${num(r.harmonized_lo)}–${num(r.harmonized_hi)}`, label: "95% calibration uncertainty" });
          rows_.push({ color: "--series-3", value: num(r.modis), label: `MODIS alone${r.modis_gap ? " (outage)" : r.modis_drifted ? " (drifting)" : ""}` });
          tipShow(`${MONTHS[mo - 1]} ${y}`, rows_, e.pageX, e.pageY);
        });
        rect.addEventListener("pointerleave", tipHide);
      }
    });
  });
  const hdr = ["Year", ...MONTHS];
  dataTable($("#table-cal"), hdr, years.map(y => [String(y), ...MONTHS.map((_, i) => {
    const r = byKey.get(`${y}-${i + 1}`);
    return r ? num(r.harmonized) : "–";
  })]));
}

function fillSummary(s) {
  state.summary = s;
  $("#meta").textContent = `Fire data: NASA FIRMS (MODIS Aqua, VIIRS S-NPP), 2003 to ${new Date(s.data_end).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}.`;
  $("#jump-naive").textContent = `${s.jump.naive_stitched.toFixed(1)}×`;
  const ch = s.chronological_holdout;
  const hb = ch.ours.by_region_season["hills/burn"];
  $("#t-err").textContent = `${ch.ours.annual_median_abs_err_pct}%`;
  $("#t-err-note").textContent = `Fit on ${ch.train[0]}–${ch.train[ch.train.length - 1]}. Hills in the burn season: ${hb.median_abs_err_pct}%. A median, not an error bound. Naive join: ${num(ch.naive.annual_median_abs_err_pct)}%.`;
  $("#t-jump").textContent = `${s.jump.harmonized.toFixed(2)}×`;
  $("#t-jump-note").textContent = `95% calibration-factor interval ${s.jump.harmonized_95ci[0]}–${s.jump.harmonized_95ci[1]}×; naive ${s.jump.naive_stitched}×. MODIS alone ${s.jump.modis_pre_drift_reference}× (on its own years, where the match is built in).`;
  $("#t-worse").textContent = `${num(s.cells_that_look_worse_after_2012.naive_stitch)} → ${num(s.cells_that_look_worse_after_2012.harmonized)}`;
  const tr = s.trend_hills_burn_season;
  $("#t-trend").textContent = tr.kendall_tau < 0 ? "Going down" : "Going up";
  $("#t-trend-note").textContent = `Detected fire activity, harmonized, ${tr.years[0]}–${tr.years[1]}: Kendall τ ${tr.kendall_tau}, p = ${tr.p_value}. Holds for all calibration re-runs; missed fires are not modelled.`;

  const w = s.watchlist.observed;
  $("#t-first").textContent = num(w.counts["inspect first"] || 0);
  const pat = w.inspect_first_by_long_term_pattern || {};
  $("#t-first-note").textContent = `2026 season · ${num(w.people_living_in_inspect_first_cells)} people live in these squares · long-term pattern: ${Object.entries(pat).map(([k, v]) => `${v} ${k}`).join(", ")}`;
  if (s.s2_check && s.s2_check.groups.priority) {
    const g = s.s2_check.groups;
    $("#t-s2").textContent = `${g.priority.scar_visible} of ${g.priority.with_clear_imagery}`;
    $("#t-s2-note").textContent = `pass the test; random fire places ${g.fire.scar_visible} of ${g.fire.with_clear_imagery}; hills with no detected fire ${g.control.scar_visible} of ${g.control.with_clear_imagery}. Top vs random fire places: not significant (p = ${s.s2_check.mann_whitney_scar_share_priority_gt_fire_p}). Spectral evidence consistent with burning, not a visual check.`;
  } else {
    $("#t-s2").textContent = "Pending";
    $("#t-s2-note").textContent = "The Sentinel-2 check has not been run yet.";
  }
  const bt = s.watchlist.forecast_inspection_backtest;
  const hits = bt.mean_hits_in_top_50;
  $("#t-fc").textContent = `${hits.inspection_ranking.steep} of 50`;
  $("#t-fc-note").textContent = `Average per season, list rebuilt from earlier seasons only. Burn forecast alone: ${hits.burn_forecast_only.steep}; chance: ${bt.mean_expected_by_chance_in_top_50.steep}. Partly by construction. Burning at all (heavy): ${hits.inspection_ranking.heavy} of 50. Tests later burning, not landslides.`;

  const ev = $("#evidence");
  ev.replaceChildren();
  const add = (strong, rest) => { const li = h("li", {}, ev); h("strong", {}, li, strong); li.append(` ${rest}`); };
  add(`${ch.ours.annual_median_abs_err_pct}% median yearly error, ${ch.ours.annual_total_bias_pct > 0 ? "+" : ""}${ch.ours.annual_total_bias_pct}% overall bias`,
    `on ${ch.test.join(", ")} (fit on ${ch.train[0]}–${ch.train[ch.train.length - 1]} only), a chronological holdout with documented outage days removed. A monthly climatology without satellite data gets ${s.baselines.climatology}%; one ratio on the same cell-days ${s.baselines.global_celldays}%; region-only ${s.baselines.region_only}%.`);
  add("Aqua's orbit drift measured from the data itself:", `its afternoon pass moved from about 13:15 to ${formatHour(s.aqua_drift.overpass_h[String(Math.max(...s.aqua_drift.drift_years))].Aqua)} by ${Math.max(...s.aqua_drift.drift_years)}; drifted years (${s.aqua_drift.drift_years.join(", ")}) are left out of calibration.`);
  add("Documented Aqua outage", `(safe mode, ${s.aqua_outages.map(o => `${o.start} to ${o.end}`).join("; ")}) removed from both sensors; a fire-count check against Terra finds no other gap. This exclusion was added after a first look at the holdout.`);
  add(`Place level: ${pct(s.cell_calibration_holdout.overall_share_modis, 1)} vs ${pct(s.cell_calibration_holdout.overall_share_harmonized, 1)}`,
    `of hill squares active per season (MODIS vs calibrated VIIRS) on unseen seasons ${s.cell_calibration_holdout.years.join(", ")}; per-square agreement is moderate (rank correlation ${s.cell_calibration_holdout.cells_active_seasons_spearman}).`);
  add("Forecast comparison, rolling origin:", `every model and calibration refitted each season on earlier seasons only. "How often it burned before" performed best in this comparison (${Math.round(chosen_p50(s) * 50)} of 50 vs ${Math.round(s.forecast.comparison.gbm.precision_at_50 * 50)} for the ML model).`);
  const rob = w.robustness;
  add("Stability:", `the top 50 overlaps the default ranking by ${pct(rob.jaccard_top_vs_default.median)} (median) across ${rob.variants} settings of our grid; ${rob.cells_robust_anywhere} places stay in the top 50 in at least 80% of them. That is stability within our grid, not a probability of being right.`);
  if (s.s2_check) {
    add("Sentinel-2:", `same districts and the same before/after dates for all ${3 * s.s2_check.sample.per_group} places; dNBR ≥ 0.27 on at least 5% of a square. Top vs no-detection hills p = ${s.s2_check.mann_whitney_scar_share_priority_gt_control_p}; top vs random fire places p = ${s.s2_check.mann_whitney_scar_share_priority_gt_fire_p}. A blind visual check is still to be done.`);
  }
}
function chosen_p50(s) { return s.forecast.comparison[s.forecast.chosen].precision_at_50; }
function formatHour(hh) {
  const hr = Math.floor(hh), mn = Math.round((hh - hr) * 60);
  return `${hr}:${String(mn).padStart(2, "0")}`;
}

let map, cells, selRect, tiles;
function isDark() {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}
function initMap() {
  map = L.map("map", { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false }).setView([22.25, 92.15], 8.5);
  map.on("focus", () => map.scrollWheelZoom.enable());
  map.on("blur", () => map.scrollWheelZoom.disable());
  setTiles();
  cells = L.layerGroup().addTo(map);
}
function setTiles() {
  // Standard OSM tiles (CARTO's free basemaps now need an API key); the gray/dark look is a CSS filter.
  if (!tiles) {
    tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 15, className: "basemap",
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
  }
}
function drawCells() {
  const data = state.mapData;
  cells.clearLayers();
  const d = data.cell_deg / 2;
  const i = Object.fromEntries(data.columns.map((c, k) => [c, k]));
  const order = { monitor: 0, inspect: 1, "inspect first": 2 };
  const rows = [...data.rows].sort((a, b) => order[a[i.priority]] - order[b[i.priority]]);
  const surface = cssVar("--surface-1");
  for (const r of rows) {
    const p = PRIO[r[i.priority]];
    const lat = r[i.lat], lon = r[i.lon];
    const rect = L.rectangle([[lat - d, lon - d], [lat + d, lon + d]], {
      stroke: true, weight: 0.6, color: surface, fillColor: cssVar(p.color), fillOpacity: p.fill,
    });
    rect.bindTooltip(() => {
      const e = document.createElement("div");
      e.textContent = `${p.label} · ${r[i.nearest_place] || r[i.upazila]} (${r[i.upazila]}) · rank ${r[i.rank]}`;
      return e;
    }, { sticky: true, direction: "top" });
    rect.on("click", () => select(lat, lon));
    cells.addLayer(rect);
  }
  const counts = rows.reduce((a, r) => (a[r[i.priority]] = (a[r[i.priority]] || 0) + 1, a), {});
  $("#map-count").textContent = `${num(counts["inspect first"] || 0)} inspect first · ${num(counts.inspect || 0)} inspect · ${num(counts.monitor || 0)} monitor`;
  if (state.sel) highlight(state.sel.lat, state.sel.lon);
}
function highlight(lat, lon) {
  const d = state.mapData.cell_deg / 2;
  if (selRect) map.removeLayer(selRect);
  selRect = L.rectangle([[lat - d, lon - d], [lat + d, lon + d]], { weight: 3, color: cssVar("--ink-1"), fill: false, interactive: false }).addTo(map);
}

function drawPlaces(rows) {
  const root = $("#places");
  root.replaceChildren();
  $("#places-title").textContent = state.kind === "observed" ? "Top places, 2026 season (seen)" : "Top places, 2027 season (forecast)";
  const t = h("table", { class: "data" }, root);
  const head = h("tr", {}, h("thead", {}, t));
  const cols = ["#", "Priority", "Near", "Upazila", "District", "Score", "Stays in top 50", "Expected active seasons (of 24)"];
  if (state.kind === "observed") cols.push("Sentinel-2 check");
  cols.forEach((c, k) => h("th", { scope: "col", class: k >= 1 && k <= 4 ? "left" : null }, head, c));
  const tb = h("tbody", {}, t);
  for (const r of rows) {
    const p = PRIO[r.priority];
    const tr = h("tr", { tabindex: 0, "data-lat": r.lat, "data-lon": r.lon, "aria-selected": "false" }, tb);
    h("td", {}, tr, String(r.rank));
    const pc = h("td", { class: "left" }, tr);
    h("i", { class: `prio-icon ${p.cls}`, "aria-hidden": "true" }, pc, p.icon);
    pc.append(` ${p.label}`);
    h("td", { class: "left" }, tr, r.nearest_place || "–");
    h("td", { class: "left" }, tr, r.upazila || "–");
    h("td", { class: "left" }, tr, r.district || "–");
    h("td", {}, tr, num(r.score, 2));
    h("td", {}, tr, pct(r.robustness));
    h("td", {}, tr, num(r.active_seasons, 1));
    if (state.kind === "observed") h("td", {}, tr, r.s2_verdict || "not sampled");
    const go = () => select(r.lat, r.lon, { pan: true });
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  }
}

function markRow(lat, lon) {
  for (const tr of $$("#places tbody tr")) {
    tr.setAttribute("aria-selected", String(Math.abs(+tr.dataset.lat - lat) < 1e-6 && Math.abs(+tr.dataset.lon - lon) < 1e-6));
  }
}

async function select(lat, lon, { pan = false } = {}) {
  state.sel = { lat, lon };
  highlight(lat, lon);
  if (pan) map.panTo([lat, lon], { animate: !matchMedia("(prefers-reduced-motion: reduce)").matches });
  markRow(lat, lon);
  const insp = $("#inspector");
  insp.setAttribute("aria-busy", "true");
  await guarded(insp, async () => renderInspector(await api(`/api/cell?lat=${lat}&lon=${lon}`)));
  insp.removeAttribute("aria-busy");
}

function historyChart(root, hist, summary) {
  responsive(root, width => {
    root.replaceChildren();
    const height = 120, m = { t: 8, r: 4, b: 20, l: 4 };
    const iw = width - m.l - m.r, ih = height - m.t - m.b;
    const n = hist.length, band = iw / n, bw = Math.min(24, band - 2);
    const svg = sv("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img",
      "aria-label": `Bar chart of this place's burn history, ${hist[0].year} to ${hist[n - 1].year}: active in about ${num(summary.active_seasons, 1)} of ${summary.seasons} seasons.` }, root);
    sv("line", { class: "baseline", x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih }, svg);
    const split = hist.findIndex(r => r.year >= 2012);
    if (split > 0) {
      const xs = m.l + split * band;
      sv("line", { class: "gridline", x1: xs, x2: xs, y1: m.t, y2: m.t + ih }, svg);
      const g = sv("g", { class: "axis" }, svg);
      sv("text", { x: xs - 4, y: height - 5, "text-anchor": "end" }, g, "◂ MODIS: seen or not");
      sv("text", { x: xs + 4, y: height - 5 }, g, "VIIRS: chance MODIS would see it ▸");
    }
    hist.forEach((r, k) => {
      const v = Math.max(0, Math.min(1, r.active));
      const bh = Math.max(v * ih, v > 0 ? 2 : 0);
      const x = m.l + k * band + (band - bw) / 2;
      const y = m.t + ih - bh;
      const rad = Math.min(3, bw / 2, bh);
      const d = bh > 0 ? `M${x},${m.t + ih}V${y + rad}Q${x},${y} ${x + rad},${y}H${x + bw - rad}Q${x + bw},${y} ${x + bw},${y + rad}V${m.t + ih}Z` : "";
      if (d) {
        const p = sv("path", { d }, svg);
        p.style.fill = "var(--series-1)";
        // A probability is a different kind of bar from a yes/no: lighter, so a lower bar isn't read as less fire.
        if (r.year >= 2012) p.style.fillOpacity = "0.55";
      }
      const hit = sv("rect", { x: m.l + k * band, y: m.t, width: band, height: ih, fill: "transparent" }, svg);
      hit.addEventListener("pointermove", e => {
        const rows = r.year < 2012
          ? [{ color: "--series-1", value: r.modis >= 1 ? "yes" : "no", label: `MODIS saw fire (${num(r.modis)} detections)` }]
          : [{ color: "--series-1", value: pct(r.active), label: "chance MODIS would have seen fire" },
            { value: num(r.viirs_celldays), label: "VIIRS fire-days (~1 km)" },
            { value: r.active_naive ? "yes" : "no", label: "naive join says burned" }];
        tipShow(`${r.year} season (Feb–May)`, rows, e.pageX, e.pageY);
      });
      hit.addEventListener("pointerleave", tipHide);
    });
  });
}

function renderInspector(c) {
  const insp = $("#inspector");
  insp.replaceChildren();
  const w = c.watchlist.find(x => x.kind === state.kind) || c.watchlist[0];
  const p = PRIO[w.priority];
  const wrap = h("div", { class: "fade-in" }, insp);
  const badge = h("span", { class: "badge" }, wrap);
  h("i", { class: `prio-icon ${p.cls}`, "aria-hidden": "true" }, badge, p.icon);
  badge.append(`${p.label} · rank ${w.rank}`);
  h("h3", { class: "place-title", id: "insp-title" }, wrap, w.nearest_place ? `Near ${w.nearest_place}` : `${w.upazila}`);
  h("p", { class: "place-sub" }, wrap,
    `${w.upazila}, ${w.district} · ~2 km square at ${w.lat.toFixed(2)}° N, ${w.lon.toFixed(2)}° E${w.nearest_place ? ` · named place ${num(w.nearest_place_km, 1)} km away` : ""}`);
  const act = h("div", { class: "action" }, wrap);
  h("strong", {}, act, "Suggested next step");
  act.append(w.action);

  const facts = h("dl", { class: "facts" }, wrap);
  const fact = (label, value, small) => {
    const d = h("div", {}, facts);
    h("dt", {}, d, label);
    const dd = h("dd", {}, d, value);
    if (small) { dd.append(" "); h("small", {}, dd, small); }
  };
  if (state.kind === "observed") fact(`Fire days, Feb–May ${w.season}`, num(w.fire_days), `${num(w.fire_points)} detections (score counts up to 3)`);
  else fact("Heavy-burn seasons before", pct(Math.min(1, w.forecast_score)), "of 2012–2026");
  fact("Slope where fires were", w.fire_slope == null ? "–" : `${num(w.fire_slope)}°`, w.fire_steep_share == null ? "" : `${pct(w.fire_steep_share)} steep`);
  fact("People within ~1.5 km", num(w.people_r1));
  fact("Stays in top 50", pct(w.robustness), "of 54 settings");

  h("h4", { class: "section-title" }, wrap, "24 seasons of burning here");
  const hs = c.history_summary;
  const chart = h("div", { class: "chart" }, wrap);
  historyChart(chart, c.history, hs);
  h("p", { class: "sub" }, wrap,
    `Expected MODIS-equivalent active seasons: ${num(hs.active_seasons, 1)} of ${hs.seasons} (${hs.pattern}). Joining the satellites naively would say ${hs.active_seasons_naive}. The lighter bars from 2012 are chances, not yes/no, so compare totals rather than bar heights.`);

  if (state.kind === "observed") {
    h("h4", { class: "section-title" }, wrap, "Sentinel-2 burn-scar test (spectral)");
    if (w.s2_verdict && w.s2_verdict !== "no clear imagery") {
      const g = h("div", { class: "s2" }, wrap);
      const fig = (src, cap) => {
        const f = h("figure", {}, g);
        if (src) h("img", { src: `/s2/${src}`, alt: `Sentinel-2 true-colour image ${cap}`, loading: "lazy", width: 220, height: 220 }, f);
        else h("div", { class: "state", style: "min-height:120px" }, f, "no thumbnail");
        h("figcaption", {}, f, cap);
      };
      fig(w.s2_thumb_pre, `before: ${(w.s2_pre_dates || "").split(",")[0]}`);
      fig(w.s2_thumb_post, `after: ${(w.s2_post_dates || "").split(",")[0]}`);
      const v = h("p", { class: "verdict" }, wrap);
      h("strong", {}, v, w.s2_verdict === "scar visible" ? "Passes the burn-scar test. " : "Below the burn-scar threshold. ");
      v.append(`${pct(w.s2_scar_share, 1)} of the clear pixels changed like a moderate-or-worse burn (dNBR ≥ 0.27)` +
        (w.s2_scar_on_steep_share != null ? `; ${pct(w.s2_scar_on_steep_share)} of that on slopes of 15° or more.` : ".") +
        " Spectral evidence, not a visual check.");
    } else if (w.s2_verdict === "no clear imagery") {
      h("p", { class: "sub" }, wrap, "Sampled, but clouds hid this square in the before or after images.");
    } else {
      h("p", { class: "sub" }, wrap, "Not in the Sentinel-2 sample (60 places were checked: the top 20, 20 random fire places, 20 without detected fire).");
    }
  }
  h("p", { class: "caveat" }, wrap,
    "A fire detection shows fire activity, not a bare slope, and this is not a landslide forecast. Use it to decide where to look first, together with the people who farm here.");
}

async function loadKind() {
  const [m, top] = await Promise.all([api(`/api/map?kind=${state.kind}`), api(`/api/watchlist?kind=${state.kind}&limit=50`)]);
  state.mapData = m;
  drawCells();
  drawPlaces(top.rows);
  const first = state.sel || { lat: top.rows[0].lat, lon: top.rows[0].lon };
  await select(first.lat, first.lon);
}

const THEMES = ["auto", "light", "dark"];
function applyTheme(t) {
  if (t === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  const btn = $("#theme-btn");
  btn.textContent = `Theme: ${t}`;
  btn.setAttribute("aria-label", `Colour theme: ${t}. Activate to change.`);
  try { localStorage.setItem("bbr-theme", t); } catch { /* storage may be blocked */ }
  if (map) { setTiles(); if (state.mapData) drawCells(); }
}
function initTheme() {
  let t = "auto";
  try { t = localStorage.getItem("bbr-theme") || "auto"; } catch { /* ignore */ }
  const forced = new URLSearchParams(location.search).get("theme");  // ?theme=light|dark for screenshots and projectors
  if (THEMES.includes(forced)) t = forced;
  applyTheme(THEMES.includes(t) ? t : "auto");
  $("#theme-btn").addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || "auto";
    applyTheme(THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length]);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!document.documentElement.dataset.theme && map) { setTiles(); if (state.mapData) drawCells(); }
  });
}
function initStepbar() {
  const links = $$(".stepbar a");
  const io = new IntersectionObserver(entries => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      links.forEach(a => {
        const on = a.getAttribute("href") === `#${e.target.id}`;
        a.setAttribute("aria-current", String(on));
        if (on) a.parentElement.parentElement.scrollTo({ left: a.offsetLeft - 16, behavior: "smooth" });
      });
    }
  }, { rootMargin: "-45% 0px -50% 0px" });
  ["step-1", "step-2", "step-3", "method"].forEach(id => io.observe(document.getElementById(id)));
}
function initControls() {
  for (const b of $$("[data-region]")) {
    b.addEventListener("click", () => {
      state.region = b.dataset.region;
      $$("[data-region]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
      drawFixed();
    });
  }
  for (const b of $$("[data-kind]")) {
    b.addEventListener("click", () => {
      if (state.kind === b.dataset.kind) return;
      state.kind = b.dataset.kind;
      $$("[data-kind]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
      guarded($("#places"), loadKind);
    });
  }
}

// Share one in-flight request between loaders, but forget a failed one so "Try again" really refetches.
function shared(fetcher) {
  let p = null;
  return () => (p ??= fetcher().catch(err => { p = null; throw err; }));
}

async function boot() {
  initTheme();
  initStepbar();
  initControls();
  initMap();
  const summary = shared(() => api("/api/summary"));
  const annual = shared(() => Promise.all(["all", "hills", "plains"].map(r => api(`/api/calendar/annual?region=${r}`))));
  guarded($("#chart-naive"), async () => {
    const [s, a] = await Promise.all([summary(), annual()]);
    fillSummary(s);
    ["all", "hills", "plains"].forEach((r, k) => { state.annual[r] = a[k].rows; });
    drawNaive();
  });
  guarded($("#chart-fixed"), async () => {
    const [s, a] = await Promise.all([summary(), annual()]);
    state.summary = s;
    ["all", "hills", "plains"].forEach((r, k) => { state.annual[r] = a[k].rows; });
    drawFixed();
  });
  guarded($("#calendar"), async () => {
    state.monthly = (await api("/api/calendar?region=hills")).rows;
    drawCalendar();
  });
  guarded($("#places"), loadKind);
}
boot();
