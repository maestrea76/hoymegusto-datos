"""Lee las estadisticas de @hoymegusto en la API de Instagram y las deja en datos/estadisticas.json.

Tambien mantiene datos/serie_cuenta.json: una fila por dia (fecha, followers_count,
profile_views, website_clicks, reach). No inventa follows_and_unfollows ni cifras.

Corre en GitHub Actions. El token vive en IG_TOKEN y nunca se escribe en la salida.
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
SERIE_PATH = os.path.join("datos", "serie_cuenta.json")
ESTADISTICAS_PATH = os.path.join("datos", "estadisticas.json")


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


def serie_a_mapa(data_list, metric_name):
    """De data[] de insights period=day -> {YYYY-MM-DD: value} para una metrica."""
    out = {}
    for fila in data_list or []:
        if fila.get("name") != metric_name:
            continue
        for v in fila.get("values") or []:
            end = v.get("end_time") or ""
            # end_time tipo 2026-09-17T07:00:00+0000 -> fecha calendario UTC
            fecha = end[:10]
            if len(fecha) == 10:
                out[fecha] = v.get("value")
    return out


def cargar_serie():
    if not os.path.exists(SERIE_PATH):
        return {"generado": None, "fuente": "instagram_graph", "dias": []}
    with open(SERIE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"generado": None, "fuente": "instagram_graph", "dias": []}
    data.setdefault("fuente", "instagram_graph")
    data.setdefault("dias", [])
    if not isinstance(data["dias"], list):
        data["dias"] = []
    return data


def upsert_dia(dias, fila):
    """Fusiona por fecha; no borra campos ya rellenados con None nuevos."""
    fecha = fila["fecha"]
    for i, old in enumerate(dias):
        if old.get("fecha") == fecha:
            merged = dict(old)
            for k, v in fila.items():
                if v is None and merged.get(k) is not None:
                    continue
                merged[k] = v
            dias[i] = merged
            return
    dias.append(fila)


def comment_username(c):
    """Username del autor: top-level o from.username (Graph IG)."""
    if not isinstance(c, dict):
        return ""
    u = c.get("username")
    if not u and isinstance(c.get("from"), dict):
        u = c["from"].get("username")
    return (u or "").lstrip("@").lower()


def primer_comentario_ok(comentarios, username):
    """True si hay al menos un comentario del propio username (regla primer comentario)."""
    if not isinstance(comentarios, list) or not username:
        return False
    uname = username.lstrip("@").lower()
    for c in comentarios:
        u = comment_username(c)
        if u and u == uname:
            return True
    return False


def get_url(url):
    """GET a una URL absoluta (p. ej. paging.next). Enmascara errores como get()."""
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


def listar_comentarios(media_id):
    """Lista comentarios con paginación y fallback de fields.

    Si comments_count > 0 pero data sale vacío, suele ser permiso/app mode
    (instagram_business_manage_comments / Live) o field username restringido.
    No inventamos True: devolvemos lista vacía y meta de diagnóstico.
    """
    field_sets = [
        "id,text,username,timestamp,like_count,from",
        "id,text,timestamp,like_count,from{id,username}",
        "id,text,timestamp,like_count",
    ]
    last_error = None
    last_raw = None
    for fields in field_sets:
        items = []
        resp = get("{}/comments".format(media_id), fields=fields, limit=50)
        last_raw = resp
        if "error" in resp:
            last_error = resp["error"]
            continue
        page = resp
        pages = 0
        while isinstance(page, dict) and pages < 10:
            pages += 1
            items.extend(page.get("data") or [])
            nxt = (page.get("paging") or {}).get("next")
            if not nxt:
                break
            page = get_url(nxt)
            if "error" in page:
                last_error = page["error"]
                break
            last_raw = page
        meta = {
            "fields": fields,
            "pages": pages,
            "n": len(items),
            "has_paging": bool((resp.get("paging") or {}).get("next") or (resp.get("paging") or {}).get("cursors")),
        }
        if items:
            return items, None, meta
        # data vacío: probar siguiente field set (username restringido a veces vacía data)
        continue
    return [], last_error, {
        "fields": field_sets[-1],
        "pages": 0,
        "n": 0,
        "has_paging": bool(((last_raw or {}).get("paging") or {}).get("cursors")),
        "raw_keys": sorted((last_raw or {}).keys()) if isinstance(last_raw, dict) else [],
    }


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
metric_errores = {}

# Serie diaria: reach + views (views a menudo falla; se registra por metrica).
bloques = [
    ("serie_diaria", {"metric": "reach,views", "period": "day", "since": desde, "until": hasta}),
    (
        "totales_30d",
        {
            "metric": "profile_views,website_clicks,accounts_engaged,total_interactions,likes,comments,saves,shares,replies,follows_and_unfollows",
            "metric_type": "total_value",
            "period": "day",
            "since": desde, "until": hasta,
        },
    ),
    # Intento de serie diaria para clicks/visitas (si Meta lo rechaza, queda en errores y nulls).
    (
        "serie_perfil_clicks",
        {
            "metric": "profile_views,website_clicks",
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
        data = r.get("data", r)
        cuenta_insights[nombre] = data
        if nombre == "serie_diaria" and isinstance(data, list):
            names = {fila.get("name") for fila in data}
            if "views" not in names:
                metric_errores["views_day"] = {
                    "message": "views no vino en serie_diaria (solo {})".format(
                        sorted(n for n in names if n)
                    )
                }
    time.sleep(1)

# ---------- las publicaciones ----------
media = get(
    "{}/media".format(USER),
    fields="id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count,media_url,thumbnail_url",
    limit=50,
)

BASE = "views,reach,likes,comments,shares,saved,total_interactions"
REELS = BASE + ",ig_reels_avg_watch_time,ig_reels_video_view_total_time"
username = (cuenta.get("username") if isinstance(cuenta, dict) else None) or ""

publicaciones = []
for m in media.get("data", []):
    metricas = REELS if m.get("media_product_type") == "REELS" else BASE
    ins = get("{}/insights".format(m["id"]), metric=metricas)
    if "error" in ins and metricas == REELS:
        m["insights_error_reels"] = ins["error"]
        ins = get("{}/insights".format(m["id"]), metric=BASE)
    m["insights"] = valores(ins)
    if "error" in ins:
        m["insights_error"] = ins["error"]

    avg = m["insights"].get("ig_reels_avg_watch_time")
    if isinstance(avg, (int, float)):
        m["insights"]["segundos_medios"] = round(avg / 1000.0, 1)
    total = m["insights"].get("ig_reels_video_view_total_time")
    if isinstance(total, (int, float)):
        m["insights"]["segundos_totales"] = round(total / 1000.0, 1)

    comentarios, comentarios_err, comentarios_meta = listar_comentarios(m["id"])
    m["comentarios"] = comentarios
    if comentarios_meta:
        m["comentarios_meta"] = comentarios_meta
    if comentarios_err:
        m["comentarios_error"] = comentarios_err
        m["primer_comentario_ok"] = False
    else:
        m["primer_comentario_ok"] = primer_comentario_ok(comentarios, username)
        # Evidencia: comments_count > 0 pero /comments vacío → lectura API, no ausencia real
        cc = m.get("comments_count")
        if not comentarios and isinstance(cc, int) and cc > 0:
            m["comentarios_discrepancia"] = {
                "comments_count": cc,
                "comentarios_n": 0,
                "nota": "API devolvió data vacía con comments_count>0; no marcar true sin autor",
            }

    publicaciones.append(m)
    time.sleep(1)

ahora = datetime.datetime.now(datetime.timezone.utc)
salida = {
    "generado": ahora.isoformat(),
    "cuenta": cuenta,
    "cuenta_insights": cuenta_insights,
    "cuenta_insights_errores": cuenta_errores,
    "metric_errores": metric_errores,
    "publicaciones": publicaciones,
}

os.makedirs("datos", exist_ok=True)
with open(ESTADISTICAS_PATH, "w", encoding="utf-8") as f:
    json.dump(salida, f, ensure_ascii=False, indent=2)

# ---------- serie_cuenta.json (append/upsert por dia) ----------
serie = cargar_serie()
reach_map = serie_a_mapa(cuenta_insights.get("serie_diaria"), "reach")
pv_map = serie_a_mapa(cuenta_insights.get("serie_perfil_clicks"), "profile_views")
wc_map = serie_a_mapa(cuenta_insights.get("serie_perfil_clicks"), "website_clicks")

fechas = set(reach_map) | set(pv_map) | set(wc_map)
fecha_run = ahora.date().isoformat()
fechas.add(fecha_run)

followers_hoy = None
if isinstance(cuenta, dict) and isinstance(cuenta.get("followers_count"), int):
    followers_hoy = cuenta["followers_count"]

for fecha in sorted(fechas):
    fila = {
        "fecha": fecha,
        "followers_count": followers_hoy if fecha == fecha_run else None,
        "profile_views": pv_map.get(fecha),
        "website_clicks": wc_map.get(fecha),
        "reach": reach_map.get(fecha),
    }
    # No inventar: si no hay ningun dato util (todo null salvo fecha), igual upsert
    # para poder rellenar reach historico; followers solo el dia del run.
    upsert_dia(serie["dias"], fila)

serie["dias"].sort(key=lambda d: d.get("fecha") or "")
serie["generado"] = ahora.isoformat()
# TT omitido: Overview oficial no trae followers netos diarios fiables.

with open(SERIE_PATH, "w", encoding="utf-8") as f:
    json.dump(serie, f, ensure_ascii=False, indent=2)

print(json.dumps(cuenta, ensure_ascii=False))
print("bloques de cuenta que funcionan:", list(cuenta_insights))
print("bloques de cuenta que fallan:", list(cuenta_errores))
print("metric_errores:", metric_errores)
print("{} publicaciones".format(len(publicaciones)))
print("serie_cuenta dias:", len(serie["dias"]))
for p in publicaciones:
    print(
        " ",
        p["timestamp"][:10],
        "primer_comentario_ok=",
        p.get("primer_comentario_ok"),
        json.dumps(p.get("insights", {}), ensure_ascii=False),
    )
