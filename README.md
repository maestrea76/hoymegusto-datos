# hoymegusto-datos
Estadisticas diarias de @hoymegusto leidas de la API de Instagram

## Automatizaciones que despiertan al bot (sin modelo)
Workflows `bot-*.yml` + scripts en `automatizaciones/`. Vigilan con la Graph API (secreto `FB_PAGE_TOKEN`)
y, solo cuando hay algo que hacer, despiertan al bot abriendo un issue con `GITHUB_TOKEN`
(`permissions: issues: write`):
- Título `aviso-bot: <tipo> <fecha>`, etiqueta `aviso-bot` (se crea si no existe), asignado a `maestrea76`.
- Cuerpo: JSON `{"tipo", "detalle"}`. **Repo público**: nunca texto de DMs, autores, tokens ni datos privados.
- Si ya hay un issue abierto con la etiqueta y el mismo título, no se abre otro
  (en comentarios, los ids nuevos se añaden como comentario a ese issue).
- El bot debe cerrar el issue cuando lo haya atendido.
- Los `workflow_dispatch` tienen la opción `seco` (activada por defecto): solo escriben en el log lo que abrirían.

| Workflow | Hora (Madrid) | Aviso |
|---|---|---|
| `bot-carrusel.yml` | 19:45 | `carrusel`: `{slug, nombre, fecha}` si en `datos/cola.json` hay un carrusel IG listo para hoy y sin publicar |
| `bot-comentarios.yml` | 13:00 y 21:00 | `comentarios`: lista `{plataforma, clase, id, enlace}`; ids vistos en `datos/comentarios-vistos.json` |
| `bot-estadisticas.yml` | 23:30 | guarda `datos/graph-diario/AAAA-MM-DD.json`; `estadisticas` (id del Reel y cifras) si un Reel supera 3x la mediana (una vez por Reel) o >10 seguidores/día |
| todos | — | `error` si el token da 190 o la Action falla (sin tokens) |
| `bot-prueba.yml` | manual | `prueba` |

Cada horario se programa en dos crons UTC (verano/invierno) y el script deja seguir solo el que toca.
GitHub puede retrasar los `schedule` varias horas.
