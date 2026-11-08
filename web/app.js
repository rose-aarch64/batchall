"use strict";

// A share link serves this page under /s/<token>/ and its API under the same prefix. The page is then a
// read-only view of the link owner's collection: edit controls are not drawn, and api() refuses writes.
const SHARE_BASE = (location.pathname.match(/^\/s\/[A-Za-z0-9_.\-]+/) || [""])[0];
const SHARED = SHARE_BASE !== "";
const apiUrl = (path) => SHARE_BASE + path;
// Set before anything is drawn, so the stylesheet hides the page's own edit controls from the first paint.
if (SHARED) document.body.classList.add("shared");

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
  selected: new Set(), // ids of the minis ticked for a bulk change
  lastPicked: null, // the mini ticked last, where a shift-click range starts
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

// The lore sections in order, each with a short line saying what it covers (shown beside the title).
const LORE_TITLES = [
  ["overview", "Overview", "What it is and who built it"],
  ["capabilities", "Capabilities", "What it carries and how it fights"],
  ["deployment", "Deployment", "Who fielded it and where"],
  ["history", "History", "How it came to be, and what it did"],
  ["notes", "Notes", "Extra details that fit nowhere else"],
];

function stat(label, value) {
  if (value == null || value === "") return null;
  return h("div", { class: "stat" }, h("span", { class: "k" }, label), h("span", { class: "v" }, value));
}

function detailSection(title, ...children) {
  const kids = children.flat().filter(Boolean);
  if (!kids.length) return null;
  return h("section", { class: "detail-section" }, h("h3", {}, title), kids);
}

