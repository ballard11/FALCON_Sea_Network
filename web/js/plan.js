// FALCON map, plan.js: Cargo: the Plan panel (the answer, what would change it, the plan by mode) and each what-if's changes.
// Loaded in order by index.html; all files share one global scope.

// ------------------------------------------------------ cargo: the plan
// The baseline movement plan (spec, planner screen): the answer first, then what would change it (the same plan
// re-run with one change each, worked out by the server; none is chosen), then the plan grouped by mode.
const MODE_LABEL = c => c.mode === "sea" ? "By sea" : c.mode === "air" ? (c.plan === "military" ? "By military air" : c.plan === "commercial" ? "By commercial air" : `By air, ${c.plan.split("_").join(" then ")}`)
                                             : c.mode === "air_sea" ? "By air, then sea" : "By sea, then air";

// What a group's lift is, in physical terms: tanker loads and gallons, how full a ship is, sorties a day.
function liftText(cells) {
  const seen = {}, out = [];
  for (const c of cells) if (c.lift && !seen[c.lift.type]) seen[c.lift.type] = c.lift;
  for (const L of Object.values(seen)) {
    if (L.kind === "ship") {
      const name = shortName(L.name), full = L.loads === 1 ? `, ${Math.max(1, Math.round(100 * L.pool_st / L.capacity_st))}% full` : "";
      const gal = L.type === "tanker" ? `, about ${fmt(Math.round(L.pool_st * 2000 / meta.lift.fuel_lb_per_gal / 1000) * 1000)} gallons` : "";
      out.push(`${L.loads} ${name} load${L.loads === 1 ? "" : "s"}${full}${gal}`);
    } else {
      out.push(`${fmt(L.sorties)} ${shortName(L.name)} sorties${L.sorties_per_day ? `, ${fmt(L.sorties_per_day, 1)} a day` : ""}`);
    }
  }
  return out.join("; ");
}

// A class's mode in a what-if: its normal mode unless the what-if moves it. "why" says where a change came from
// (a lever), so the changes box can say so; null is a change made by hand.
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

// A lever or a mode switch tried on the baseline goes into a new what-if, so the baseline stays as it was.
function forkIfBaseline() {
  if (cur !== base) return;
  turns.push({ areas: areas.map(a => ({ ...a })), lift: JSON.parse(JSON.stringify(turns[cur].lift)), modes: { ...turns[cur].modes }, why: {} });
  cur = turns.length - 1; areas = turns[cur].areas;
}

function applyLever(kind, key, to) {
  forkIfBaseline();
  if (kind === "switch") return setMode2(key, to, "lever");
  const t = turns[cur];
  t.lift[key] = [...liftGroups(t, key).map(g => ({ ...g })), { count: 1, at: null, ready: 0 }];
  t.why = { ...(t.why || {}), ["lift:" + key]: "lever" };
  picked = null; setMode(null); drawAreas(); go();
}

// What this what-if changes from the baseline, in plain words, each with a way to undo it.
function whatIfChanges() {
  if (cur === base) return [];
  const t = turns[cur], b = turns[base], why = t.why || {}, out = [];
  const src = k => why[k] === "lever" ? "From What would change it" : "";
  for (const c of meta.classes) {
    const m = modeOf(t, c.id), bm = modeOf(b, c.id);
    if (m !== bm) out.push({ text: `Class ${c.id} (${c.name.toLowerCase()}): by ${m}, ${bm === meta.lift.normal_mode[c.id] ? "normally" : "baseline"} ${bm}`, src: src("mode:" + c.id),
                             undo: () => setMode2(c.id, bm, null) });
  }
  const same = (x, y) => JSON.stringify(x) === JSON.stringify(y);
  t.areas.forEach((a, i) => {
    if (b.areas.some(x => same(x, a))) return;
    out.push({ text: `${a.kind === "wez" ? "WEZ" : "Area of denial"}: ${a.type === "circle" ? `circle, ${fmt(a.radius_nm)} nm radius` : `polygon, ${a.points.length} corners`}`, src: "Drawn on the map",
               undo: () => { t.areas.splice(i, 1); picked = null; setMode(null); drawAreas(); go(); } });
  });
  for (const a of b.areas) if (!t.areas.some(x => same(x, a))) out.push({ text: `Removed: the baseline's ${a.kind === "wez" ? "WEZ" : "area of denial"}`, src: "",
    undo: () => { t.areas.push({ ...a }); picked = null; setMode(null); drawAreas(); go(); } });
  for (const [k, x] of liftTypes()) {
    if (same(liftGroups(t, k), liftGroups(b, k))) continue;
    const gs = liftGroups(t, k), n = liftCount(t, k), bn = liftCount(b, k);
    const where = gs.filter(g => g.count && (g.at || g.ready)).map(g => `${g.count} at ${placeName(k, g.at)}${g.ready ? `, D+${fmt(g.ready, g.ready % 1 ? 1 : 0)}` : ""}`);
    out.push({ text: `${x.name}: ${n} on hand${n !== bn ? ` (baseline ${bn})` : ""}${where.length ? "; " + where.join("; ") : ""}`, src: src("lift:" + k) || "Edit lift",
               undo: () => { if (b.lift[k]) t.lift[k] = JSON.parse(JSON.stringify(b.lift[k])); else delete t.lift[k]; picked = null; setMode(null); drawAreas(); go(); } });
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
     : `<div class="chg"><span>Nothing yet. Draw an area, edit the lift, or change a class's mode under What's moving.</span></div>`) + `</div>`;
}

