// FALCON map, app.js: Fetching results (go), drawing tools and other interaction, and boot.
// Loaded in order by index.html; all files share one global scope.

let pending = 0;
const post = (u, b) => fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) })
  .then(r => r.json());

async function go(fit = false) {
  const msg = el("msg");
  currentFrom = byLabel[el("from").value]; currentTo = byLabel[el("to").value];
  drawMarks();
  if (!currentFrom || !currentTo) { msg.className = "err"; msg.textContent = "Pick both ports from the suggestions as you type."; return; }
  if (appMode === "cargo") return goCargo(fit);
  const body = { from: currentFrom, to: currentTo, k: +el("k").value, method: el("method").value, areas: plain(denialAreas()) };
  msg.className = ""; msg.textContent = "Routing...";
  const ticket = ++pending;
  // Routes also asks for the transport options (by sea, by air) for the same move, alongside the sea routes.
  const ways = post("/api/options", { from: currentFrom, to: currentTo, areas: plain(denialAreas()) }).catch(() => null);
  const data = await post("/api/routes", body);
  const o = await ways;
  if (ticket !== pending || appMode !== "routes") return;   // a newer request has been sent since
  opts = o && !o.error && !data.error ? o : null;
  if (!opts || !opts.air.available) routeView = "sea";
  renderOptions();
  if (data.error) { msg.className = "err"; msg.textContent = data.error; last = null; renderResults(); drawRoutes(); drawMarks(); return; }
  last = data;
  const n = data.routes.length;
  msg.textContent = data.blocked ? "" : `${n} route${n === 1 ? "" : "s"} in ${data.ms} ms` +
    (n < data.k ? ` (no more ${data.method === "distinct" ? "distinct corridors" : "routes"} found)` : "");
  renderResults(); drawRoutes(); drawMarks();
  if (fit) fitRoutes();
}

// Cargo: the lens over this turn's situation, and over the baseline's when this turn isn't the baseline.
async function goCargo(fit) {
  const msg = el("msg");
  const cargo = meta.classes.filter(c => el("cls-" + c.id).checked).map(c => ({ class: c.id, short_tons: tons[c.id] }));
  if (!cargo.length) { msg.className = "err"; msg.textContent = "Pick at least one class of supply."; cmp = null; renderCompare(); drawCargo(); return; }
  const split = list => ({ areas: plain(list.filter(a => a.kind !== "wez")), wez: plain(list.filter(a => a.kind === "wez")) });
  const body = { from: currentFrom, to: currentTo, cargo, lens, ...split(turns[cur].areas), lift: liftBody(turns[cur]),
                 modes: turns[cur].modes,
                 base: cur === base ? null : { ...split(turns[base].areas), lift: liftBody(turns[base]), modes: turns[base].modes } };
  msg.className = ""; msg.textContent = "";
  el("csum").textContent = "Working it out...";
  const ticket = ++pending;
  const data = await post("/api/compare", body).catch(e => ({ error: String(e) }));
  if (ticket !== pending || appMode !== "cargo") return;
  if (data.error) { msg.className = "err"; msg.textContent = data.error; cmp = null; renderCompare(); drawCargo(); drawMarks(); return; }
  cmp = data;
  if (picked && !(cmp.rows.some(r => r.class === picked.cls) && cmp.columns.some(c => c.id === picked.col))) picked = null;
  el("right").title = `Worked out in ${data.ms} ms`;
  renderCompare(); renderLift(); drawCargo(); drawMarks();
  if (fit) fitRoutes();
}

// ---------------------------------------------------------- interaction
function kindHint() {
  if (drawKind === "denial") return "Draw an area of denial: every sea lane through it is closed, to every carrier.";
  return appMode === "cargo" ? "Draw a weapons engagement zone (WEZ): commercial carriers stay out of it; the military may enter."
                             : "A WEZ only matters in Cargo mode. Routes here go around areas of denial only.";
}

function setMode(m) {
  mode = m; draft = null; drawDraft();
  svg.classList.toggle("drawing", !!m);
  el("drawCircle").classList.toggle("armed", m === "circle");
  el("drawPoly").classList.toggle("armed", m === "polygon");
  el("drawHint").className = "hint" + (m ? " draw" : "");
  el("drawHint").textContent = m ? DRAW_HINTS[m] : kindHint();
}

