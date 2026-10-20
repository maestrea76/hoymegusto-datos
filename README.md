# hoymegusto-datos
Estadisticas diarias de @hoymegusto leidas de la API de Instagram

## Automatizaciones que despiertan al bot (sin modelo)
Workflows `bot-*.yml` + scripts en `automatizaciones/`. Vigilan con la Graph API (secreto `FB_PAGE_TOKEN`)
y solo despiertan al bot por webhook (`BOT_WEBHOOK_URL`, `BOT_WEBHOOK_KEY`) cuando hay algo que hacer.
Sin esos dos secretos escriben en el log lo que habrían enviado y terminan en verde.
- `bot-carrusel.yml`: 19:45 Madrid, carrusel IG listo para hoy en `datos/cola.json` -> tipo `carrusel`.
- `bot-comentarios.yml`: 13:00 y 21:00 Madrid, comentarios IG/FB y DMs IG nuevos -> tipo `comentarios` (ids en `datos/comentarios-vistos.json`).
- `bot-estadisticas.yml`: 23:30 Madrid, `datos/graph-diario/AAAA-MM-DD.json`; Reel >3x mediana o >10 seguidores/día -> tipo `estadisticas`; token 190 o fallo -> `error`.
- `bot-prueba.yml`: manual, manda tipo `prueba`.
