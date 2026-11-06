"use strict";

const state = {
  meta: null,
  items: [],
  boxes: [],
  forces: [],
  sort: { key: null, dir: 1 },
  groupBy: "box", // which field to group by
  groupOn: false, // whether grouping is switched on, so turning it off keeps the field
  view: "table",
  compact: false,
  collapsed: new Set(),
  editingCopy: null, // id of the mini whose fields are open for editing
  tray: { units: new Map(), forceId: null },
};

// What a mini can be made of. The material box also takes any other brand or process typed in.
const MATERIALS = ["CGL Plastic", "Resin", "Metal"];
const NO_MATERIAL = "No Material"; // first in every list, standing for a mini with no material set

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
      const stops = [...box.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled])")];
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
    tag: $("filter-tag").value.trim(),
    copy_faction: $("filter-copy-faction").value.trim(),
    copy_company: $("filter-copy-company").value.trim(),
    material: $("filter-material").value.trim(),
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
  if (f.tag) p.set("tag", f.tag);
  if (f.copy_faction) p.set("copy_faction", f.copy_faction);
  if (f.copy_company) p.set("copy_company", f.copy_company);
  if (f.material) p.set("material", f.material);
  if (f.painted_only) p.set("painted_only", "1");
  return p.toString();
}

async function loadCollection() {
  await guarded(async () => {
    entryBoxes.clear(); // a variant switch can change which unit an entry holds
    state.items = await api("GET", "/api/collection?" + filterQuery());
    renderRoster();
    renderTray();
    await loadBoxes();
  });
}

