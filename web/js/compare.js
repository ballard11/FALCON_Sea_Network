// FALCON map, compare.js: Cargo: the comparison grid for the Mode and Port of debarkation lenses, and each cell's detail.
// Loaded in order by index.html; all files share one global scope.

// ------------------------------------------------------ cargo: compare
// Planner screen, step 2. The server's lens gives the columns (see comparisons.py); every cell has the
// same shape, so one grid draws any lens. A lens never picks: each cell says whether it makes the
// arrive-by date, and by how much. Each lens adds its own headline numbers and summary line below.
const classList = ids => ids.length === 1 ? `Class ${ids[0]}` : `Classes ${ids.slice(0, -1).join(", ")} and ${ids[ids.length - 1]}`;
// A cell makes the date when its last ton arrives by then (closure, with the lift on hand).
const late = c => !c.available || c.closure == null || c.closure > due + 1e-9;
const r1 = x => Math.round(x * 10) / 10;
const signed = (x, d = 1) => `${x > 0 ? "+" : ""}${fmt(x, d)}`;
const turnName = i => i ? `What-if ${i}` : "Start";
const lateEverywhere = r => r.rows.filter(x => Object.values(x.cells).every(late)).map(x => x.class);

function carrierPhrase(c) {
  const h = c.handover ? c.handover.name : "a handover port";
  return { commercial: "Commercial carriers sail the whole way", military: "The military sails the whole way",
           commercial_military: `Commercial carriers hand over to the military at ${h}`,
           military_commercial: `The military carries it out to ${h}, where commercial carriers take over` }[c.plan] || planText(c.plan);
}

// A sea cell's distance and named passages.
function seaFacts(r, c) {
  const legs = c.legs.map(k => r.legs[k]).filter(l => l.mode === "sea");
  return { nm: legs.reduce((n, l) => n + l.length_nm, 0), via: [...new Set(legs.flatMap(l => l.passages))] };
}

const LENS_UI = {
  mode: {
    showMode: false,
    metrics(r) {
      const sea = r.rows.map(x => x.cells.sea), ok = sea.filter(c => c.closure != null).map(c => c.closure);
      const bok = r.base ? r.base.rows.map(x => x.cells.sea).filter(c => c.closure != null).map(c => c.closure) : [];
      const span = d => !d.length ? null : Math.min(...d) === Math.max(...d) ? fmt(Math.max(...d), 1) : `${fmt(Math.min(...d), 1)} to ${fmt(Math.max(...d), 1)}`;
      let note = "";
      if (r.base) note = !bok.length ? "baseline had no sea route" : !ok.length ? `baseline ${span(bok)} days`
                       : r1(Math.max(...ok) - Math.max(...bok)) ? `${signed(r1(Math.max(...ok) - Math.max(...bok)))} on the baseline` : "same as the baseline";
      // Flying the classes that miss by sea but make it by air: tons per carrier, then aircraft loads.
      const need = {};
      for (const x of r.rows) {
        const a = x.cells.air;
        if (late(x.cells.sea) && !late(a)) for (const k in a.sorties) need[k] = (need[k] || 0) + (x.short_tons || 0);
      }
      const sorties = Object.entries(need).map(([k, t]) => [k, Math.ceil(t / meta.lift.aircraft[k].planning_payload_st)]);
      const total = sorties.reduce((n, [, s]) => n + s, 0);
      const lateAny = lateEverywhere(r);
      return [
        ["All by sea, last ton in", ok.length ? `${span(ok)} days` : "No route", note],
        [`Miss D+${due} by sea`, `${sea.filter(late).length} of ${sea.length}`, ""],
        [`Miss D+${due} any way`, `${lateAny.length}`, lateAny.length ? classList(lateAny) : "every class can make it"],
        [`Sorties to keep D+${due}`, fmt(total), total ? sorties.map(([k, s]) => `${fmt(s)} ${k}`).join(", ") : "none needed"],
      ];
    },
    summary(r) {
      const lateAny = lateEverywhere(r), n = r.rows.length;
      const missSea = r.rows.filter(x => late(x.cells.sea)).length;
      let s = lateAny.length ? `${classList(lateAny)} can't make D+${due} any way. `
            : !missSea ? `Every class makes D+${due} by sea. `
            : `Every class can make D+${due}, ${missSea === n ? "but only by air" : `${missSea === 1 ? "one" : missSea} only by air`}. `;
      const first = r.rows.map(x => x.cells.sea).find(c => c.available);
      if (!first) return s + (r.rows[0].cells.sea.why || "No sea route.");
      const f = seaFacts(r, first);
      s += `${carrierPhrase(first)}: ${fmt(f.nm)} nm${f.via.length ? " via " + f.via.join(", ") : ""}.`;
      const slow = r.rows.map(x => x.cells.sea).filter(c => c.limit).sort((a, b) => b.closure - a.closure)[0];
      if (slow) s += ` By sea, the last ton waits on ${slow.limit}.`;
      const bfirst = r.base && r.base.rows.map(x => x.cells.sea).find(c => c.available);
      if (bfirst) {
        const dnm = f.nm - seaFacts(r.base, bfirst).nm, dd = r1(first.days - bfirst.days);
        if (Math.abs(dnm) >= 1 || dd) s += ` ${signed(dnm, 0)} nm, ${signed(dd)} days on the baseline.`;
      }
      return s;
    },
  },
  pod: {
    showMode: true,
    metrics(r) {
      const cols = r.columns, all = cols.filter(col => r.rows.every(x => !late(x.cells[col.id])));
      const lateAny = lateEverywhere(r);
      return [
        ["Ports compared", `${cols.length}`, cols.length === 1 ? "no Medium or Large harbor nearby" : `the destination and ${cols.length - 1} nearby`],
        [`Every class makes D+${due}`, `${all.length} of ${cols.length}`, all.map(c => c.label).join(", ")],
        [`Late at every port`, `${lateAny.length}`, lateAny.length ? classList(lateAny) : "none"],
      ];
    },
    summary(r) {
      const lateAny = lateEverywhere(r);
      return (lateAny.length ? `${classList(lateAny)} can't make D+${due} at any of these ports. ` : "") +
        `Each cell is the soonest way to that port, by sea or by air. Days stop at the port: the move on to ${r.columns[0].label} isn't counted.`;
    },
  },
};

