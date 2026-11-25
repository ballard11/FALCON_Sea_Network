// FALCON map, routes.js: Routes mode: the transport options (by sea, by air) and the sea route list.
// Loaded in order by index.html; all files share one global scope.

// ----------------------------------------------------- transport options
// Planner screen, step 1: by sea and by air side by side for this move, in days port to port.
const OPT_TITLE = {
  sea: "Port to port: sailing at 18 knots, plus a day in each port where cargo is worked. Illustrative allowances, not planning factors. Click to show the sea routes.",
  air: "Port to port: the road to and from each airport, flying at cruise speed with any refuelling stops, and 4 hours at each airport. Illustrative allowances, not planning factors. Click to show it on the map.",
};

function renderOptions() {
  const box = el("options"); box.innerHTML = "";
  document.body.classList.toggle("air-view", routeView === "air" && appMode !== "cargo");
  if (!opts || appMode === "cargo") return;
  box.innerHTML = `<div class="head">Transport options</div>`;
  for (const kind of ["sea", "air"]) {
    const o = opts[kind], before = opts.before && opts.before[kind];
    const label = kind === "sea" ? "By sea" : o.mode === "air_sea" ? "By air, then sea"
                : o.mode === "sea_air" ? "By sea, then air" : "By air";
    const card = document.createElement("div");
    card.className = "opt" + (o.available ? (routeView === kind ? " on" : "") : " na");
    card.title = OPT_TITLE[kind];
    if (!o.available) {
      card.innerHTML = `<div class="k">${label}</div><div class="d">No route</div><div class="s">${esc(o.reason || "")}</div>`;
    } else {
      const moved = o.legs.filter(l => l.mode === kind), stops = moved.reduce((n, l) => n + (l.stops || []).length, 0);
      const how = o.transfer ? `changes ${o.mode === "air_sea" ? "to sea" : "to air"} at ${esc(o.transfer.name)}`
                : `${fmt(moved.reduce((n, l) => n + l.length_nm, 0))} nm` +
                  (kind === "air" ? (stops ? `, ${stops} refuelling stop${stops === 1 ? "" : "s"}` : ", nonstop") : "");
      let change = "";
      if (before && before.available) {
        const d = Math.round((o.days - before.days) * 10) / 10;
        change = d ? `<div class="chg">${d > 0 ? "+" : ""}${fmt(d, 1)} days from the areas</div>`
                   : `<div class="same">No change from the areas</div>`;
      }
      card.innerHTML = `<div class="k">${label}</div><div class="d">${fmt(o.days, 1)} days</div><div class="s">${how}</div>${change}`;
      card.onclick = () => { if (routeView !== kind) { routeView = kind; renderOptions(); drawRoutes(); fitRoutes(); } };
    }
    box.appendChild(card);
  }
}

