"use strict";

const state = {
  meta: null,
  items: [],
  boxes: [],
  forces: [],
  sort: { key: null, dir: 1 },
  view: "table",
  compact: false,
  collapsed: new Set(),
  tray: { units: new Map(), forceId: null },
};

const $ = (id) => document.getElementById(id);

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

let toastTimer = null;
function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 7000 : 3500);
}

// In-page replacement for window.confirm. Resolves true on confirm, false on cancel, Escape or a click outside.
// Shows `box` over a dimmed backdrop. Escape and a click outside close it, Tab cycles through its buttons,
// and focus returns to where it was. Returns { close(value) }; onClose receives the value.
function openModal(box, onClose) {
  const previous = document.activeElement;
  const backdrop = h("div", { class: "modal-backdrop" }, box);
  let closed = false;
  const close = (value) => {
    if (closed) return;
    closed = true;
    document.removeEventListener("keydown", onKey, true);
    backdrop.remove();
    if (previous && previous.isConnected) previous.focus();
    if (onClose) onClose(value);
  };
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(false); }
    else if (e.key === "Tab") {
      const stops = [...box.querySelectorAll("button")];
      if (!stops.length) return;
      e.preventDefault();
      const i = stops.indexOf(document.activeElement);
      const next = e.shiftKey ? (i <= 0 ? stops.length - 1 : i - 1) : (i + 1) % stops.length;
      stops[next].focus();
    }
  };
  backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) close(false); });
  document.addEventListener("keydown", onKey, true);
  document.body.append(backdrop);
  return { close };
}

function confirmDialog(message, { confirmLabel = "OK", danger = false } = {}) {
  return new Promise((resolve) => {
    const cancel = h("button", { type: "button", onclick: () => modal.close(false) }, "Cancel");
    const ok = h("button", { type: "button", class: danger ? "primary danger-fill" : "primary", onclick: () => modal.close(true) }, confirmLabel);
    const box = h("div", { class: "modal", role: "alertdialog", "aria-modal": "true", "aria-label": message },
      h("p", { class: "modal-text" }, message),
      h("div", { class: "modal-actions" }, cancel, ok));
    const modal = openModal(box, resolve);
    (danger ? cancel : ok).focus();
  });
}

// ---------- unit detail panel ----------

const LORE_TITLES = [["overview", "Overview"], ["capabilities", "Capabilities"], ["deployment", "Deployment"], ["history", "History"], ["notes", "Notes"]];

function stat(label, value) {
  if (value == null || value === "") return null;
  return h("div", { class: "stat" }, h("span", { class: "k" }, label), h("span", { class: "v" }, value));
}

function detailSection(title, ...children) {
  const kids = children.flat().filter(Boolean);
  if (!kids.length) return null;
  return h("section", { class: "detail-section" }, h("h3", {}, title), kids);
}

function weaponTable(weapons) {
  if (!weapons || !weapons.length) return null;
  return h("table", { class: "weapons" },
    h("thead", {}, h("tr", {}, ["Weapon", "Loc", "Qty", "Damage", "Range"].map((t) => h("th", {}, t)))),
    h("tbody", {}, weapons.map((w) => h("tr", {},
      h("td", {}, w.name), h("td", {}, w.location || ""), h("td", {}, w.qty), h("td", {}, w.damage || ""), h("td", {}, w.range || "")))));
}

function buildDetail(unit, close) {
  const s = unit.specs || {};
  const mv = s.movement || {};
  const heat = s.heat || {};
  const armor = s.armor || {};
  const structure = s.structure || {};
  const engine = s.engine || {};
  const lore = unit.lore || {};
  const moves = [["Walk", mv.walk], ["Run", mv.run], ["Jump", mv.jump]].filter(([, v]) => v != null).map(([k, v]) => `${k} ${v}`).join(" / ");
  const summary = [kindLabel(unit), unit.tons != null ? `${unit.tons}t` : null, `${unit.bv} BV`, unit.year, unit.tech_base, unit.role].filter(Boolean).join(" · ");
  const loreSections = LORE_TITLES.filter(([key]) => lore[key]).map(([key, title]) =>
    detailSection(title, lore[key].map((p) => h("p", {}, p))));
  const credits = [lore.manufacturer && `Manufacturer: ${lore.manufacturer}`, lore.primaryFactory && `Primary factory: ${lore.primaryFactory}`].filter(Boolean);
  const closeButton = h("button", { type: "button", class: "icon detail-close", "aria-label": "Close", onclick: () => close() }, "Close");
  return h("div", { class: "modal detail", role: "dialog", "aria-modal": "true", "aria-label": `${unit.name} details` },
    h("div", { class: "detail-head" },
      h("div", {}, h("h2", {}, unit.name), h("p", { class: "muted small" }, summary)),
      closeButton),
    h("div", { class: "detail-top" },
      h("div", { class: "photo" + (unit.has_art ? " has-art" : "") }, artImage({ has_art: unit.has_art, unit_uuid: unit.uuid })),
      unit.has_details
        ? h("div", { class: "stats" },
            stat("Movement", moves), stat("Move type", mv.type),
            stat("Armor", armor.points != null ? `${armor.points}${armor.type ? ` (${armor.type})` : ""}` : null),
            stat("Structure", structure.type), stat("Internal", structure.internal),
            stat("Engine", engine.type ? `${engine.type}${engine.rating ? ` ${engine.rating}` : ""}` : null),
            stat("Heat", heat.generated != null ? `${heat.generated} generated, ${heat.dissipation ?? "?"} dissipated` : null),
            stat("Tech", [s.level, s.tech_rating].filter(Boolean).join(" · ")),
            stat("Crew", s.crew), stat("Cost", s.cost != null ? `${s.cost.toLocaleString()} C-bills` : null))
        : h("p", { class: "muted" }, "No detail data yet. Run python3 -m bt catalog import once to load it.")),
    h("div", { class: "detail-body" },
      h("div", {},
        unit.has_details ? detailSection("Weapons", weaponTable(s.weapons)) : null,
        unit.has_details && s.quirks && s.quirks.length ? detailSection("Quirks", h("p", {}, s.quirks.join(", "))) : null),
      h("div", {},
        loreSections,
        unit.has_details && !loreSections.length ? h("p", { class: "muted small" }, "No lore text for this unit. The lore file may not have been imported.") : null)),
    credits.length ? h("p", { class: "muted small" }, credits.join(" · ")) : null,
    h("p", { class: "muted small credit" }, "Unit data and text via MekBay and the MegaMek team."));
}

