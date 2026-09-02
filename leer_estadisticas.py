"""Lee las estadisticas de @hoymegusto en la API de Instagram y las deja en datos/estadisticas.json.

Corre en GitHub Actions, que si tiene salida hacia graph.instagram.com.
El token vive en el secreto IG_TOKEN y nunca se escribe en el fichero de salida.

Cada bloque de metricas se pide por separado y, si Meta lo rechaza, el error se guarda
en el JSON en vez de tumbar la ejecucion. Asi vemos que acepta y que no sin adivinar.
"""

import datetime
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://graph.instagram.com/v23.0"
TOKEN = os.environ["IG_TOKEN"]
USER = os.environ["IG_USER"]


def get(path, **params):
    params["access_token"] = TOKEN
    url = "{}/{}?{}".format(API, path, urllib.parse.urlencode(params))
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return {"error": json.loads(e.read().decode())}
        except Exception:
            return {"error": {"message": "HTTP {}".format(e.code)}}
    except Exception as e:
        return {"error": {"message": str(e)}}


def valores(respuesta):
    """Aplana la respuesta de /insights a {metrica: valor}."""
    fuera = {}
    for fila in respuesta.get("data", []):
        nombre = fila.get("name")
        if "total_value" in fila:
            fuera[nombre] = fila["total_value"].get("value")
        else:
            try:
                fuera[nombre] = fila["values"][0]["value"]
            except Exception:
                pass
    return fuera


# ---------- la cuenta ----------
cuenta = get(
    USER,
    fields="username,followers_count,follows_count,media_count,biography,profile_picture_url",
)

hoy = datetime.date.today()
desde = int(
    datetime.datetime.combine(hoy - datetime.timedelta(days=29), datetime.time()).timestamp()
)
hasta = int(datetime.datetime.combine(hoy, datetime.time()).timestamp())

cuenta_insights = {}
cuenta_errores = {}

bloques = [
    ("serie_diaria", {"metric": "reach,views", "period": "day", "since": desde, "until": hasta}),
    (
        "totales_30d",
        {
            "metric": "profile_views,website_clicks,accounts_engaged,total_interactions,likes,comments,saves,shares,replies,follows_and_unfollows",
            "metric_type": "total_value",
            "period": "day",
            "since": desde,
            "until": hasta,
        },
    ),
]

for nombre, params in bloques:
    r = get("{}/insights".format(USER), **params)
    if "error" in r:
        cuenta_errores[nombre] = r["error"]
    else:
        cuenta_insights[nombre] = r.get("data", r)
    time.sleep(1)

# ---------- las publicaciones ----------
media = get(
    "{}/media".format(USER),
    fields="id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count,media_url,thumbnail_url",
    limit=50,
)

BASE = "views,reach,likes,comments,shares,saved,total_interactions"
REELS = BASE + ",ig_reels_avg_watch_time,ig_reels_video_view_total_time"

publicaciones = []
for m in media.get("data", []):
    metricas = REELS if m.get("media_product_type") == "REELS" else BASE
    ins = get("{}/insights".format(m["id"]), metric=metricas)
    if "error" in ins and metricas == REELS:
        # Si el bloque de reels lo rechaza, reintenta con las metricas basicas.
        m["insights_error_reels"] = ins["error"]
        ins = get("{}/insights".format(m["id"]), metric=BASE)
    m["insights"] = valores(ins)
    if "error" in ins:
        m["insights_error"] = ins["error"]

    # Tiempo medio de visionado en segundos, que es como lo leemos en la app.
    avg = m["insights"].get("ig_reels_avg_watch_time")
    if isinstance(avg, (int, float)):
        m["insights"]["segundos_medios"] = round(avg / 1000.0, 1)
    total = m["insights"].get("ig_reels_video_view_total_time")
    if isinstance(total, (int, float)):
        m["insights"]["segundos_totales"] = round(total / 1000.0, 1)

    comentarios = get(
        "{}/comments".format(m["id"]), fields="id,text,username,timestamp,like_count", limit=50
    )
    if "error" in comentarios:
        m["comentarios_error"] = comentarios["error"]
    else:
        m["comentarios"] = comentarios.get("data", [])

    publicaciones.append(m)
    time.sleep(1)

salida = {
    "generado": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "cuenta": cuenta,
    "cuenta_insights": cuenta_insights,
    "cuenta_insights_errores": cuenta_errores,
    "publicaciones": publicaciones,
}

os.makedirs("datos", exist_ok=True)
with open("datos/estadisticas.json", "w", encoding="utf-8") as f:
    json.dump(salida, f, ensure_ascii=False, indent=2)

print(json.dumps(cuenta, ensure_ascii=False))
print("bloques de cuenta que funcionan:", list(cuenta_insights))
print("bloques de cuenta que fallan:", list(cuenta_errores))
print("{} publicaciones".format(len(publicaciones)))
for p in publicaciones:
    print(" ", p["timestamp"][:10], json.dumps(p.get("insights", {}), ensure_ascii=False))
