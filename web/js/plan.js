// FALCON map, plan.js: Cargo: the Plan view (the answer, the plan by class, capacity used, every change tested,
// the figures used) and each what-if's changes.
// Loaded in order by index.html; all files share one global scope.

// ------------------------------------------------------ cargo: the plan
// The Plan view, always in the same order so the eye learns where to look:
//   1. the answer: required, all cargo in, margin, and the one thing that limits it;
//   2. the plan by class, latest first (mode is an input: it's set under What's moving, on the left);
//   3. capacity used: each port's work and each pool of ships or aircraft, the limit marked;
//   4. every single change the server tested, soonest first, each one click to try;
//   5. the figures the answer rests on, each with its status and source.
// Everything comes from the server as data (comparisons.by_plan); this file only lays it out and words it.
// Numbers sit in tables with units in the headers; colour means late (red) or in time (green) and nothing else.
const MODE_LABEL = c => c.mode === "sea" ? "By sea" : c.mode === "air" ? (c.plan === "military" ? "By military air" : c.plan === "commercial" ? "By commercial air" : `By air, ${c.plan.split("_").join(" then ")}`)
                                             : c.mode === "air_sea" ? "By air, then sea" : "By sea, then air";
const STATUS_TEXT = { sourced: "sourced", unverified: "unverified", illustrative: "illustrative" };
const dPlus = x => x == null ? "no lift" : `D+${fmt(x, 1)}`;
const marginText = last => last == null ? "no lift" : `${signed(r1(due - last))} d`;
const gallons = st => Math.round(st * 2000 / meta.lift.fuel_lb_per_gal / 1000) * 1000;

// A class's mode in a what-if: its normal mode unless the what-if moves it. "why" says where a change came from
// (the list of changes tested), so the changes box can say so; null is a change made by hand.
const modeOf = (t, cid) => (t.modes || {})[cid] || meta.lift.normal_mode[cid];
function setMode2(cid, to, why) {
  const t = turns[cur];
  t.modes = { ...t.modes }; t.why = { ...(t.why || {}) };
  if (to === meta.lift.normal_mode[cid]) { delete t.modes[cid]; delete t.why["mode:" + cid]; }
  else { t.modes[cid] = to; if (why) t.why["mode:" + cid] = why; else delete t.why["mode:" + cid]; }
  picked = null; setMode(null); drawAreas(); go();
}

function renderModes() {
  if (!meta) return;
  const t = turns[cur];
  for (const c of meta.classes) {
    const m = modeOf(t, c.id), moved = m !== meta.lift.normal_mode[c.id];
    el("mode-" + c.id).value = m;
    el("mode-" + c.id).closest(".cl").classList.toggle("moved", moved);
    el("moved-" + c.id).textContent = moved ? `normally ${meta.lift.normal_mode[c.id]}` : "";
  }
}

// A change tried on the baseline goes into a new what-if, so the baseline stays as it was.
function forkIfBaseline() {
  if (cur !== base) return;
  const t = turns[cur];
  turns.push({ areas: areas.map(a => ({ ...a })), lift: JSON.parse(JSON.stringify(t.lift)), modes: { ...t.modes },
               port_rates: { ...(t.port_rates || {}) }, why: {} });
  cur = turns.length - 1; areas = turns[cur].areas;
}

// Apply one of the changes the server tested (comparisons.by_plan "changes").
function applyChange(x) {
  forkIfBaseline();
  const t = turns[cur];
  if (x.kind === "mode") return setMode2(x.class, x.to === "sea" ? "sea" : "air", "tested");
  if (x.kind === "lift") {
    t.lift[x.type] = [...liftGroups(t, x.type).map(g => ({ ...g })), { count: 1, at: null, ready: 0 }];
    t.why = { ...(t.why || {}), ["lift:" + x.type]: "tested" };
  } else if (x.kind === "port_rate") {
    t.port_rates = { ...(t.port_rates || {}), [x.port_id]: x.to };
    t.why = { ...(t.why || {}), ["rate:" + x.port_id]: "tested" };
  }
  picked = null; setMode(null); drawAreas(); go();
}

