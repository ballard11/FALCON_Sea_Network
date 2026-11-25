// FALCON map, search.js: Ranked port search for the origin and destination boxes.
// Loaded in order by index.html; all files share one global scope.

// ----------------------------------------------------------- port search
// The origin and destination boxes suggest ports as you type, best first: the exact name, then the
// ports of a country typed in full ("guam"), then names that start with what you typed, then a word in the name that does, then names containing it, then
// names and countries that hold every word typed ("busan korea"). Bigger harbors win ties. Top 8 shown.
// Arrow keys move, Enter picks and finds routes, Esc closes.
const SIZE_RANK = { Large: 0, Medium: 1, Small: 2, "Very Small": 3 };
const norm = s => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

function searchPorts(text) {
  const q = norm(text.trim()), words = q.split(/[^a-z0-9]+/).filter(Boolean);
  if (!words.length) return [];
  const hits = [];
  for (const p of portList) {
    const tier = p._n === q ? 0 : p._c === q ? 1 : p._n.startsWith(q) ? 2 : p._nw.some(w => w.startsWith(q)) ? 3
               : p._n.includes(q) ? 4 : words.every(t => p._w.some(w => w.startsWith(t))) ? 5 : -1;
    if (tier >= 0) hits.push([tier, SIZE_RANK[p.size] ?? 4, p.name.length, p]);
  }
  hits.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2] || a[3].name.localeCompare(b[3].name));
  return hits.slice(0, 8).map(h => h[3]);
}

function marked(p, text) {          // the name, with the part that matched what was typed in bold
  const q = norm(text.trim()), i = p._n.indexOf(q);
  if (!q || i < 0 || p._n.length !== p.name.length) return esc(p.name);
  return esc(p.name.slice(0, i)) + `<mark>${esc(p.name.slice(i, i + q.length))}</mark>` + esc(p.name.slice(i + q.length));
}

function attachSearch(input) {
  const list = document.createElement("ul");
  list.className = "suggest"; list.id = input.id + "-list"; list.hidden = true;
  list.setAttribute("role", "listbox");
  input.parentElement.appendChild(list);
  input.setAttribute("role", "combobox"); input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", list.id); input.setAttribute("aria-expanded", "false");
  let items = [], active = -1;
  const close = () => {
    list.hidden = true; items = []; active = -1;
    input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant");
  };
  const mark = i => {
    active = i;
    [...list.children].forEach((li, j) => li.classList.toggle("on", j === i));
    if (i >= 0) { input.setAttribute("aria-activedescendant", `${list.id}-${i}`); list.children[i].scrollIntoView({ block: "nearest" }); }
  };
  const pick = p => { input.value = labelOf[p.id]; close(); go(true); };
  const render = () => {
    const text = input.value;
    if (!text.trim()) return close();
    items = searchPorts(text); list.innerHTML = "";
    if (!items.length) list.innerHTML = `<li class="none">No port matches “${esc(text.trim())}”</li>`;
    items.forEach((p, i) => {
      const li = document.createElement("li");
      li.id = `${list.id}-${i}`; li.setAttribute("role", "option");
      const extra = p._tag ? ` · ${esc(p._tag)}` : "";
      li.innerHTML = `<div class="n">${marked(p, text)}${p.country ? ", " + esc(p.country) : ""}</div>` +
                     `<div class="m">Port${p.size ? ` · ${esc(p.size)} harbor` : ""}${extra}</div>`;
      li.addEventListener("pointerdown", e => { e.preventDefault(); pick(p); });   // before the box loses focus
      li.addEventListener("pointermove", () => active !== i && mark(i));
      list.appendChild(li);
    });
    list.hidden = false; input.setAttribute("aria-expanded", "true");
    mark(items.length ? 0 : -1);
  };
  input.addEventListener("input", render);
  input.addEventListener("focus", () => input.select());          // start typing to replace the port
  input.addEventListener("blur", close);
  input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (list.hidden) return render();
      if (items.length) mark((active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!list.hidden && active >= 0) return pick(items[active]);
      close(); go(true);
    } else if (e.key === "Escape") close();
  });
}