async function showUnitDetail(uuid) {
  await guarded(async () => {
    const unit = await api("GET", `/api/units/${encodeURIComponent(uuid)}`);
    const modal = openModal(buildDetail(unit, () => modal.close()));
    document.querySelector(".detail-close").focus();
  });
}

function nameLink(item) {
  return h("button", { type: "button", class: "unit-link", title: "Show unit details", onclick: () => showUnitDetail(item.unit_uuid) }, item.name);
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

async function guarded(fn) {
  try {
    return await fn();
  } catch (err) {
    toast(err.message, true);
  }
}

// ---------- filters ----------

function currentFilters() {
  return {
    q: $("filter-q").value.trim(),
    kind: $("filter-kind").value,
    era: $("filter-era").value,
    faction: $("filter-faction").value,
    box: $("filter-box").value.trim(),
    painted_only: $("filter-painted").checked,
  };
}

function filterQuery() {
  const f = currentFilters();
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.kind) p.set("kind", f.kind);
  if (f.era) p.set("era", f.era);
  if (f.faction) p.set("faction", f.faction);
  if (f.box) p.set("box", f.box);
  if (f.painted_only) p.set("painted_only", "1");
  return p.toString();
}

async function loadCollection() {
  await guarded(async () => {
    state.items = await api("GET", "/api/collection?" + filterQuery());
    renderRoster();
    renderTray();
    await loadBoxes();
  });
}

async function loadBoxes() {
  state.boxes = await api("GET", "/api/boxes");
}

// ---------- suggestions ----------
// One shared dropdown, attached by event delegation to every input with a data-suggest source.
//   box:      your own box labels plus official boxes; picking fills the field.
//   addbox:   the same list in the Add units panel; picking an official box adds all its minis.
//   unit:     a catalog search; picking adds one copy of that unit to the collection.
//   pickunit: a catalog search inside the box review panel; picking selects that unit for a mini.

// `pending` is true while the list on screen is older than what was typed, so keys ignore it.
const suggest = { input: null, source: null, items: [], index: -1, token: 0, picking: false, pending: false };

function fillInput(input, value) {
  input.value = value;
  suggest.picking = true;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  suggest.picking = false;
}

function boxSource(addsOnPick) {
  return {
    minChars: 0,
    delay: 150,
    focusShowsAll: true, // clicking into a filled field lists your boxes so the label can be changed
    query: async (term) => {
      const t = term.toLowerCase();
      const own = state.boxes
        .filter((b) => !t || b.box.toLowerCase().includes(t))
        .slice(0, 4)
        .map((b) => ({ type: "own", name: b.box, entries: b.entries }));
      let official = [];
      if (t && state.meta.box_catalog) {
        const found = await api("GET", "/api/box-catalog?limit=8&q=" + encodeURIComponent(term));
        official = found.map((b) => ({ type: "official", ...b }));
      }
      const officialNames = new Set(official.map((b) => b.name.toLowerCase()));
      const items = [...own.filter((o) => !officialNames.has(o.name.toLowerCase())), ...official];
      const onlyExactOwn = items.length === 1 && items[0].type === "own" && items[0].name.toLowerCase() === t;
      return onlyExactOwn ? [] : items;
    },
    label: (b) => b.type === "own"
      ? [b.name, String(b.entries)]
      : [b.name, `${b.kind === "core" ? "Box set" : "ForcePack"} · ${b.minis} minis`],
    pick: (input, b) => {
      if (b.type === "official" && addsOnPick) {
        input.value = "";
        addOfficialBox(b);
      } else {
        fillInput(input, b.name);
      }
    },
  };
}

const UNIT_QUERY = (term) => api("GET", "/api/catalog/search?limit=12&q=" + encodeURIComponent(term));
const UNIT_LABEL = (u) => [u.name, `${u.kind} · ${u.tons ?? "?"}t · ${u.bv} BV`];