function renderPlan() {
  const r = cmp, L = r.levers, cells = r.rows.map(x => ({ x, c: x.cells.plan }));
  const tons = list => list.reduce((n, { x }) => n + (x.short_tons || 0), 0);
  // Group classes that share an answer: the same way there, the same last-ton day, the same thing holding it.
  const groups = [];
  for (const it of cells) {
    const key = it.c.available ? `${MODE_LABEL(it.c)}|${it.c.closure}|${it.c.limit}` : `none|${it.x.class}`;
    let g = groups.find(y => y.key === key);
    if (!g) groups.push(g = { key, items: [] });
    g.items.push(it);
  }
  groups.sort((a, b) => (b.items[0].c.closure ?? 1e9) - (a.items[0].c.closure ?? 1e9));
  const ids = g => g.items.map(i => i.x.class);
  const lateG = groups.filter(g => late(g.items[0].c));
  // The answer
  let v;
  if (L.last == null) {
    const stuck = cells.filter(i => i.c.closure == null).map(i => i.x.class);
    v = ["bad", `Can't get everything there with the lift on hand`, `${classList(stuck)}: ${esc(cells.find(i => i.c.closure == null).c.limit || cells.find(i => i.c.closure == null).c.why || "no way there")}.`];
  } else if (L.last <= due + 1e-9) {
    v = ["good", `Makes D+${due} with ${fmt(r1(due - L.last), 1)} days to spare`, `The last ton is in by D+${fmt(L.last, 1)}.`];
  } else {
    const g = lateG[0], c = g.items[0].c;
    v = ["bad", `Misses D+${due} by ${fmt(r1(L.last - due), 1)} days`,
         `${classList(ids(g))} (${fmt(tons(g.items))} short tons ${MODE_LABEL(c).toLowerCase()}) ${g.items.length === 1 ? "is" : "are all"} in by D+${fmt(c.closure, 1)}.` +
         (c.limit ? ` They wait on ${esc(c.limit)}.` : "")];
  }
  const blast = r.base && r.base.levers ? r.base.levers.last : undefined;
  if (blast !== undefined) v[2] += blast == null ? " The baseline couldn't get everything there." : ` The baseline had everything in by D+${fmt(blast, 1)}.`;
  let h = changesBox() + `<div class="verdict ${v[0]}"><div class="big">${v[1]}</div><div class="why">${v[2]}</div></div>`;
  // What would change it
  const lv = [];
  if (L.last != null && L.last > due) lv.push({ info: true, t: `Arrive by D+${Math.ceil(L.last - 1e-9)}`, s: `Makes it with ${fmt(r1(Math.ceil(L.last - 1e-9) - L.last), 1)} days to spare.` });
  const better = x => x.last != null && (L.last == null || x.last < L.last - 1e-9);
  const sw = Object.entries(L.switch).filter(([, x]) => better(x)).sort((a, b) => a[1].last - b[1].last).slice(0, 3);
  for (const [cid, x] of sw) {
    const name = meta.classes.find(k => k.id === cid).name, use = x.lift ? (x.lift.kind === "ship" ? `${x.lift.loads} ${shortName(x.lift.name)} load${x.lift.loads === 1 ? "" : "s"}` : `${fmt(x.lift.sorties)} ${shortName(x.lift.name)} sorties in all`) : "";
    lv.push({ kind: "switch", key: cid, to: x.to === "sea" ? "sea" : "air", t: `Move Class ${cid} (${esc(name.toLowerCase())}) to ${x.to === "sea" ? "sea" : "air"}`,
              s: `Everything in by D+${fmt(x.last, 1)}${x.last <= due + 1e-9 ? `: makes D+${due}` : `: still misses D+${due}`}.${use ? ` It then shares ${use}.` : ""}` });
  }
  const mo = Object.entries(L.more).filter(([, x]) => better(x)).sort((a, b) => a[1].last - b[1].last).slice(0, 2);
  for (const [k, x] of mo) {
    const nm = (meta.lift.ships[k] || meta.lift.aircraft[k]).name;
    lv.push({ kind: "more", key: k, t: `One more ${esc(shortName(nm))}`, s: `Everything in by D+${fmt(x.last, 1)}${x.last <= due + 1e-9 ? `: makes D+${due}` : `: still misses D+${due}`}.` });
  }
  const slow = lateG[0] && lateG[0].items[0].c.limit;
  if (slow && / (loading|discharging) /.test(slow)) lv.push({ info: true, t: `A faster rate at ${esc(slow.split(/ (loading|discharging) /)[0])}`, s: "Port rates are illustrative, by harbor size. A sourced rate could change the answer either way." });
  if (lv.length) h += `<div class="box"><div class="lab">What would change it${lv.some(x => !x.info) ? ` · click one to try it${cur === base ? " in a new what-if" : " in this what-if"}` : ""}</div>` +
    lv.map((x, i) => `<button class="lever${x.info ? " info" : ""}" data-i="${i}"><b>${x.t}</b><span class="sm">${x.s}</span></button>`).join("") + `</div>`;
  // The plan, grouped
  const bcell = cid => { const y = r.base && r.base.rows.find(z => z.class === cid); return y ? y.cells.plan : null; };
  h += `<div class="box"><div class="lab">The plan: each class on its normal mode${Object.keys(turns[cur].modes || {}).length ? ", with this what-if's changes" : ""} · change a class's mode under What's moving</div>`;
  groups.forEach((g, gi) => {
    const c = g.items[0].c, cs = g.items.map(i => i.c);
    const movedIn = g.items.map(i => i.x.class).filter(cid => modeOf(turns[cur], cid) !== meta.lift.normal_mode[cid]);
    const fall = cs.filter(k => k.fallback).map(k => k.fallback);
    let right = "";
    if (!c.available) right = `<span class="dd no">no way</span>`;
    else if (c.closure == null) right = `<span class="dd no">no lift</span>`;
    else {
      const m = r1(due - c.closure);
      right = `<span class="dd ${late(c) ? "no" : "ok"}">D+${fmt(c.closure, 1)}</span><span class="sm">${m >= 0 ? fmt(m, 1) + " to spare" : fmt(-m, 1) + " late"}</span>`;
      // Change from the baseline, class by class; one figure when every class in the group moved the same.
      const d = g.items.map(i => { const b = bcell(i.x.class); return b ? [i.x.class, b.closure == null ? null : r1(c.closure - b.closure)] : null; }).filter(Boolean);
      if (d.length) {
        const same = d.every(x => x[1] === d[0][1]);
        const say = x => x == null ? "baseline had no lift" : x ? `${signed(x)} on baseline` : "same as baseline";
        right += same ? `<span class="sm">${say(d[0][1])}</span>` : d.map(([k, x]) => `<span class="sm">${k}: ${x == null ? "was no lift" : x ? signed(x) : "same"}</span>`).join("");
      }
    }
    const on = picked && g.items.some(i => i.x.class === picked.cls);
    h += `<div class="grp2${on ? " pick" : ""}" data-g="${gi}"><div><span class="t">${c.available ? MODE_LABEL(c) : "No way there"}</span>` +
      `<span class="sm">${classList(ids(g))} · ${fmt(tons(g.items))} short tons</span>` +
      (movedIn.length ? `<span class="sm" style="color:var(--military)">${movedIn.map(cid => `${cid} moved from ${meta.lift.normal_mode[cid]}`).join(", ")}</span>` : "") +
      (c.available ? `<span class="sm">${esc(liftText(cs))}</span>` : `<span class="sm">${esc(c.why || "")}</span>`) +
      fall.map(f => `<span class="sm">${esc(f)}</span>`).join("") + `</div><div class="r">${right}</div></div>`;
  });
  h += `</div>`;
  el("plan").innerHTML = h;
  // Footer: the sea route, if any class sails.
  const sea = cells.find(i => i.c.available && i.c.mode === "sea");
  if (sea) { const f = seaFacts(r, sea.c); el("csum").textContent = `${carrierPhrase(sea.c)}: ${fmt(f.nm)} nm${f.via.length ? " via " + f.via.join(", ") : ""}. Days are port to port; allowances are illustrative.`; }
  else el("csum").textContent = "Days are port to port; allowances are illustrative.";
  // Wiring
  el("plan").querySelectorAll(".chg button").forEach(b => b.onclick = () => changeList[+b.dataset.u].undo());
  el("plan").querySelectorAll(".lever:not(.info)").forEach(b => { const x = lv[+b.dataset.i]; b.onclick = () => applyLever(x.kind, x.key, x.to); });
  el("plan").querySelectorAll(".grp2").forEach(d => {
    const g = groups[+d.dataset.g], cls = g.items[0].x.class;
    d.onmouseenter = () => { hoverRow = cls; d.classList.add("on"); drawCargo(); drawMarks(); };
    d.onmouseleave = () => { hoverRow = null; d.classList.remove("on"); drawCargo(); drawMarks(); };
    d.onclick = () => { picked = picked && picked.cls === cls ? null : { cls, col: "plan" }; renderPlan(); drawCargo(); drawMarks(); };
  });
  renderDetail();
}

