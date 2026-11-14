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