async function loadBoxes() {
  state.boxes = await api("GET", "/api/boxes");
  const tags = await api("GET", "/api/copy-tags");
  $("tag-options").replaceChildren(...tags.map((t) => h("option", { value: t.tag }, `${t.copies} ${t.copies === 1 ? "copy" : "copies"}`)));
  const [factions, companies] = await Promise.all([api("GET", "/api/copy-factions"), api("GET", "/api/copy-companies")]);
  const options = (rows) => rows.map((r) => h("option", { value: r.value }, `${r.copies} ${r.copies === 1 ? "copy" : "copies"}`));
  $("copy-faction-options").replaceChildren(...options(factions));
  $("copy-company-options").replaceChildren(...options(companies));
  // The usual materials are always offered. Any other the user has typed is listed after them.
  const used = await api("GET", "/api/copy-materials");
  const counts = new Map(used.map((r) => [r.value, r.copies]));
  const names = [...MATERIALS, ...used.map((r) => r.value).filter((v) => !MATERIALS.includes(v))];
  $("material-options").replaceChildren(...names.map((name) =>
    h("option", { value: name }, counts.has(name) ? `${counts.get(name)} ${counts.get(name) === 1 ? "mini" : "minis"}` : "")));
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

// Official boxes tied to one collection entry's variant. The catalog does not change while the page is open.
const entryBoxes = new Map();
async function unitBoxes(entryId) {
  if (!entryBoxes.has(entryId)) entryBoxes.set(entryId, await api("GET", `/api/collection/${entryId}/boxes`));
  return entryBoxes.get(entryId);
}

function boxSource(addsOnPick) {
  return {
    minChars: 0,
    delay: 150,
    focusShowsAll: true, // clicking into a filled field lists your boxes so the label can be changed
    query: async (term, input) => {
      const t = term.toLowerCase();
      // A box field on one of your minis lists the official boxes that ship that variant first.
      const forUnit = input?.dataset.entry ? (await unitBoxes(input.dataset.entry))
        .filter((b) => !t || b.name.toLowerCase().includes(t))
        .map((b) => ({ type: "unit", ...b })) : [];
      const own = state.boxes
        .filter((b) => !t || b.box.toLowerCase().includes(t))
        .slice(0, 4)
        .map((b) => ({ type: "own", name: b.box, entries: b.entries }));
      let official = [];
      if (t && state.meta.box_catalog) {
        const found = await api("GET", "/api/box-catalog?limit=8&q=" + encodeURIComponent(term));
        official = found.map((b) => ({ type: "official", ...b }));
      }
      const taken = new Set(forUnit.map((b) => b.name.toLowerCase()));
      official = official.filter((b) => !taken.has(b.name.toLowerCase()));
      const officialNames = new Set([...taken, ...official.map((b) => b.name.toLowerCase())]);
      const items = [...forUnit, ...own.filter((o) => !officialNames.has(o.name.toLowerCase())), ...official];
      const onlyExactOwn = items.length === 1 && items[0].type === "own" && items[0].name.toLowerCase() === t;
      return onlyExactOwn ? [] : items;
    },
    label: (b) => b.type === "own"
      ? [b.name, String(b.entries)]
      : b.type === "unit"
        ? [b.name, b.relation === "mini" ? "has this mini" : "has this card"]
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
  // The page is zoomed with CSS (html { zoom }). getBoundingClientRect reports on-screen pixels, but a
  // fixed element's left, top and width are scaled by the zoom again, so convert back before setting them.
  const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  const height = el.offsetHeight * zoom;
  const roomBelow = window.innerHeight - rect.bottom - 8;
  const flip = height > roomBelow && rect.top > roomBelow;
  Object.assign(el.style, {
    left: `${rect.left / zoom}px`,
    top: `${(flip ? Math.max(8, rect.top - height - 2) : rect.bottom + 2) / zoom}px`,
    minWidth: `${rect.width / zoom}px`,
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
    items = await source.query(term, input);
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
// Two views of the same filtered collection, a table and a card grid, either of which can be grouped by a chosen field.
// The view, the density and which groups are collapsed are remembered between visits.

const VIEWS = ["table", "cards"];
const PREFS_KEY = "bt.view";
const SORT_CHOICES = {
  "": null,
  name: { key: "name", dir: 1 },
  kind: { key: "kind", dir: 1 },
  bv: { key: "bv", dir: -1 },
  tons: { key: "tons", dir: -1 },
  year: { key: "year", dir: 1 },
};
const GROUP_BY_CHOICES = ["box", "era", "avail", "faction", "weight", "kind", "tech", "role", "painted", "material"];

function loadPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
    if (VIEWS.includes(saved.view)) state.view = saved.view;
    if (GROUP_BY_CHOICES.includes(saved.groupBy)) state.groupBy = saved.groupBy;
    // Grouped used to be a view of its own. It is now the table with a grouping, by box as before.
    // Earlier versions had a Grouped view, then a "none" field choice. Both mean grouping was on or off.
    if (typeof saved.groupOn === "boolean") state.groupOn = saved.groupOn;
    else if (saved.view === "grouped") state.groupOn = true;
    else state.groupOn = GROUP_BY_CHOICES.includes(saved.groupBy);
    if (saved.view === "grouped") state.view = "table";
    if (saved.sort && SORT_CHOICES[saved.sort]) state.sort = SORT_CHOICES[saved.sort];
    state.compact = Boolean(saved.compact);
    if (Array.isArray(saved.collapsed)) state.collapsed = new Set(saved.collapsed.map(String));
  } catch {
    // Storage can be unavailable or hold something unexpected. The defaults work fine.
  }
}

function savePrefs() {
  try {
    const sort = Object.entries(SORT_CHOICES).find(([, v]) => v && v.key === state.sort.key);
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      view: state.view, compact: state.compact, collapsed: [...state.collapsed],
      groupBy: state.groupBy, groupOn: state.groupOn, sort: sort ? sort[0] : "",
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

// ---------- minis in the roster ----------
// Every owned mini is its own row or card, named or not. Any mini can carry a tag, a material, a box and a
// paint state. A mini with a note, faction or company is a named copy and also gets a crimson strip.

// The tag, mini faction, mini company and material boxes narrow by mini. They match part of the text, ignoring case.
function miniFilters() {
  const lower = (id) => $(id).value.trim().toLowerCase();
  return {
    tag: lower("filter-tag"), faction: lower("filter-copy-faction"), company: lower("filter-copy-company"),
    material: lower("filter-material"),
  };
}

const miniFilterActive = () => Object.values(miniFilters()).some(Boolean);

// With any of those filters on, only the minis that match all of them are shown.
function visibleMinis(item) {
  const f = miniFilters();
  if (!miniFilterActive()) return item.minis;
  return item.minis.filter((m) =>
    m.tag.toLowerCase().includes(f.tag) && m.faction.toLowerCase().includes(f.faction) &&
    m.company.toLowerCase().includes(f.company) && m.material.toLowerCase().includes(f.material));
}

// A mini with a note, faction or company gets a crimson strip on its card, naming what it is painted for.
const isNamed = (mini) => Boolean(mini.note || mini.faction || mini.company);

// What a named mini is called on its card: the company, with the faction under it. A mini that has
// neither (it only has a note) falls back to its note.
function copyHeading(copy) {
  if (copy.company || copy.faction) return { main: copy.company || copy.faction, sub: copy.company ? copy.faction : "" };
  return { main: copy.note, sub: "" };
}

const copyLine = (copy) => [copy.company, copy.faction].filter(Boolean).join(" · ") || copy.note;
const copyName = (copy) => copy.note || copy.company || copy.faction || "mini";

async function changeCopy(run) {
  await guarded(run);
  await loadCollection(); // also puts a field back when the change was refused
}

// The boxes for editing one mini. Each saves when you leave it. The page is not rebuilt until
// Done, so tabbing from one box to the next keeps working.
function copyEditor(item, copy) {
  const save = (name, read = (e) => e.target.value, restore = (e) => { e.target.value = copy[name]; }) => async (e) => {
    const value = read(e);
    const saved = await guarded(async () => Object.assign(copy, await api("PATCH", `/api/copies/${copy.id}`, { [name]: value })));
    if (!saved) restore(e); // a refused change puts the old text back
  };
  const field = (label, name, extra) => h("label", { class: "fld" }, label,
    h("input", { type: "text", value: copy[name], "aria-label": label, onchange: save(name), ...extra }));
  return h("div", { class: "copy-editor" },
    field("Note", "note"),
    field("Faction", "faction", { list: "copy-faction-options" }),
    field("Company", "company", { list: "copy-company-options" }),
    field("Tag", "tag", { list: "tag-options" }),
    field("Material", "material", { list: "material-options", placeholder: "e.g. Metal, Resin" }),
    field("Box", "box", { "data-suggest": "box", "data-entry": item.id, autocomplete: "off", placeholder: item.box || "none" }),
    h("label", { class: "fld inline" }, "Painted", h("input", {
      type: "checkbox", checked: copy.painted, "aria-label": "Painted",
      onchange: save("painted", (e) => e.target.checked, (e) => { e.target.checked = copy.painted; }),
    })));
}

// Where a mini's box shows: its own, or the entry's when it has none.
const copyBox = (item, copy) => copy.box || item.box;

async function setCopy(copy, patch) {
  await changeCopy(() => api("PATCH", `/api/copies/${copy.id}`, patch));
}

const swapCell = (button) => button.closest("article")?.querySelector(".card-name") || button.closest("tr").firstElementChild;

const tagPill = (copy) => (copy.tag ? h("span", { class: "tag-pill" }, copy.tag) : null);

async function finishEditing() {
  state.editingCopy = null;
  await loadCollection(); // refreshes the suggestions and any filter the edit changed
}

// One more mini of the same variant, in the entry's box.
async function addAnother(item) {
  await guarded(async () => {
    await api("POST", "/api/collection", { unit_uuid: item.unit_uuid, qty: 1 });
    await loadCollection();
  });
}

async function removeMini(item, mini) {
  const last = item.qty <= 1;
  const message = last
    ? `Remove ${item.name} from your collection? It is your last one, so it will also leave any saved forces.`
    : `Remove this ${item.name} from your collection?`;
  if (!(await confirmDialog(message, { confirmLabel: "Remove", danger: true }))) return;
  await guarded(async () => {
    await api("DELETE", `/api/minis/${mini.id}`);
    if (last) state.tray.units.delete(item.id);
    await loadCollection();
    await loadForces();
  });
}

function copyActions(item, copy, quickPaint = false) {
  if (state.editingCopy === copy.id) {
    return h("button", { class: "icon primary", title: "Finish editing", onclick: finishEditing }, "Done");
  }
  return [
    h("button", { class: "icon", title: "Add one to the force tray", onclick: () => trayAdd(item.id, 1) }, "+ tray"),
    " ",
    quickPaint ? paintToggle(copy) : null,
    quickPaint ? " " : null,
    h("button", { class: "icon", title: "Switch this one mini to another variant", onclick: (e) => startSwap(item, swapCell(e.currentTarget), copy) }, "Variant"),
    " ",
    h("button", { class: "icon", title: "Add another mini of this variant", onclick: () => addAnother(item) }, "+ another"),
    " ",
    h("button", { class: "icon", title: "Edit this mini", "aria-label": `Edit ${copyName(copy)}`, onclick: () => { state.editingCopy = copy.id; renderRoster(); } }, "✎"),
    " ",
    h("button", { class: "icon danger", title: "Remove this mini from the collection", "aria-label": `Remove ${item.name}`, onclick: () => removeMini(item, copy) }, "×"),
  ];
}

// Set a mini's material from its card without opening the editor. A mini with none shows a "+ Material"
// button, and one with a material shows it as a pill. Either opens a dropdown, which closes again when it
// loses focus or Escape is pressed. A material typed in the editor that is not one of the usual ones
// stays in the list so it keeps showing.
function materialControl(mini) {
  const names = !mini.material || MATERIALS.includes(mini.material) ? MATERIALS : [...MATERIALS, mini.material];
  const holder = h("div", { class: "material-control" });
  const show = () => holder.replaceChildren(h("button", {
    type: "button", class: mini.material ? "material-pill" : "icon add-material",
    title: mini.material ? "Change this mini's material" : "Add a material",
    "aria-label": mini.material ? `Material of ${copyName(mini)}: ${mini.material}. Change it` : `Add a material to ${copyName(mini)}`,
    onclick: open,
  }, mini.material || "+ Material"));
  const open = () => {
    const select = h("select", {
      class: "material-select", "aria-label": `Material of ${copyName(mini)}`,
      onchange: (e) => setCopy(mini, { material: e.target.value }),
      onblur: show,
      onkeydown: (e) => { if (e.key === "Escape") show(); },
    },
      h("option", { value: "", selected: !mini.material }, NO_MATERIAL),
      ...names.map((name) => h("option", { value: name, selected: name === mini.material }, name)));
    holder.replaceChildren(select);
    select.focus();
    try { select.showPicker(); } catch { /* the list opens on the next click where the browser does not allow this */ }
  };
  show();
  return holder;
}

function buildMiniRow(item, mini) {
  const editing = state.editingCopy === mini.id;
  const detail = isNamed(mini) ? copyLine(mini) : "";
  return h("tr", { class: isNamed(mini) ? "copy-line" : "mini-line" },
    h("td", {}, nameLink(item),
      editing ? copyEditor(item, mini)
        : (detail || mini.tag) ? h("div", { class: "copy-label", title: mini.note || null }, detail, detail && mini.tag ? " " : null, tagPill(mini)) : null),
    h("td", {}, kindLabel(item)),
    h("td", {}, h("input", {
      type: "text", class: "box-input", value: mini.box, "data-suggest": "box", "data-entry": item.id, autocomplete: "off",
      placeholder: item.box || "none", "aria-label": `Box for ${copyName(mini)}`,
      onchange: (e) => setCopy(mini, { box: e.target.value }),
    })),
    h("td", { class: "num" }, item.tons == null ? "" : Number(item.tons).toString()),
    h("td", { class: "num" }, item.bv),
    h("td", {}, h("input", {
      type: "text", class: "box-input", value: mini.material, list: "material-options", autocomplete: "off",
      placeholder: "none", "aria-label": `Material of ${copyName(mini)}`,
      onchange: (e) => setCopy(mini, { material: e.target.value }),
    })),
    h("td", { class: "num" }, h("input", {
      type: "checkbox", checked: mini.painted, "aria-label": `Painted: ${copyName(mini)}`,
      onchange: (e) => setCopy(mini, { painted: e.target.checked }),
    })),
    h("td", { class: "actions" }, copyActions(item, mini)));
}

// A mini's card shows the photo, the unit's stats, its material, tag and box, and a brush badge when painted.
// A named mini also has a crimson strip under the photo naming its company and faction, with its note below
// the stats. The pencil swaps the details for the editing boxes.
function buildMiniCard(item, mini) {
  const editing = state.editingCopy === mini.id;
  const heading = isNamed(mini) ? copyHeading(mini) : null;
  const noteLine = isNamed(mini) && (mini.company || mini.faction) && mini.note ? h("div", { class: "copy-note-line" }, mini.note) : null;
  return h("article", { class: "mini-card" + (isNamed(mini) ? " is-copy" : "") },
    h("div", { class: "photo clickable" + (item.has_art ? " has-art" : ""), onclick: () => showUnitDetail(item.unit_uuid) },
      artImage(item), mini.painted ? paintBadge() : null),
    heading ? h("div", { class: "copy-strip" }, h("span", {}, heading.main), heading.sub ? h("small", {}, heading.sub) : null) : null,
    h("div", { class: "card-body" },
      h("div", { class: "card-name" }, nameLink(item)),
      h("div", { class: "card-meta" }, `${kindLabel(item)} · ${item.tons ?? "?"}t · ${item.bv} BV`),
      editing ? copyEditor(item, mini) : [materialControl(mini), noteLine, tagPill(mini) ? h("div", {}, tagPill(mini)) : null],
      copyBox(item, mini) ? h("div", { class: "card-box" }, copyBox(item, mini)) : null,
      h("div", { class: "card-actions" }, copyActions(item, mini, true))));
}

// Grouped by box or by material, an entry sits in every group its minis are in, and each group shows only
// those minis. `scope` is a test of one mini for the group being drawn, or null to show the whole entry.
const miniBoxName = (item, mini) => copyBox(item, mini) || "";
const boxesOf = (item) => [...new Set(item.minis.map((m) => miniBoxName(item, m)))];
const materialsOf = (item) => [...new Set(item.minis.map((m) => m.material))];
const minisIn = (item, scope) => visibleMinis(item).filter((m) => !scope || scope(item, m));

const rowsFor = (item, scope = null) => minisIn(item, scope).map((m) => buildMiniRow(item, m));
const cardsFor = (item, scope = null) => minisIn(item, scope).map((m) => buildMiniCard(item, m));

// How the grouped view splits the collection. Each choice names the groups an entry belongs to
// (several for faction groups, since a mini can be fielded by more than one) and a fixed order
// for the groups, or null for name order. Groups that hold nothing are simply absent.
const WEIGHT_ORDER = ["Ultra Light/PA(L)/Exoskeleton", "Light", "Medium", "Heavy", "Assault", "Colossal/Super-Heavy"];
const FACTION_GROUP_ORDER = ["Inner Sphere", "IS Clan", "HW Clan", "Periphery", "Mercenary", "Other"];
const PAINT_ORDER = ["Unpainted", "Partly painted", "Fully painted"];
const PRE_ERA = "Before the first era";
const NO_GROUP = {
  box: "No box", era: "Unknown year", avail: "No availability data", faction: "Unaffiliated", weight: "Unknown", kind: "Unknown",
  tech: "Unknown", role: "No role", painted: "Unpainted", material: NO_MATERIAL,
};

// An era is named by the year the unit debuted. Years before the first era get their own group.
const eraLabel = (e) => `${e.name} (${e.from}–${e.to >= 9999 ? "on" : e.to})`;
function eraNames() {
  return state.meta.eras.map(eraLabel);
}

// Every era the unit is available in, from the faction availability data. This is the era filter's
// meaning, where "era (debut)" above is only the one era the unit first appeared in.
function availableEraNames(item) {
  const byId = new Map(state.meta.eras.map((e) => [e.id, eraLabel(e)]));
  return item.available_eras.map((id) => byId.get(id)).filter(Boolean);
}

function eraOf(item) {
  const eras = state.meta.eras;
  if (item.year == null) return null;
  if (!eras.length) return PRE_ERA;
  let found = -1;
  eras.forEach((e, i) => { if (e.from <= item.year) found = i; });
  return found < 0 ? PRE_ERA : eraNames()[found];
}

function paintOf(item) {
  if (item.painted <= 0) return "Unpainted";
  return item.painted >= item.qty ? "Fully painted" : "Partly painted";
}

const GROUPERS = {
  box: { of: boxesOf, order: null },
  era: { of: (i) => [eraOf(i)], order: () => [PRE_ERA, ...eraNames()] },
  avail: {
    of: (i) => { const names = availableEraNames(i); return names.length ? names : [null]; },
    order: () => eraNames(),
  },
  faction: { of: (i) => (i.faction_groups.length ? i.faction_groups : [null]), order: () => FACTION_GROUP_ORDER },
  weight: { of: (i) => [i.weight_class], order: () => WEIGHT_ORDER },
  kind: { of: (i) => [kindLabel(i)], order: () => state.meta.kinds.map((k) => k.label) },
  tech: { of: (i) => [i.tech_base], order: null },
  role: { of: (i) => [i.role], order: null },
  painted: { of: (i) => [paintOf(i)], order: () => PAINT_ORDER },
  material: { of: (i) => materialsOf(i).map((m) => m || null), order: () => [NO_MATERIAL, ...MATERIALS] },
};

const isGrouped = () => state.groupOn;

// Group names in their natural order, then any name the order list does not know, then the
// "none of the above" group last.
function groupsOf(items) {
  if (!isGrouped()) return [];
  const by = GROUPERS[state.groupBy];
  const none = NO_GROUP[state.groupBy];
  const map = new Map();
  for (const item of items) {
    for (const name of by.of(item)) {
      const key = name || none;
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(item);
    }
  }
  const order = by.order ? by.order() : [];
  const rank = (name) => {
    if (name === none && !order.includes(none)) return Infinity; // the "none of the above" group is last unless the order places it
    const i = order.indexOf(name);
    return i < 0 ? order.length : i;
  };
  return [...map.entries()].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}

// Collapsed groups are remembered per grouping, so "Clan" under tech base and under faction differ.
// Box keeps the plain name so what people already collapsed still applies.
function collapseKey(name) {
  return state.groupBy === "box" ? name : `${state.groupBy}:${name}`;
}

// The test a group applies to each of an entry's minis, or null when the group holds whole entries.
// Grouped by box or by material, a group holds only the minis in that box or made of that material.
function groupScope(name) {
  if (state.groupBy === "box") {
    const box = name === NO_GROUP.box ? "" : name;
    return (item, mini) => miniBoxName(item, mini) === box;
  }
  if (state.groupBy === "material") {
    const material = name === NO_GROUP.material ? "" : name;
    return (item, mini) => mini.material === material;
  }
  return null;
}

// What an entry puts in a group: its minis that pass the group's test, or all of them.
function share(item, scope) {
  const minis = scope ? item.minis.filter((m) => scope(item, m)) : item.minis;
  return { models: minis.length, painted: minis.filter((m) => m.painted).length };
}

function totals(list, scope = null) {
  const sums = { models: 0, painted: 0, bv: 0 };
  for (const item of list) {
    const s = share(item, scope);
    sums.models += s.models;
    sums.painted += s.painted;
    sums.bv += item.bv * s.models;
  }
  return sums;
}

function groupHeader(name, list, collapsed) {
  const t = totals(list, groupScope(name));
  const pct = t.models ? Math.round((100 * t.painted) / t.models) : 0;
  const toggle = () => {
    if (collapsed) state.collapsed.delete(collapseKey(name)); else state.collapsed.add(collapseKey(name));
    savePrefs();
    renderRoster();
  };
  return h("button", { class: "group-toggle", "aria-expanded": String(!collapsed), onclick: toggle },
    h("span", { class: "chev", "aria-hidden": "true" }, collapsed ? "▸" : "▾"), " ",
    h("strong", {}, name), " ",
    h("span", { class: "group-meta" }, `${list.length} entries · ${t.models} models · ${t.bv.toLocaleString()} BV`), " ",
    h("span", { class: "bar", "aria-hidden": "true", title: `${t.painted} of ${t.models} painted` },
      h("i", { style: `width:${pct}%` })), " ",
    h("span", { class: "group-meta" }, `${t.painted}/${t.models} painted`));
}

// The default picture is the unit's artwork. A unit with no art, or art that fails to load, gets a placeholder.
// A paintbrush in the corner of a photo marks a painted mini.
const BRUSH_SVG = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M7 14c-1.66 0-3 1.34-3 3 0 1.31-1.16 2-2 2 .92 1.22 2.49 2 4 2 2.21 0 4-1.79 4-4 0-1.66-1.34-3-3-3zm13.71-9.37l-1.34-1.34a.996.996 0 0 0-1.41 0L9 12.25 11.75 15l8.96-8.96a.996.996 0 0 0 0-1.41z"/></svg>';

function paintBadge(title = "Painted") {
  const badge = h("span", { class: "paint-badge", title, role: "img", "aria-label": title });
  badge.innerHTML = BRUSH_SVG;
  return badge;
}

// One-click toggle for a named mini's painted flag, so the edit boxes are not needed for it.
function paintToggle(copy) {
  const button = h("button", {
    type: "button", class: "icon paint-toggle" + (copy.painted ? " on" : ""), "aria-pressed": String(copy.painted),
    title: copy.painted ? "Mark as unpainted" : "Mark as painted",
    "aria-label": `${copy.painted ? "Unmark" : "Mark"} ${copyName(copy)} as painted`,
    onclick: () => setCopy(copy, { painted: !copy.painted }),
  });
  button.innerHTML = BRUSH_SVG;
  return button;
}

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
  if (isGrouped()) {
    for (const [name, list] of groupsOf(items)) {
      const collapsed = state.collapsed.has(collapseKey(name));
      body.append(h("tr", { class: "group-row" }, h("td", { colspan: 8 }, groupHeader(name, list, collapsed))));
      if (!collapsed) body.append(...list.flatMap((item) => rowsFor(item, groupScope(name))));
    }
  } else {
    body.append(...items.flatMap((item) => rowsFor(item)));
  }
  const message = emptyMessage();
  $("roster-empty").hidden = !message;
  $("roster-empty").textContent = message;
}

// Ungrouped, the container is itself the grid. Grouped, it holds a header and a grid per group.
function renderCards(items) {
  const message = emptyMessage();
  const container = cardGrid();
  const grouped = isGrouped() && !message;
  container.classList.toggle("card-grid", !grouped);
  container.classList.toggle("card-groups", grouped);
  if (message) { container.replaceChildren(h("p", { class: "muted empty" }, message)); return; }
  if (!grouped) { container.replaceChildren(...items.flatMap((item) => cardsFor(item))); return; }
  container.replaceChildren(...groupsOf(items).flatMap(([name, list]) => {
    const collapsed = state.collapsed.has(collapseKey(name));
    const head = h("div", { class: "card-group-head" }, groupHeader(name, list, collapsed));
    // replaceChildren turns a null into the text "null", so a collapsed group adds only its header.
    return collapsed ? [head] : [head, h("div", { class: "card-grid" }, list.flatMap((item) => cardsFor(item, groupScope(name))))];
  }));
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
  $("group-actions").hidden = !isGrouped();
  $("group-by").value = state.groupBy;
  for (const button of $("group-by-group").querySelectorAll("button[data-group]")) {
    button.setAttribute("aria-pressed", String((button.dataset.group === "1") === state.groupOn));
  }
  $("group-by").classList.toggle("is-off", !state.groupOn);
  const current = Object.entries(SORT_CHOICES).find(([, v]) => v && v.key === state.sort.key);
  $("card-sort").value = current ? current[0] : "";
  const models = state.items.reduce((n, i) => n + i.qty, 0);
  $("view-count").textContent = `${state.items.length} entries · ${models} models`;
}

function renderRoster() {
  document.body.classList.toggle("view-cards", state.view === "cards");
  // The group header already names the box, so the table's Box column would only repeat it.
  document.body.classList.toggle("group-by-box", isGrouped() && state.view === "table" && state.groupBy === "box");
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
    for (const [name] of groupsOf(state.items)) state.collapsed.add(collapseKey(name));
    savePrefs();
    renderRoster();
  });
  $("expand-all").addEventListener("click", () => {
    for (const [name] of groupsOf(state.items)) state.collapsed.delete(collapseKey(name));
    savePrefs();
    renderRoster();
  });
  $("card-sort").addEventListener("change", (e) => {
    state.sort = SORT_CHOICES[e.target.value] || { key: null, dir: 1 };
    savePrefs();
    renderRoster();
  });
  for (const button of $("group-by-group").querySelectorAll("button[data-group]")) {
    button.addEventListener("click", () => {
      state.groupOn = button.dataset.group === "1";
      savePrefs();
      renderRoster();
    });
  }
  $("group-by").addEventListener("change", (e) => {
    state.groupBy = e.target.value;
    state.groupOn = true; // picking a field is asking for grouping
    savePrefs();
    renderRoster();
  });
}

// Switch one mini to another variant of the same chassis. A plastic mini can stand for any variant.
async function startSwap(item, cell, copy) {
  await guarded(async () => {
    const variants = await api("GET", `/api/collection/${item.id}/variants`);
    if (variants.length < 2) { toast(`No other variants of ${item.chassis} in the catalog`); return; }
    // On a card the chassis is already showing and the picker is narrow, so list just the model and BV.
    const onCardView = cell.classList.contains("card-name");
    const label = (v) => (onCardView && v.name.startsWith(item.chassis) ? v.name.slice(item.chassis.length).trim() || v.name : v.name);
    const option = (v) => h("option", { value: v.uuid, selected: v.current, title: v.name }, `${label(v)}  ·  ${v.bv} BV`);
    const onCard = variants.filter((v) => v.on_card);
    const others = variants.filter((v) => !v.on_card);
    // Variants the mini's official box has a card for go first, in their own group.
    const choices = onCard.length
      ? [h("optgroup", { label: "On your box's card" }, onCard.map(option)),
         others.length ? h("optgroup", { label: "Other variants" }, others.map(option)) : null]
      : variants.map(option);
    const select = h("select", { "aria-label": `Variant of ${item.chassis}` }, choices);
    select.addEventListener("change", () => swapVariant(item, variants.find((v) => v.uuid === select.value), copy));
    select.addEventListener("blur", renderRoster);
    select.addEventListener("keydown", (e) => { if (e.key === "Escape") renderRoster(); });
    // A card keeps its name showing and puts the picker under it. A table cell swaps itself for the picker.
    if (onCardView) cell.append(select); else cell.replaceChildren(select);
    select.focus();
  });
}

async function swapVariant(item, variant, copy) {
  // One mini moves to the other variant; the rest of the entry stays put.
  await guarded(async () => {
    await api("POST", `/api/copies/${copy.id}/swap`, { unit_uuid: variant.uuid });
    toast(`Switched ${item.name} to ${variant.name}`);
    state.editingCopy = null;
    await loadCollection();
    if (!itemById(item.id)) state.tray.units.delete(item.id); // the old entry goes when its last mini moves
    await loadForces();
  });
  renderRoster();
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
      mode: $("import-mode").value,
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
  for (const id of ["filter-q", "filter-box", "filter-tag", "filter-copy-faction", "filter-copy-company", "filter-material", "filter-kind", "filter-era", "filter-faction", "filter-painted"]) {
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
  $("import-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    await guarded(async () => {
      $("import-text").value = (await file.text()).replace(/^﻿/, "");
    });
    event.target.value = "";
  });
  $("import-mode").addEventListener("change", () => {
    const mul = $("import-mode").value === "mul";
    $("import-mul-hint").hidden = !mul;
    $("import-text").placeholder = mul
      ? "1\n2x MUL 2\nhttps://masterunitlist.info/Unit/Details/4"
      : "Atlas AS7-D\n2x Locust LCT-1V\nShadow Hawk SHD-2H";
  });

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
