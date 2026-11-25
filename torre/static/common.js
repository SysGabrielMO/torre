// Torre — utilidades compartidas: llamadas a la API, avisos, tema y buscador.
"use strict";

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    toast("No se pudo conectar con Torre. ¿Sigue abierto el servidor?", { error: true });
    throw e;
  }
  if (res.status === 401) {
    // Sesión caducada o cerrada en otra pestaña (Torre en un servidor)
    location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search + location.hash);
    throw new Error("Sesión caducada");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map(d => d.msg).join(" · ");
    toast(msg || `Error ${res.status}`, { error: true });
    throw new Error(msg || res.status);
  }
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(c));
  }
  return node;
}

let toastTimer;
function toast(text, { error = false, action = null, onAction = null, ms = 4000 } = {}) {
  const box = document.getElementById("toast");
  box.replaceChildren(el("span", {}, text));
  if (action) {
    box.append(el("button", { onclick: () => { box.hidden = true; onAction(); } }, action));
  }
  box.className = "toast" + (error ? " error" : "");
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, ms);
}

// Edición en el sitio: Enter guarda, Escape cancela, perder el foco guarda.
function editable(node, save, { multiline = false } = {}) {
  let original = node.textContent;
  node.addEventListener("focus", () => { original = node.textContent; });
  node.addEventListener("keydown", e => {
    if (e.key === "Enter" && !(multiline && e.shiftKey)) { e.preventDefault(); node.blur(); }
    if (e.key === "Escape") { node.textContent = original; node.blur(); }
  });
  node.addEventListener("blur", async () => {
    const value = node.textContent.replace(/\s+/g, " ").trim();
    if (!value) { node.textContent = original; return; }
    node.textContent = value;
    if (value !== original) {
      try { await save(value); original = value; }
      catch { node.textContent = original; }
    }
  });
}

// --- Tema: automático → claro → oscuro -------------------------------------
(function themeToggle() {
  const btn = document.getElementById("theme-toggle");
  const names = { auto: "automático", light: "claro", dark: "oscuro" };
  btn.addEventListener("click", async () => {
    const order = ["auto", "light", "dark"];
    const next = order[(order.indexOf(btn.dataset.theme) + 1) % 3];
    btn.dataset.theme = next;
    if (next === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = next;
    await api("PUT", "/api/settings/theme", { theme: next });
    toast(`Tema ${names[next]}`, { ms: 1500 });
  });
})();

// --- Buscador ---------------------------------------------------------------
(function search() {
  const input = document.getElementById("search");
  const box = document.getElementById("search-results");
  let timer, seq = 0, active = -1;

  const links = () => [...box.querySelectorAll("a")];
  const highlight = i => {
    const all = links();
    active = Math.max(-1, Math.min(i, all.length - 1));
    all.forEach((a, n) => a.classList.toggle("active", n === active));
  };

  async function run() {
    const q = input.value.trim();
    if (!q) { box.hidden = true; return; }
    const mine = ++seq;
    const results = await api("GET", "/api/search?q=" + encodeURIComponent(q));
    if (mine !== seq) return;
    box.replaceChildren(...(results.length ? results.map(r => el("a", { href: `/b/${r.board_id}#card-${r.id}` },
      r.title,
      el("small", {}, el("span", { class: `dot c-${r.board_color}` }), `${r.board_name} · ${r.list_name}`)))
      : [el("div", { class: "none" }, "Sin resultados")]));
    active = -1;
    box.hidden = false;
  }

  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 150); });
  input.addEventListener("focus", () => { if (input.value.trim()) run(); });
  input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); highlight(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
    else if (e.key === "Enter") {
      const target = links()[active >= 0 ? active : 0];
      if (target) { box.hidden = true; target.click(); }
    } else if (e.key === "Escape") { input.value = ""; box.hidden = true; input.blur(); }
  });
  box.addEventListener("click", e => {
    const a = e.target.closest("a");
    if (a && typeof openCardFromHash === "function" && a.pathname === location.pathname) {
      e.preventDefault();
      history.replaceState(null, "", a.hash);
      box.hidden = true;
      openCardFromHash();
    }
  });
  document.addEventListener("click", e => { if (!e.target.closest(".search")) box.hidden = true; });
  document.addEventListener("keydown", e => {
    const typing = e.target.closest("input, textarea, select, [contenteditable]");
    if (e.key === "/" && !typing && !document.querySelector("dialog[open]")) {
      e.preventDefault(); input.focus(); input.select();
    }
  });
})();
