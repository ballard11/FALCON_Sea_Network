// FALCON map, map.js: Drawing on the map: areas, routes, cargo legs, marks, and the port and airport layers.
// Loaded in order by index.html; all files share one global scope.

// --------------------------------------------------------------- drawing
const polyD = pts => "M" + pts.map(p => projection(p).join(",")).join("L") + "Z";
const circleD = a => geoPath(d3.geoCircle().center(a.center).radius(a.radius_nm / 60).precision(2)());

function drawAreas() {
  const g = el("areas"); g.innerHTML = "";
  for (const a of areas) {
    // Polygon sides are straight in longitude/latitude (as the server tests
    // them), which on this map means straight on screen: no geodesic curves.
    g.appendChild(svgEl("path", { d: a.type === "circle" ? circleD(a) : polyD(a.points),
      fill: KIND[a.kind], "fill-opacity": .14, stroke: KIND[a.kind], "stroke-width": 1.5,
      "stroke-dasharray": a.kind === "wez" ? "10 4" : "6 4", "vector-effect": "non-scaling-stroke" }));
  }
  const list = el("areaList"); list.innerHTML = "";
  areas.forEach((a, i) => {
    const li = document.createElement("li");
    if (a.kind === "wez") li.className = "wez";
    li.innerHTML = `<span>${a.kind === "wez" ? "WEZ" : "Denial"} · ${a.type === "circle" ? `circle, ${fmt(a.radius_nm)} nm radius` : `polygon, ${a.points.length} corners`}</span>`;
    const x = document.createElement("button");
    x.title = "Remove this area"; x.textContent = "×";
    x.onclick = () => { areas.splice(i, 1); drawAreas(); go(); };
    li.appendChild(x); list.appendChild(li);
  });
  renderTurns();
}

function drawDraft() {
  const g = el("draft"); g.innerHTML = "";
  if (!draft) return;
  const s = scale(), colour = KIND[drawKind];
  if (draft.type === "circle" && draft.radius_nm > 0) {
    g.appendChild(svgEl("path", { d: circleD(draft), fill: colour, "fill-opacity": .1,
      stroke: colour, "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" }));
    const [x, y] = projection(draft.center);
    const t = svgEl("text", { x: x + 8 * s, y: y - 8 * s, class: "lbl", "font-size": 12 * s, "stroke-width": 3 * s });
    t.textContent = `${fmt(draft.radius_nm)} nm`;
    g.appendChild(t);
  }
  if (draft.type === "polygon" && draft.points.length) {
    const pts = draft.hover ? [...draft.points, draft.hover] : draft.points;
    g.appendChild(svgEl("path", { d: "M" + pts.map(p => projection(p).join(",")).join("L"),
      fill: "none", stroke: colour, "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" }));
    draft.points.forEach((p, i) => {
      const [x, y] = projection(p);
      g.appendChild(svgEl("circle", { cx: x, cy: y, r: (i === 0 ? 5 : 3.5) * s,
        fill: i === 0 ? colour : "var(--bg)", stroke: colour, "stroke-width": 1.5,
        "vector-effect": "non-scaling-stroke" }));
    });
  }
}

function drawRoutes(highlight = null) {
  if (appMode === "cargo") return drawCargo(highlight);
  const g = el("routes"); g.innerHTML = "";
  if (routeView === "air" && opts && opts.air.available) {
    for (const leg of opts.air.legs) g.appendChild(legPath(leg));
    return;
  }
  if (!last || !last.routes) return;
  const showOriginal = denialAreas().length && last.original && (last.affected || !last.routes.length);
  if (showOriginal) {
    g.appendChild(svgEl("path", { d: geoPath({ type: "LineString", coordinates: last.original.line }),
      fill: "none", stroke: "var(--orig-route)", "stroke-width": 2.4, "stroke-dasharray": "7 5",
      "stroke-opacity": highlight === null ? .85 : .3, "vector-effect": "non-scaling-stroke" }));
  }
  for (const r of [...last.routes].reverse()) {      // best on top
    const on = highlight === null || highlight === r.rank;
    g.appendChild(svgEl("path", { d: geoPath({ type: "LineString", coordinates: r.line }), fill: "none",
      stroke: COLORS[r.rank - 1], "stroke-width": r.rank === 1 ? 4 : 2.6, "stroke-opacity": on ? .95 : .15,
      "stroke-linecap": "round", "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" }));
  }
}

// One leg of a move: sea solid, air dashed, road thin; coloured by who carries it.
const legPath = (leg, width = leg.mode === "road" ? 2 : 3.5, opacity = .95) =>
  svgEl("path", { d: geoPath({ type: "LineString", coordinates: leg.line }), fill: "none",
    stroke: CARRIER[leg.carrier], "stroke-width": width, "stroke-opacity": opacity,
    "stroke-dasharray": leg.mode === "air" ? "9 6" : "none",
    "stroke-linecap": "round", "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" });

// The cells the map is showing: the picked cell, else the hovered class's row, else none (everything shows).
function focusCells() {
  if (!cmp) return null;
  const row = c => cmp.rows.find(r => r.class === c);
  if (picked && row(picked.cls)) return [row(picked.cls).cells[picked.col]];
  if (hoverRow && row(hoverRow)) return Object.values(row(hoverRow).cells);
  return null;
}