function setKind(k) {
  drawKind = k;
  document.body.classList.toggle("wez-kind", k === "wez");
  el("kindDenial").classList.toggle("on", k === "denial");
  el("kindWez").classList.toggle("on", k === "wez");
  if (!mode) el("drawHint").textContent = kindHint();
  drawDraft();
}

function setAppMode(m) {
  appMode = m;
  document.body.classList.toggle("cargo", m === "cargo");
  el("modeRoutes").classList.toggle("on", m === "routes");
  el("modeCargo").classList.toggle("on", m === "cargo");
  last = null; opts = null; cmp = null; picked = null; routeView = "sea";
  if (m !== "cargo") document.body.classList.remove("lift-open");
  setMode(null); renderResults(); renderOptions(); renderCompare(); renderTurns(); drawRoutes(); drawMarks(); setView(view);
  go(true);
}

function loadScenario(id) {
  const sc = meta.scenarios.find(x => x.id === id);
  el("scenarioHint").textContent = sc ? sc.purpose : "";
  if (!sc) return;
  el("from").value = labelOf[sc.from]; el("to").value = labelOf[sc.to];
  // A scenario's areas become What-if 1, against a Start with none: the change they make is the point.
  const own = [...sc.areas.map(a => ({ kind: "denial", ...a })), ...sc.wez.map(a => ({ kind: "wez", ...a }))];
  turns = own.length ? [{ areas: [], lift: {}, modes: {} }, { areas: own, lift: {}, modes: {} }] : [{ areas: [], lift: {}, modes: {} }];
  base = 0; cur = turns.length - 1; areas = turns[cur].areas; picked = null;
  tons = Object.fromEntries(meta.classes.map(c => [c.id, c.short_tons]));
  for (const c of meta.classes) el("cls-" + c.id).checked = false;
  for (const item of sc.cargo) { el("cls-" + item.class).checked = true; tons[item.class] = item.short_tons; }
  showTons();
  setMode(null); drawAreas(); go(true);
}

function showTons(fill = true) {
  if (fill) for (const c of meta.classes) el("tons-" + c.id).value = tons[c.id] ?? "";
  const total = meta.classes.filter(c => el("cls-" + c.id).checked).reduce((n, c) => n + (+tons[c.id] || 0), 0);
  el("tonsTotal").textContent = `${fmt(total)} short tons`;
}

function addArea(a) { areas.push({ kind: drawKind, ...a }); setMode(null); drawAreas(); go(); }

function finishPolygon() {
  if (draft && draft.points.length >= 3) addArea({ type: "polygon", points: draft.points });
  else setMode(null);
}

let pan = null;
svg.addEventListener("pointerdown", e => {
  if (e.button !== 0) return;
  if (mode === "circle") {
    draft = { type: "circle", center: toLonLat(e), radius_nm: 0 };
    svg.setPointerCapture(e.pointerId);
  } else if (!mode) {
    pan = { x: e.clientX, y: e.clientY, view: { ...view } };
    svg.classList.add("panning"); svg.setPointerCapture(e.pointerId);
  }
});
svg.addEventListener("pointermove", e => {
  if (mode === "circle" && draft) { draft.radius_nm = nmBetween(draft.center, toLonLat(e)); drawDraft(); return; }
  if (mode === "polygon" && draft) { draft.hover = toLonLat(e); drawDraft(); return; }
  if (pan) {
    const r = svg.getBoundingClientRect();
    view = { ...pan.view, x: pan.view.x - (e.clientX - pan.x) / r.width * pan.view.w,
                          y: pan.view.y - (e.clientY - pan.y) / r.height * pan.view.h };
    setView(view);
  } else if (!mode) showHover(nodeAt(e), e);
});
svg.addEventListener("pointerleave", () => clearHover());
svg.addEventListener("pointerup", e => {
  if (mode === "circle" && draft) {
    if (draft.radius_nm >= 1) addArea({ type: "circle", center: draft.center, radius_nm: Math.round(draft.radius_nm) });
    else { draft = null; drawDraft(); }
    return;
  }
  pan = null; svg.classList.remove("panning");
});
svg.addEventListener("click", e => {
  if (mode !== "polygon") return;
  const p = toLonLat(e);
  draft = draft || { type: "polygon", points: [] };
  if (draft.points.length >= 3) {
    // Clicking near the first corner closes the shape.
    const [fx, fy] = projection(draft.points[0]), [mx, my] = toMap(e);
    if (Math.hypot(fx - mx, fy - my) < 9 * scale()) return finishPolygon();
  }
  draft.points.push(p); drawDraft();
});
svg.addEventListener("dblclick", e => {
  if (mode !== "polygon" || !draft) return;
  e.preventDefault();
  draft.points.pop();                 // the dblclick's second click added a duplicate corner
  finishPolygon();
});
svg.addEventListener("wheel", e => {
  e.preventDefault();
  const [mx, my] = toMap(e), f = e.deltaY < 0 ? 0.8 : 1.25;
  const w = Math.min(W * 1.2, Math.max(W / 60, view.w * f)), k = w / view.w;
  setView({ x: mx - (mx - view.x) * k, y: my - (my - view.y) * k, w, h: view.h * k });
}, { passive: false });
window.addEventListener("keydown", e => {
  if (!mode || /^(INPUT|SELECT)$/.test(document.activeElement.tagName)) return;
  if (e.key === "Escape") setMode(null);
  else if (e.key === "Enter" && mode === "polygon") finishPolygon();
  else if (e.key === "Backspace" && mode === "polygon" && draft) { draft.points.pop(); drawDraft(); }
});

