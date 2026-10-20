"""Carrusel IG del día: si en datos/cola.json hay un carrusel de IG listo para hoy
(hora de Madrid) y sin publicar, despierta al bot con tipo "carrusel".

cola.json: siguientes[] con {nombre, estado (listo|producido|usado), fecha AAAA-MM-DD,
canal IG|TT, [slug], [tipo]}; publicados[] con {fecha, red ig|fb|tt, tipo, slug}.
Env opcional FECHA=AAAA-MM-DD para probar otro día.
"""
import datetime
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comun

# 19:45 Madrid -> crons 17:45 UTC (CEST) y 18:45 UTC (CET)
if not comun.puerta_horaria({19}):
    sys.exit(0)


def slugify(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


ahora = comun.ahora_madrid()
fecha = os.environ.get("FECHA", "").strip()
if not fecha:
    d = ahora.date()
    # GitHub puede retrasar el cron horas: si ya pasó la medianoche, es el carrusel de ayer
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and ahora.hour < 6:
        d -= datetime.timedelta(days=1)
    fecha = d.isoformat()
print(f"Fecha objetivo: {fecha} (ahora {ahora:%Y-%m-%d %H:%M} Madrid)")

cola = json.load(open("datos/cola.json"))
publicados = {
    p.get("slug") for p in cola.get("publicados", [])
    if (p.get("red") or "").lower() == "ig" and (p.get("tipo") or "").lower() == "carrusel"
}
candidatos = []
for e in cola.get("siguientes", []):
    canal = (e.get("canal") or e.get("red") or "").upper()
    tipo = (e.get("tipo") or "carrusel").lower()
    estado = (e.get("estado") or "").lower()
    if canal != "IG" or tipo != "carrusel" or e.get("fecha") != fecha:
        continue
    slug = e.get("slug") or slugify(e.get("nombre", ""))
    if estado not in ("listo", "producido"):
        print(f"- {slug}: estado '{estado}', no listo")
        continue
    if slug in publicados or e.get("publicado"):
        print(f"- {slug}: ya publicado")
        continue
    candidatos.append({"slug": slug, "fecha": fecha, "nombre": e.get("nombre"), "estado": estado})

if not candidatos:
    print("No hay carrusel de IG listo para hoy. No se avisa al bot.")
    sys.exit(0)

for c in candidatos:
    print(f"Carrusel listo: {c['slug']} ({c['fecha']})")
    comun.avisar_bot("carrusel", c)
