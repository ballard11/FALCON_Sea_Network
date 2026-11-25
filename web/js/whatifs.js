// FALCON map, whatifs.js: Cargo: what-ifs ("turns" in the code): the bar over the map, and the lift editor.
// Loaded in order by index.html; all files share one global scope.

// ------------------------------------------------------ cargo: turns
// What-ifs ("turns" in the code): each holds its own situation (areas and WEZs) and lift. Add what-if copies
// the current one, so the next change is recorded as a new what-if. The baseline is the one the others are compared with.
function situation(t) {
  const d = t.areas.filter(a => a.kind !== "wez").length, w = t.areas.length - d;
  const parts = [d && `${d} area${d === 1 ? "" : "s"} of denial`, w && `${w} WEZ${w === 1 ? "" : "s"}`].filter(Boolean);
  if (!parts.length) parts.push("no areas");
  // Classes moved off their normal mode: "IV by air".
  for (const [c, m] of Object.entries(t.modes || {})) if (meta && m !== meta.lift.normal_mode[c]) parts.push(`${c} by ${m}`);
  // Port rates a what-if sets: "Subic Bay 4,000 st/d".
  for (const [p, rate] of Object.entries(t.port_rates || {})) parts.push(`${byId[p] ? byId[p].name : p} ${fmt(rate)} st/d`);
  // Lift that differs from the starting set: "-1 tanker", "+4 C-17".
  for (const [k, x] of liftTypes()) {
    const n = liftCount(t, k);
    if (n !== x.count) parts.push(`${n > x.count ? "+" : ""}${n - x.count} ${shortName(x.name)}`);
    for (const g of liftGroups(t, k)) {
      if (g.count && (g.at || g.ready)) parts.push(`${g.count} ${shortName(x.name)} ${g.at ? "at " + placeName(k, g.at) : "at the origin"}${g.ready ? `, D+${fmt(g.ready, g.ready % 1 ? 1 : 0)}` : ""}`);
    }
  }
  return parts.join(", ");
}

// ------------------------------------------------------ cargo: lift editor
// Each turn holds its own lift counts ({type: count}, only those changed from the starting set).
const liftTypes = () => meta ? [...Object.entries(meta.lift.ships), ...Object.entries(meta.lift.aircraft)] : [];
const shortName = name => { const m = name.match(/\((.*) class\)$/); return m ? m[1] : name.replace(/ \(.*\)$/, "").toLowerCase(); };
// A type's lift in a turn: absent means the starting set (one group at the origin, ready on D+0).
const liftGroups = (t, k) => t.lift[k] || [{ count: Object.fromEntries(liftTypes())[k].count, at: null, ready: 0 }];
const liftCount = (t, k) => liftGroups(t, k).reduce((n, g) => n + g.count, 0);
const liftBody = t => Object.fromEntries(liftTypes().map(([k]) => [k, liftGroups(t, k)]));
const placeName = (k, id) => !id ? "the origin" : k in meta.lift.ships ? byId[id].name : (fieldById[id] || {}).name || id;
let fieldById = {}, fieldByLabel = {}, fieldLabel = {};

function renderLiftLine() {
  const t = turns[cur];
  el("liftLine").textContent = "Lift: " + liftTypes().map(([k, x]) => `${liftCount(t, k)} ${shortName(x.name)}`).join(", ");
}

// What each type carries in the current result: loads or sorties, and whether the last ton waits on it.
function liftUse(k) {
  if (!cmp) return "";
  let used = null, binds = false;
  for (const r of cmp.rows) for (const c of Object.values(r.cells)) {
    if (!c.lift || c.lift.type !== k) continue;
    used = Math.max(used ?? 0, c.lift.kind === "ship" ? c.lift.loads : c.lift.sorties);
    if (c.limit && c.limit.type === k && ["ships", "no_ships", "aircraft", "no_aircraft"].includes(c.limit.kind)) binds = true;
  }
  if (used == null) return "Not used in this comparison.";
  const what = k in meta.lift.ships ? `${used} load${used === 1 ? "" : "s"}` : `${fmt(used)} sorties`;
  return `Carries <b>${what}</b>${binds ? " · <b>the last ton waits on these</b>" : ""}`;
}