// How the picked cell's days are built: each leg, then the handling time, then sorties, reasons and checks.
function renderDetail() {
  const box = el("detail"), r = cmp;
  const x = picked && r.rows.find(y => y.class === picked.cls), c = x && x.cells[picked.col];
  if (!c) {
    box.innerHTML = `<div class="dim">Click a cell to see its legs on the map and how its days are built. ` +
      `Each cell is when the last ton arrives, with the lift on hand; the first arrival is underneath. Days are port to port. Illustrative allowances, not planning factors.</div>`;
    return;
  }
  const col = r.columns.find(k => k.id === picked.col);
  let h = `<div class="t">Class ${x.class} ${esc(x.name)} · ${esc(col.label)}${c.available && c.closure != null ? ` · last ton in ${fmt(c.closure, 1)} days` : ""}</div>`;
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
    h += `<div>Last ton in: ${c.closure != null ? fmt(c.closure, 1) + " days" : "can't, with the lift on hand"}. ` +
         (L.kind === "ship" ? `Lift: ${L.count} ${esc(L.name)}${L.count === 1 ? "" : "s"} of ${fmt(L.capacity_st)} short tons, ${L.loads} load${L.loads === 1 ? "" : "s"}${shared}.`
                            : `Lift: ${L.count} ${esc(L.name)}, ${fmt(L.planning_payload_st)} short tons a sortie, ${fmt(L.sorties)} sorties${shared}.`) +
         (c.limit ? ` The last ton waits on ${esc(c.limit)}.` : " One load does it: nothing makes it wait.") + `</div>`;
  }
  if (c.alt) h += `<div class="dim">By ${MODE_TEXT[c.alt.mode]}: last ton in ${c.alt.closure != null ? fmt(c.alt.closure, 1) + " days" : "can't, with the lift on hand"}.</div>`;
  if (c.why) h += `<div class="dim">${esc(c.why)}</div>`;
  const flags = c.flags.filter(f => f.level !== "note");
  if (flags.length) h += `<ul>${flags.map(f => `<li><span class="pill ${f.level}">${f.level}</span> ${esc(f.text)}</li>`).join("")}</ul>`;
  box.innerHTML = h + `<div class="dim">Allowances are illustrative, not planning factors.</div>`;
}