function drawMarks() {
  const g = el("marks"); g.innerHTML = "";
  const s = scale();
  if (appMode === "cargo" && cmp) {
    // Where cargo changes hands (handover) or changes mode (transfer), for the cells on show.
    const spots = {};
    for (const c of focusCells() || cmp.rows.flatMap(r => Object.values(r.cells))) {
      for (const p of [c.handover, c.transfer]) if (p) spots[p.id] = p;
    }
    for (const p of Object.values(spots)) {
      const [x, y] = projection([p.lon, p.lat]), r = 7 * s;
      g.appendChild(svgEl("path", { d: `M${x} ${y - r}l${r} ${r}l${-r} ${r}l${-r} ${-r}Z`, fill: "#fff",
                                     stroke: "var(--bg)", "stroke-width": 2, "vector-effect": "non-scaling-stroke" }));
      const t = svgEl("text", { x: x + 10 * s, y: y + 4 * s, class: "lbl", "font-size": 12 * s, "stroke-width": 3 * s });
      t.textContent = p.name.replace(/ \([A-Z0-9]+\)$/, "");
      g.appendChild(t);
    }
  }
  if (appMode === "cargo" && meta) {
    // Lift that starts away from the origin: a square at its port or airport, with its count and ready day.
    const at = {};
    for (const [k, x] of liftTypes()) for (const g of liftGroups(turns[cur], k)) {
      if (!g.at || !g.count) continue;
      const p = k in meta.lift.ships ? byId[g.at] : fieldById[g.at];
      if (!p) continue;
      (at[g.at] = at[g.at] || { p, text: [] }).text.push(`${g.count} ${shortName(x.name)}${g.ready ? ` D+${fmt(g.ready, g.ready % 1 ? 1 : 0)}` : ""}`);
    }
    for (const { p, text } of Object.values(at)) {
      const [x, y] = projection([p.lon, p.lat]), r = 5 * s;
      g.appendChild(svgEl("rect", { x: x - r, y: y - r, width: 2 * r, height: 2 * r, fill: "var(--military)",
                                    stroke: "var(--bg)", "stroke-width": 2, "vector-effect": "non-scaling-stroke" }));
      const t = svgEl("text", { x: x + 9 * s, y: y + 4 * s, class: "lbl", "font-size": 12 * s, "stroke-width": 3 * s });
      t.textContent = `${p.name}: ${text.join(", ")}`;
      g.appendChild(t);
    }
  }
  for (const [id, colour, role] of [[currentFrom, "var(--origin)", "Origin"], [currentTo, "var(--dest)", "Destination"]]) {
    const p = byId[id];
    if (!p) continue;
    const [x, y] = projection([p.lon, p.lat]);
    g.appendChild(svgEl("circle", { cx: x, cy: y, r: 6 * s, fill: colour, stroke: "var(--bg)",
                                     "stroke-width": 2, "vector-effect": "non-scaling-stroke" }));
    const t = svgEl("text", { x: x + 9 * s, y: y + 4 * s, class: "lbl", "font-size": 13 * s, "stroke-width": 3 * s });
    t.textContent = `${role}: ${p.name}`;
    g.appendChild(t);
  }
}

// -------------------------------------------------------------- results
function renderResults() {
  const ol = el("results"), sum = el("summary");
  ol.innerHTML = ""; sum.innerHTML = ""; sum.className = "";
  if (!last) return;
  if (appMode === "cargo") return;
  const areas = denialAreas();
  if (areas.length) {
    sum.className = "summary" + (last.blocked ? " blocked" : "");
    const orig = `<div class="orig"><span class="dash"></span>Original route ${fmt(last.original.length_nm)} nm` +
                 (last.original.passages.length ? ` via ${last.original.passages.join(", ")}` : "") + "</div>";
    if (last.blocked) sum.innerHTML = `<b>${last.blocked}</b>${orig}`;
    else if (!last.affected) sum.innerHTML = `The ${areas.length === 1 ? "area closes" : "areas close"} ${fmt(last.closed_links)} links, but not the original route, which stays the best.`;
    else sum.innerHTML = `${areas.length === 1 ? "The area closes" : "The areas close"} ${fmt(last.closed_links)} links, including the original route. ` +
      `Best route now <b>${fmt(last.routes[0].length_nm)} nm</b>: <b>+${fmt(last.extra_nm)} nm</b>, about ${fmt(last.extra_days_at_18kt, 1)} more days at 18 knots.${orig}`;
  }
  const best = last.routes.length ? last.routes[0].length_nm : 0;
  for (const r of last.routes) {
    const li = document.createElement("li");
    const delta = r.rank === 1 ? "best" : `+${fmt(r.length_nm - best, 1)} nm`;
    li.innerHTML = `<div class="top"><span class="sw" style="background:${COLORS[r.rank - 1]}"></span>
      #${r.rank} &nbsp;${fmt(r.length_nm)} nm<span class="delta">${delta}</span></div>
      <div class="via">${r.passages.length ? "via " + r.passages.join(", ") : "open ocean, no named strait"}</div>`;
    li.onmouseenter = () => { drawRoutes(r.rank); li.classList.add("on"); };
    li.onmouseleave = () => { drawRoutes(); li.classList.remove("on"); };
    ol.appendChild(li);
  }
}