const changeLabel = x => x.kind === "mode" ? `Class ${x.class} → ${x.to === "sea" ? "sea" : "air"}`
  : x.kind === "lift" ? `+1 ${liftShort(x.type)}`
  : `${x.place} at the ${x.size} harbor rate (${fmt(x.to)} st/d)`;

// What this what-if changes from the baseline, each with a way to undo it.
function whatIfChanges() {
  if (cur === base) return [];
  const t = turns[cur], b = turns[base], why = t.why || {}, out = [];
  const src = k => why[k] === "tested" ? "From Every change tested" : "";
  const redo = () => { picked = null; setMode(null); drawAreas(); go(); };
  for (const c of meta.classes) {
    const m = modeOf(t, c.id), bm = modeOf(b, c.id);
    if (m !== bm) out.push({ text: `Class ${c.id} (${c.name.toLowerCase()}): by ${m}, ${bm === meta.lift.normal_mode[c.id] ? "normally" : "baseline"} ${bm}`, src: src("mode:" + c.id),
                             undo: () => setMode2(c.id, bm, null) });
  }
  const same = (x, y) => JSON.stringify(x) === JSON.stringify(y);
  t.areas.forEach((a, i) => {
    if (b.areas.some(x => same(x, a))) return;
    out.push({ text: `${a.kind === "wez" ? "WEZ" : "Area of denial"}: ${a.type === "circle" ? `circle, ${fmt(a.radius_nm)} nm radius` : `polygon, ${a.points.length} corners`}`, src: "Drawn on the map",
               undo: () => { t.areas.splice(i, 1); redo(); } });
  });
  for (const a of b.areas) if (!t.areas.some(x => same(x, a))) out.push({ text: `Removed: the baseline's ${a.kind === "wez" ? "WEZ" : "area of denial"}`, src: "",
    undo: () => { t.areas.push({ ...a }); redo(); } });
  for (const [k, x] of liftTypes()) {
    if (same(liftGroups(t, k), liftGroups(b, k))) continue;
    const gs = liftGroups(t, k), n = liftCount(t, k), bn = liftCount(b, k);
    const where = gs.filter(g => g.count && (g.at || g.ready)).map(g => `${g.count} at ${placeName(k, g.at)}${g.ready ? `, D+${fmt(g.ready, g.ready % 1 ? 1 : 0)}` : ""}`);
    out.push({ text: `${x.name}: ${n} on hand${n !== bn ? ` (baseline ${bn})` : ""}${where.length ? "; " + where.join("; ") : ""}`, src: src("lift:" + k) || "Edit lift",
               undo: () => { if (b.lift[k]) t.lift[k] = JSON.parse(JSON.stringify(b.lift[k])); else delete t.lift[k]; redo(); } });
  }
  const tr = t.port_rates || {}, br = b.port_rates || {};
  for (const p of new Set([...Object.keys(tr), ...Object.keys(br)])) {
    if (tr[p] === br[p]) continue;
    out.push({ text: `${byId[p].name}: ${tr[p] ? `${fmt(tr[p])} short tons a day` : "rate from the data"}${br[p] ? ` (baseline ${fmt(br[p])})` : ""}`, src: src("rate:" + p),
               undo: () => { t.port_rates = { ...tr }; if (br[p]) t.port_rates[p] = br[p]; else delete t.port_rates[p]; redo(); } });
  }
  return out;
}

let changeList = [];
function changesBox() {
  changeList = whatIfChanges();
  if (cur === base) return "";
  return `<div class="changes"><div class="lab">${turnName(cur)} changes from ${turnName(base)}</div>` +
    (changeList.length ? changeList.map((x, i) => `<div class="chg"><span>${esc(x.text)}${x.src ? `<span class="src">${esc(x.src)}</span>` : ""}</span>` +
       `<button data-u="${i}" title="Put this back as in ${turnName(base)}">Undo</button></div>`).join("")
     : `<div class="chg"><span>Nothing yet. Draw an area, edit the lift, change a class's mode, or try a change below.</span></div>`) + `</div>`;
}