const SOURCES = {
  box: boxSource(false),
  addbox: boxSource(true),
  unit: {
    minChars: 2,
    delay: 150,
    emptyText: "No catalog units match.",
    pickSole: true,
    query: UNIT_QUERY,
    label: UNIT_LABEL,
    pick: async (input, u) => {
      input.value = "";
      await addUnit(u, $("add-box").value.trim());
    },
  },
  pickunit: {
    minChars: 2,
    delay: 150,
    emptyText: "No catalog units match.",
    query: UNIT_QUERY,
    label: UNIT_LABEL,
    pick: (input, u) => {
      const select = input.closest(".review-row").querySelector("select");
      if (![...select.options].some((o) => o.value === u.uuid)) {
        select.append(h("option", { value: u.uuid }, `${u.name} · ${u.bv} BV`));
      }
      select.value = u.uuid;
      input.value = "";
    },
  },
};

function closeSuggest() {
  suggest.token++; // drop any search still in flight
  suggest.pending = false;
  $("suggest").hidden = true;
  suggest.input = null;
  suggest.source = null;
  suggest.items = [];
  suggest.index = -1;
}

function paintSuggest(emptyMessage) {
  const el = $("suggest");
  const input = suggest.input;
  if (!input) { el.hidden = true; return; }
  if (!suggest.items.length) {
    if (!emptyMessage) { el.hidden = true; return; }
    el.replaceChildren(h("div", { class: "suggest-empty muted" }, emptyMessage));
  } else {
    el.replaceChildren(
      ...suggest.items.map((item, i) => {
        const [main, side] = suggest.source.label(item);
        return h("div", {
          class: "suggest-item" + (i === suggest.index ? " active" : ""),
          role: "option",
          "aria-selected": i === suggest.index ? "true" : "false",
          // mousedown, not click, so the input keeps focus and the list does not close first
          onmousedown: (e) => { e.preventDefault(); pickSuggest(i); },
        }, h("span", {}, main), h("span", { class: "muted small" }, side));
      }),
    );
  }
  el.hidden = false;
  placeSuggest();
}

// Keep the list attached to its input: under it, or above it when there is no room below.
function placeSuggest() {
  const el = $("suggest");
  const input = suggest.input;
  if (!input || el.hidden) return;
  const rect = input.getBoundingClientRect();
  if (rect.bottom < 0 || rect.top > window.innerHeight) { closeSuggest(); return; } // scrolled out of view
  const roomBelow = window.innerHeight - rect.bottom - 8;
  const flip = el.offsetHeight > roomBelow && rect.top > roomBelow;
  Object.assign(el.style, {
    left: `${rect.left}px`,
    top: flip ? `${Math.max(8, rect.top - el.offsetHeight - 2)}px` : `${rect.bottom + 2}px`,
    minWidth: `${rect.width}px`,
  });
}

async function refreshSuggest(fromFocus = false) {
  const input = suggest.input;
  if (!input) return;
  const source = SOURCES[input.dataset.suggest];
  const term = fromFocus && source.focusShowsAll ? "" : input.value.trim();
  suggest.source = source;
  suggest.index = -1;
  const token = ++suggest.token;
  if (term.length < source.minChars) { suggest.items = []; suggest.pending = false; paintSuggest(); return; }
  suggest.pending = true;
  if (source.delay && term) await new Promise((resolve) => setTimeout(resolve, source.delay));
  if (token !== suggest.token) return;
  let items;
  try {
    items = await source.query(term);
  } catch (err) {
    if (token === suggest.token) { suggest.pending = false; toast(err.message, true); }
    return;
  }
  if (token !== suggest.token) return; // a newer keystroke or a close superseded this search
  suggest.items = items;
  suggest.pending = false;
  paintSuggest(items.length ? null : source.emptyText);
}

function pickSuggest(i) {
  const { input, source } = suggest;
  const item = suggest.items[i];
  if (!input || !item) return;
  closeSuggest();
  source.pick(input, item);
}

function suggestInput(el) {
  return el instanceof HTMLElement && el.matches("[data-suggest]") && SOURCES[el.dataset.suggest] ? el : null;
}

function setupSuggest() {
  const open = (e) => {
    const input = suggestInput(e.target);
    if (suggest.picking || !input) return;
    suggest.input = input;
    refreshSuggest(e.type === "focusin");
  };
  document.addEventListener("focusin", open);
  document.addEventListener("input", open);
  document.addEventListener("focusout", (e) => {
    if (e.target === suggest.input) closeSuggest();
  });
  document.addEventListener("keydown", (e) => {
    if (!suggest.input || $("suggest").hidden || suggest.pending) return;
    const n = suggest.items.length;
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && n) {
      e.preventDefault();
      suggest.index = e.key === "ArrowDown" ? (suggest.index + 1) % n : (suggest.index - 1 + n) % n;
      paintSuggest();
      $("suggest").querySelector(".active")?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && (suggest.index >= 0 || (suggest.source.pickSole && n === 1))) {
      e.preventDefault();
      pickSuggest(suggest.index >= 0 ? suggest.index : 0);
    } else if (e.key === "Escape") {
      closeSuggest();
    }
  });
  // Scrolling or resizing moves the input, so the list follows it. Scrolling the list itself is ignored.
  window.addEventListener("scroll", (e) => {
    if (!$("suggest").contains(e.target)) placeSuggest();
  }, true);
  window.addEventListener("resize", placeSuggest);
}

// ---------- roster ----------

