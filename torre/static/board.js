// Torre — el tablero: listas, tarjetas, arrastrar y soltar y el detalle de tarjeta.
"use strict";

const boardEl = document.getElementById("board");
const BOARD_ID = Number(boardEl.dataset.board);
const ROLE = boardEl.dataset.role;          // «owner» o «member» (compartido conmigo)
const ME = boardEl.dataset.me;              // usuario (vacío en el portátil)
const COLOR_PAIRS = JSON.parse(boardEl.dataset.colors);   // [["blue", "Azul"], …] en orden
const COLOR_NAMES = Object.fromEntries(COLOR_PAIRS);
const COLORS = COLOR_PAIRS.map(([c]) => c);
const modal = document.getElementById("card-modal");
const modalInner = document.getElementById("modal-inner");

let board = null;               // último estado recibido de /api/boards/:id
const filter = new Set();       // etiquetas marcadas en «Filtrar»

const labelById = id => board.labels.find(l => l.id === id);
const chip = (label, extra = {}) => el("span", { class: `chip c-${label.color}`, ...extra }, label.name);

// --- Fechas -------------------------------------------------------------------
const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
function localISO(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function dueInfo(due, done) {
  const [y, m, d] = due.split("-").map(Number);
  const today = localISO();
  const tomorrow = localISO(new Date(Date.now() + 864e5));
  let text = `${d} ${MONTHS[m - 1]}` + (y !== new Date().getFullYear() ? ` ${y}` : "");
  let cls = "";
  if (done) cls = "complete";
  else if (due < today) { cls = "overdue"; text += " · vencida"; }
  else if (due === today) { cls = "soon"; text = "Hoy"; }
  else if (due === tomorrow) { cls = "soon"; text = "Mañana"; }
  return { text, cls };
}

// --- Pintar el tablero ----------------------------------------------------------
async function refresh() {
  board = await api("GET", `/api/boards/${BOARD_ID}`);
  render();
}

function render() {
  const scroll = boardEl.scrollLeft;
  const listScroll = Object.fromEntries([...boardEl.querySelectorAll(".list")]
    .map(l => [l.dataset.id, l.querySelector(".cards").scrollTop]));
  boardEl.replaceChildren(...board.lists.map(renderList), renderAddList());
  boardEl.scrollLeft = scroll;
  for (const l of boardEl.querySelectorAll(".list")) {
    l.querySelector(".cards").scrollTop = listScroll[l.dataset.id] || 0;
  }
  applyFilter();
}

function renderList(list) {
  const name = el("h2", { class: "list-name", spellcheck: "false", title: "Clic para renombrar · arrastra para mover" }, list.name);
  editable(name, value => api("PATCH", `/api/lists/${list.id}`, { name: value }).then(() => { list.name = value; }));
  name.addEventListener("click", () => {
    if (justDragged) return;
    name.contentEditable = "plaintext-only";
    name.focus();
    getSelection().selectAllChildren(name);
  });
  name.addEventListener("blur", () => name.removeAttribute("contenteditable"));
  const cards = el("ul", { class: "cards" }, list.cards.map(renderCard));
  const node = el("section", { class: "list", dataset: { id: list.id } },
    el("header", { class: "list-head" }, name,
      el("span", { class: "list-count" }, list.cards.length || ""),
      el("button", { class: "icon-btn", title: "Opciones de la lista", "aria-label": "Opciones de la lista",
        onclick: e => listMenu(e, list, node) }, "⋯")),
    cards,
    renderAddCard(list));
  return node;
}

function renderCard(card) {
  const badges = [];
  if (card.due) {
    const d = dueInfo(card.due, card.done);
    badges.push(el("span", { class: `badge ${d.cls}`, title: "Fecha límite" }, "◷ ", d.text));
  }
  if (card.has_description) badges.push(el("span", { class: "badge", title: "Tiene descripción" }, "≡"));
  if (card.items_total) {
    const full = card.items_done === card.items_total;
    badges.push(el("span", { class: `badge ${full ? "complete" : ""}`, title: "Checklist" },
      "☑ ", `${card.items_done}/${card.items_total}`));
  }
  const labels = card.label_ids.map(labelById).filter(Boolean);
  return el("li", { class: "card" + (card.done ? " is-done" : ""), tabindex: "0", dataset: { id: card.id } },
    labels.length ? el("div", { class: "card-labels" }, labels.map(l => chip(l, { title: l.name }))) : null,
    el("div", { class: "card-title" }, card.title),
    el("div", { class: "badges" }, badges));
}

function renderAddCard(list) {
  const wrap = el("div", { class: "add-card" });
  const closed = () => wrap.replaceChildren(
    el("button", { class: "btn ghost", onclick: open }, "+ Añadir tarjeta"));
  function open() {
    const input = el("textarea", { placeholder: "Título de la tarjeta…", maxlength: "300", rows: "2",
      "aria-label": "Título de la tarjeta" });
    const submit = async () => {
      const title = input.value.replace(/\s+/g, " ").trim();
      if (!title) return;
      input.value = "";
      await api("POST", `/api/lists/${list.id}/cards`, { title });
      await refresh();
      // Seguir añadiendo en la misma lista
      const again = boardEl.querySelector(`.list[data-id="${list.id}"] .add-card > button`);
      if (again) { again.click(); const c = boardEl.querySelector(`.list[data-id="${list.id}"] .cards`); c.scrollTop = c.scrollHeight; }
    };
    input.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); submit(); }
      if (e.key === "Escape") closed();
    });
    input.addEventListener("blur", () => setTimeout(() => {
      if (!wrap.contains(document.activeElement) && !input.value.trim()) closed();
    }, 120));
    wrap.replaceChildren(input, el("div", { class: "row" },
      el("button", { class: "btn primary small", onclick: submit }, "Añadir"),
      el("button", { class: "icon-btn", "aria-label": "Cancelar", onclick: closed }, "✕")));
    input.focus();
  }
  closed();
  wrap.open = open;
  return wrap;
}