async function boot() {
  const topo = await (await fetch("/land-50m.json")).json();
  el("land").appendChild(svgEl("path", { d: geoPath(topojson.feature(topo, topo.objects.land)), fill: "var(--land)",
    stroke: "var(--coast)", "stroke-width": 0.7, "vector-effect": "non-scaling-stroke" }));

  const ports = await (await fetch("/api/ports")).json();
  airfields = await (await fetch("/api/airfields")).json();
  for (const n of [...ports, ...airfields]) [n.x, n.y] = projection([n.lon, n.lat]);
  portList = ports;
  mapNodes = { port: ports.filter(p => !OFF_MAP.port.has(p.country) && !OFF_MAP.sizes.has(p.size)), air: airfields.filter(a => !OFF_MAP.air.has(a.country)) };
  el("portCount").textContent = `Ports ${fmt(mapNodes.port.length)}`;
  el("airCount").textContent = `Airports ${fmt(mapNodes.air.length)}`;
  el("showPorts").onclick = () => { layers.port = !layers.port; drawNodes(); clearHover(); };
  el("showAirfields").onclick = () => { layers.air = !layers.air; drawNodes(); clearHover(); };
  drawNodes();
  // A port's label is "Name, Country"; ports that share one are told apart by their region,
  // or by their id if they share that too.
  const count = {}, base = p => p.country ? `${p.name}, ${p.country}` : p.name;
  for (const p of ports) count[base(p)] = (count[base(p)] || 0) + 1;
  const regional = {}, region = p => p.region.replace(/\s*--\s*\d+\s*$/, "");     // "Kriti -- 43080" -> "Kriti"
  for (const p of ports) if (count[base(p)] > 1) { const k = `${base(p)} (${region(p)})`; regional[k] = (regional[k] || 0) + 1; }
  for (const p of ports) {
    let label = base(p);
    if (count[label] > 1) {
      p._tag = region(p) && regional[`${label} (${region(p)})`] === 1 ? region(p) : p.id;
      label = `${label} (${p._tag})`;
    }
    byLabel[label] = p.id; byId[p.id] = p;
    p._n = norm(p.name); p._c = norm(p.country);
    p._nw = p._n.split(/[^a-z0-9]+/).filter(Boolean);
    p._w = norm(base(p)).split(/[^a-z0-9]+/).filter(Boolean);
  }
  for (const l in byLabel) labelOf[byLabel[l]] = l;
  const labelNamed = name => Object.keys(byLabel).find(l => byId[byLabel[l]].name === name);
  el("from").value = labelNamed("Busan"); el("to").value = labelNamed("Subic Bay");

  meta = await (await fetch("/api/cargo/meta")).json();
  // What's moving: a row per class, ticked, with its short tons (editable). The rule line says how it may fly.
  let typing = null;
  for (const c of meta.classes) {
    tons[c.id] = c.short_tons;
    const row = document.createElement("div");
    row.className = "cl";
    row.title = `Class ${c.id}, ${c.name}. Moves as: ${c.moves_as}`;
    const rule = meta.flies[c.id] === "none" ? "never flies" : meta.flies[c.id] === "military" ? "military aircraft only" : "";
    row.innerHTML = `<input type="checkbox" id="cls-${c.id}" checked aria-label="Class ${c.id}, ${esc(c.name)}"><b>${c.id}</b>` +
      `<label for="cls-${c.id}" style="margin:0;text-transform:none;letter-spacing:0;color:var(--ink);font-size:12.5px">${esc(c.name)}` +
      (rule ? `<span class="rule">${rule}</span>` : "") +
      `<span class="moved-note" id="moved-${c.id}"></span></label>` +
      `<input type="number" id="tons-${c.id}" min="0" step="10" aria-label="Short tons of Class ${c.id}">` +
      `<select class="md" id="mode-${c.id}" aria-label="How Class ${c.id} moves in this what-if">` +
      `<option value="sea">Sea</option><option value="air"${meta.flies[c.id] === "none" ? " disabled" : ""}>Air</option></select>`;
    row.querySelector("input[type=checkbox]").onchange = () => { showTons(); go(); };
    row.querySelector("select.md").onchange = e => setMode2(c.id, e.target.value, null);
    row.querySelector("input[type=number]").oninput = e => {
      tons[c.id] = Math.max(0, +e.target.value || 0); showTons(false);
      clearTimeout(typing); typing = setTimeout(go, 400);
    };
    el("classes").appendChild(row);
  }
  showTons();
  for (const f of airfields) {
    const lab = `${f.name}${f.code ? ` (${f.code})` : ""}, ${f.country}`;
    fieldById[f.id] = f; fieldByLabel[lab] = f.id; fieldLabel[f.id] = lab;
  }
  el("portNames").innerHTML = Object.keys(byLabel).map(l => `<option value="${esc(l)}">`).join("");
  el("fieldNames").innerHTML = Object.keys(fieldByLabel).map(l => `<option value="${esc(l)}">`).join("");
  el("editLift").onclick = () => openLift(true);
  el("closeLift").onclick = () => openLift(false);
  el("resetLift").onclick = () => { turns[cur].lift = {}; liftChanged(); };
  renderLiftLine();
  el("due").oninput = () => { const v = Math.round(+el("due").value); if (v >= 1) { due = v; renderCompare(); } };
  for (const l of meta.lenses) el("lens").appendChild(new Option(l.label, l.id));
  el("lens").value = lens;
  el("lens").onchange = () => { lens = el("lens").value; picked = null; document.body.classList.toggle("plan-lens", lens === "plan"); go(true); };
  document.body.classList.toggle("plan-lens", lens === "plan");
  el("addTurn").onclick = () => {
    turns.push({ areas: areas.map(a => ({ ...a })), lift: JSON.parse(JSON.stringify(turns[cur].lift)), modes: { ...turns[cur].modes }, why: { ...(turns[cur].why || {}) } });
    cur = turns.length - 1; areas = turns[cur].areas; picked = null;
    setMode(null); drawAreas(); go();
  };
  el("setBase").onclick = () => { base = cur; renderTurns(); go(); };
  const panel = (side, btn, hide, show) => {
    const off = document.body.classList.toggle("no-" + side);
    el(btn).textContent = off ? show : hide; setView(view);
  };
  el("tglLeft").onclick = () => panel("left", "tglLeft", "‹ Scenario", "Scenario ›");
  el("tglRight").onclick = () => panel("right", "tglRight", "Results ›", "‹ Results");
  for (const sc of meta.scenarios) el("scenario").appendChild(new Option(`${sc.id} · ${sc.name}`, sc.id));
  el("scenario").onchange = () => loadScenario(el("scenario").value);
  el("modeRoutes").onclick = () => appMode !== "routes" && setAppMode("routes");
  el("modeCargo").onclick = () => appMode !== "cargo" && setAppMode("cargo");
  el("kindDenial").onclick = () => setKind("denial");
  el("kindWez").onclick = () => setKind("wez");

  const hint = () => { el("methodHint").textContent = HINTS[el("method").value]; };
  hint();
  el("method").onchange = () => { hint(); go(); };
  el("k").onchange = () => go();
  el("go").onclick = () => go(true);
  attachSearch(el("from")); attachSearch(el("to"));
  el("drawCircle").onclick = () => setMode(mode === "circle" ? null : "circle");
  el("drawPoly").onclick = () => setMode(mode === "polygon" ? null : "polygon");
  el("clearAreas").onclick = () => { areas.length = 0; setMode(null); drawAreas(); go(); };
  el("fit").onclick = fitRoutes;
  el("world").onclick = () => setView({ x: 0, y: 0, w: W, h: H });
  window.addEventListener("resize", () => setView(view));
  setView({ x: 0, y: 0, w: W, h: H });
  setKind("denial");
  renderTurns();
  go(true);
}
boot();
