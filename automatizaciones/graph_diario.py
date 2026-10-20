"""Estadísticas nocturnas IG+FB de @hoymegusto -> datos/graph-diario/AAAA-MM-DD.json

IG: cada Reel o carrusel de los últimos 14 días (views, reach, saved, shares,
total_interactions); cuenta: reach diario y follower_count (Meta solo da
follower_count a cuentas con 100+ seguidores; si viene vacío se compara
followers_count con el snapshot anterior). FB: cada Reel de la página con sus
video_insights disponibles.
Avisa con tipo "estadisticas" solo si un Reel supera 3x la mediana de
reproducciones (una vez por Reel) o si hay más de 10 seguidores nuevos en un día.
Token caducado (190) -> tipo "error".
"""
import datetime
import glob
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comun

# 23:30 Madrid -> crons 21:30 UTC (CEST) y 22:30 UTC (CET)
if not comun.puerta_horaria({23}):
    sys.exit(0)

DIR = "datos/graph-diario"
ahora = comun.ahora_madrid()
dia = ahora.date()
if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and ahora.hour < 12:
    dia -= datetime.timedelta(days=1)  # cron retrasado pasada la medianoche
destino = os.environ.get("DESTINO") or f"{DIR}/{dia.isoformat()}.json"

errores = []
g = lambda path, **p: comun.graph_get(path, errores, **p)
utc = datetime.datetime.now(datetime.timezone.utc)
desde = utc - datetime.timedelta(days=14)
out = {"generated_at": ahora.replace(tzinfo=None).isoformat(timespec="seconds"),
       "tz": "Europe/Madrid", "origen": "github-actions", "ig": {}, "fb": {}}

try:
    acct = g(comun.IG_ID, fields="username,followers_count,follows_count,media_count")
    out["ig"]["account"] = acct
    rango = dict(period="day", since=int(desde.timestamp()), until=int(utc.timestamp()))
    for met, clave in (("reach", "reach_daily"), ("follower_count", "follower_count_daily")):
        r = g(f"{comun.IG_ID}/insights", metric=met, **rango)
        out["ig"][clave] = [{"end_time": v["end_time"], "value": v["value"]}
                            for d in (r or {}).get("data", []) for v in d.get("values", [])]

    media = []
    r = g(f"{comun.IG_ID}/media", limit=50,
          fields="id,media_type,media_product_type,timestamp,permalink,caption,like_count,comments_count")
    for m in (r or {}).get("data", []):
        if datetime.datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z") < desde:
            continue
        if m.get("media_product_type") != "REELS" and m.get("media_type") != "CAROUSEL_ALBUM":
            continue
        ins = g(f"{m['id']}/insights", metric="views,reach,saved,shares,total_interactions")
        m["insights"] = {d["name"]: (d.get("values") or [{}])[0].get("value", d.get("total_value", {}).get("value"))
                         for d in (ins or {}).get("data", [])}
        m["caption"] = (m.get("caption") or "")[:120]
        media.append(m)
    out["ig"]["media_14d"] = media

    fb = []
    r = g(f"{comun.PAGE_ID}/video_reels", fields="id,description,created_time,permalink_url,length", limit=50)
    for v in (r or {}).get("data", []):
        ins = g(f"{v['id']}/video_insights")
        v["insights"] = {d["name"]: (d.get("values") or [{}])[0].get("value") for d in (ins or {}).get("data", [])}
        v["description"] = (v.get("description") or "")[:120]
        fb.append(v)
    out["fb"]["page"] = g(comun.PAGE_ID, fields="name,followers_count,fan_count")
    out["fb"]["reels"] = fb
except comun.TokenCaducado:
    print("::error::Token de Graph caducado o inválido (error 190)")
    comun.avisar_bot("error", {"origen": "estadisticas", "codigo": 190,
                               "mensaje": "FB_PAGE_TOKEN rechazado por Graph (error 190)"})
    comun.marcar_error_enviado()
    sys.exit(1)

out["errors"] = errores

# --- Alertas ---
previos = sorted(p for p in glob.glob(f"{DIR}/*.json") if os.path.basename(p) < os.path.basename(destino))
ya_alertados = set()
for p in previos[-14:]:
    try:
        snap = json.load(open(p))
        if not snap.get("aviso_enviado"):
            continue  # en seco o con el webhook caído, la alerta se repite hasta entregarse
        for a in snap.get("alerts", []):
            if a.get("media_id"):
                ya_alertados.add(a["media_id"])
    except Exception:
        pass

alertas = []
views = [m["insights"].get("views") for m in media
         if m.get("media_product_type") == "REELS" and isinstance(m["insights"].get("views"), (int, float))]
med = statistics.median(views) if len(views) >= 3 else None
for m in media:
    v = m["insights"].get("views")
    if med and m.get("media_product_type") == "REELS" and isinstance(v, (int, float)) and v > 3 * med:
        alertas.append({"type": "reel_3x_mediana", "media_id": m["id"], "permalink": m["permalink"],
                        "views": v, "mediana": med, "nueva": m["id"] not in ya_alertados})
for d in out["ig"]["follower_count_daily"]:
    if isinstance(d["value"], (int, float)) and d["value"] > 10:
        alertas.append({"type": "seguidores_dia_gt10", "end_time": d["end_time"], "value": d["value"],
                        "nueva": d["end_time"][:10] >= (dia - datetime.timedelta(days=1)).isoformat()})
if not out["ig"]["follower_count_daily"]:
    out["notes"] = ["follower_count diario vacío (Meta lo da a partir de 100 seguidores); "
                    "se compara ig.account.followers_count con el snapshot anterior."]
    if previos and acct:
        try:
            antes = json.load(open(previos[-1]))["ig"]["account"]["followers_count"]
            neto = acct["followers_count"] - antes
            out["ig"]["followers_neto_vs_anterior"] = {"anterior": os.path.basename(previos[-1]), "neto": neto}
            if neto > 10:
                alertas.append({"type": "seguidores_dia_gt10", "desde_snapshot": os.path.basename(previos[-1]),
                                "value": neto, "nueva": True})
        except Exception:
            pass
out["alerts"] = alertas

os.makedirs(DIR, exist_ok=True)
json.dump(out, open(destino, "w"), ensure_ascii=False, indent=1)
print(json.dumps({"archivo": destino, "media_ig": len(media), "views_reels": views, "mediana": med,
                  "reels_fb": len(fb), "seguidores": (acct or {}).get("followers_count"),
                  "alertas": alertas, "errores_api": len(errores)}, ensure_ascii=False, indent=1))

nuevas = [a for a in alertas if a.get("nueva")]
if nuevas:
    out["aviso_enviado"] = comun.avisar_bot("estadisticas", {"fecha": dia.isoformat(), "archivo": destino, "alertas": nuevas})
    json.dump(out, open(destino, "w"), ensure_ascii=False, indent=1)
else:
    print("Sin alertas nuevas. No se avisa al bot.")