const sec = (title, right) => `<div class="sec"><span>${title}</span>${right ? `<span>${right}</span>` : ""}</div>`;

// Does this usage row hold the plan's last ton? Matched on the limit's fields, never on its wording.
function isLimit(u, l) {
  if (!l) return false;
  if (u.kind === "port") return l.kind === "port" && l.port_id === u.port_id && l.work === u.work;
  if (u.kind === "ships") return ["ships", "ships_arriving", "no_ships"].includes(l.kind) && l.type === u.type;
  return ["aircraft", "mog", "aircraft_arriving", "no_aircraft"].includes(l.kind) && l.type === u.type;
}

function renderPlan() {
  const r = cmp, t = turns[cur];
  const rows = r.rows.map(x => ({ x, c: x.cells.plan }))
    .sort((a, b) => (b.c.closure ?? 1e9) - (a.c.closure ?? 1e9) || a.x.class.localeCompare(b.x.class));
  const top = rows[0] && rows[0].c, lim = top && top.limit;
  const bl = r.base ? r.base.last : undefined;
  let h = changesBox();

  // 1. The answer
  const bad = r.last == null || r.last > due + 1e-9;
  h += `<div class="tiles">` +
    `<div class="tile"><span>Required</span><b>D+${due}</b></div>` +
    `<div class="tile"><span>All cargo in</span><b class="${bad ? "late" : "ok"}">${dPlus(r.last)}</b>` +
      (bl !== undefined ? `<i>${turnName(base)}: ${dPlus(bl)}</i>` : "") + `</div>` +
    `<div class="tile"><span>Margin</span><b class="${bad ? "late" : "ok"}">${marginText(r.last)}</b></div></div>`;
  h += `<div class="limitby"><span class="k">Limited by</span> ` +
    (lim ? `<b title="${esc(limitText(lim))}">${esc(limitLabel(lim))}</b>${limitNumbers(lim) ? `<span class="k"> · ${esc(limitNumbers(lim))}</span>` : ""}`
         : top && top.available ? "nothing: every class's first load is its last" : esc((top && top.why) || "no way there")) + `</div>`;

  // 2. The plan by class
  h += sec("By class", "latest first · change a mode under What's moving");
  h += `<table class="tb"><thead><tr><th>Class</th><th class="r">Short tons</th><th>Mode</th><th>Lift</th><th class="r">First</th><th class="r">All in</th></tr></thead><tbody>`;
  for (const { x, c } of rows) {
    const moved = modeOf(t, x.class) !== meta.lift.normal_mode[x.class];
    const mode = c.available ? MODE_TEXT[c.mode][0].toUpperCase() + MODE_TEXT[c.mode].slice(1) : "—";
    const sel = `<span class="${moved ? "moved" : ""}" title="${moved ? `Moved by this what-if; normally ${meta.lift.normal_mode[x.class]}` : "Its normal mode"}">${mode}</span>`;
    const fell = c.fallback ? `<span class="fell" title="${esc(c.fallback)}">*</span>` : "";
    const on = picked && picked.cls === x.class;
    h += `<tr class="cls${on ? " pick" : ""}${hoverRow === x.class ? " on" : ""}" data-cls="${x.class}">` +
      `<td><b>${x.class}</b> ${esc(x.name)}</td><td class="r">${x.short_tons != null ? fmt(x.short_tons) : ""}</td>` +
      `<td>${sel}${fell}</td><td>${c.available && c.lift ? esc(liftShort(c.lift.type)) : "—"}</td>` +
      `<td class="r">${c.available ? fmt(c.days, 1) : "—"}</td>` +
      `<td class="r ${late(c) ? "late" : ""}">${c.available ? (c.closure == null ? "no lift" : fmt(c.closure, 1)) : "no way"}</td></tr>`;
  }
  h += `</tbody></table>`;
  const notes = rows.filter(i => i.c.fallback || !i.c.available);
  if (notes.length) h += notes.map(i => `<div class="tnote">${i.c.fallback ? "* " : ""}Class ${i.x.class}: ${esc(i.c.fallback || i.c.why || "no way there")}</div>`).join("");

  // 3. Capacity used
  const usage = [...r.usage].sort((a, b) => (b.days ?? -1) - (a.days ?? -1));
  h += sec("Capacity used", "days: port work at its rate; ships and aircraft, the last load's wait");
  h += `<table class="tb"><thead><tr><th>Resource</th><th class="r">Demand</th><th class="r">Rate or size</th><th class="r">Days</th></tr></thead><tbody>`;
  for (const u of usage) {
    let name, demand, rate;
    if (u.kind === "port") {
      name = `${u.place} ${u.work === "loading" ? "loading" : "discharge"}`;
      demand = `${fmt(u.demand_st)} st`;
      rate = `${fmt(u.rate_st_per_day)} st/d${u.figure ? "" : " (what-if)"}`;
    } else if (u.kind === "ships") {
      name = `${liftShort(u.type)} × ${u.count}`;
      demand = `${u.loads} load${u.loads === 1 ? "" : "s"} · ${Math.max(1, Math.round(100 * u.fill))}% full` +
               (u.type === "tanker" ? ` · ≈${fmt(gallons(u.pool_st) / 1000)}k gal` : "");
      rate = `${fmt(u.capacity_st)} st/load`;
    } else {
      name = `${liftShort(u.type)} × ${u.count}`;
      demand = `${fmt(u.sorties)} sorties`;
      rate = u.sorties_per_day ? `${fmt(u.sorties_per_day, 1)}/day${u.bound && u.bound !== "aircraft" ? " (MOG)" : ""}` : "—";
    }
    const binds = isLimit(u, lim);
    h += `<tr${binds ? ' class="binds"' : ""}><td>${esc(name)}${binds ? ` <span class="tag lim">limit</span>` : ""}</td>` +
      `<td class="r">${demand}</td><td class="r">${rate}</td><td class="r${binds && bad ? " late" : ""}">${u.days == null ? "—" : fmt(u.days, 1)}</td></tr>`;
  }
  h += `</tbody></table>`;

  // 4. Every change tested. Changes that make no difference are grouped into one row each, not dropped.
  const moves = r.changes.filter(x => x.last !== r.last), still = r.changes.filter(x => x.last === r.last);
  h += sec(`Every single change, tested (${r.changes.length})`, `click one to try it${cur === base ? " in a new what-if" : ""}`);
  h += `<table class="tb"><thead><tr><th>Change</th><th class="r">All cargo in</th><th class="r">Margin</th></tr></thead><tbody>`;
  moves.forEach(x => {
    const ok = x.last != null && x.last <= due + 1e-9;
    h += `<tr class="try" data-id="${esc(x.id)}" title="${x.last == null ? "Some class can't get there" : `${signed(r1(x.last - r.last))} days on now`}">` +
      `<td>${esc(changeLabel(x))}</td><td class="r">${dPlus(x.last)}</td><td class="r ${ok ? "ok" : "late"}">${marginText(x.last)}</td></tr>`;
  });
  const groupsOf = kind => still.filter(x => x.kind === kind);
  for (const [kind, words] of [["mode", xs => `${xs.length === 1 ? "Class" : "Classes"} ${xs.map(x => x.class).join(", ")} → other mode`],
                               ["lift", xs => `+1 of ${xs.length === 1 ? liftShort(xs[0].type) : `any lift type (${xs.map(x => liftShort(x.type)).join(", ")})`}`],
                               ["port_rate", xs => xs.map(changeLabel).join("; ")]]) {
    const xs = groupsOf(kind);
    if (xs.length) h += `<tr class="same" title="${esc(xs.map(changeLabel).join("\n"))}"><td>${esc(words(xs))}</td><td class="r">${dPlus(r.last)}</td><td class="r">no change</td></tr>`;
  }
  h += `</tbody></table>`;

  // 5. Figures used
  const figs = [...r.figures].sort((a, b) => a.label.localeCompare(b.label));
  const n = s => figs.filter(f => f.source.status === s).length;
  const counts = ["sourced", "unverified", "illustrative"].filter(n).map(s => `${n(s)} ${s}`).join(", ");
  const sea = rows.find(i => i.c.available && i.c.mode === "sea");
  h += `<details class="figs"${figsOpen ? " open" : ""}><summary>${sec(`Figures used (${figs.length})`, counts)}</summary>`;
  h += `<table class="tb"><thead><tr><th>Figure</th><th class="r">Value</th><th class="r">Status</th></tr></thead><tbody>`;
  if (sea) {
    const f = seaFacts(r, sea.c);
    h += `<tr><td>Sea route, ${esc(byId[currentFrom].name)} → ${esc(byId[currentTo].name)}${f.via.length ? ` via ${esc(f.via.join(", "))}` : ""}</td><td class="r">${fmt(f.nm)} nm</td><td class="r"><span class="tag computed">computed</span></td></tr>`;
  }
  for (const [p, rate] of Object.entries(r.port_rates || {}))
    h += `<tr><td>Port rate, ${esc(byId[p].name)}</td><td class="r">${fmt(rate)} short tons a day</td><td class="r"><span class="tag whatif">what-if</span></td></tr>`;
  for (const f of figs) {
    const unit = f.value === 1 ? f.unit.replace(/s$/, "").replace(/s (a|at|per) /, " $1 ") : f.unit;
    const v = Array.isArray(f.value) ? `${f.value.length} listed` : typeof f.value === "number" ? `${fmt(f.value, f.value % 1 ? 2 : 0)} ${unit}` : `${f.value} ${unit}`;
    const tag = `<span class="tag ${f.source.status}" title="${esc(f.source.source + (f.source.checked ? ` (checked ${f.source.checked})` : ""))}">${STATUS_TEXT[f.source.status]}</span>`;
    h += `<tr><td>${esc(f.label)}</td><td class="r">${esc(v.trim())}</td><td class="r">${f.source.url ? `<a href="${esc(f.source.url)}" target="_blank" rel="noopener">${tag}</a>` : tag}</td></tr>`;
  }
  h += `</tbody></table><div class="tnote">Hover a status for its source. Change a figure in cargo/*.json; falcon_data.py checks every file when the server starts.</div></details>`;

  el("plan").innerHTML = h;
  el("csum").textContent = "";

  // Wiring
  el("plan").querySelectorAll(".chg button").forEach(b => b.onclick = () => changeList[+b.dataset.u].undo());
  el("plan").querySelectorAll("tr.try").forEach(tr => { tr.onclick = () => applyChange(r.changes.find(x => x.id === tr.dataset.id)); });
  el("plan").querySelector("details.figs").ontoggle = e => { figsOpen = e.target.open; };
  el("plan").querySelectorAll("tr.cls").forEach(tr => {
    const cls = tr.dataset.cls;
    tr.onmouseenter = () => { hoverRow = cls; tr.classList.add("on"); drawCargo(); drawMarks(); };
    tr.onmouseleave = () => { hoverRow = null; tr.classList.remove("on"); drawCargo(); drawMarks(); };
    tr.onclick = () => { picked = picked && picked.cls === cls ? null : { cls, col: "plan" }; renderPlan(); drawCargo(); drawMarks(); };
  });
  renderDetail();
}
let figsOpen = false;