// Cargo legs, commercial under military; the focused cells' legs stand out. When this turn isn't the
// baseline, the baseline's sea route shows underneath, dashed grey, so the change can be seen.
function drawCargo() {
  const g = el("routes"), b = el("base"); g.innerHTML = ""; b.innerHTML = "";
  if (!cmp) return;
  if (cmp.base) {
    const keys = new Set();
    for (const r of cmp.base.rows) for (const c of Object.values(r.cells)) if (c.available && c.mode === "sea") c.legs.forEach(k => keys.add(k));
    for (const k of keys) b.appendChild(svgEl("path", { d: geoPath({ type: "LineString", coordinates: cmp.base.legs[k].line }),
      fill: "none", stroke: "var(--orig-route)", "stroke-width": 2.4, "stroke-dasharray": "7 5", "stroke-opacity": .7,
      "vector-effect": "non-scaling-stroke" }));
  }
  const focus = focusCells(), mine = focus && new Set(focus.flatMap(c => c.legs));
  for (const carrier of ["commercial", "military"]) {
    for (const [k, leg] of Object.entries(cmp.legs)) {
      if (leg.carrier !== carrier) continue;
      const on = !mine || mine.has(k);
      g.appendChild(legPath(leg, leg.mode === "road" ? 2 : on && mine ? 4.5 : 3.5, on ? .95 : .1));
    }
  }
}

// ------------------------------------------------------------ map layers
// Every port and airport as a dot, sized by the dataset's own size rating: NGA World Port Index harbor
// size for ports, OurAirports' type for airports. One path per size of zero-length round-capped
// strokes, so each dot stays the same size on screen at any zoom without redrawing.
const NODE = { port: { colour: "var(--port)", source: "World Port Index",
                       px: { Large: 8, Medium: 6, Small: 4.5, "Very Small": 3, "": 3 } },
               air: { colour: "var(--airfield)", source: "OurAirports", px: { Large: 7.5, Medium: 5 } } };
let portList = [], airfields = [], layers = { port: true, air: true }, hovered = null;
// Left off the map layers (Ben, 25 Nov 2026): ports and airports in mainland China, and Small and Very Small
// harbors. They stay in the data, so port search and routing are unchanged. Hong Kong and Macau are separate
// entries and still show.
const OFF_MAP = { port: new Set(["China"]), air: new Set(["CN"]), sizes: new Set(["Small", "Very Small"]) };
let mapNodes = { port: [], air: [] };

function drawNodes() {
  const g = el("nodes"); g.innerHTML = "";
  for (const kind of ["port", "air"]) {
    if (!layers[kind]) continue;
    const list = mapNodes[kind];
    for (const [size, px] of Object.entries(NODE[kind].px)) {       // biggest first, so small dots sit on top
      const d = list.filter(n => n.size === size).map(n => `M${n.x.toFixed(2)} ${n.y.toFixed(2)}h0`).join("");
      if (d) g.appendChild(svgEl("path", { d, fill: "none", stroke: NODE[kind].colour, "stroke-width": px,
        "stroke-linecap": "round", "stroke-opacity": 0.8, "vector-effect": "non-scaling-stroke" }));
    }
  }
  el("showPorts").classList.toggle("on", layers.port);
  el("showAirfields").classList.toggle("on", layers.air);
}

function nodeAt(e) {               // the nearest visible port or airfield within 7 px of the pointer
  const [mx, my] = toMap(e);
  let best = null, bd = 7 * view.w / svg.getBoundingClientRect().width;
  for (const kind of ["air", "port"]) {
    if (!layers[kind]) continue;
    for (const n of mapNodes[kind]) {
      const dx = Math.abs(n.x - mx), dy = Math.abs(n.y - my);
      if (dx > bd || dy > bd) continue;
      const d = Math.hypot(dx, dy);
      if (d < bd) { bd = d; best = { kind, n }; }
    }
  }
  return best;
}

function clearHover() {
  hovered = null; el("hover").innerHTML = ""; el("tip").style.display = "none";
}

function showHover(hit, e) {          // what it is, and its size as the source dataset rates it
  if (!hit) return clearHover();
  const tip = el("tip"), box = tip.parentElement.getBoundingClientRect();
  if (!hovered || hovered.n !== hit.n) {
    hovered = hit;
    const n = hit.n, g = el("hover"), r = view.w / svg.getBoundingClientRect().width;
    const name = hit.kind === "air" && n.code ? `${n.name} (${n.code})` : n.name;
    const size = hit.kind === "port" ? (n.size ? `harbor size ${n.size}` : "harbor size not rated") : `rated ${n.size}`;
    tip.innerHTML = `<b>${esc(name)}</b>${n.country ? ", " + esc(n.country) : ""}` +
                    `<br><span class="dim">${hit.kind === "port" ? "Port" : "Airport"} · ${esc(size)} (${NODE[hit.kind].source})</span>`;
    g.innerHTML = "";
    g.appendChild(svgEl("circle", { cx: n.x, cy: n.y, r: 6 * r, fill: "none", stroke: "#fff",
      "stroke-width": 2, "vector-effect": "non-scaling-stroke" }));
    tip.style.display = "block";
  }
  let x = e.clientX - box.left + 14, y = e.clientY - box.top + 14;
  if (x + tip.offsetWidth > box.width - 8) x = e.clientX - box.left - tip.offsetWidth - 14;
  if (y + tip.offsetHeight > box.height - 8) y = e.clientY - box.top - tip.offsetHeight - 14;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