function sortedItems() {
  const { key, dir } = state.sort;
  if (!key) return state.items;
  return [...state.items].sort((a, b) => {
    const x = a[key], y = b[key];
    return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
  });
}

// ---------- roster views ----------
// Three views of the same filtered collection: a table, the table grouped by box, and a card grid.
// The view, the density and which groups are collapsed are remembered between visits.

const VIEWS = ["table", "grouped", "cards"];
const PREFS_KEY = "bt.view";
const SORT_CHOICES = {
  "": null,
  name: { key: "name", dir: 1 },
  kind: { key: "kind", dir: 1 },
  bv: { key: "bv", dir: -1 },
  tons: { key: "tons", dir: -1 },
};

function loadPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
    if (VIEWS.includes(saved.view)) state.view = saved.view;
    state.compact = Boolean(saved.compact);
    if (Array.isArray(saved.collapsed)) state.collapsed = new Set(saved.collapsed.map(String));
  } catch {
    // Storage can be unavailable or hold something unexpected. The defaults work fine.
  }
}

function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      view: state.view, compact: state.compact, collapsed: [...state.collapsed],
    }));
  } catch {
    // Not being able to remember the view is not worth an error message.
  }
}

function kindLabel(item) {
  return (state.meta.kinds.find((k) => k.key === item.kind) || {}).label || item.kind;
}

function emptyMessage() {
  if (state.items.length) return "";
  return Object.values(currentFilters()).some(Boolean)
    ? "Nothing in your collection matches these filters."
    : "Your collection is empty. Use Add units to search the catalog, or Import to paste a list.";
}

function buildRow(item) {
  const qty = h("input", {
    type: "number", min: 0, value: item.qty, "aria-label": `Quantity of ${item.name}`,
    onchange: (e) => updateEntry(item.id, { qty: Number(e.target.value) }),
  });
  const painted = h("input", {
    type: "number", min: 0, max: item.qty, value: item.painted, "aria-label": `Painted ${item.name}`,
    onchange: (e) => updateEntry(item.id, { painted: Number(e.target.value) }),
  });
  const nameCell = h("td", {}, nameLink(item));
  return h("tr", {},
    nameCell,
    h("td", {}, kindLabel(item)),
    h("td", {}, h("input", {
      type: "text", class: "box-input", value: item.box, "data-suggest": "box",
      autocomplete: "off", placeholder: "none", "aria-label": `Box for ${item.name}`,
      onchange: (e) => updateEntry(item.id, { box: e.target.value }),
    })),
    h("td", { class: "num" }, item.tons == null ? "" : Number(item.tons).toString()),
    h("td", { class: "num" }, item.bv),
    h("td", { class: "num" }, qty),
    h("td", { class: "num" }, painted),
    h("td", { class: "actions" },
      h("button", { class: "icon", title: "Add one to the force tray", onclick: () => trayAdd(item.id, 1) }, "+ tray"),
      " ",
      h("button", { class: "icon", title: "Switch this mini to another variant", onclick: () => startSwap(item, nameCell) }, "Variant"),
      " ",
      h("button", {
        class: "icon danger", title: "Remove from collection", "aria-label": `Remove ${item.name}`,
        onclick: () => removeEntry(item),
      }, "×")),
  );
}

// Boxes in name order, with entries that have no box last.
function groupsOf(items) {
  const map = new Map();
  for (const item of items) {
    const key = item.box || "No box";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(item);
  }
  return [...map.entries()].sort(([a], [b]) => (a === "No box") - (b === "No box") || a.localeCompare(b));
}

function totals(list) {
  return {
    models: list.reduce((n, i) => n + i.qty, 0),
    painted: list.reduce((n, i) => n + Math.min(i.painted, i.qty), 0),
    bv: list.reduce((n, i) => n + i.bv * i.qty, 0),
  };
}

function groupHeader(name, list, collapsed) {
  const t = totals(list);
  const pct = t.models ? Math.round((100 * t.painted) / t.models) : 0;
  const toggle = () => {
    if (collapsed) state.collapsed.delete(name); else state.collapsed.add(name);
    savePrefs();
    renderRoster();
  };
  return h("tr", { class: "group-row" },
    h("td", { colspan: 8 },
      h("button", { class: "group-toggle", "aria-expanded": String(!collapsed), onclick: toggle },
        h("span", { class: "chev", "aria-hidden": "true" }, collapsed ? "▸" : "▾"), " ",
        h("strong", {}, name), " ",
        h("span", { class: "group-meta" }, `${list.length} entries · ${t.models} models · ${t.bv.toLocaleString()} BV`), " ",
        h("span", { class: "bar", "aria-hidden": "true", title: `${t.painted} of ${t.models} painted` },
          h("i", { style: `width:${pct}%` })), " ",
        h("span", { class: "group-meta" }, `${t.painted}/${t.models} painted`))));
}

// The default picture is the unit's artwork. A unit with no art, or art that fails to load, gets a placeholder.
function artImage(item) {
  const placeholder = () => h("span", { class: "no-art" }, "No art yet");
  if (!item.has_art) return placeholder();
  return h("img", {
    src: `/api/art/${encodeURIComponent(item.unit_uuid)}`, loading: "lazy", alt: "", // the name sits right beside it
    onerror: (e) => {
      e.target.parentElement?.classList.remove("has-art");
      e.target.replaceWith(placeholder());
    },
  });
}