function renderAddList() {
  const wrap = el("div", { class: "add-list" });
  const closed = () => wrap.replaceChildren(el("button", { class: "btn", onclick: open }, "+ Añadir lista"));
  function open() {
    const input = el("input", { placeholder: "Nombre de la lista…", maxlength: "100", "aria-label": "Nombre de la lista" });
    const form = el("form", {
      onsubmit: async e => {
        e.preventDefault();
        const name = input.value.trim();
        if (!name) return;
        await api("POST", `/api/boards/${BOARD_ID}/lists`, { name });
        await refresh();
        boardEl.querySelector(".add-list > button").click();
        boardEl.scrollLeft = boardEl.scrollWidth;
      }
    }, input, el("div", { class: "row" },
      el("button", { class: "btn primary small" }, "Añadir lista"),
      el("button", { type: "button", class: "icon-btn", "aria-label": "Cancelar", onclick: closed }, "✕")));
    input.addEventListener("keydown", e => { if (e.key === "Escape") closed(); });
    wrap.replaceChildren(form);
    input.focus();
  }
  closed();
  return wrap;
}

// --- Menú de lista ----------------------------------------------------------------
let openMenu = null;
function closeMenu() { if (openMenu) { openMenu.remove(); openMenu = null; } }
document.addEventListener("click", e => { if (openMenu && !openMenu.contains(e.target) && !e.target.closest(".list-head .icon-btn")) closeMenu(); });

function listMenu(e, list, node) {
  closeMenu();
  const r = e.currentTarget.getBoundingClientRect();
  const item = (text, fn, cls) => el("button", { class: cls, onclick: () => { closeMenu(); fn(); } }, text);
  openMenu = el("div", { class: "menu", role: "menu" },
    item("Añadir tarjeta", () => node.querySelector(".add-card").open()),
    item("Archivar lista", async () => {
      await api("PATCH", `/api/lists/${list.id}`, { archived: true });
      await refresh();
      toast(`Lista «${list.name}» archivada`, { action: "Deshacer", onAction: async () => {
        await api("PATCH", `/api/lists/${list.id}`, { archived: false }); refresh();
      } });
    }),
    item("Borrar lista…", async () => {
      const n = list.cards.length;
      if (!confirm(`¿Borrar la lista «${list.name}»${n ? ` y sus ${n} tarjeta${n === 1 ? "" : "s"}` : ""}?\nNo se puede deshacer.`)) return;
      await api("DELETE", `/api/lists/${list.id}`);
      refresh();
    }, "danger"));
  document.body.append(openMenu);
  openMenu.style.top = `${r.bottom + 4}px`;
  openMenu.style.left = `${Math.min(r.left, innerWidth - openMenu.offsetWidth - 8)}px`;
}

