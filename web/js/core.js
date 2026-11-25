// FALCON map, core.js: Constants, shared state and the map view (pan, zoom, fit).
// Loaded in order by index.html; all files share one global scope.

const W = 960, H = 480, NM_PER_RAD = 3440.069;
// Rotated so the Pacific sits in the middle; d3 handles lines that cross 180°.
const projection = d3.geoEquirectangular().rotate([-159, 0]).scale(W / (2 * Math.PI)).translate([W / 2, H / 2]);
const geoPath = d3.geoPath(projection);
const COLORS = ["#4ad4a0", "#e8b04c", "#c58bdd", "#4a9eda", "#f08a7c"];
const CARRIER = { commercial: "var(--commercial)", military: "var(--military)" };
const KIND = { denial: "var(--deny)", wez: "var(--wez)" };
const PLAN = { commercial: "Commercial the whole way", commercial_military: "Commercial, then military",
               military_commercial: "Military, then commercial", military: "Military the whole way", blocked: "Blocked" };
const planText = p => PLAN[p] || p.split("_").map((c, i) => i ? c : c[0].toUpperCase() + c.slice(1)).join(", then ");
const MODE_TEXT = { sea: "sea", air: "air", air_sea: "air, then sea", sea_air: "sea, then air" };
const HINTS = {
  yen: "Exact next-shortest routes. On a mesh of shipping lanes these are often the same route shifted slightly, so lines can overlap.",
  distinct: "The shortest route through each separate corridor: alternatives at least 10% of the route length apart (50 nm minimum).",
};
const DRAW_HINTS = {
  circle: "Press on the map and drag out the circle. Esc cancels.",
  polygon: "Click to add corners. Double-click, press Enter or click the first corner to finish. Backspace removes the last corner; Esc cancels.",
};

const el = id => document.getElementById(id);
const svg = el("svg");
const svgEl = (tag, attrs) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  return n;
};
const fmt = (n, d = 0) => n.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
const esc = t => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const legKey = l => `${l.mode}|${l.carrier}|${l.from}|${l.to}`;

let byLabel = {}, byId = {}, view = { x: 0, y: 0, w: W, h: H };
let currentFrom = null, currentTo = null, last = null;
let areas = [];           // [{kind: "denial" | "wez", type: "circle", center, radius_nm} | {kind, type: "polygon", points}]
let mode = null, draft = null;          // drawing mode
let appMode = "routes", drawKind = "denial", meta = null, tons = {}, labelOf = {};
let opts = null, routeView = "sea";     // Routes: the transport options (by sea, by air), and which one the map shows
// Cargo: turns change the situation (areas); the move (ports, cargo, arrive-by) stays the same across turns.
// `areas` is always the current turn's own list. cmp is the comparison for the current turn (and its baseline).
let turns = [{ areas, lift: {}, modes: {} }], cur = 0, base = 0, lens = "plan", due = 7, cmp = null, picked = null, hoverRow = null;
const denialAreas = () => areas.filter(a => a.kind !== "wez");
const wezAreas = () => areas.filter(a => a.kind === "wez");
const plain = list => list.map(({ kind, ...a }) => a);

// ------------------------------------------------------------------ view
function setView(v) {
  // Match the svg's on-screen shape so the mapping from screen to map stays linear.
  const r = svg.getBoundingClientRect(), ar = (r.width || W) / (r.height || H);
  let { x, y, w, h } = v;
  if (w / h < ar) { const nw = h * ar; x -= (nw - w) / 2; w = nw; }
  else { const nh = w / ar; y -= (nh - h) / 2; h = nh; }
  view = { x, y, w, h };
  svg.setAttribute("viewBox", `${x} ${y} ${w} ${h}`);
  const o = el("ocean");
  o.setAttribute("x", x - w); o.setAttribute("y", y - h); o.setAttribute("width", w * 3); o.setAttribute("height", h * 3);
  drawMarks(); drawDraft(); clearHover();
}
const scale = () => view.w / W;          // map units per screen-sized unit at this zoom

function toMap(e) {
  const r = svg.getBoundingClientRect();
  return [view.x + (e.clientX - r.left) / r.width * view.w, view.y + (e.clientY - r.top) / r.height * view.h];
}
const toLonLat = e => projection.invert(toMap(e));
const nmBetween = (a, b) => d3.geoDistance(a, b) * NM_PER_RAD;

function fitRoutes() {
  const feats = [];
  if (appMode === "cargo") { if (cmp) for (const l of Object.values(cmp.legs)) feats.push(l.line); }
  else if (routeView === "air" && opts && opts.air.available) for (const l of opts.air.legs) feats.push(l.line);
  else if (last) {
    if (last.original) feats.push(last.original.line);
    for (const r of last.routes) feats.push(r.line);
  }
  if (!feats.length) return setView({ x: 0, y: 0, w: W, h: H });
  const [[x0, y0], [x1, y1]] = geoPath.bounds({ type: "MultiLineString", coordinates: feats });
  const pad = Math.max(x1 - x0, y1 - y0) * 0.12 + 4;
  setView({ x: x0 - pad, y: y0 - pad, w: x1 - x0 + 2 * pad, h: y1 - y0 + 2 * pad });
}