function buildCard(item) {
  const nameEl = h("div", { class: "card-name" }, nameLink(item));
  const allPainted = item.qty > 0 && item.painted >= item.qty;
  return h("article", { class: "mini-card" + (allPainted ? " is-painted" : "") },
    // Mouse shortcut to the details. Keyboard users get the same thing from the name button.
    h("div", { class: "photo clickable" + (item.has_art ? " has-art" : ""), onclick: () => showUnitDetail(item.unit_uuid) }, artImage(item)),
    h("div", { class: "card-body" },
      nameEl,
      h("div", { class: "card-meta" }, `${kindLabel(item)} · ${item.tons ?? "?"}t · ${item.bv} BV`),
      h("div", { class: "card-foot" }, h("span", {}, `×${item.qty}`), h("span", {}, `${item.painted}/${item.qty} painted`)),
      item.box ? h("div", { class: "card-box" }, item.box) : null,
      h("div", { class: "card-actions" },
        h("button", { class: "icon", title: "Add one to the force tray", onclick: () => trayAdd(item.id, 1) }, "+ tray"),
        h("button", { class: "icon", title: "Switch this mini to another variant", onclick: () => startSwap(item, nameEl) }, "Variant"))));
}

function cardGrid() {
  let grid = $("card-grid");
  if (!grid) {
    grid = h("div", { id: "card-grid", class: "card-grid" });
    document.querySelector(".table-wrap").after(grid);
  }
  return grid;
}

function renderTable(items) {
  $("card-grid")?.remove();
  const body = document.querySelector("#roster tbody");
  body.replaceChildren();
  if (state.view === "grouped") {
    for (const [name, list] of groupsOf(items)) {
      const collapsed = state.collapsed.has(name);
      body.append(groupHeader(name, list, collapsed));
      if (!collapsed) body.append(...list.map(buildRow));
    }
  } else {
    body.append(...items.map(buildRow));
  }
  const message = emptyMessage();
  $("roster-empty").hidden = !message;
  $("roster-empty").textContent = message;
}

function renderCards(items) {
  const message = emptyMessage();
  cardGrid().replaceChildren(...(message ? [h("p", { class: "muted empty" }, message)] : items.map(buildCard)));
}

function syncViewControls() {
  const bar = $("view-controls");
  for (const button of bar.querySelectorAll("button[data-view]")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === state.view));
  }
  for (const button of bar.querySelectorAll("button[data-compact]")) {
    button.setAttribute("aria-pressed", String((button.dataset.compact === "1") === state.compact));
  }
  $("density-group").hidden = state.view === "cards";
  $("group-actions").hidden = state.view !== "grouped";
  $("sort-group").hidden = state.view !== "cards";
  const current = Object.entries(SORT_CHOICES).find(([, v]) => v && v.key === state.sort.key);
  $("card-sort").value = current ? current[0] : "";
  const models = state.items.reduce((n, i) => n + i.qty, 0);
  $("view-count").textContent = `${state.items.length} entries · ${models} models`;
}

function renderRoster() {
  document.body.classList.toggle("view-cards", state.view === "cards");
  document.body.classList.toggle("view-grouped", state.view === "grouped");
  document.body.classList.toggle("compact", state.compact && state.view !== "cards");
  const items = sortedItems();
  if (state.view === "cards") renderCards(items); else renderTable(items);
  syncViewControls();
}

function setupViewControls() {
  const bar = $("view-controls");
  for (const button of bar.querySelectorAll("button[data-view]")) {
    button.addEventListener("click", () => { state.view = button.dataset.view; savePrefs(); renderRoster(); });
  }
  for (const button of bar.querySelectorAll("button[data-compact]")) {
    button.addEventListener("click", () => { state.compact = button.dataset.compact === "1"; savePrefs(); renderRoster(); });
  }
  $("collapse-all").addEventListener("click", () => {
    state.collapsed = new Set(groupsOf(state.items).map(([name]) => name));
    savePrefs();
    renderRoster();
  });
  $("expand-all").addEventListener("click", () => { state.collapsed = new Set(); savePrefs(); renderRoster(); });
  $("card-sort").addEventListener("change", (e) => {
    state.sort = SORT_CHOICES[e.target.value] || { key: null, dir: 1 };
    renderRoster();
  });
}

async function updateEntry(id, patch) {
  await guarded(async () => {
    await api("PATCH", `/api/collection/${id}`, patch);
    await loadCollection();
    await loadForces();
  });
}

// Switch a mini to another variant of the same chassis. A plastic mini can stand for any variant.
async function startSwap(item, cell) {
  await guarded(async () => {
    const variants = await api("GET", `/api/collection/${item.id}/variants`);
    if (variants.length < 2) { toast(`No other variants of ${item.chassis} in the catalog`); return; }
    const option = (v) => h("option", { value: v.uuid, selected: v.current }, `${v.name}  ·  ${v.bv} BV`);
    const onCard = variants.filter((v) => v.on_card);
    const others = variants.filter((v) => !v.on_card);
    // Variants the mini's official box has a card for go first, in their own group.
    const choices = onCard.length
      ? [h("optgroup", { label: "On your box's card" }, onCard.map(option)),
         others.length ? h("optgroup", { label: "Other variants" }, others.map(option)) : null]
      : variants.map(option);
    const select = h("select", { "aria-label": `Variant of ${item.chassis}` }, choices);
    select.addEventListener("change", () => swapVariant(item, variants.find((v) => v.uuid === select.value)));
    select.addEventListener("blur", renderRoster);
    select.addEventListener("keydown", (e) => { if (e.key === "Escape") renderRoster(); });
    cell.replaceChildren(select);
    select.focus();
  });
}