// The first sentence of a paragraph, so it can be set in bold as something to skim by. Returns null when
// there is no clear sentence break, so a paragraph is never split in the middle of "3.5 m" or "Dr. Smith".
function leadSentence(text) {
  const m = /^(.{25,}?[.!?])\s+(?=[A-Z“"'(])/.exec(text);
  if (!m || /\b(?:[A-Z][a-z]?|Dr|Mr|Mrs|Ms|St|Lt|Col|Gen|Cmdr|vs|No)\.$/.test(m[1])) return null;
  return { lead: m[1], rest: text.slice(m[0].length) };
}

function lorePara(text) {
  const split = leadSentence(text);
  return split ? h("p", {}, h("strong", { class: "lead" }, split.lead), " ", split.rest) : h("p", {}, text);
}

// A lore section that folds up. Closed, it is its title and a short line about what the section covers.
function loreSection(title, blurb, paragraphs, open) {
  return h("details", { class: "detail-section lore", open }, // `open` is only added to the page when true
    h("summary", {}, h("span", { class: "lore-title" }, title), h("span", { class: "lore-blurb" }, blurb)),
    paragraphs.map(lorePara));
}

function weaponTable(weapons) {
  if (!weapons || !weapons.length) return null;
  return h("table", { class: "weapons" },
    h("thead", {}, h("tr", {}, ["Weapon", "Loc", "Qty", "Damage", "Range"].map((t) => h("th", {}, t)))),
    h("tbody", {}, weapons.map((w) => h("tr", {},
      h("td", {}, w.name), h("td", {}, w.location || ""), h("td", {}, w.qty), h("td", {}, w.damage || ""), h("td", {}, w.range || "")))));
}

// A bigger version of the mini's card, for the top left of the panel: the photo (or the unit's line art), the
// crimson strip with the company and faction when the panel was opened from a mini that has them, then the
// mech's name and a line of facts. Opened without a mini (from a table row), it shows the line art and name.
function detailCard(unit, summary, mini) {
  const photo = mini?.photo;
  const showStrip = mini && isNamed(mini);
  return h("div", { class: "detail-card" },
    h("div", { class: "photo" + (photo ? " has-photo" : unit.has_art ? " has-art" : "") },
      photo ? miniPhotoImage(mini) : artImage({ has_art: unit.has_art, unit_uuid: unit.uuid }),
      mini?.painted ? paintBadge() : null),
    showStrip ? h("div", { class: "copy-strip", style: stripStyle(mini) }, stripLines(mini)) : null,
    h("div", { class: "detail-card-body" },
      h("h2", {}, unit.chassis || unit.name),
      h("div", { class: "detail-card-meta" }, [unit.model, summary].filter(Boolean).join(" · ")),
      mini && (mini.material || mini.tag) ? h("div", { class: "detail-card-pills" },
        mini.material ? h("span", { class: "material-static" }, mini.material) : null, tagPill(mini)) : null,
      mini && mini.note && (mini.company || mini.faction) ? h("div", { class: "copy-note-line" }, mini.note) : null));
}

function buildDetail(unit, close, mini) {
  const s = unit.specs || {};
  const mv = s.movement || {};
  const heat = s.heat || {};
  const armor = s.armor || {};
  const structure = s.structure || {};
  const engine = s.engine || {};
  const lore = unit.lore || {};
  const moves = [["Walk", mv.walk], ["Run", mv.run], ["Jump", mv.jump]].filter(([, v]) => v != null).map(([k, v]) => `${k} ${v}`).join(" / ");
  const summary = [kindLabel(unit), unit.tons != null ? `${unit.tons}t` : null, `${unit.bv} BV`, unit.year, unit.tech_base, unit.role].filter(Boolean).join(" · ");
  // Every lore section starts folded up, so the panel is not a wall of text. Click one to read it.
  const loreSections = LORE_TITLES.filter(([key]) => lore[key] && lore[key].length)
    .map(([key, title, blurb]) => loreSection(title, blurb, lore[key], false));
  const credits = [lore.manufacturer && `Manufacturer: ${lore.manufacturer}`, lore.primaryFactory && `Primary factory: ${lore.primaryFactory}`].filter(Boolean);
  const closeButton = h("button", { type: "button", class: "icon detail-close", "aria-label": "Close", onclick: () => close() }, "Close");
  return h("div", { class: "modal detail", role: "dialog", "aria-modal": "true", "aria-label": `${unit.name} details` },
    h("div", { class: "detail-head" }, closeButton),
    h("div", { class: "detail-layout" },
      detailCard(unit, summary, mini),
      h("div", { class: "detail-main" },
        unit.has_details
          ? h("div", { class: "stats" },
              stat("Movement", moves), stat("Move type", mv.type),
              stat("Armor", armor.points != null ? `${armor.points}${armor.type ? ` (${armor.type})` : ""}` : null),
              stat("Structure", structure.type), stat("Internal", structure.internal),
              stat("Engine", engine.type ? `${engine.type}${engine.rating ? ` ${engine.rating}` : ""}` : null),
              stat("Heat", heat.generated != null ? `${heat.generated} generated, ${heat.dissipation ?? "?"} dissipated` : null),
              stat("Tech", [s.level, s.tech_rating].filter(Boolean).join(" · ")),
              stat("Crew", s.crew), stat("Cost", s.cost != null ? `${s.cost.toLocaleString()} C-bills` : null))
          : h("p", { class: "muted" }, "No detail data yet. Run python3 -m batchall catalog import once to load it."),
        unit.has_details ? detailSection("Weapons", weaponTable(s.weapons)) : null,
        unit.has_details && s.quirks && s.quirks.length ? detailSection("Quirks", h("p", {}, s.quirks.join(", "))) : null,
        loreSections,
        unit.has_details && !loreSections.length ? h("p", { class: "muted small" }, "No lore text for this unit. The lore file may not have been imported.") : null)),
    credits.length ? h("p", { class: "muted small" }, credits.join(" · ")) : null,
    h("p", { class: "muted small credit" }, "Unit data and text via MekBay and the MegaMek team."));
}

// `mini` is the mini whose card opened the panel, so the panel can show its photo and strip. It is
// omitted when the panel is opened from somewhere that is not one mini, like a table row.
async function showUnitDetail(uuid, mini) {
  await guarded(async () => {
    const unit = await api("GET", `/api/units/${encodeURIComponent(uuid)}`);
    const modal = openModal(buildDetail(unit, () => modal.close(), mini));
    document.querySelector(".detail-close").focus();
  });
}

function nameLink(item) {
  return h("button", { type: "button", class: "unit-link", title: "Show unit details", onclick: () => showUnitDetail(item.unit_uuid) }, item.name);
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

// The force builder stores nothing, so it is the one request other than a read that a shared page may send.
const sharedMayRequest = (method, path) => method === "GET" || (method === "POST" && path === "/api/build");

async function api(method, path, body) {
  if (SHARED && !sharedMayRequest(method, path)) throw new Error("read-only");
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  // The hosted server sets this cookie and requires it back on writes. The local server never sets it.
  const token = csrfToken();
  if (method !== "GET" && token) opts.headers["X-CSRFToken"] = token;
  const res = await fetch(apiUrl(path), opts);
  if (res.status === 401 && !SHARED && location.pathname !== "/login.html") {
    location.href = "/login.html";
    throw new Error("sign in required");
  }
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
  // Factions you have used come first with their counts. MekBay's own faction names follow, so typing
  // "Wolf" offers "Wolf's Dragoons", and a name picked from the list gets its logo.
  const usedFactions = new Set(factions.map((r) => r.value.toLowerCase()));
  const known = (state.meta?.factions || []).filter((f) => !usedFactions.has(f.name.toLowerCase()));
  $("copy-faction-options").replaceChildren(...options(factions), ...known.map((f) => h("option", { value: f.name })));
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
      if (t && state.meta.box_catalog && !SHARED) { // the shared routes do not serve the box catalog search
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
const PREFS_KEY = "batchall.view";
const OLD_PREFS_KEY = "bt.view"; // the key before the rename, read once so saved view choices carry over
const SORT_CHOICES = {
  "": null,
  name: { key: "name", dir: 1 },
  kind: { key: "kind", dir: 1 },
  bv: { key: "bv", dir: -1 },
  tons: { key: "tons", dir: -1 },
  year: { key: "year", dir: 1 },
};
const GROUP_BY_CHOICES = ["box", "era", "avail", "faction", "weight", "kind", "tech", "role", "painted", "material", "company"];

function loadPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFS_KEY) || localStorage.getItem(OLD_PREFS_KEY) || "{}");
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
    // Some groups start collapsed. Each default is applied once, so a group you open stays open afterwards.
    const applied = new Set(Array.isArray(saved.defaultsApplied) ? saved.defaultsApplied.map(String) : []);
    for (const key of [`company:${NO_GROUP.company}`]) {
      if (!applied.has(key)) { state.collapsed.add(key); applied.add(key); }
    }
    state.defaultsApplied = applied;
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
      defaultsApplied: [...(state.defaultsApplied || [])],
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
  if (SHARED) {
    return Object.values(currentFilters()).some(Boolean) ? "Nothing in this collection matches these filters." : "This collection is empty.";
  }
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

// The logo of the faction a mini is painted for, or of its company when MekBay lists the company as a
// faction too (Wolf's Dragoons, Kell Hounds). It is matched on the exact name from MekBay, ignoring case.
// A name MekBay does not list, or a logo that fails to load, shows nothing.
function factionIcon(name) {
  const key = (name || "").trim().toLowerCase();
  if (!key || !state.meta) return null;
  const faction = state.meta.factions.find((f) => f.has_icon && f.name.toLowerCase() === key);
  if (!faction) return null;
  return h("img", {
    class: "faction-icon", src: apiUrl(`/api/faction-icon/${faction.id}`), alt: "", title: faction.name, loading: "lazy",
    onerror: (e) => e.target.remove(),
  });
}

// ---------- company logos ----------
// A company's logo is one you confirmed from Sarna. Names are matched ignoring case and outer space.
const companyKey = (name) => (name || "").trim().toLowerCase();

async function loadCompanyLogos() {
  try {
    state.companyLogos = new Map((await api("GET", "/api/company-logos")).map((l) => [l.key, l]));
  } catch {
    state.companyLogos = new Map(); // logos are decoration, so a failure here must not stop the page
  }
  await cleanSavedLogos();
  try {
    state.companyColors = new Map((await api("GET", "/api/company-colors")).map((c) => [c.key, c.color]));
  } catch {
    state.companyColors = new Map();
  }
}

// ---------- company strip colors ----------
const DEFAULT_STRIP_COLOR = "#8A1C2B"; // the crimson every strip has until its company is given a color
const STRIP_SWATCHES = ["#8A1C2B", "#2E3036", "#2F5D3A", "#1F4E8C", "#C9A227", "#6B3FA0", "#B5541C", "#0F6E6E", "#7A7F87", "#D9D2C5"];
const HEX6 = /^#[0-9A-F]{6}$/;

// Near-black text on a pale color and white on a dark one, by relative luminance.
function textOnColor(hex) {
  const channel = (i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5) > 0.35 ? "#14100F" : "#FFFFFF";
}

// The inline style that colors a mini's strip, or null when its company has no color (the stylesheet's crimson applies).
function stripStyle(copy) {
  const color = state.companyColors?.get(companyKey(copy.company));
  return color && HEX6.test(color) ? `--strip: ${color}; --strip-text: ${textOnColor(color)}` : null;
}

const companyLogoUrl = (logo) => apiUrl(`/api/company-logos/image?name=${encodeURIComponent(logo.name)}&v=${encodeURIComponent(logo.version)}`);

// The logo for a company name: the one you saved, else MekBay's faction logo when the company is also a faction.
function companyIcon(name) {
  const logo = state.companyLogos?.get(companyKey(name));
  if (!logo) return factionIcon(name);
  const { src, solid } = logoImage(logo);
  return h("img", {
    class: "faction-icon company-logo" + (solid ? " opaque" : ""), src, alt: "",
    title: logo.name, loading: "lazy", onerror: (e) => e.target.remove(),
  });
}

// A logo with a solid white background (most JPEGs) gets that white cut out, so it sits on a banner or strip
// like a see-through logo. Only the white that touches the picture's edge is removed, so white inside the
// logo, and a colored disc like Eridani's gold, stay as they are. The result is a PNG kept in memory by
// logo and version. A logo on any other solid color is shown unchanged.
const LOGO_CLEAN_SIZE = 192; // long side in pixels, plenty for the 56px banner
const WHITE = 225; // a pixel counts as background white when its darkest channel is at least this

async function cleanLogo(logo) {
  const img = new Image();
  img.src = companyLogoUrl(logo);
  await img.decode();
  const scale = Math.min(1, LOGO_CLEAN_SIZE / Math.max(img.naturalWidth, img.naturalHeight));
  const w = Math.max(1, Math.round(img.naturalWidth * scale));
  const hgt = Math.max(1, Math.round(img.naturalHeight * scale));
  const canvas = h("canvas", { width: w, height: hgt });
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, w, hgt);
  const image = ctx.getImageData(0, 0, w, hgt);
  const px = image.data;
  const lightness = (i) => Math.min(px[i * 4], px[i * 4 + 1], px[i * 4 + 2]);
  const corners = [0, w - 1, (hgt - 1) * w, hgt * w - 1];
  if (!corners.every((i) => lightness(i) >= WHITE)) return null; // the background is not white

  // Flood in from every edge pixel that is white, clearing the white that connects to the outside.
  const gone = new Uint8Array(w * hgt);
  const stack = [];
  const push = (i) => { if (!gone[i] && lightness(i) >= WHITE) { gone[i] = 1; stack.push(i); } };
  for (let x = 0; x < w; x++) { push(x); push((hgt - 1) * w + x); }
  for (let y = 0; y < hgt; y++) { push(y * w); push(y * w + w - 1); }
  while (stack.length) {
    const i = stack.pop();
    const x = i % w;
    if (x > 0) push(i - 1);
    if (x < w - 1) push(i + 1);
    if (i >= w) push(i - w);
    if (i < w * (hgt - 1)) push(i + w);
  }
  // Pixels next to the cleared area that are still light are the soft edge of the logo. Fade them out
  // by how white they are, so there is no pale ring around it.
  const soft = [];
  for (let i = 0; i < gone.length; i++) {
    if (gone[i]) continue;
    const x = i % w;
    const near = (x > 0 && gone[i - 1]) || (x < w - 1 && gone[i + 1]) || (i >= w && gone[i - w]) || (i < w * (hgt - 1) && gone[i + w]);
    if (near && lightness(i) > 150) soft.push(i);
  }
  for (const i of soft) px[i * 4 + 3] = Math.round(255 * Math.min(1, Math.max(0, (255 - lightness(i)) / (255 - 150))));
  for (let i = 0; i < gone.length; i++) if (gone[i]) px[i * 4 + 3] = 0;
  ctx.putImageData(image, 0, 0);
  return canvas.toDataURL("image/png");
}

const logoCacheKey = (logo) => `${logo.key}|${logo.version}`;

// Cuts the white out of every saved logo that has a solid background, before the roster is drawn, so a
// logo never flashes up with its white box. A failure just leaves that logo as it is.
async function cleanSavedLogos() {
  state.logoClean = new Map();
  await Promise.all([...(state.companyLogos?.values() || [])].filter((l) => l.opaque).map(async (logo) => {
    try {
      const url = await cleanLogo(logo);
      if (url) state.logoClean.set(logoCacheKey(logo), url);
    } catch {
      // cannot read the pixels or decode the image: the original is used
    }
  }));
}

// What to put in an <img> for a saved logo: the white-cut copy when there is one, else the stored file.
// `solid` is true when the logo still has a solid background, so the page can round its corners.
function logoImage(logo) {
  const clean = state.logoClean?.get(logoCacheKey(logo));
  return { src: clean || companyLogoUrl(logo), solid: logo.opaque && !clean };
}

// The strip color part of the company dialog: swatches, a color box and a hex box, with a live preview strip.
// Nothing is saved until "Save color" is pressed.
function colorSection(name, finish) {
  const saved = state.companyColors?.get(companyKey(name)) || null;
  let draft = saved || DEFAULT_STRIP_COLOR;
  const preview = h("div", { class: "copy-strip color-preview" }, stripLine(null, name));
  const hexBox = h("input", { type: "text", class: "hex-input", maxlength: "7", spellcheck: "false", "aria-label": "Hex color", placeholder: DEFAULT_STRIP_COLOR });
  const picker = h("input", { type: "color", class: "color-input", "aria-label": "Pick a color" });
  const swatches = h("div", { class: "swatches" });
  const root = h("div", { class: "color-section" });
  const normalize = (value) => {
    let v = value.trim().toUpperCase();
    if (!v.startsWith("#")) v = "#" + v;
    if (/^#[0-9A-F]{3}$/.test(v)) v = "#" + [...v.slice(1)].map((d) => d + d).join("");
    return HEX6.test(v) ? v : null;
  };
  const show = () => {
    preview.setAttribute("style", `--strip: ${draft}; --strip-text: ${textOnColor(draft)}`);
    picker.value = draft.toLowerCase();
    if (document.activeElement !== hexBox) hexBox.value = draft;
    for (const b of root.querySelectorAll("button.swatch")) b.setAttribute("aria-pressed", String(b.dataset.color === draft));
  };
  const set = (color) => { draft = color; show(); };
  swatches.append(...STRIP_SWATCHES.map((color) => h("button", {
    type: "button", class: "swatch", style: `background: ${color}`, title: color, "data-color": color, "aria-label": `Color ${color}`,
    onclick: () => set(color),
  })));
  picker.addEventListener("input", () => set(picker.value.toUpperCase()));
  hexBox.addEventListener("input", () => { const v = normalize(hexBox.value); if (v) set(v); });
  hexBox.addEventListener("blur", show);
  const save = async () => {
    if (await guarded(() => api("PUT", "/api/company-colors", { name, color: draft }))) { toast(`Saved the ${name} strip color`); await finish(); }
  };
  const reset = async () => {
    if (!saved) { set(DEFAULT_STRIP_COLOR); return; }
    if (await guarded(() => api("DELETE", `/api/company-colors?name=${encodeURIComponent(name)}`))) { toast(`${name} is back to the default color`); await finish(); }
  };
  // Colors taken from the faction logos, loaded the first time the section is opened.
  const paletteBody = h("div", { class: "palette-body" }, h("p", { class: "muted small" }, "Loading…"));
  const palette = h("details", { class: "faction-palette" }, h("summary", {}, "More colors from faction logos"), paletteBody);
  palette.addEventListener("toggle", async () => {
    if (!palette.open || palette.dataset.loaded) return;
    palette.dataset.loaded = "1";
    try {
      state.factionColors ??= await api("GET", "/api/faction-colors");
      paletteBody.replaceChildren(...factionPaletteGroups(state.factionColors, set));
      show();
    } catch (err) {
      palette.dataset.loaded = ""; // try again the next time it is opened
      paletteBody.replaceChildren(h("p", { class: "muted small" }, err.message));
    }
  });
  show();
  root.append(
    h("h4", {}, "Strip color"),
    preview, swatches, palette,
    h("div", { class: "color-row" }, picker, hexBox),
    h("div", { class: "color-row" },
      h("button", { type: "button", class: "icon primary", onclick: save }, "Save color"),
      h("button", { type: "button", class: "icon", onclick: reset }, "Back to crimson")),
    h("p", { class: "muted small" }, "Text on the strip turns dark on a pale color so it stays readable."));
  return root;
}

// The faction-logo colors as labelled groups of swatches. Factions that share a color (the Wolf splinters, the
// Star League entries) get one swatch named for all of them, and each group runs around the color wheel.
const PALETTE_GROUPS = [
  ["Inner Sphere", ["Inner Sphere"]], ["Clans", ["IS Clan", "HW Clan"]],
  ["Periphery", ["Periphery"]], ["Mercenary and other", ["Mercenary", "Other"]],
];

function hueOf(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  if (max === min) return -1; // greys come first
  const d = max - min;
  const hue = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return (hue * 60 + 360) % 360;
}

function factionPaletteGroups(colors, set) {
  return PALETTE_GROUPS.map(([label, groups]) => {
    const byColor = new Map();
    for (const f of colors.filter((c) => groups.includes(c.group))) {
      if (!byColor.has(f.color)) byColor.set(f.color, []);
      byColor.get(f.color).push(f.name);
    }
    if (!byColor.size) return null;
    const entries = [...byColor.entries()].sort(([a], [b]) => hueOf(a) - hueOf(b));
    return h("div", { class: "palette-group" },
      h("h5", {}, label),
      h("div", { class: "swatches" }, entries.map(([color, names]) => h("button", {
        type: "button", class: "swatch", style: `background: ${color}`, "data-color": color,
        title: `${names.join(", ")} · ${color}`, "aria-label": `${names.join(", ")} ${color}`, onclick: () => set(color),
      }))));
  }).filter(Boolean);
}

// Looks the company up on Sarna and shows what it found, and lets you pick its strip color. Nothing is
// saved until "Use this" or "Save color" is pressed.
async function pickCompanyLogo(name, onDone) {
  name = (name || "").trim();
  if (!name) { toast("Type a company name first.", true); return; }
  const existing = state.companyLogos?.get(companyKey(name));
  const body = h("div", { class: "logo-results" }, h("p", { class: "muted" }, "Looking on Sarna…"));
  let modal;
  const finish = async () => { await loadCompanyLogos(); renderRoster(); modal.close(true); if (onDone) onDone(); };
  const use = async (c) => {
    const saved = await guarded(() => api("PUT", "/api/company-logos", { name, image_url: c.image_url, source_title: c.title }));
    if (saved) { toast(`Saved the ${name} logo`); await finish(); }
  };
  const drop = async () => {
    if (await guarded(() => api("DELETE", `/api/company-logos?name=${encodeURIComponent(name)}`))) { toast(`Removed the ${name} logo`); await finish(); }
  };
  const cancel = h("button", { type: "button", onclick: () => modal.close(false) }, "Close");
  modal = openModal(h("div", { class: "modal logo-picker", role: "dialog", "aria-modal": "true", "aria-label": `Logo for ${name}` },
    h("h3", {}, `Logo and color for ${name}`),
    existing ? h("div", { class: "logo-current" }, h("img", { src: companyLogoUrl(existing), alt: "", class: "logo-thumb" + (existing.opaque ? " opaque" : "") }),
      h("span", {}, "Current logo"), h("button", { type: "button", class: "icon danger", onclick: drop }, "Remove")) : null,
    body,
    colorSection(name, () => finish()),
    h("div", { class: "modal-actions" }, cancel)));
  cancel.focus();
  try {
    const found = await api("GET", `/api/company-logos/candidates?name=${encodeURIComponent(name)}`);
    body.replaceChildren(...(found.length
      ? [h("p", { class: "muted small" }, "Pick the one that is the logo. Nothing is saved until you do."),
        h("div", { class: "logo-grid" }, found.map((c) => h("figure", { class: "logo-card" },
          h("img", { src: c.image_url, alt: c.title, class: "logo-thumb", referrerpolicy: "no-referrer" }),
          h("figcaption", {}, h("a", { href: c.page_url, target: "_blank", rel: "noopener noreferrer" }, c.title)),
          h("button", { type: "button", class: "icon primary", onclick: () => use(c) }, "Use this"))))]
      : [h("p", { class: "muted" }, `Sarna has no page with an image for "${name}".`)]));
  } catch (err) {
    body.replaceChildren(h("p", { class: "muted" }, err.message));
  }
}

// One line of a card's strip: an optional logo, then the text.
const stripLine = (icon, text, sub) => h("div", { class: "strip-line" + (sub ? " sub" : "") }, icon, h("span", { class: "strip-text", title: text }, text));

// What a named mini is called on its card: the company with the faction under it. A mini with only a
// faction shows that on the big line, and one with only a note shows the note there.
function stripLines(copy) {
  if (copy.company) {
    return [stripLine(companyIcon(copy.company), copy.company), copy.faction ? stripLine(factionIcon(copy.faction), copy.faction, true) : null];
  }
  if (copy.faction) return [stripLine(factionIcon(copy.faction), copy.faction)];
  return [stripLine(null, copy.note)];
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
    h("button", {
      type: "button", class: "icon logo-find", title: "Find this company's logo on Sarna and choose its strip color",
      onclick: (e) => pickCompanyLogo(e.currentTarget.previousElementSibling.querySelector("input").value),
    }, "Logo & color…"),
    h("button", {
      type: "button", class: "icon photo-find", title: "Add or crop a photo of this mini",
      onclick: () => openPhotoDialog(item, copy),
    }, copy.photo ? "Change photo…" : "Photo…"),
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
  const toTray = h("button", { class: "icon", title: "Add one to the force tray", onclick: () => trayAdd(item.id, 1) }, "+ tray");
  if (SHARED) return [toTray]; // the tray is the visitor's own, so it is the one action left
  if (state.editingCopy === copy.id) {
    return h("button", { class: "icon primary", title: "Finish editing", onclick: finishEditing }, "Done");
  }
  return [
    toTray,
    " ",
    quickPaint ? paintToggle(copy) : null,
    quickPaint ? " " : null,
    h("button", { class: "icon", title: "Switch this one mini to another variant", onclick: (e) => startSwap(item, swapCell(e.currentTarget), copy) }, "Variant"),
    " ",
    h("button", { class: "icon", title: "Add another mini of this variant", onclick: () => addAnother(item) }, "+ another"),
    " ",
    h("button", { class: "icon", title: "Edit this mini", "aria-label": `Edit ${copyName(copy)}`, onclick: () => { state.editingCopy = copy.id; renderRoster(); } }, "✎"),
    quickPaint ? null : " ", // a card has the remove button in its top bar
    quickPaint ? null : h("button", { class: "icon danger", title: "Remove this mini from the collection", "aria-label": `Remove ${item.name}`, onclick: () => removeMini(item, copy) }, "×"),
  ];
}

// Set a mini's material from its card without opening the editor. A mini with none shows a "+ Material"
// button, and one with a material shows it as a pill. Either opens a dropdown, which closes again when it
// loses focus or Escape is pressed. A material typed in the editor that is not one of the usual ones
// stays in the list so it keeps showing.
function materialControl(mini) {
  if (SHARED) return mini.material ? h("div", {}, h("span", { class: "material-static" }, mini.material)) : null;
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

// ---------- selecting minis for a bulk change ----------
// A mini is ticked with its checkbox, and a shift-click ticks everything between it and the one ticked last.
// Ticked minis are remembered by id across redraws. A mini that is no longer shown (filtered out or removed)
// drops out of the selection, so a bulk change never touches something you cannot see.

const CIRCLE_X_SVG = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="10.2"/><path d="M8 8l8 8M16 8l-8 8"/></svg>';
const plural = (n) => `${n} ${n === 1 ? "mini" : "minis"}`;

// Every mini the current filters show, whatever the grouping, each counted once.
const shownMiniIds = () => [...new Set(state.items.flatMap((item) => visibleMinis(item).map((m) => m.id)))];

function pruneSelection() {
  const shown = new Set(shownMiniIds());
  for (const id of [...state.selected]) if (!shown.has(id)) state.selected.delete(id);
  if (state.lastPicked != null && !shown.has(state.lastPicked)) state.lastPicked = null;
}

function setSelected(ids, on) {
  for (const id of ids) { if (on) state.selected.add(id); else state.selected.delete(id); }
  syncSelectionUI();
}

function clearSelection() {
  state.selected.clear();
  state.lastPicked = null;
  syncSelectionUI();
}

function pickMini(mini, on, shift) {
  let ids = [mini.id];
  if (shift && state.lastPicked != null) {
    const order = [...document.querySelectorAll("input[data-mini]")].map((box) => Number(box.dataset.mini));
    const from = order.indexOf(state.lastPicked), to = order.indexOf(mini.id);
    if (from >= 0 && to >= 0) ids = order.slice(Math.min(from, to), Math.max(from, to) + 1);
  }
  state.lastPicked = mini.id;
  setSelected(ids, on);
}

function selectBox(item, mini) {
  return h("input", {
    type: "checkbox", class: "sel-input", "data-mini": mini.id, checked: state.selected.has(mini.id),
    "aria-label": `Select ${item.name}`,
    onclick: (e) => pickMini(mini, e.target.checked, e.shiftKey),
  });
}

// A checkbox over a set of minis (a group, or everything shown): ticked when all are, a dash when some are.
// `ids` is a list, or a function that gives the list, for a set that changes.
function scopeCheckbox(label, ids) {
  const box = h("input", { type: "checkbox", class: "sel-input sel-scope", "aria-label": label });
  box._ids = ids;
  box.addEventListener("click", () => {
    const list = typeof box._ids === "function" ? box._ids() : box._ids;
    setSelected(list, !(list.length && list.every((id) => state.selected.has(id))));
  });
  return box;
}

// Brings every checkbox, highlight and the bulk bar in line with the selection without redrawing the roster.
function syncSelectionUI() {
  for (const box of document.querySelectorAll("input[data-mini]")) {
    const on = state.selected.has(Number(box.dataset.mini));
    box.checked = on;
    box.closest("article, tr")?.classList.toggle("is-selected", on);
  }
  for (const box of document.querySelectorAll("input.sel-scope")) {
    const ids = typeof box._ids === "function" ? box._ids() : box._ids || [];
    const picked = ids.filter((id) => state.selected.has(id)).length;
    box.checked = ids.length > 0 && picked === ids.length;
    box.indeterminate = picked > 0 && picked < ids.length;
  }
  renderBulkBar();
}

function renderBulkBar() {
  const picked = state.selected.size, shown = shownMiniIds().length;
  $("bulkbar").hidden = picked === 0;
  document.body.classList.toggle("has-bulkbar", picked > 0); // room at the bottom so the bar never hides the last cards
  if (!picked) { closeBulkPopover(); return; }
  $("bulk-count-text").replaceChildren(`${picked} selected `, h("small", {}, `of ${shown} shown`));
  $("bulk-select-all").hidden = picked >= shown;
  $("bulk-select-all").textContent = `Select all ${shown} shown`;
  $("bulk-remove").textContent = `Remove ${picked}…`;
}

// The small panel above a bulk button: the material choices, or a box to type a box, faction or tag in.
function closeBulkPopover() {
  $("bulk-popover").hidden = true;
  for (const button of document.querySelectorAll("#bulkbar [aria-haspopup]")) button.setAttribute("aria-expanded", "false");
}

function openBulkPopover(button, parts) {
  const pop = $("bulk-popover");
  pop.replaceChildren(...parts);
  pop.hidden = false;
  const bar = $("bulkbar");
  pop.style.left = `${Math.max(8, Math.min(button.offsetLeft, bar.clientWidth - pop.offsetWidth - 12))}px`; // stays inside the bar
  for (const b of document.querySelectorAll("#bulkbar [aria-haspopup]")) b.setAttribute("aria-expanded", String(b === button));
  (pop.querySelector("input") || pop.querySelector(".bulk-menu-item"))?.focus();
}

// Saves the same change on every selected mini, then reloads. The selection stays so another change can
// follow, and the reload also puts things right if the change was refused.
async function bulkPatch(fields, message) {
  const ids = [...state.selected];
  closeBulkPopover();
  await guarded(async () => {
    const result = await api("PATCH", "/api/minis", { ids, ...fields });
    toast(message(plural(result.updated)));
  });
  await loadCollection();
}

const bulkMaterial = (value) => bulkPatch({ material: value }, (n) => (value ? `Set ${value} on ${n}` : `Cleared the material on ${n}`));
const bulkPainted = (on) => bulkPatch({ painted: on }, (n) => `Marked ${n} ${on ? "painted" : "unpainted"}`);

function materialMenu() {
  const choices = [["", NO_MATERIAL], ...MATERIALS.map((m) => [m, m])]; // the same order as a card's dropdown
  return [
    h("div", { class: "bulk-menu-head" }, `Material for ${plural(state.selected.size)}`),
    ...choices.map(([value, label]) => h("button", {
      type: "button", role: "menuitem", class: "bulk-menu-item", onclick: () => bulkMaterial(value),
    }, label)),
  ];
}

const BULK_FIELDS = {
  box: { label: "Box", attrs: { "data-suggest": "box" } },
  faction: { label: "Faction", attrs: { list: "copy-faction-options" } },
  tag: { label: "Tag", attrs: { list: "tag-options" } },
};

// A box to type a value in, with the same suggestions as the matching field elsewhere. Apply sets it on every
// selected mini. Clearing is its own button, so an empty box can never wipe a field by accident.
function fieldPopover(field) {
  const spec = BULK_FIELDS[field];
  const lower = spec.label.toLowerCase();
  const input = h("input", {
    type: "text", class: "bulk-input", autocomplete: "off", placeholder: spec.label,
    "aria-label": `${spec.label} for the selected minis`, ...spec.attrs,
  });
  const set = (value) => bulkPatch({ [field]: value }, (n) => (value ? `Set the ${lower} to "${value}" on ${n}` : `Cleared the ${lower} on ${n}`));
  const apply = () => { const value = input.value.trim(); if (value) set(value); else input.focus(); };
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    if (suggest.input === input && !$("suggest").hidden && suggest.index >= 0) return; // Enter picks the highlighted suggestion
    e.preventDefault();
    apply();
  });
  return [
    h("div", { class: "bulk-menu-head" }, `${spec.label} for ${plural(state.selected.size)}`),
    h("div", { class: "bulk-field" }, input, h("button", { type: "button", class: "primary", onclick: apply }, "Apply")),
    h("button", { type: "button", class: "bulk-menu-item", onclick: () => set("") }, `Clear the ${lower}`),
  ];
}

async function bulkRemove() {
  const ids = [...state.selected];
  closeBulkPopover();
  const sure = await confirmDialog(
    `Remove ${plural(ids.length)} from your collection? A unit with none left also leaves any saved forces.`,
    { confirmLabel: `Remove ${ids.length}`, danger: true });
  if (!sure) return;
  await guarded(async () => {
    const result = await api("DELETE", "/api/minis", { ids });
    toast(`Removed ${plural(result.removed)}`);
  });
  await loadCollection();
  for (const id of [...state.tray.units.keys()]) if (!itemById(id)) state.tray.units.delete(id); // a unit with no minis left
  renderTray();
  await loadForces();
}

function setupBulkBar() {
  const all = $("select-all-shown");
  all.classList.add("sel-scope");
  all._ids = shownMiniIds;
  all.addEventListener("click", () => {
    const ids = shownMiniIds();
    setSelected(ids, !(ids.length && ids.every((id) => state.selected.has(id))));
  });
  $("bulk-select-all").addEventListener("click", () => setSelected(shownMiniIds(), true));
  $("bulk-clear").addEventListener("click", clearSelection);
  $("bulk-remove").addEventListener("click", bulkRemove);
  for (const button of document.querySelectorAll("#bulkbar [data-bulk]")) {
    button.addEventListener("click", () => {
      const kind = button.dataset.bulk;
      if (kind === "painted" || kind === "unpainted") { bulkPainted(kind === "painted"); return; }
      if (button.getAttribute("aria-expanded") === "true") { closeBulkPopover(); return; }
      openBulkPopover(button, kind === "material" ? materialMenu() : fieldPopover(kind));
    });
  }
  // A click outside the panel closes it. A suggestion belongs to the panel, but the list removes the item you
  // press before the click arrives, so whether the press was inside is decided on mouse-down, while it still exists.
  const insidePanel = "#bulk-popover, #bulkbar [data-bulk], #suggest";
  let pressedInside = false;
  document.addEventListener("mousedown", (e) => { pressedInside = Boolean(e.target.closest(insidePanel)); }, true);
  document.addEventListener("click", (e) => {
    const inside = pressedInside || Boolean(e.target.closest(insidePanel)); // a key press has no mouse-down
    pressedInside = false;
    if (!inside && !$("bulk-popover").hidden) closeBulkPopover();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || document.querySelector(".modal-backdrop")) return;
    if (!$("bulk-popover").hidden) {
      const opener = document.querySelector('#bulkbar [aria-expanded="true"]');
      closeBulkPopover();
      opener?.focus();
      return;
    }
    if (e.target.closest?.("input[type=text], input[type=search], textarea, select")) return; // Escape in a box belongs to that box
    if (state.selected.size) clearSelection();
  });
}

function buildMiniRow(item, mini) {
  const editing = state.editingCopy === mini.id;
  const detail = isNamed(mini) ? copyLine(mini) : "";
  return h("tr", { class: isNamed(mini) ? "copy-line" : "mini-line" },
    h("td", { class: "sel-cell" }, SHARED ? null : selectBox(item, mini)),
    h("td", {}, nameLink(item),
      editing ? copyEditor(item, mini)
        : (detail || mini.tag) ? h("div", { class: "copy-label", title: mini.note || null }, companyIcon(mini.company), factionIcon(mini.faction), detail, detail && mini.tag ? " " : null, tagPill(mini)) : null),
    h("td", {}, kindLabel(item)),
    // A shared page shows the box, material and painted state as text. The owner edits them in place.
    h("td", {}, SHARED ? copyBox(item, mini) || "" : h("input", {
      type: "text", class: "box-input", value: mini.box, "data-suggest": "box", "data-entry": item.id, autocomplete: "off",
      placeholder: item.box || "none", "aria-label": `Box for ${copyName(mini)}`,
      onchange: (e) => setCopy(mini, { box: e.target.value }),
    })),
    h("td", { class: "num" }, item.tons == null ? "" : Number(item.tons).toString()),
    h("td", { class: "num" }, item.bv),
    h("td", {}, SHARED ? mini.material : h("input", {
      type: "text", class: "box-input", value: mini.material, list: "material-options", autocomplete: "off",
      placeholder: "none", "aria-label": `Material of ${copyName(mini)}`,
      onchange: (e) => setCopy(mini, { material: e.target.value }),
    })),
    h("td", { class: "num" }, SHARED ? (mini.painted ? "Yes" : "No") : h("input", {
      type: "checkbox", checked: mini.painted, "aria-label": `Painted: ${copyName(mini)}`,
      onchange: (e) => setCopy(mini, { painted: e.target.checked }),
    })),
    h("td", { class: "actions" }, copyActions(item, mini)));
}

// A mini's card has a bar on top with its select box, the chassis name and a button that removes it, then the
// photo, the unit's stats, its material, tag and box, and a brush badge when painted. A named mini also has
// a crimson strip under the photo naming its company and faction, with its note below the stats. The mech's
// name is the first line under the photo or strip. The pencil swaps the details for the editing boxes.
// The model code sits in the stats line.
function buildMiniCard(item, mini) {
  const editing = state.editingCopy === mini.id;
  // Inside a company's group the banner already names the company and faction, so the strip is left off.
  const inCompanyGroup = isGrouped() && state.groupBy === "company" && Boolean(mini.company.trim());
  const noteLine = isNamed(mini) && (mini.company || mini.faction) && mini.note ? h("div", { class: "copy-note-line" }, mini.note) : null;
  const remove = h("button", {
    type: "button", class: "sel-x", title: "Remove this mini from the collection", "aria-label": `Remove ${item.name}`,
    onclick: () => removeMini(item, mini),
  });
  remove.innerHTML = CIRCLE_X_SVG;
  const stats = [item.model || null, item.kind === "mech" ? null : kindLabel(item), `${item.tons ?? "?"}t`, `${item.bv} BV`];
  return h("article", { class: "mini-card" + (isNamed(mini) ? " is-copy" : "") + (editing ? " is-editing" : "") + (state.selected.has(mini.id) ? " is-selected" : "") },
    SHARED ? null : h("div", { class: "sel-bar" }, // it holds only edit controls, so a shared page leaves it off
      selectBox(item, mini),
      h("div", { class: "sel-title" }), // empty: the mech's name is in the strip under the photo
      remove),
    miniPhotoFrame(item, mini),
    isNamed(mini) && !inCompanyGroup ? h("div", { class: "copy-strip", style: stripStyle(mini) }, stripLines(mini)) : null,
    h("div", { class: "card-body" },
      h("div", { class: "card-title" }, h("button", {
        type: "button", class: "unit-link", title: `${item.name}: show unit details`, onclick: () => showUnitDetail(item.unit_uuid, mini),
      }, item.chassis)),
      h("div", { class: "card-name card-variant-slot" }), // empty until the Variant picker opens in it
      h("div", { class: "card-meta" }, stats.filter(Boolean).join(" · ")),
      editing ? copyEditor(item, mini) : [materialControl(mini), noteLine, tagPill(mini) ? h("div", {}, tagPill(mini)) : null],
      copyBox(item, mini) ? h("div", { class: "card-box" }, copyBox(item, mini)) : null,
      h("div", { class: "card-actions" }, copyActions(item, mini, true))));
}

// Grouped by box or by material, an entry sits in every group its minis are in, and each group shows only
// those minis. `scope` is a test of one mini for the group being drawn, or null to show the whole entry.
const miniBoxName = (item, mini) => copyBox(item, mini) || "";
const boxesOf = (item) => [...new Set(item.minis.map((m) => miniBoxName(item, m)))];
const materialsOf = (item) => [...new Set(item.minis.map((m) => m.material))];
const companiesOf = (item) => [...new Set(item.minis.map((m) => m.company.trim()))];
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
  tech: "Unknown", role: "No role", painted: "Unpainted", material: NO_MATERIAL, company: "No company",
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
  company: { of: (i) => companiesOf(i).map((c) => c || null), order: null },
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
  if (state.groupBy === "company") {
    const company = name === NO_GROUP.company ? "" : name;
    return (item, mini) => mini.company.trim() === company;
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

// "6 minis" when every entry holds one mini, else "3 entries · 5 minis". Each mini is its own item now, so
// the entry count only says something when it differs from the mini count.
function countText(entries, minis) {
  const minisText = `${minis} ${minis === 1 ? "mini" : "minis"}`;
  return entries === minis ? minisText : `${entries} ${entries === 1 ? "entry" : "entries"} · ${minisText}`;
}

// Adds every mini of a group to the force tray, once each. Pressing it again changes nothing.
function trayAddGroup(list, scope) {
  for (const item of list) {
    const count = minisIn(item, scope).length;
    const have = state.tray.units.get(item.id) || 0;
    if (count > have) trayAdd(item.id, count - have);
  }
}

// The header of a company group: a banner in the company's strip color with its logo, its faction (the most
// common one among its minis), how many minis and BV it holds, and how many are painted.
function companyBanner(name, list, collapsed, toggle) {
  const scope = groupScope(name);
  const minis = list.flatMap((item) => minisIn(item, scope));
  const t = totals(list, scope);
  const pct = t.models ? Math.round((100 * t.painted) / t.models) : 0;
  const counts = new Map();
  for (const m of minis) if (m.faction) counts.set(m.faction, (counts.get(m.faction) || 0) + 1);
  const faction = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || "";
  const color = state.companyColors?.get(companyKey(name));
  const bg = color && HEX6.test(color) ? color : DEFAULT_STRIP_COLOR;
  const logo = state.companyLogos?.get(companyKey(name));
  const ids = minis.map((m) => m.id);
  return h("div", { class: "group-head-row company-head", style: `--strip: ${bg}; --strip-text: ${textOnColor(bg)}` },
    SHARED ? null : scopeCheckbox(`Select every mini in ${name}`, ids),
    h("button", { class: "group-toggle company-banner", "aria-expanded": String(!collapsed), onclick: toggle },
      h("span", { class: "chev", "aria-hidden": "true" }, collapsed ? "▸" : "▾"),
      h("span", { class: "banner-logo-slot" }, logo
        ? h("img", { class: "banner-logo" + (logoImage(logo).solid ? " opaque" : ""), src: logoImage(logo).src, alt: "", onerror: (e) => e.target.remove() })
        : factionIcon(name) || factionIcon(faction)), // no company logo: the company as a faction, else its faction's logo
      h("span", { class: "banner-names" }, h("strong", {}, name), faction ? h("span", { class: "banner-faction" }, faction) : null),
      h("span", { class: "banner-stats" },
        h("span", { class: "banner-count" }, `${countText(list.length, t.models)} · ${t.bv.toLocaleString()} BV`),
        h("span", { class: "bar", "aria-hidden": "true", title: `${t.painted} of ${t.models} painted` }, h("i", { style: `width:${pct}%` })),
        h("span", { class: "banner-painted" }, `${t.painted}/${t.models} painted`))),
    h("button", {
      type: "button", class: "icon banner-tray", title: `Add all ${t.models} to the force tray`,
      onclick: () => trayAddGroup(list, scope),
    }, "+ tray"));
}

function groupHeader(name, list, collapsed) {
  if (state.groupBy === "company" && name !== NO_GROUP.company) {
    return companyBanner(name, list, collapsed, () => {
      if (collapsed) state.collapsed.delete(collapseKey(name)); else state.collapsed.add(collapseKey(name));
      savePrefs();
      renderRoster();
    });
  }
  const t = totals(list, groupScope(name));
  const pct = t.models ? Math.round((100 * t.painted) / t.models) : 0;
  const toggle = () => {
    if (collapsed) state.collapsed.delete(collapseKey(name)); else state.collapsed.add(collapseKey(name));
    savePrefs();
    renderRoster();
  };
  const ids = list.flatMap((item) => minisIn(item, groupScope(name)).map((m) => m.id));
  return h("div", { class: "group-head-row" },
    SHARED ? null : scopeCheckbox(`Select every mini in ${name}`, ids),
    h("button", { class: "group-toggle", "aria-expanded": String(!collapsed), onclick: toggle },
      h("span", { class: "chev", "aria-hidden": "true" }, collapsed ? "▸" : "▾"), " ",
      h("strong", {}, name), " ",
      h("span", { class: "group-meta" }, `${countText(list.length, t.models)} · ${t.bv.toLocaleString()} BV`), " ",
      h("span", { class: "bar", "aria-hidden": "true", title: `${t.painted} of ${t.models} painted` },
        h("i", { style: `width:${pct}%` })), " ",
      h("span", { class: "group-meta" }, `${t.painted}/${t.models} painted`)));
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

// ---------- mini photos ----------
// A mini's photo is shown through a square window. The crop is a zoom (1 just covers the window) and the point
// of the photo (cx, cy, each 0 to 1) that sits in the middle of the window. The card and the crop dialog use
// the same math, so what you see while cropping is what the card shows. Sizes are fractions of the window.
const PHOTO_MAX_SIDE = 1200; // photos are shrunk to this many pixels on the long side before they are sent
const PHOTO_MAX_ZOOM = 6;
const clamp = (v, low, high) => Math.min(high, Math.max(low, v));

function cropGeometry(photo, zoom = photo.zoom, cx = photo.cx, cy = photo.cy) {
  const ratio = photo.width / photo.height;
  const W = (ratio >= 1 ? ratio : 1) * zoom; // the photo's width and height, in windows
  const H = (ratio >= 1 ? 1 : 1 / ratio) * zoom;
  const x = clamp(cx, 0.5 / W, 1 - 0.5 / W); // keep the window inside the photo
  const y = clamp(cy, 0.5 / H, 1 - 0.5 / H);
  return { W, H, cx: x, cy: y, left: 0.5 - x * W, top: 0.5 - y * H };
}

function applyCrop(img, photo, zoom, cx, cy) {
  const g = cropGeometry(photo, zoom, cx, cy);
  img.style.cssText = `width: ${g.W * 100}%; height: ${g.H * 100}%; left: ${g.left * 100}%; top: ${g.top * 100}%`;
  return g;
}

const miniPhotoUrl = (mini) => apiUrl(`/api/minis/${mini.id}/photo?v=${encodeURIComponent(mini.photo.version)}`);

function miniPhotoImage(mini) {
  const img = h("img", { class: "mini-photo", src: miniPhotoUrl(mini), alt: "", loading: "lazy", draggable: "false" });
  applyCrop(img, mini.photo);
  return img;
}

// Shrinks a photo file to a JPEG data URL. Returns { dataUrl, width, height }.
async function prepareImage(file) {
  if (!file || !file.type.startsWith("image/")) throw new Error("That file is not an image.");
  const bitmap = await createImageBitmap(file).catch(() => { throw new Error("The browser could not open that image."); });
  const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(1, Math.round(bitmap.width * scale));
  const height = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = h("canvas", { width, height });
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff"; // a see-through PNG becomes JPEG, so give it a background
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close?.();
  return { dataUrl: canvas.toDataURL("image/jpeg", 0.88), width, height };
}

const imageFileFrom = (list) => [...(list || [])].find((f) => f.type?.startsWith("image/")) || null;

// The photo dialog: choose a photo (button, drop or paste), drag it to move and zoom it, then save.
// Saving without choosing a new photo only changes the crop of the one the mini already has.
function openPhotoDialog(item, mini, firstFile) {
  let source = mini.photo ? { url: miniPhotoUrl(mini), width: mini.photo.width, height: mini.photo.height, dataUrl: null } : null;
  let zoom = mini.photo ? mini.photo.zoom : 1;
  let cx = mini.photo ? mini.photo.cx : 0.5;
  let cy = mini.photo ? mini.photo.cy : 0.5;
  let modal;
  let pasteHandler;

  const view = h("div", { class: "crop-view", tabindex: "0", role: "img", "aria-label": "Photo crop. Drag to move it." });
  const img = h("img", { class: "crop-img", alt: "", draggable: "false" });
  const empty = h("div", { class: "crop-empty" }, "Choose a photo, drop one here, or paste one.");
  const slider = h("input", { type: "range", min: "1", max: String(PHOTO_MAX_ZOOM), step: "0.01", value: String(zoom), "aria-label": "Zoom" });
  const saveButton = h("button", { type: "button", class: "icon primary" }, "Save photo");
  const fileInput = h("input", { type: "file", accept: "image/*" }); // never added to the page, opened by the button

  const show = () => {
    const shown = source ? img : empty;
    if (view.firstChild !== shown) view.replaceChildren(shown);
    slider.disabled = saveButton.disabled = !source;
    if (!source) return;
    if (img.getAttribute("src") !== source.url) img.src = source.url;
    const g = applyCrop(img, source, zoom, cx, cy);
    cx = g.cx; cy = g.cy;
    slider.value = String(zoom);
  };
  const setZoom = (value) => { zoom = clamp(value, 1, PHOTO_MAX_ZOOM); show(); };

  const load = async (file) => {
    try {
      const prepared = await prepareImage(file);
      source = { url: prepared.dataUrl, width: prepared.width, height: prepared.height, dataUrl: prepared.dataUrl };
      zoom = 1; cx = 0.5; cy = 0.5;
      show();
    } catch (err) {
      toast(err.message, true);
    }
  };

  // Dragging moves the photo under the window.
  let drag = null;
  view.addEventListener("pointerdown", (e) => {
    if (!source) return;
    view.setPointerCapture(e.pointerId);
    drag = { x: e.clientX, y: e.clientY };
    view.classList.add("dragging");
  });
  view.addEventListener("pointermove", (e) => {
    if (!drag || !source) return;
    const size = view.getBoundingClientRect().width / (parseFloat(getComputedStyle(document.documentElement).zoom) || 1);
    const g = cropGeometry(source, zoom, cx, cy);
    // Pointer movement is reported in on-screen pixels, so undo the page zoom before turning it into window fractions.
    const scale = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
    cx -= (e.clientX - drag.x) / scale / (size * g.W);
    cy -= (e.clientY - drag.y) / scale / (size * g.H);
    drag = { x: e.clientX, y: e.clientY };
    show();
  });
  const endDrag = () => { drag = null; view.classList.remove("dragging"); };
  view.addEventListener("pointerup", endDrag);
  view.addEventListener("pointercancel", endDrag);
  view.addEventListener("wheel", (e) => { if (source) { e.preventDefault(); setZoom(zoom * Math.exp(-e.deltaY * 0.0015)); } }, { passive: false });
  view.addEventListener("keydown", (e) => {
    if (!source) return;
    const step = 0.02 / zoom;
    const move = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
    if (!move) return;
    e.preventDefault();
    cx -= move[0]; cy -= move[1];
    show();
  });
  slider.addEventListener("input", () => setZoom(parseFloat(slider.value)));
  fileInput.addEventListener("change", () => { if (fileInput.files[0]) load(fileInput.files[0]); });

  const body = h("div", { class: "modal photo-dialog", role: "dialog", "aria-modal": "true", "aria-label": `Photo of ${item.name}` },
    h("h3", {}, `Photo of ${item.name}`),
    view,
    h("p", { class: "muted small" }, "Drag to move the photo. Scroll or use the slider to zoom. You can also drop or paste an image."),
    h("label", { class: "zoom-row" }, "Zoom", slider),
    h("div", { class: "modal-actions photo-actions" },
      mini.photo ? h("button", {
        type: "button", class: "icon danger", onclick: async () => {
          if (await guarded(() => api("DELETE", `/api/minis/${mini.id}/photo`))) { toast("Removed the photo"); modal.close(true); await loadCollection(); }
        },
      }, "Remove photo") : null,
      h("button", { type: "button", class: "icon", onclick: () => fileInput.click() }, source ? "Choose another…" : "Choose photo…"),
      h("button", { type: "button", class: "icon", onclick: () => modal.close(false) }, "Cancel"),
      saveButton));

  saveButton.addEventListener("click", async () => {
    const crop = { zoom, cx, cy };
    const saved = await guarded(() => (source.dataUrl
      ? api("PUT", `/api/minis/${mini.id}/photo`, { image: source.dataUrl, width: source.width, height: source.height, ...crop })
      : api("PATCH", `/api/minis/${mini.id}/photo`, crop)));
    if (saved) { toast("Saved the photo"); modal.close(true); await loadCollection(); }
  });
  body.addEventListener("dragover", (e) => e.preventDefault());
  body.addEventListener("drop", (e) => {
    e.preventDefault();
    const file = imageFileFrom(e.dataTransfer?.files);
    if (file) load(file);
  });
  pasteHandler = (e) => {
    const file = imageFileFrom(e.clipboardData?.files);
    if (file) { e.preventDefault(); load(file); }
  };
  document.addEventListener("paste", pasteHandler);
  modal = openModal(body, () => document.removeEventListener("paste", pasteHandler));
  show();
  if (firstFile) load(firstFile);
  if (source) view.focus();
}

// The picture area of a card: the mini's own photo when it has one, else the unit's line art. Dropping an
// image file on it opens the photo dialog with that file. Clicking it opens the unit's details.
function miniPhotoFrame(item, mini) {
  const frame = h("div", {
    class: "photo clickable" + (mini.photo ? " has-photo" : item.has_art ? " has-art" : ""),
    onclick: () => showUnitDetail(item.unit_uuid, mini),
  }, mini.photo ? miniPhotoImage(mini) : artImage(item), mini.painted ? paintBadge() : null);
  if (SHARED) return frame; // no photo can be dropped on a shared page
  frame.addEventListener("dragover", (e) => {
    if ([...(e.dataTransfer?.items || [])].some((i) => i.kind === "file")) { e.preventDefault(); frame.classList.add("drop-target"); }
  });
  frame.addEventListener("dragleave", () => frame.classList.remove("drop-target"));
  frame.addEventListener("drop", (e) => {
    frame.classList.remove("drop-target");
    const file = imageFileFrom(e.dataTransfer?.files);
    if (!file) return;
    e.preventDefault();
    openPhotoDialog(item, mini, file);
  });
  return frame;
}

function artImage(item) {
  const placeholder = () => h("span", { class: "no-art" }, "No art yet");
  if (!item.has_art) return placeholder();
  return h("img", {
    src: apiUrl(`/api/art/${encodeURIComponent(item.unit_uuid)}`), loading: "lazy", alt: "", // the name sits right beside it
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
      body.append(h("tr", { class: "group-row" }, h("td", { colspan: 9 }, groupHeader(name, list, collapsed))));
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
  $("density-group").hidden = effectiveView() === "cards";
  $("group-actions").hidden = !isGrouped();
  $("group-by").value = state.groupBy;
  for (const button of $("group-by-group").querySelectorAll("button[data-group]")) {
    button.setAttribute("aria-pressed", String((button.dataset.group === "1") === state.groupOn));
  }
  $("group-by").classList.toggle("is-off", !state.groupOn);
  const current = Object.entries(SORT_CHOICES).find(([, v]) => v && v.key === state.sort.key);
  $("card-sort").value = current ? current[0] : "";
  const models = state.items.reduce((n, i) => n + i.qty, 0);
  $("view-count").textContent = countText(state.items.length, models);
}

// The view on screen. A phone always shows cards, since the table is a desktop tool. The saved choice in
// state.view is left alone, so using a phone never changes what a wide screen shows.
function effectiveView() {
  return isPhone() ? "cards" : state.view;
}

function renderRoster() {
  const view = effectiveView();
  document.body.classList.toggle("view-cards", view === "cards");
  // The group header already names the box, so the table's Box column would only repeat it.
  document.body.classList.toggle("group-by-box", isGrouped() && view === "table" && state.groupBy === "box");
  document.body.classList.toggle("compact", state.compact && view !== "cards");
  pruneSelection(); // a mini that is no longer shown leaves the selection
  const items = sortedItems();
  if (view === "cards") renderCards(items); else renderTable(items);
  syncViewControls();
  syncSelectionUI();
}

function setupViewControls() {
  const bar = $("view-controls");
  for (const button of bar.querySelectorAll("button[data-view]")) {
    button.addEventListener("click", () => { state.view = button.dataset.view; savePrefs(); renderRoster(); });
  }
  for (const button of bar.querySelectorAll("button[data-compact]")) {
    button.addEventListener("click", () => { state.compact = button.dataset.compact === "1"; savePrefs(); renderRoster(); });
  }
  // Crossing the phone width swaps between the cards and the saved desktop view.
  window.matchMedia(PHONE_QUERY).addEventListener("change", renderRoster);
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
          SHARED ? null : h("button", { class: "icon danger", title: "Delete force", onclick: (e) => { e.stopPropagation(); deleteForce(force); } }, "×")),
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
    : "Catalog not loaded. Run: python3 -m batchall catalog import";
  const boxText = meta.box_catalog
    ? `${meta.box_catalog} official boxes`
    : "No official boxes yet. Run: python3 -m batchall boxes import";
  $("catalog-status").textContent = `${unitsText} · ${boxText}`;

  let filterTimer = null;
  const refilter = () => { clearTimeout(filterTimer); filterTimer = setTimeout(loadCollection, 200); };
  for (const id of ["filter-q", ...SHEET_FILTER_IDS]) {
    $(id).addEventListener("input", refilter);
  }
  setupFilterSheet();
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
    tab.addEventListener("click", () => setTab(tab.dataset.view));
  }
  for (const tab of document.querySelectorAll("#tabbar [data-tab]")) {
    tab.addEventListener("click", () => setTab(tab.dataset.tab));
  }
  const phone = window.matchMedia(PHONE_QUERY);
  phone.addEventListener("change", placeCatalogStatus);
  placeCatalogStatus();
}

const PHONE_QUERY = "(max-width: 640px)";

function isPhone() {
  return window.matchMedia(PHONE_QUERY).matches;
}

// ---------- filter sheet ----------

// The filters that live in the phone sheet. The search box stays in the toolbar row.
const SHEET_FILTER_IDS = ["filter-box", "filter-tag", "filter-copy-faction", "filter-copy-company", "filter-material", "filter-kind", "filter-era", "filter-faction", "filter-painted"];

// Controls of #view-controls that move into the sheet's View section on a phone, in sheet order.
const SHEET_VIEW_CONTROLS = ["group-by-group", "sort-group"];

function activeFilterCount() {
  return SHEET_FILTER_IDS.filter((id) => {
    const el = $(id);
    return el.type === "checkbox" ? el.checked : el.value.trim() !== "";
  }).length;
}

function updateFilterBadge() {
  const count = activeFilterCount();
  const badge = $("filter-badge");
  badge.textContent = String(count);
  badge.hidden = count === 0;
}

function filterSheetOpen() {
  return !$("filter-sheet").hidden;
}

// Everything on the page outside the sheet and its backdrop, while the sheet is open. The sheet sits
// inside the page's own markup, so this is the siblings of the sheet and of each of its ancestors.
let inertBehindSheet = [];

function makePageInert() {
  const sheet = $("filter-sheet");
  for (let node = sheet; node !== document.body; node = node.parentElement) {
    for (const sibling of node.parentElement.children) {
      if (sibling === node || sibling === $("sheet-backdrop") || sibling.inert) continue;
      sibling.inert = true;
      inertBehindSheet.push(sibling);
    }
  }
}

function restorePage() {
  for (const el of inertBehindSheet) el.inert = false;
  inertBehindSheet = [];
}

function openFilterSheet() {
  if (!isPhone() || filterSheetOpen()) return;
  const sheet = $("filter-sheet");
  // The sheet is a dialog only while it is open on a phone. Wider, it is part of the toolbar.
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.hidden = false;
  $("sheet-backdrop").hidden = false;
  $("open-filters").setAttribute("aria-expanded", "true");
  makePageInert();
  $("filter-box").focus({ preventScroll: true });
}

function closeFilterSheet(returnFocus = true) {
  if (!filterSheetOpen()) return;
  const sheet = $("filter-sheet");
  sheet.hidden = true;
  sheet.removeAttribute("role");
  sheet.removeAttribute("aria-modal");
  $("sheet-backdrop").hidden = true;
  $("open-filters").setAttribute("aria-expanded", "false");
  restorePage(); // before focus moves back, since an inert button cannot take focus
  if (returnFocus) $("open-filters").focus();
}

function clearSheetFilters() {
  for (const id of SHEET_FILTER_IDS) {
    const el = $(id);
    if (el.type === "checkbox") el.checked = false; else el.value = "";
  }
  // The same event a typed change raises, so the collection reloads and the badge updates as they do.
  $("filter-kind").dispatchEvent(new Event("input", { bubbles: true }));
}

// The View controls are the page's own elements. They move into the sheet on a phone and back to
// their place in #view-controls on a wider screen, so ids and listeners stay as they are.
function placeViewControls(anchors) {
  const section = $("sheet-view");
  for (const id of SHEET_VIEW_CONTROLS) {
    const el = $(id);
    if (isPhone()) section.append(el); else anchors.get(el).before(el);
  }
}

function setupFilterSheet() {
  // Each control goes back before the element that followed it in #view-controls.
  const anchors = new Map(SHEET_VIEW_CONTROLS.map((id) => [$(id), $(id).nextElementSibling]));
  const sheet = $("filter-sheet");
  const toolbar = document.querySelector("#view-collection > .toolbar");

  $("open-filters").addEventListener("click", openFilterSheet);
  $("sheet-done").addEventListener("click", () => closeFilterSheet());
  $("sheet-backdrop").addEventListener("click", () => closeFilterSheet());
  $("filters-clear").addEventListener("click", clearSheetFilters);
  toolbar.addEventListener("input", updateFilterBadge);
  toolbar.addEventListener("change", updateFilterBadge);

  // Capture phase, so Escape closes the sheet before the page-level handlers see it.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !filterSheetOpen() || document.querySelector(".modal-backdrop")) return;
    e.preventDefault();
    e.stopPropagation();
    closeFilterSheet();
  }, true);

  // Keep the field being typed in clear of the on-screen keyboard.
  const reveal = (el) => {
    if (isPhone() && el?.matches?.("input, select, textarea")) el.scrollIntoView({ block: "center" });
  };
  sheet.addEventListener("focusin", (e) => reveal(e.target));
  window.visualViewport?.addEventListener("resize", () => {
    if (filterSheetOpen() && sheet.contains(document.activeElement)) reveal(document.activeElement);
  });

  window.matchMedia(PHONE_QUERY).addEventListener("change", () => {
    placeViewControls(anchors);
    if (!isPhone()) closeFilterSheet(false);
  });
  placeViewControls(anchors);
  updateFilterBadge();
}

// name is "collection", "forces" or "import". On a wide screen the tray and the saved forces are always
// on the page, so "forces" shows the collection view there. On a phone, CSS keyed on body[data-tab]
// decides what each tab shows.
function setTab(name) {
  if (SHARED && name === "import") return; // a shared page has no Import tab
  document.body.dataset.tab = name;
  const wide = name === "import" ? "import" : "collection";
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === wide));
  document.querySelectorAll("#tabbar [data-tab]").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === name)));
  $("view-collection").hidden = wide !== "collection";
  $("view-import").hidden = wide !== "import";
}