// --- Arrastrar y soltar -------------------------------------------------------------
// Con eventos de puntero (no el drag&drop nativo): así la tarjeta sigue al ratón
// tal cual, el hueco se ve donde caerá y funciona igual en la ventana de Qt.
let drag = null;
let justDragged = false;   // evita que el clic que cierra un arrastre abra la edición

boardEl.addEventListener("pointerdown", e => {
  if (e.button !== 0 || drag) return;
  if (e.target.closest("button, input, textarea, [contenteditable], a, .add-card, .add-list")) return;
  const card = e.target.closest(".card");
  const head = !card && e.target.closest(".list-head");
  const item = card || (head && head.closest(".list"));
  if (!item) return;
  drag = { item, kind: card ? "card" : "list", startX: e.clientX, startY: e.clientY, started: false, pointer: e.pointerId };
});

window.addEventListener("pointermove", e => {
  if (!drag || e.pointerId !== drag.pointer) return;
  if (!drag.started) {
    if (Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) < 6) return;
    startDrag(e);
  }
  drag.x = e.clientX; drag.y = e.clientY;
  drag.ghost.style.transform = `translate(${e.clientX - drag.offX}px, ${e.clientY - drag.offY}px) rotate(${drag.kind === "card" ? 3 : 2}deg)`;
  placePlaceholder();
});

window.addEventListener("pointerup", e => {
  if (!drag || e.pointerId !== drag.pointer) return;
  const d = drag;
  drag = null;
  if (!d.started) {
    if (d.kind === "card") openCard(Number(d.item.dataset.id));
    return;
  }
  justDragged = true;
  setTimeout(() => { justDragged = false; }, 0);
  finishDrag(d);
});
window.addEventListener("keydown", e => { if (e.key === "Escape" && drag && drag.started) cancelDrag(); });
window.addEventListener("blur", () => { if (drag && drag.started) cancelDrag(); });

function startDrag(e) {
  const r = drag.item.getBoundingClientRect();
  drag.started = true;
  drag.offX = e.clientX - r.left;
  drag.offY = e.clientY - r.top;
  drag.origin = { parent: drag.item.parentNode, next: drag.item.nextSibling };
  const ghost = drag.item.cloneNode(true);
  Object.assign(ghost.style, {
    position: "fixed", left: "0", top: "0", width: `${r.width}px`, height: drag.kind === "list" ? `${r.height}px` : "",
    pointerEvents: "none", zIndex: 100, boxShadow: "var(--shadow-lg)", opacity: ".95", margin: 0,
  });
  document.body.append(ghost);
  drag.ghost = ghost;
  drag.ph = el("div", { class: drag.kind === "card" ? "placeholder" : "placeholder list-placeholder" });
  drag.ph.style.height = `${r.height}px`;
  drag.item.replaceWith(drag.ph);
  drag.item.classList.add("dragging");
  document.body.style.cursor = "grabbing";
  document.body.style.userSelect = "none";
  drag.scroller = requestAnimationFrame(autoScroll);
}