function cellHtml(c, bc, showMode) {
  if (!c.available) return `<span class="na">${/never flies/.test(c.why || "") ? "never flies" : "no way"}</span>`;
  const sub = [];
  if (showMode || (c.mode !== "sea" && c.mode !== "air")) sub.push(MODE_TEXT[c.mode]);
  if (c.plan && c.plan !== "commercial") sub.push(c.plan.split("_").join(", then "));
  if (c.closure == null) return `<span class="dd no">no lift</span><div class="sub">${esc(c.limit || "")}</div>`;
  sub.push(`first ${fmt(c.days, 1)}`);
  if (c.lift) sub.push(c.lift.kind === "ship" ? `${c.lift.loads} load${c.lift.loads === 1 ? "" : "s"}` : `${fmt(c.lift.sorties)} sorties`);
  const m = r1(due - c.closure);
  sub.push(m >= 0 ? `${fmt(m, 1)} to spare` : `${fmt(-m, 1)} late`);
  if (bc) {
    if (!bc.available || bc.closure == null) sub.push("was no way");
    else { const d = r1(c.closure - bc.closure); if (d) sub.push(`${signed(d)} on baseline`); }
  }
  return `<span class="dd ${late(c) ? "no" : "ok"}">${fmt(c.closure, 1)} d</span><div class="sub">${sub.join(" · ")}</div>`;
}

function renderCompare() {
  const r = cmp, ui = r && LENS_UI[r.lens];
  el("rhead").textContent = `${turnName(cur)} · ${currentFrom ? byId[currentFrom].name : "?"} → ${currentTo ? byId[currentTo].name : "?"} · arrive by D+${due}`;
  const l = meta && meta.lenses.find(x => x.id === lens);
  el("lensNote").textContent = l ? l.note : "";
  document.body.classList.toggle("plan-lens", lens === "plan");
  if (r && r.lens === "plan") { renderPlan(); return; }
  if (!r || !ui) { el("mets").innerHTML = ""; el("csum").textContent = ""; el("grid").innerHTML = ""; el("plan").innerHTML = ""; el("detail").innerHTML = ""; return; }
  el("mets").innerHTML = ui.metrics(r).map(([k, v, n]) =>
    `<div class="met"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>${n ? `<div class="n">${esc(n)}</div>` : ""}</div>`).join("");
  el("csum").textContent = ui.summary(r);

  const baseCell = (cls, col) => { const x = r.base && r.base.rows.find(y => y.class === cls); return x ? x.cells[col] || null : null; };
  let h = `<thead><tr><th style="width:${r.columns.length > 2 ? 27 : 36}%">Class</th><th style="width:${r.columns.length > 2 ? 13 : 14}%">Tons</th>`;
  for (const col of r.columns) {
    const k = r.rows.filter(x => !late(x.cells[col.id])).length;
    h += `<th><b>${esc(col.label)}</b>${esc(col.sub)}<br>${k} of ${r.rows.length} make D+${due}</th>`;
  }
  h += "</tr></thead><tbody>";
  for (const x of r.rows) {
    const rule = meta.flies[x.class] === "none" ? "Never flies" : meta.flies[x.class] === "military" ? "Military aircraft only" : "";
    const checks = new Set(Object.values(x.cells).flatMap(c => c.flags.filter(f => f.level !== "note").map(f => f.code)));
    const notes = [rule, checks.size ? `${checks.size} check${checks.size === 1 ? "" : "s"}, click a cell` : ""].filter(Boolean).join(" · ");
    h += `<tr data-cls="${x.class}"${hoverRow === x.class ? ' class="on"' : ""}><td class="cls"><b>${x.class}</b>${esc(x.name)}` +
         (notes ? `<span class="note">${esc(notes)}</span>` : "") + `</td><td>${x.short_tons != null ? fmt(x.short_tons) : ""}</td>`;
    for (const col of r.columns) {
      const c = x.cells[col.id], on = picked && picked.cls === x.class && picked.col === col.id;
      h += `<td class="c${on ? " pick" : ""}" data-col="${esc(col.id)}" title="${esc([c.limit && "Last ton waits on " + c.limit, c.why, "Click to see how the days are built"].filter(Boolean).join(". "))}">` +
           cellHtml(c, baseCell(x.class, col.id), ui.showMode) + "</td>";
    }
    h += "</tr>";
  }
  el("grid").innerHTML = h + "</tbody>";
  for (const tr of el("grid").querySelectorAll("tbody tr")) {
    const cls = tr.dataset.cls;
    tr.onmouseenter = () => { hoverRow = cls; tr.classList.add("on"); drawCargo(); drawMarks(); };
    tr.onmouseleave = () => { hoverRow = null; tr.classList.remove("on"); drawCargo(); drawMarks(); };
    for (const td of tr.querySelectorAll("td.c")) td.onclick = () => {
      const same = picked && picked.cls === cls && picked.col === td.dataset.col;
      picked = same ? null : { cls, col: td.dataset.col };
      renderCompare(); drawCargo(); drawMarks();
    };
  }
  renderDetail();
}