// On a phone the catalog status is a small line in the Collection tab. Elsewhere it stays in the top bar.
// There is one element, moved between the two places.
function placeCatalogStatus() {
  const status = $("catalog-status");
  if (isPhone()) {
    const toolbar = document.querySelector("#view-collection > .toolbar");
    if (status.parentElement !== toolbar.parentElement) toolbar.after(status);
  } else {
    const top = document.querySelector(".topbar");
    if (status.parentElement !== top) top.insertBefore(status, $("account"));
  }
}

function showAccount(user) {
  const box = $("account");
  box.replaceChildren(
    h("span", { class: "account-name" }, user.display_name || user.handle),
    h("button", { type: "button", id: "sign-out", onclick: signOut }, "Sign out"),
  );
  box.hidden = false;
}

// A shared page names whose collection it is in the top bar. The visitor has no account, so #account stays hidden.
function showOwner(owner) {
  const title = `${owner || "Shared"}'s collection`;
  $("owner-name").textContent = title;
  $("owner-name").hidden = false;
  document.title = `${title} · Batchall`;
}

async function signOut() {
  await guarded(async () => {
    await api("POST", "/api/auth/logout", {});
    location.href = "/login.html";
  });
}

(async function init() {
  await guarded(async () => {
    const me = await api("GET", "/api/auth/me");
    if (SHARED) showOwner(me.owner);
    else if (me.mode !== "local") showAccount(me);
    state.meta = await api("GET", "/api/meta");
    await loadCompanyLogos();
    loadPrefs();
    setupControls();
    setupViewControls();
    setupBulkBar();
    setupSuggest();
    await loadCollection();
    await loadForces();
  });
})();
