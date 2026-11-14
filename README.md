# Torre

Tu organizador personal de tareas: **tableros**, **listas** y **tarjetas** que
se mueven arrastrando, como en un kanban. Todo se guarda en este equipo, en un
único fichero (`data/torre.db`), sin cuentas ni nube.

Se usa como **aplicación de escritorio** (ventana propia, en el menú de
aplicaciones) o desde el **navegador**. Es la hermana de Hangar: si Hangar es
donde están las máquinas, Torre es la torre de control desde donde se organiza todo.

## Qué puedes hacer

- **Tableros** con su color. Se pueden archivar y restaurar, o borrar desde la portada.
- **Listas**: cada tablero nuevo empieza con «Por hacer», «En progreso» y «Hecho».
  Clic en el título para renombrar, arrastra el título para cambiar el orden y
  usa **⋯** para archivar o borrar.
- **Tarjetas**: arrástralas entre listas o dentro de la misma lista. Al abrir una tienes:
  - una **descripción** en Markdown (`**negrita**`, listas con `-`,
    `[enlaces](https://…)`, código…). Si cierras a medio escribir, se guarda sola;
  - una **fecha límite**, que sale en rojo si está vencida y en amarillo si es hoy o mañana;
  - **etiquetas** de colores, que se crean en «Menú» del tablero;
  - una **checklist** con barra de progreso;
  - una casilla de **hecha**, y botones para archivar (con «Deshacer») o borrar.
- **Filtrar** el tablero por etiquetas.
- **Buscar** tarjetas de todos los tableros (tecla `/`, flechas y Enter).
- **Archivo**: en «Menú» del tablero se restaura lo archivado.
- **Tema** automático (sigue al de Debian), claro u oscuro: el botón de arriba a la derecha.
- **Copias de seguridad automáticas**, una al día (ver más abajo).
- **Avisos de fechas límite**: una notificación del escritorio al día si tienes
  tarjetas vencidas o que vencen hoy o mañana. Se activan o desactivan en
  «Ajustes», en la portada, donde también está «Probar aviso».

## Instalar (Debian 13)

**1. Paquetes del sistema:**

```bash
sudo apt install python3-venv libxcb-cursor0 libnotify-bin
```

**2. Instala la aplicación** (con tu usuario, sin sudo):

```bash
cd ~/prueba-organizador && ./scripts/install-desktop.sh
```

La primera vez descarga unos 700 MB (Qt WebEngine, la ventana). Si ya tienes
Hangar instalado, suele salir de la caché de pip y tarda segundos.

**3. Ábrela:** busca **Torre** en el menú de aplicaciones.

Para quitarla del menú: `./scripts/install-desktop.sh --remove`. Tus datos
siguen en `data/`.

### En el navegador

```bash
./run.sh
```

Y abre <http://127.0.0.1:8091>. Solo responde a este equipo. Para usar otro
puerto: `PORT=9000 ./run.sh`.

## Atajos

| Tecla | Dónde | Qué hace |
|---|---|---|
| `/` | Siempre | Buscar tarjetas |
| `Enter` | Tarjeta con foco | Abrirla |
| `Esc` | Arrastrando / diálogo | Cancelar el arrastre / cerrar |
| `Ctrl+Enter` | Descripción | Guardar |
| `F5` / `Ctrl+R` | Ventana | Recargar |
| `F11` | Ventana | Pantalla completa |
| `Ctrl+W` / `Ctrl+Q` | Ventana | Cerrar |

## Copias de seguridad

Mientras Torre está abierto, hace **una copia al día** de tus datos en
`~/.local/share/torre/copias/` (fuera de la carpeta del proyecto, por si la
borras) y guarda **las últimas 14**. En la portada, en «Ajustes», ves cuándo
se hizo la última y puedes hacer una al momento con «Hacer copia ahora».

**Para recuperar una copia:** cierra Torre y ejecuta, cambiando la fecha:

```bash
cd ~/prueba-organizador && rm -f data/torre.db-wal data/torre.db-shm && cp ~/.local/share/torre/copias/torre-2026-11-14.db data/torre.db
```

Cada copia es un fichero SQLite completo; para guardarla en otro sitio (un
USB, la nube) basta con copiar el fichero.

## Cómo está hecho

| Parte | Fichero |
|---|---|
| Servidor web y API JSON (FastAPI) | `torre/main.py` |
| Base de datos (SQLite) | `torre/db.py` |
| Páginas (Jinja2) | `torre/templates/` |
| Interfaz: tablero, arrastrar y soltar, detalle | `torre/static/board.js` |
| Utilidades, buscador y tema | `torre/static/common.js` |
| Copias diarias y avisos de fechas | `torre/background.py` |
| Ventana de escritorio (PySide6 / Qt WebEngine) | `torre/desktop.py` |
| Lanzadores | `torre-app`, `run.sh`, `scripts/` |

La ventana arranca el servidor en un puerto libre y con una clave aleatoria que
solo ella conoce, y lo apaga al cerrarse. El servidor rechaza peticiones que no
vengan de este equipo o que lleguen desde otras webs.

Si la ventana se cierra de golpe por la tarjeta gráfica, `torre-app` la vuelve
a abrir sin aceleración y lo recuerda. Para volver a probar con GPU:
`rm ~/.cache/torre/no-gpu`. Si algo falla al abrirla desde el menú, los
detalles están en `~/.cache/torre/torre.log`.

### Tests

```bash
./run-tests.sh
```