function placePlaceholder() {
  if (drag.kind === "card") {
    const under = document.elementFromPoint(drag.x, drag.y);
    let cards = under && under.closest(".list") && under.closest(".list").querySelector(".cards");
    if (!cards) {
      // Fuera de cualquier lista: la más cercana en horizontal
      const lists = [...boardEl.querySelectorAll(".list")];
      if (!lists.length) return;
      const nearest = lists.reduce((best, l) => {
        const r = l.getBoundingClientRect();
        const dist = Math.abs(drag.x - (r.left + r.width / 2));
        return dist < best.dist ? { l, dist } : best;
      }, { l: null, dist: Infinity }).l;
      cards = nearest.querySelector(".cards");
    }
    const siblings = [...cards.querySelectorAll(".card:not(.filtered)")];
    const before = siblings.find(c => {
      const r = c.getBoundingClientRect();
      return drag.y < r.top + r.height / 2;
    });
    if (before) { if (drag.ph.nextSibling !== before) cards.insertBefore(drag.ph, before); }
    else if (cards.lastElementChild !== drag.ph) cards.append(drag.ph);
  } else {
    const lists = [...boardEl.querySelectorAll(".list")];
    const before = lists.find(l => {
      const r = l.getBoundingClientRect();
      return drag.x < r.left + r.width / 2;
    });
    const target = before || boardEl.querySelector(".add-list");
    if (drag.ph.nextSibling !== target) boardEl.insertBefore(drag.ph, target);
  }
}

function autoScroll() {
  if (!drag || !drag.started) return;
  const edge = 70, speed = 14;
  const br = boardEl.getBoundingClientRect();
  if (drag.x < br.left + edge) boardEl.scrollLeft -= speed;
  else if (drag.x > br.right - edge) boardEl.scrollLeft += speed;
  if (drag.kind === "card") {
    const cards = drag.ph.closest(".cards");
    if (cards) {
      const cr = cards.getBoundingClientRect();
      if (drag.y < cr.top + 40) cards.scrollTop -= speed;
      else if (drag.y > cr.bottom - 40) cards.scrollTop += speed;
    }
  }
  placePlaceholder();
  drag.scroller = requestAnimationFrame(autoScroll);
}

function endDragVisuals(d) {
  cancelAnimationFrame(d.scroller);
  d.ghost.remove();
  d.item.classList.remove("dragging");
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
}

function cancelDrag() {
  const d = drag;
  drag = null;
  endDragVisuals(d);
  d.ph.remove();
  d.origin.parent.insertBefore(d.item, d.origin.next);
}

async function finishDrag(d) {
  endDragVisuals(d);
  const container = d.ph.parentNode;
  const selector = d.kind === "card" ? ".card" : ".list";
  const index = [...container.children].filter(c => c === d.ph || c.matches(selector)).indexOf(d.ph);
  d.ph.replaceWith(d.item);
  const id = Number(d.item.dataset.id);
  try {
    if (d.kind === "card") {
      const listId = Number(container.closest(".list").dataset.id);
      await api("POST", `/api/cards/${id}/move`, { list_id: listId, index });
    } else {
      await api("POST", `/api/lists/${id}/move`, { index });
    }
  } finally {
    refresh();
  }
}

// Teclado: Enter sobre una tarjeta la abre
boardEl.addEventListener("keydown", e => {
  const card = e.target.closest(".card");
  if (card && e.key === "Enter") openCard(Number(card.dataset.id));
});

// --- Filtro por etiquetas -----------------------------------------------------------
const filterBtn = document.getElementById("filter-btn");
const filterPop = document.getElementById("filter-pop");

function applyFilter() {
  const visible = c => !filter.size || board.lists.some(l => l.cards.some(x =>
    x.id === Number(c.dataset.id) && x.label_ids.some(id => filter.has(id))));
  boardEl.querySelectorAll(".card").forEach(c => c.classList.toggle("filtered", !visible(c)));
  filterBtn.classList.toggle("on", filter.size > 0);
  filterBtn.textContent = filter.size ? `Filtrando (${filter.size})` : "Filtrar";
}

