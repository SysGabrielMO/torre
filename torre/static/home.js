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
document.getElementById("backup-now").addEventListener("click", async e => {
  e.target.disabled = true;
  try {
    await api("POST", "/api/backup");
    toast("Copia de seguridad hecha");
    setTimeout(() => location.reload(), 900);
  } finally { e.target.disabled = false; }
});

document.getElementById("reminders").addEventListener("change", async e => {
  await api("PUT", "/api/settings/reminders", { enabled: e.target.checked });
  toast(e.target.checked ? "Avisos activados" : "Avisos desactivados", { ms: 1500 });
});

document.getElementById("test-reminder").addEventListener("click", async () => {
  const r = await api("POST", "/api/reminders/test");
  toast(r.shown === "pending" ? "Aviso mostrado con tus tarjetas pendientes" : "Aviso de prueba mostrado");
});