// How the picked cell's days are built: each leg, then the handling time, then the lift and what it waits on.
function renderDetail() {
  const box = el("detail"), r = cmp;
  const x = picked && r.rows.find(y => y.class === picked.cls), c = x && x.cells[picked.col];
  if (!c) {
    box.innerHTML = `<div class="dim">Click a class${lens === "plan" ? "" : " cell"} to see its legs on the map and how its days are built.</div>`;
    return;
  }
  const col = r.columns.find(k => k.id === picked.col);
  let h = `<div class="t">Class ${x.class} ${esc(x.name)} · ${esc(col.label)}${c.available && c.closure != null ? ` · all in by D+${fmt(c.closure, 1)}` : ""}</div>`;
  if (!c.available) { box.innerHTML = h + `<div>${esc(c.why || "No way there.")}</div>`; return; }
  let moving = 0, sea = false, air = false;
  const lines = c.legs.map(k => {
    const L = r.legs[k], who = L.carrier === "commercial" ? "Commercial" : "Military";
    if (L.mode === "sea") {
      sea = true; moving += L.days;
      return `<b class="${L.carrier}">${who}</b> by sea: ${esc(L.from_name)} → ${esc(L.to_name)}, ${fmt(L.length_nm)} nm, ` +
             `${fmt(L.days, 1)} days at ${meta.knots[L.carrier]} kt${L.passages.length ? `, via ${esc(L.passages.join(", "))}` : ""}`;
    }
    moving += L.hours / 24;
    if (L.mode === "road") return `<span class="dim">Road: ${esc(L.from_name)} → ${esc(L.to_name)}, ${fmt(L.length_nm)} nm</span>`;
    air = true;
    return `<b class="${L.carrier}">${who}</b> flight (${esc(L.aircraft)}): ${esc(L.from_name)} → ${esc(L.to_name)}, ${fmt(L.length_nm)} nm, ` +
           `${fmt(L.hours, 1)} hours${L.stops.length ? `, refuelling at ${esc(L.stops.join(", "))}` : ""}`;
  });
  const hd = meta.handling;
  const why = [sea && `${hd.ship_port_days} day in each port where cargo is worked`, air && `${hd.airfield_hours} hours at each airport`].filter(Boolean).join(", ");
  h += `<div class="dim">First arrival, ${fmt(c.days, 1)} days:</div>`;
  h += `<ul>${lines.map(l => `<li>${l}</li>`).join("")}<li>Handling: ${fmt(Math.max(0, c.days - moving), 1)} days (${why})</li></ul>`;
  if (c.lift) {
    const L = c.lift, shared = L.pool_st != null && L.pool_st !== x.short_tons ? `, shared with other classes: ${fmt(L.pool_st)} short tons in all` : "";
    h += `<div>All in: ${c.closure != null ? "D+" + fmt(c.closure, 1) : "can't, with the lift on hand"}. ` +
         (L.kind === "ship" ? `Lift: ${L.count} ${esc(L.name)}${L.count === 1 ? "" : "s"} of ${fmt(L.capacity_st)} short tons, ${L.loads} load${L.loads === 1 ? "" : "s"}${shared}.`
                            : `Lift: ${L.count} ${esc(L.name)}, ${fmt(L.planning_payload_st)} short tons a sortie, ${fmt(L.sorties)} sorties${shared}.`) +
         (c.limit ? ` Waits on ${esc(limitText(c.limit))}.` : " One load does it: nothing makes it wait.") + `</div>`;
  }
  if (c.alt) h += `<div class="dim">By ${MODE_TEXT[c.alt.mode]}: all in ${c.alt.closure != null ? "D+" + fmt(c.alt.closure, 1) : "can't, with the lift on hand"}.</div>`;
  if (c.why) h += `<div class="dim">${esc(c.why)}</div>`;
  const flags = c.flags.filter(f => f.level !== "note");
  if (flags.length) h += `<ul>${flags.map(f => `<li><span class="pill ${f.level}">${f.level}</span> ${esc(f.text)}</li>`).join("")}</ul>`;
  box.innerHTML = h;
}
