// Torre — página de inicio: crear, restaurar y borrar tableros.
"use strict";

document.getElementById("new-board").addEventListener("submit", async e => {
  e.preventDefault();
  const form = new FormData(e.target);
  const { id } = await api("POST", "/api/boards", { name: form.get("name"), color: form.get("color") });
  location.href = `/b/${id}`;
});

document.querySelectorAll("[data-restore]").forEach(btn => btn.addEventListener("click", async () => {
  await api("PATCH", `/api/boards/${btn.dataset.restore}`, { archived: false });
  location.reload();
}));

document.querySelectorAll("[data-delete]").forEach(btn => btn.addEventListener("click", async () => {
  if (!confirm(`¿Borrar para siempre el tablero «${btn.dataset.name}» y todas sus tarjetas?\nNo se puede deshacer.`)) return;
  await api("DELETE", `/api/boards/${btn.dataset.delete}`);
  location.reload();
}));

// --- Ajustes: copias y avisos ---------------------------------------------------
document.getElementById("backup-now")?.addEventListener("click", async e => {
  e.target.disabled = true;
  try {
    await api("POST", "/api/backup");
    toast("Copia de seguridad hecha");
    setTimeout(() => location.reload(), 900);
  } finally { e.target.disabled = false; }
});

document.getElementById("reminders")?.addEventListener("change", async e => {
  await api("PUT", "/api/settings/reminders", { enabled: e.target.checked });
  toast(e.target.checked ? "Avisos activados" : "Avisos desactivados", { ms: 1500 });
});

document.getElementById("test-reminder")?.addEventListener("click", async () => {
  const r = await api("POST", "/api/reminders/test");
  toast(r.shown === "pending" ? "Aviso mostrado con tus tarjetas pendientes" : "Aviso de prueba mostrado");
});

// --- Importar de Trello -----------------------------------------------------------
(function trelloImport() {
  const modal = document.getElementById("import-modal");
  const input = document.getElementById("import-files");
  const drop = document.getElementById("file-drop");
  const label = document.getElementById("file-label");
  const go = document.getElementById("import-go");
  const results = document.getElementById("import-results");
  let files = [], imported = [];

  function choose(list) {
    files = [...list].filter(f => /\.json$/i.test(f.name) || f.type === "application/json");
    label.textContent = files.length
      ? files.map(f => f.name).join(", ")
      : "Elige o arrastra aquí los ficheros .json";
    go.disabled = !files.length;
    results.replaceChildren();
  }

  document.getElementById("import-btn").addEventListener("click", () => {
    choose([]); imported = []; input.value = ""; modal.showModal();
  });
  document.getElementById("import-cancel").addEventListener("click", () => modal.close());
  modal.addEventListener("close", () => { if (imported.length) location.reload(); });
  input.addEventListener("change", () => choose(input.files));
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); choose(e.dataTransfer.files); });

  go.addEventListener("click", async () => {
    go.disabled = true;
    results.replaceChildren();
    for (const file of files) {
      const row = el("li", {}, el("strong", {}, file.name), " — importando…");
      results.append(row);
      try {
        const res = await fetch("/api/import/trello", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: await file.text(),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
        imported.push(data);
        const n = (k, one, many) => `${data[k]} ${data[k] === 1 ? one : many}`;
        row.replaceChildren(el("span", { class: "ok" }, "✓ "), el("a", { href: `/b/${data.id}` }, data.name), ": ",
          [n("lists", "lista", "listas"), n("cards", "tarjeta", "tarjetas"), n("labels", "etiqueta", "etiquetas"),
           n("items", "elemento de checklist", "elementos de checklist")].join(", "),
          data.archived ? ` (${data.archived} archivados)` : "");
      } catch (err) {
        row.replaceChildren(el("span", { class: "bad" }, "✗ "), el("strong", {}, file.name), ": ", err.message);
      }
    }
    files = [];
    go.disabled = true;
    label.textContent = "Elige o arrastra aquí más ficheros .json";
    input.value = "";
  });
})();