filterBtn.addEventListener("click", () => {
  if (!filterPop.hidden) { filterPop.hidden = true; return; }
  filterPop.replaceChildren(board.labels.length
    ? el("div", {}, board.labels.map(l => el("label", {},
        el("input", { type: "checkbox", checked: filter.has(l.id), onchange: e => {
          e.target.checked ? filter.add(l.id) : filter.delete(l.id); applyFilter();
        } }), chip(l))),
      el("button", { class: "btn small", style: "margin-top:8px", onclick: () => {
        filter.clear(); applyFilter(); filterPop.hidden = true;
      } }, "Quitar filtro"))
    : el("p", { class: "muted" }, "Este tablero no tiene etiquetas. Créalas en «Menú»."));
  const r = filterBtn.getBoundingClientRect();
  filterPop.style.top = `${r.bottom + 6}px`;
  filterPop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - 256))}px`;
  filterPop.hidden = false;
});
document.addEventListener("click", e => {
  if (!filterPop.hidden && !filterPop.contains(e.target) && e.target !== filterBtn) filterPop.hidden = true;
});

// --- Menú del tablero (panel lateral) ---------------------------------------------
const drawer = document.getElementById("drawer");
document.getElementById("menu-btn").addEventListener("click", () => {
  drawer.hidden = !drawer.hidden;
  if (!drawer.hidden) renderDrawer();
});
drawer.querySelector("[data-close]").addEventListener("click", () => { drawer.hidden = true; });

if (ROLE === "owner") editable(document.getElementById("board-title"), async value => {
  await api("PATCH", `/api/boards/${BOARD_ID}`, { name: value });
  document.title = `${value} · Torre`;
});

async function renderDrawer() {
  renderSharing();
  const colorsBox = document.getElementById("board-colors");
  if (colorsBox) colorsBox.replaceChildren(...COLORS.map(c => el("button", {
    class: `swatch c-${c}${board.color === c ? " active" : ""}`, title: COLOR_NAMES[c], "aria-label": COLOR_NAMES[c],
    onclick: async () => {
      await api("PATCH", `/api/boards/${BOARD_ID}`, { color: c });
      document.body.className = document.body.className.replace(/\bc-\w+/, `c-${c}`);
      board.color = c;
      renderDrawer();
    } })));

  const editor = document.getElementById("label-editor");
  editor.replaceChildren(...board.labels.map(l => {
    const name = el("input", { value: l.name, maxlength: "40", placeholder: "(sin nombre)", "aria-label": "Nombre" });
    name.addEventListener("change", async () => {
      await api("PATCH", `/api/labels/${l.id}`, { name: name.value }); refresh();
    });
    const color = el("select", { "aria-label": "Color", onchange: async e => {
      await api("PATCH", `/api/labels/${l.id}`, { color: e.target.value }); await refresh(); renderDrawer();
    } }, COLORS.map(c => el("option", { value: c, selected: c === l.color }, COLOR_NAMES[c])));
    return el("li", {}, el("span", { class: `dot c-${l.color}` }), name, color,
      el("button", { class: "icon-btn", title: "Borrar etiqueta", "aria-label": "Borrar etiqueta", onclick: async () => {
        if (!confirm(`¿Borrar la etiqueta «${l.name || COLOR_NAMES[l.color]}»? Se quitará de todas las tarjetas.`)) return;
        await api("DELETE", `/api/labels/${l.id}`);
        filter.delete(l.id);
        await refresh(); renderDrawer();
      } }, "✕"));
  }));

  const archive = await api("GET", `/api/boards/${BOARD_ID}/archive`);
  const box = document.getElementById("archive");
  const restore = (url, extra = {}) => async () => {
    await api("PATCH", url, { archived: false, ...extra }); await refresh(); renderDrawer();
  };
  const remove = (url, what) => async () => {
    if (!confirm(`¿Borrar ${what} para siempre? No se puede deshacer.`)) return;
    await api("DELETE", url); await refresh(); renderDrawer();
  };
  box.replaceChildren(
    ...(archive.lists.length ? [el("h4", {}, "Listas"), el("ul", {}, archive.lists.map(l => el("li", {},
      el("span", {}, l.name),
      el("button", { class: "btn small", onclick: restore(`/api/lists/${l.id}`) }, "Restaurar"),
      el("button", { class: "icon-btn", title: "Borrar", onclick: remove(`/api/lists/${l.id}`, `la lista «${l.name}»`) }, "✕"))))] : []),
    ...(archive.cards.length ? [el("h4", {}, "Tarjetas"), el("ul", {}, archive.cards.map(c => el("li", {},
      el("span", {}, c.title, " ", el("small", {}, `en ${c.list_name}${c.list_archived ? " (archivada)" : ""}`)),
      el("button", { class: "btn small", onclick: restore(`/api/cards/${c.id}`) }, "Restaurar"),
      el("button", { class: "icon-btn", title: "Borrar", onclick: remove(`/api/cards/${c.id}`, `la tarjeta «${c.title}»`) }, "✕"))))] : []),
    ...(!archive.lists.length && !archive.cards.length ? [el("p", { class: "hint" }, "No hay nada archivado.")] : []));
}

// --- Compartir (solo en un servidor, con usuarios) ----------------------------------
async function renderSharing() {
  const box = document.getElementById("sharing");
  if (!box) return;
  const info = await api("GET", `/api/boards/${BOARD_ID}/members`);
  const person = (name, removable, label) => el("li", {},
    el("span", { class: "avatar", "aria-hidden": "true" }, name[0].toUpperCase()),
    el("span", { class: "who" }, name, label ? el("small", {}, ` · ${label}`) : null),
    removable ? el("button", { class: "icon-btn", title: `Quitar a ${name}`, "aria-label": `Quitar a ${name}`,
      onclick: async () => {
        if (!confirm(`¿Dejar de compartir «${board.name}» con ${name}?`)) return;
        await api("DELETE", `/api/boards/${BOARD_ID}/members/${encodeURIComponent(name)}`);
        renderSharing();
      } }, "✕") : null);
  const list = el("ul", { class: "people" },
    person(info.owner, false, info.owner === ME ? "dueño (tú)" : "dueño"),
    info.members.map(m => person(m, ROLE === "owner", m === ME ? "tú" : "")));

  if (ROLE !== "owner") {
    box.replaceChildren(list,
      el("p", { class: "hint" }, "Puedes editar todo su contenido. Solo el dueño puede renombrarlo, cambiar su color, compartirlo o archivarlo."),
      el("button", { class: "btn", onclick: async () => {
        if (!confirm(`¿Salir de «${board.name}»? Dejarás de verlo hasta que ${info.owner} te lo vuelva a compartir.`)) return;
        await api("DELETE", `/api/boards/${BOARD_ID}/members/${encodeURIComponent(ME)}`);
        location.href = "/";
      } }, "Salir del tablero"));
    return;
  }
  const users = await api("GET", "/api/users");
  const available = users.filter(u => u !== info.owner && !info.members.includes(u));
  const input = el("input", { list: "share-users", placeholder: "Usuario con quien compartir", maxlength: "64",
    autocomplete: "off", autocapitalize: "none", spellcheck: "false", "aria-label": "Usuario con quien compartir" });
  const form = el("form", { class: "row", onsubmit: async e => {
    e.preventDefault();
    const name = input.value.trim().toLowerCase();
    if (!name) return;
    await api("POST", `/api/boards/${BOARD_ID}/members`, { username: name });
    toast(`Compartido con ${name}`, { ms: 2000 });
    renderSharing();
  } }, input, el("button", { class: "btn small" }, "Compartir"));
  box.replaceChildren(list, form,
    el("datalist", { id: "share-users" }, available.map(u => el("option", { value: u }))),
    el("p", { class: "hint" }, info.members.length
      ? "Quien tenga acceso puede editar listas, tarjetas y etiquetas."
      : "Solo tú ves este tablero. Compártelo para que otros puedan verlo y editarlo."));
}

document.getElementById("new-label").addEventListener("submit", async e => {
  e.preventDefault();
  const form = new FormData(e.target);
  await api("POST", `/api/boards/${BOARD_ID}/labels`, { name: form.get("name"), color: form.get("color") });
  e.target.reset();
  await refresh(); renderDrawer();
});

document.getElementById("archive-board")?.addEventListener("click", async () => {
  if (!confirm("¿Archivar este tablero? Podrás restaurarlo desde la página de inicio.")) return;
  await api("PATCH", `/api/boards/${BOARD_ID}`, { archived: true });
  location.href = "/";
});

// --- Detalle de tarjeta -------------------------------------------------------------
let current = null;   // tarjeta abierta
let descDraft = null; // texto del editor de descripción, si está abierto

async function openCard(id) {
  if (!current || current.id !== id) descDraft = null;
  current = await api("GET", `/api/cards/${id}`);
  if (current.board_id !== BOARD_ID) { location.href = `/b/${current.board_id}#card-${id}`; return; }
  history.replaceState(null, "", `#card-${id}`);
  renderModal();
  if (!modal.open) modal.showModal();
}