async function swapVariant(item, variant) {
  const owned = state.items.find((i) => i.unit_uuid === variant.uuid && i.id !== item.id);
  if (owned && !(await confirmDialog(`You already own ${variant.name}. Merge these into it?`, { confirmLabel: "Merge" }))) { renderRoster(); return; }
  await guarded(async () => {
    await api("POST", `/api/collection/${item.id}/swap`, { unit_uuid: variant.uuid });
    toast(`Switched to ${variant.name}`);
    state.tray.units.delete(item.id);
    await loadCollection();
    await loadForces();
  });
}

async function removeEntry(item) {
  if (!(await confirmDialog(`Remove ${item.name} from your collection? It will also leave any saved forces.`, { confirmLabel: "Remove", danger: true }))) return;
  await guarded(async () => {
    await api("DELETE", `/api/collection/${item.id}`);
    state.tray.units.delete(item.id);
    await loadCollection();
    await loadForces();
  });
}

// ---------- adding units and boxes ----------

async function addOfficialBox(box) {
  const already = state.boxes.some((b) => b.box.toLowerCase() === box.name.toLowerCase());
  if (already && !(await confirmDialog(`You already added minis from "${box.name}". Add them again? Quantities will go up.`, { confirmLabel: "Add again" }))) return;
  await guarded(async () => {
    const result = await api("POST", `/api/box-catalog/${box.id}/add`, {});
    const n = result.added.length;
    const need = result.review.length;
    toast(`Added ${n} mini${n === 1 ? "" : "s"} from ${result.box.name}` + (need ? `. ${need} need a choice.` : ""));
    $("add-box").value = "";
    await loadCollection();
    if (need) showReview(result);
  });
}

function reviewRow(r) {
  const select = h("select", { "aria-label": `Variant for ${r.mini_name}` },
    h("option", { value: "" }, "Skip this mini"),
    r.candidates.map((c) => h("option", { value: c.uuid }, `${c.name} · ${c.bv} BV`)));
  return h("div", { class: "review-row", "data-item": r.item_id },
    h("div", { class: "review-name" },
      h("strong", {}, `${r.qty > 1 ? r.qty + "x " : ""}${r.mini_name}`),
      r.card_variant ? h("span", { class: "muted small" }, `  card: ${r.card_variant}`) : null,
      r.note ? h("span", { class: "muted small" }, `  (${r.note})`) : null),
    select,
    h("input", {
      type: "search", "data-suggest": "pickunit", autocomplete: "off",
      placeholder: "or search the catalog", "aria-label": `Search a unit for ${r.mini_name}`,
    }));
}

function showReview(result) {
  const panel = $("review-panel");
  panel.replaceChildren(
    h("h2", {}, `Choose variants for ${result.box.name}`),
    h("p", { class: "muted small" },
      "These minis did not match a single unit on their own. Pick a variant, or leave Skip. Source: ",
      h("a", { href: result.box.source_url, target: "_blank", rel: "noopener" }, "BattleTechWiki")),
    ...result.review.map(reviewRow),
    h("div", { class: "row" },
      h("button", { onclick: () => { panel.hidden = true; } }, "Cancel"),
      h("button", { class: "primary", onclick: () => submitReview(result) }, "Add selected")),
  );
  panel.hidden = false;
  panel.scrollIntoView({ block: "nearest" });
}

async function submitReview(result) {
  const only = {};
  for (const row of document.querySelectorAll("#review-panel .review-row")) {
    only[row.dataset.item] = row.querySelector("select").value || null;
  }
  await guarded(async () => {
    const res = await api("POST", `/api/box-catalog/${result.box.id}/add`, { only });
    $("review-panel").hidden = true;
    toast(`Added ${res.added.length}, skipped ${res.skipped.length}`);
    await loadCollection();
  });
}

async function addUnit(unit, box = "") {
  await guarded(async () => {
    const entry = await api("POST", "/api/collection", { unit_uuid: unit.uuid, qty: 1, box });
    toast(`Added ${unit.name} (now ${entry.qty} owned)`);
    await loadCollection();
  });
}

// ---------- force tray ----------

function itemById(id) {
  return state.items.find((i) => i.id === id);
}

function trayAdd(id, delta) {
  const next = (state.tray.units.get(id) || 0) + delta;
  if (next <= 0) state.tray.units.delete(id);
  else state.tray.units.set(id, next);
  renderTray();
}

function selectedFormation() {
  return state.meta.formations.find((f) => f.key === $("tray-formation").value);
}