function renderLift() {
  if (!meta) return;
  renderLiftLine();
  if (!document.body.classList.contains("lift-open")) return;
  const t = turns[cur];
  el("liftHead").textContent = `Lift on hand · ${turnName(cur)}`;
  for (const [box, group] of [["liftShips", meta.lift.ships], ["liftAir", meta.lift.aircraft]]) {
    el(box).innerHTML = "";
    for (const [k, x] of Object.entries(group)) {
      const n = liftCount(t, k), groups = liftGroups(t, k), div = document.createElement("div");
      const moved = groups.length > 1 || groups.some(g => g.at || g.ready);
      div.className = "lt" + (n !== x.count || moved ? " changed" : "");
      const info = x.capacity_st != null ? `${fmt(x.capacity_st)} short tons a load · carries ${x.classes.join(", ")}`
                                         : `${fmt(x.planning_payload_st)} short tons a sortie · ${fmt(x.ground_hours, 2)} h on the ground`;
      const list = k in meta.lift.ships ? "portNames" : "fieldNames", label = id => id ? (k in meta.lift.ships ? labelOf[id] : fieldLabel[id]) : "";
      div.innerHTML = `<div class="top"><span>${esc(x.name)}</span><span>${n} on hand</span></div>` +
        `<div class="via">${info}${n !== x.count ? ` · starting set ${x.count}` : ""}</div>` +
        groups.map((g, i) => `<div class="grp" data-i="${i}"><span class="stepper"><button aria-label="One fewer">−</button>` +
          `<input type="number" class="n" min="0" max="1000" step="1" value="${g.count}" aria-label="How many in this group"><button aria-label="One more">+</button></span>` +
          `<input class="at" list="${list}" value="${esc(label(g.at))}" placeholder="at the origin" aria-label="Where this group starts" spellcheck="false">` +
          `<span class="rd" title="The day this group is free to move">D+<input type="number" class="ready" min="0" max="90" step="0.5" value="${g.ready}" aria-label="Ready day"></span>` +
          (groups.length > 1 ? `<button class="rm" title="Remove this group" aria-label="Remove this group">×</button>` : "<span></span>") + `</div>`).join("") +
        `<button class="addgrp">+ Add a group</button><div class="use">${liftUse(k)}</div>`;
      // Any change stores this type's groups in the turn; back to the starting set when they match it again.
      const save = gs => {
        const same = gs.length === 1 && !gs[0].at && !gs[0].ready && gs[0].count === x.count;
        if (same) delete t.lift[k]; else t.lift[k] = gs;
        liftChanged(); renderLift();
      };
      const copy = () => liftGroups(t, k).map(g => ({ ...g }));
      div.querySelectorAll(".grp").forEach(row => {
        const i = +row.dataset.i, [minus, input, plus] = row.querySelectorAll(".stepper > *");
        const setN = v => { const gs = copy(); gs[i].count = Math.max(0, Math.min(1000, Math.round(v) || 0)); save(gs); };
        minus.onclick = () => setN(groups[i].count - 1);
        plus.onclick = () => setN(groups[i].count + 1);
        input.onchange = () => setN(+input.value);
        const at = row.querySelector(".at");
        at.onchange = () => {
          const v = at.value.trim(), id = !v ? null : k in meta.lift.ships ? byLabel[v] : fieldByLabel[v];
          if (v && !id) { at.setCustomValidity("Pick a place from the list"); at.reportValidity(); return; }
          at.setCustomValidity(""); const gs = copy(); gs[i].at = id; save(gs);
        };
        const rd = row.querySelector(".ready");
        rd.onchange = () => { const gs = copy(); gs[i].ready = Math.max(0, Math.min(90, +rd.value || 0)); save(gs); };
        const rm = row.querySelector(".rm");
        if (rm) rm.onclick = () => { const gs = copy(); gs.splice(i, 1); save(gs); };
      });
      div.querySelector(".addgrp").onclick = () => { const gs = copy(); gs.push({ count: 1, at: null, ready: 0 }); save(gs); };
      el(box).appendChild(div);
    }
  }
}

let liftTimer = null;
function liftChanged() {
  renderTurns(); drawMarks();
  clearTimeout(liftTimer); liftTimer = setTimeout(go, 250);
}

function openLift(open) {
  document.body.classList.toggle("lift-open", open);
  renderLift(); setView(view);
}

function renderTurns() {
  const box = el("turnList"); box.innerHTML = "";
  turns.forEach((t, i) => {
    const b = document.createElement("button");
    b.className = "turn" + (i === cur ? " on" : "");
    b.innerHTML = `${turnName(i)} · ${esc(situation(t))}${i === base ? '<span class="basetag">baseline</span>' : ""}` +
                  (turns.length > 1 ? `<span class="x" title="Remove this what-if">×</span>` : "");
    b.onclick = e => e.target.classList.contains("x") ? removeTurn(i) : selectTurn(i);
    box.appendChild(b);
  });
  el("setBase").style.display = cur === base ? "none" : "";
  renderLift(); renderModes();
}

function selectTurn(i) {
  if (i === cur) return;
  cur = i; areas = turns[i].areas; picked = null;
  setMode(null); drawAreas(); go();
}

function removeTurn(i) {
  turns.splice(i, 1);
  if (base === i) base = 0; else if (base > i) base--;
  if (cur === i) cur = Math.max(0, i - 1); else if (cur > i) cur--;
  areas = turns[cur].areas; picked = null;
  setMode(null); drawAreas(); go();
}
