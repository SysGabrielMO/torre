// Torre — administración: registro abierto o cerrado y usuarios.
"use strict";

document.getElementById("registration").addEventListener("change", async e => {
  await api("PUT", "/api/admin/registration", { open: e.target.checked });
  toast(e.target.checked ? "Registro abierto" : "Registro cerrado", { ms: 2000 });
});

document.querySelectorAll("tr[data-user]").forEach(row => {
  const user = row.dataset.user;
  const url = `/api/admin/users/${encodeURIComponent(user)}`;

  row.querySelector(".admin-toggle")?.addEventListener("change", async e => {
    try {
      await api("PUT", `${url}/admin`, { admin: e.target.checked });
      toast(e.target.checked ? `${user} ya es administrador` : `${user} ya no es administrador`, { ms: 2000 });
    } catch { e.target.checked = !e.target.checked; }
  });

  row.querySelector("[data-reset]")?.addEventListener("click", async () => {
    if (!confirm(`¿Dar a ${user} una contraseña temporal?\nSu contraseña actual dejará de funcionar y se cerrarán sus sesiones.`)) return;
    const { password } = await api("POST", `${url}/password`);
    prompt(`Contraseña temporal de ${user} (cópiala y pásasela; tendrá que cambiarla al entrar). No se volverá a mostrar:`, password);
    location.reload();
  });

  row.querySelector("[data-delete]")?.addEventListener("click", async e => {
    const n = Number(e.target.dataset.boards);
    if (!confirm(`¿Borrar la cuenta de ${user}${n ? ` y sus ${n} tablero${n === 1 ? "" : "s"}` : ""}?\nNo se puede deshacer.`)) return;
    await api("DELETE", url);
    row.remove();
    toast(`Cuenta de ${user} borrada`);
  });
});