function renderTray() {
  const list = $("tray-list");
  list.replaceChildren();
  let total = 0, count = 0;
  const comp = {};
  for (const [id, qty] of state.tray.units) {
    const item = itemById(id) || state.trayCache?.get(id);
    if (!item) continue;
    total += item.bv * qty;
    count += qty;
    comp[item.kind] = (comp[item.kind] || 0) + qty;
    const over = qty > item.qty;
    list.append(
      h("li", {},
        h("span", {}, item.name, over ? h("span", { class: "over small" }, `  (only ${item.qty} owned)`) : null),
        h("span", { class: "qty-ctl" },
          h("button", { class: "icon", "aria-label": "Fewer", onclick: () => trayAdd(id, -1) }, "−"),
          qty,
          h("button", { class: "icon", "aria-label": "More", onclick: () => trayAdd(id, 1) }, "+")),
        h("span", { class: "num" }, `${item.bv * qty} BV`)),
    );
  }
  const fm = selectedFormation();
  const target = Number($("tray-target").value) || 0;
  const summary = $("tray-summary");
  summary.replaceChildren();
  if (!count) {
    summary.append(h("span", { class: "muted" }, "Empty. Add units with the + tray buttons, or press Build for me."));
    return;
  }
  const gap = target - total;
  const mix = Object.entries(comp).map(([k, n]) => `${n} ${k}`).join(", ");
  summary.append(
    `${count}${fm ? ` / ${fm.size}` : ""} units, ${total}${target ? ` / ${target}` : ""} BV`,
    target
      ? h("span", { class: "gap " + (gap < 0 ? "over" : "ok") }, gap < 0 ? `${-gap} over` : `${gap} under`)
      : null,
    h("div", { class: "muted small" }, mix),
  );
}

function rememberTray(units) {
  state.trayCache = state.trayCache || new Map();
  for (const u of units) {
    // Saved-force units carry the quantity used in "qty" and the quantity owned in "owned".
    const entry = "owned" in u ? { ...u, qty: u.owned, painted: u.painted_owned } : u;
    state.trayCache.set(u.collection_id ?? u.id, entry);
  }
}

async function buildForce() {
  const f = currentFilters();
  await guarded(async () => {
    const body = {
      target_bv: Number($("tray-target").value),
      formation: $("tray-formation").value,
      mix: $("tray-mix").value.trim(),
      era: f.era || null,
      faction: f.faction || null,
      box: f.box || null,
      painted_only: f.painted_only,
    };
    const result = await api("POST", "/api/build", body);
    const build = result.builds[0];
    state.tray.units = new Map(build.units.map((u) => [u.collection_id, u.qty]));
    state.tray.forceId = null;
    // Build results may include units hidden by the roster filters, so cache them for display.
    const all = await api("GET", "/api/collection");
    rememberTray(all.map((i) => ({ ...i, collection_id: i.id })));
    renderTray();
    const note = build.in_window ? "" : " (outside the slack window, best available)";
    toast(`Built ${build.total_bv} / ${build.target_bv} BV${note}`);
    const skipped = result.pool.excluded_unknown_availability;
    if (skipped) toast(`Built ${build.total_bv} BV. ${skipped} owned unit(s) skipped: no faction data.`);
  });
}

async function saveForce() {
  const name = $("tray-name").value.trim();
  if (!name) { toast("Give the force a name first", true); return; }
  if (!state.tray.units.size) { toast("The tray is empty", true); return; }
  await guarded(async () => {
    const f = currentFilters();
    const body = {
      name,
      formation: $("tray-formation").value,
      target_bv: Number($("tray-target").value) || null,
      faction_id: f.faction ? Number(f.faction) : null,
      era_id: f.era ? Number(f.era) : null,
      units: [...state.tray.units].map(([collection_id, qty]) => ({ collection_id, qty })),
    };
    const saved = state.tray.forceId
      ? await api("PUT", `/api/forces/${state.tray.forceId}`, body)
      : await api("POST", "/api/forces", body);
    state.tray.forceId = saved.id;
    toast(`Saved ${saved.name} (${saved.total_bv} BV)`);
    await loadForces();
  });
}

function clearTray() {
  state.tray = { units: new Map(), forceId: null };
  $("tray-name").value = "";
  renderTray();
  renderForces();
}

// ---------- saved forces ----------

async function loadForces() {
  await guarded(async () => {
    state.forces = await api("GET", "/api/forces");
    renderForces();
  });
}

function renderForces() {
  const list = $("forces-list");
  list.replaceChildren();
  $("forces-empty").hidden = state.forces.length > 0;
  for (const force of state.forces) {
    const detail = [
      state.meta.formations.find((f) => f.key === force.formation)?.label || force.formation,
      `${force.count} units`,
      `${force.total_bv}${force.target_bv ? ` / ${force.target_bv}` : ""} BV`,
    ].join(" · ");
    list.append(
      h("li", { class: "force-item" + (state.tray.forceId === force.id ? " active" : ""), onclick: () => loadForceIntoTray(force) },
        h("div", { class: "name" }, force.name,
          h("button", { class: "icon danger", title: "Delete force", onclick: (e) => { e.stopPropagation(); deleteForce(force); } }, "×")),
        h("div", { class: "muted small" }, detail),
        force.warnings.length ? h("div", { class: "over small" }, force.warnings[0]) : null,
        force.count ? h("div", { class: "force-actions" },
          h("button", { class: "primary icon", title: "Open this force in MekBay in a new tab", onclick: (e) => { e.stopPropagation(); openInMekBay(force); } }, "Open in MekBay"),
          h("button", { class: "icon", title: "Download a MegaMek .mul file", onclick: (e) => { e.stopPropagation(); downloadMul(force); } }, "Download .mul")) : null),
    );
  }
}