function openCardFromHash() {
  const m = location.hash.match(/^#card-(\d+)$/);
  if (m) openCard(Number(m[1])).catch(() => history.replaceState(null, "", location.pathname));
}

modal.addEventListener("close", async () => {
  // Una descripción a medio escribir se guarda al cerrar, no se pierde
  const pending = current && descDraft !== null && descDraft.trim() !== current.description
    ? { id: current.id, description: descDraft.trim() } : null;
  current = null;
  descDraft = null;
  history.replaceState(null, "", location.pathname);
  if (pending) {
    await api("PATCH", `/api/cards/${pending.id}`, { description: pending.description });
    toast("Descripción guardada", { ms: 1500 });
  }
  refresh();
});
modal.addEventListener("click", e => { if (e.target === modal) modal.close(); });   // clic en el fondo

async function patchCard(fields) {
  current = await api("PATCH", `/api/cards/${current.id}`, fields);
  renderModal();
}

function renderModal() {
  const c = current;
  const title = el("h2", { class: "modal-title", contenteditable: "plaintext-only", spellcheck: "false" }, c.title);
  editable(title, value => patchCard({ title: value }));

  // Descripción: se ve en Markdown; clic para editar
  const desc = el("section", {}, el("h3", {}, "Descripción"));
  const showDesc = () => {
    descDraft = null;
    const view = el("div", { class: "description" + (c.description ? "" : " empty"), tabindex: "0",
      title: "Clic para editar" });
    if (c.description) view.innerHTML = c.description_html;   // HTML ya saneado por el servidor
    else view.textContent = "Añade más detalles… (admite Markdown)";
    view.addEventListener("click", e => { if (!e.target.closest("a")) editDesc(); });
    view.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); editDesc(); } });
    desc.replaceChildren(desc.firstChild, view);
  };
  const editDesc = (focus = true) => {
    const ta = el("textarea", { maxlength: "50000", "aria-label": "Descripción" });
    ta.value = descDraft ?? c.description;
    descDraft = ta.value;
    ta.addEventListener("input", () => { descDraft = ta.value; });
    const save = async () => { descDraft = null; await patchCard({ description: ta.value.trim() }); };
    ta.addEventListener("keydown", e => {
      if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); save(); }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); showDesc(); }
    });
    desc.replaceChildren(desc.firstChild, el("div", { class: "desc-edit" }, ta, el("div", { class: "row" },
      el("button", { class: "btn primary small", onclick: save }, "Guardar"),
      el("button", { class: "btn small", onclick: showDesc }, "Cancelar"),
      el("small", {}, "Ctrl+Enter guarda · **negrita**, - listas, [enlace](https://…)"))));
    if (focus) ta.focus();
  };
  if (descDraft !== null) editDesc(false); else showDesc();

  // Checklist
  const total = c.items.length, doneCount = c.items.filter(i => i.done).length;
  const pct = total ? Math.round(doneCount / total * 100) : 0;
  const newItem = el("input", { placeholder: "Añadir elemento…", maxlength: "300", "aria-label": "Nuevo elemento" });
  const checklist = el("section", {},
    el("h3", {}, "Checklist", total ? ` · ${doneCount}/${total}` : ""),
    total ? el("div", { class: "progress" + (pct === 100 ? " full" : "") }, el("div", { style: `width:${pct}%` })) : null,
    el("ul", { class: "checklist" }, c.items.map(item => {
      const text = el("span", { class: "item-text", contenteditable: "plaintext-only", spellcheck: "false" }, item.text);
      editable(text, value => api("PATCH", `/api/items/${item.id}`, { text: value }).then(() => { item.text = value; }));
      return el("li", { class: item.done ? "done" : "" },
        el("input", { type: "checkbox", checked: !!item.done, "aria-label": item.text, onchange: async e => {
          await api("PATCH", `/api/items/${item.id}`, { done: e.target.checked });
          item.done = e.target.checked ? 1 : 0;
          renderModal();
        } }),
        text,
        el("button", { class: "icon-btn", title: "Quitar", "aria-label": "Quitar elemento", onclick: async () => {
          await api("DELETE", `/api/items/${item.id}`);
          c.items = c.items.filter(i => i !== item);
          renderModal();
        } }, "✕"));
    })),
    el("form", { id: "new-item", class: "row", onsubmit: async e => {
      e.preventDefault();
      const text = newItem.value.trim();
      if (!text) return;
      current = await api("POST", `/api/cards/${c.id}/items`, { text });
      renderModal();
      modalInner.querySelector("#new-item input").focus();
    } }, newItem, el("button", { class: "btn small" }, "Añadir")));

  // Lateral: fecha, etiquetas y acciones
  const due = el("input", { type: "date", value: c.due || "", "aria-label": "Fecha límite" });
  due.addEventListener("change", () => patchCard({ due: due.value || null }));
  const side = el("aside", { class: "modal-side" },
    el("div", { class: "field" }, el("h3", {}, "Fecha límite"), due,
      c.due ? el("button", { class: "btn small", onclick: () => patchCard({ due: null }) }, "Quitar fecha") : null),
    el("div", { class: "field" }, el("h3", {}, "Etiquetas"),
      board.labels.length
        ? el("div", { class: "label-picker" }, board.labels.map(l => el("label", {},
            el("input", { type: "checkbox", checked: c.label_ids.includes(l.id), onchange: async e => {
              const ids = new Set(c.label_ids);
              e.target.checked ? ids.add(l.id) : ids.delete(l.id);
              current = await api("PUT", `/api/cards/${c.id}/labels`, { label_ids: [...ids] });
              renderModal();
            } }), chip(l))))
        : el("p", { class: "hint" }, "Crea etiquetas en «Menú» del tablero.")),
    el("div", { class: "side-actions" },
      el("button", { class: "btn", onclick: async () => {
        await patchCard({ archived: true });
        const id = c.id, name = c.title;
        modal.close();
        toast(`Tarjeta «${name}» archivada`, { action: "Deshacer", onAction: async () => {
          await api("PATCH", `/api/cards/${id}`, { archived: false }); refresh();
        } });
      } }, "Archivar"),
      el("button", { class: "btn danger", onclick: async () => {
        if (!confirm(`¿Borrar la tarjeta «${c.title}»? No se puede deshacer.`)) return;
        await api("DELETE", `/api/cards/${c.id}`);
        modal.close();
      } }, "Borrar")));

  modalInner.replaceChildren(
    el("button", { class: "icon-btn modal-close", "aria-label": "Cerrar", onclick: () => modal.close() }, "✕"),
    el("div", { class: "modal-head" },
      el("input", { type: "checkbox", class: "done-box", checked: !!c.done, title: "Marcar como hecha",
        "aria-label": "Hecha", onchange: e => patchCard({ done: e.target.checked }) }),
      title),
    el("p", { class: "modal-sub" }, "En la lista ", el("strong", {}, c.list_name)),
    el("div", { class: "modal-body" }, el("div", { class: "modal-main" }, desc, checklist), side));
}

// El Escape del diálogo no debe cerrarlo mientras se edita un título
modal.addEventListener("cancel", e => {
  if (document.activeElement && document.activeElement.matches("[contenteditable], textarea")) e.preventDefault();
});

// --- Arranque -------------------------------------------------------------------------
refresh().then(openCardFromHash);
window.addEventListener("hashchange", openCardFromHash);
// Al volver a la ventana, por si se cambió algo en otra pestaña
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !drag && !modal.open) refresh();
});