// The tab is opened inside the click so pop-up blockers allow it, then pointed at MekBay once the link is built.
async function openInMekBay(force) {
  const tab = window.open("", "_blank");
  try {
    const out = await api("GET", `/api/forces/${force.id}/export`);
    if (tab) {
      tab.opener = null;
      tab.location.href = out.url;
    } else {
      await navigator.clipboard.writeText(out.url);
      toast("Your browser blocked the new tab, so the MekBay link was copied instead");
    }
  } catch (err) {
    if (tab) tab.close();
    toast(err.message, true);
  }
}

async function downloadMul(force) {
  await guarded(async () => {
    const out = await api("GET", `/api/forces/${force.id}/export`);
    const url = URL.createObjectURL(new Blob([out.content], { type: "application/xml" }));
    const link = h("a", { href: url, download: out.filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`Downloaded ${out.filename}`);
  });
}

function loadForceIntoTray(force) {
  rememberTray(force.units);
  state.tray = { units: new Map(force.units.map((u) => [u.collection_id, u.qty])), forceId: force.id };
  $("tray-name").value = force.name;
  $("tray-formation").value = force.formation;
  if (force.target_bv) $("tray-target").value = force.target_bv;
  renderTray();
  renderForces();
}

async function deleteForce(force) {
  if (!(await confirmDialog(`Delete the force "${force.name}"?`, { confirmLabel: "Delete", danger: true }))) return;
  await guarded(async () => {
    await api("DELETE", `/api/forces/${force.id}`);
    if (state.tray.forceId === force.id) clearTray();
    await loadForces();
  });
}

// ---------- import ----------

async function runImport() {
  const text = $("import-text").value;
  if (!text.trim()) return;
  await guarded(async () => {
    const result = await api("POST", "/api/import", {
      text,
      painted: $("import-painted").checked,
      box: $("import-box").value.trim(),
    });
    const out = $("import-result");
    out.replaceChildren();
    const group = (title, lines) => lines.length
      ? h("div", { class: "import-group" }, h("h3", {}, title), h("ul", {}, lines.map((l) => h("li", {}, l))))
      : null;
    out.append(
      group(`Added (${result.added.length})`, result.added.map((a) => `${a.qty}x ${a.name}`)),
      group("Did you mean? Fix the line and import again",
        result.ambiguous.map((a) => `${a.line} → ${a.candidates.join(" | ")}`)),
      group("No match", result.unmatched),
    );
    if (!result.ambiguous.length && !result.unmatched.length) $("import-text").value = "";
    await loadCollection();
  });
}

// ---------- startup ----------

function fillSelect(select, options, placeholder) {
  select.replaceChildren(h("option", { value: "" }, placeholder), ...options);
}

function setupControls() {
  const { meta } = state;
  fillSelect($("filter-kind"), meta.kinds.map((k) => h("option", { value: k.key }, k.label)), "All types");
  fillSelect($("filter-era"), meta.eras.map((e) => h("option", { value: e.id }, `${e.name} (${e.from}–${e.to})`)), "Any era");
  const groups = {};
  for (const f of meta.factions) (groups[f.group || "Other"] ||= []).push(f);
  fillSelect($("filter-faction"),
    Object.entries(groups).map(([g, fs]) => h("optgroup", { label: g }, fs.map((f) => h("option", { value: f.id }, f.name)))),
    "Any faction");
  $("tray-formation").replaceChildren(
    ...meta.formations.map((f) => h("option", { value: f.key }, `${f.label} (${f.size})`)));

  const c = meta.catalog;
  const unitsText = c.units
    ? `Catalog: ${c.units.toLocaleString()} units from MekBay`
    : "Catalog not loaded. Run: python3 -m bt catalog import";
  const boxText = meta.box_catalog
    ? `${meta.box_catalog} official boxes`
    : "No official boxes yet. Run: python3 -m bt boxes import";
  $("catalog-status").textContent = `${unitsText} · ${boxText}`;

  let filterTimer = null;
  const refilter = () => { clearTimeout(filterTimer); filterTimer = setTimeout(loadCollection, 200); };
  for (const id of ["filter-q", "filter-box", "filter-kind", "filter-era", "filter-faction", "filter-painted"]) {
    $(id).addEventListener("input", refilter);
  }
  $("toggle-add").addEventListener("click", () => {
    const panel = $("add-panel");
    panel.hidden = !panel.hidden;
    if (!panel.hidden) $("add-q").focus();
  });
  $("tray-build").addEventListener("click", buildForce);
  $("tray-clear").addEventListener("click", clearTray);
  $("tray-save").addEventListener("click", saveForce);
  $("tray-target").addEventListener("input", renderTray);
  $("tray-formation").addEventListener("change", renderTray);
  $("import-go").addEventListener("click", runImport);

  for (const th of document.querySelectorAll("th[data-sort]")) {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : 1 };
      renderRoster();
    });
  }
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      $("view-collection").hidden = tab.dataset.view !== "collection";
      $("view-import").hidden = tab.dataset.view !== "import";
    });
  }
}

(async function init() {
  await guarded(async () => {
    state.meta = await api("GET", "/api/meta");
    loadPrefs();
    setupControls();
    setupViewControls();
    setupSuggest();
    await loadCollection();
    await loadForces();
  });
})();
