"""Lee las estadisticas de @hoymegusto y las deja en datos/estadisticas.json.

Host: Instagram API con Facebook Login, el mismo que automatizaciones/comun.py
y graph_diario.py (graph.facebook.com/v26.0, secreto FB_PAGE_TOKEN, cuenta
business 17841430322145064). No usa graph.instagram.com ni /me: con el token
de pagina, /me es la Page, no la cuenta de Instagram.

Tambien mantiene datos/serie_cuenta.json: una fila por dia (fecha, followers_count,
profile_views, website_clicks, views, reach). No inventa follows_and_unfollows ni cifras.
Si una metrica diaria no esta disponible queda null y el motivo en motivos_null.

website_clicks y profile_views salieron del catalogo de insights (deprecacion de
la serie temporal en v21, 8 ene 2025, todas las versiones). No se piden en el
mismo request que el resto: un nombre invalido tumba el bloque. Si Graph no las
devuelve quedan null. profile_links_taps (botones de contacto) no se copia a
website_clicks.

Corre en GitHub Actions. El token vive en FB_PAGE_TOKEN y nunca se escribe en la salida.
"""

import datetime
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo
import urllib.error
import urllib.parse
import urllib.request

# Mismos valores que automatizaciones/comun.py (GRAPH v26, IG_ID).
API = os.environ.get("IG_GRAPH_API", "https://graph.facebook.com/v26.0").rstrip("/")
TOKEN = os.environ.get("FB_PAGE_TOKEN", "").strip()
USER = os.environ.get("IG_USER", "17841430322145064").strip()
SERIE_PATH = os.environ.get("SERIE_PATH", os.path.join("datos", "serie_cuenta.json"))
ESTADISTICAS_PATH = os.environ.get("ESTADISTICAS_PATH", os.path.join("datos", "estadisticas.json"))
PAUSA = float(os.environ.get("IG_API_SLEEP", "1"))
PAUSA_TV = float(os.environ.get("IG_API_SLEEP_TV", "0.3"))

# total_value de cuenta que el catalogo de graph.facebook.com sigue listando.
# No meter aqui website_clicks ni profile_views.
TOTALES_30D = [
    "accounts_engaged",
    "total_interactions",
    "likes",
    "comments",
    "saves",
    "shares",
    "replies",
    "follows_and_unfollows",
    "views",
    "profile_links_taps",
    "reposts",
]
# Se prueban aparte. Si Graph las rechaza, la serie las deja null.
LEGACY_TV = ["website_clicks", "profile_views"]
NOTA_LEGACY = {
    "website_clicks": (
        "no esta en Instagram User Insights de graph.facebook.com "
        "(retirada con la serie temporal en v21, 8 ene 2025). "
        "profile_links_taps cuenta botones de contacto, no el enlace de la bio, y no se copia aqui"
    ),
    "profile_views": (
        "no esta en Instagram User Insights de graph.facebook.com "
        "(retirada con la serie temporal en v21, 8 ene 2025). No hay reemplazo"
    ),
}
NOTA_FOLLOWS = "Meta no lo devuelve si la cuenta tiene menos de 100 seguidores"
# Lo que graph_diario.py ya lee con este token. Si BASE falla, se reintenta esto.
BASE_MIN = "views,reach,saved,shares,total_interactions"

if not TOKEN:
    raise SystemExit("Falta el secreto FB_PAGE_TOKEN")
if not USER:
    raise SystemExit("Falta IG_USER")

_TOKEN_RE = re.compile(r"(access_token=)[^&\s\"']+")


def pausa(segundos):
    if segundos > 0:
        time.sleep(segundos)


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


def cuerpo_error(resp):
    """Desanida el error de Graph. get() guarda el cuerpo entero ({error: {...}})."""
    if not isinstance(resp, dict) or "error" not in resp:
        return None
    e = resp["error"]
    if isinstance(e, dict) and isinstance(e.get("error"), dict):
        e = e["error"]
    return e if isinstance(e, dict) else {"message": str(e)}


def codigo_error(resp):
    e = cuerpo_error(resp)
    if not e:
        return None
    return e.get("code")


def motivo_api(resp):
    e = cuerpo_error(resp) or {}
    return "API error code={} subcode={}: {}".format(
        e.get("code"), e.get("error_subcode"), e.get("message")
    )


def anotar_metrica(metrica, motivo):
    if not motivo:
        return motivo
    if metrica in NOTA_LEGACY and NOTA_LEGACY[metrica] not in motivo:
        return "{}. {}".format(motivo, NOTA_LEGACY[metrica])
    if metrica == "follows_and_unfollows" and "100 seguidores" not in motivo:
        return "{}. {}".format(motivo, NOTA_FOLLOWS)
    return motivo


def redactar(o):
    """Quita paging (lleva el token en next) y cualquier access_token en texto."""
    if isinstance(o, dict):
        return {k: redactar(v) for k, v in o.items() if k != "paging"}
    if isinstance(o, list):
        return [redactar(x) for x in o]
    if isinstance(o, str) and "access_token=" in o:
        return _TOKEN_RE.sub(r"\1REDACTED", o)
    return o


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
                if k == "motivos_null":
                    continue
                if v is None and merged.get(k) is not None:
                    continue
                merged[k] = v
            motivos = dict(old.get("motivos_null") or {})
            motivos.update(fila.get("motivos_null") or {})
            motivos = {k: m for k, m in motivos.items() if merged.get(k) is None}
            if motivos:
                merged["motivos_null"] = motivos
            else:
                merged.pop("motivos_null", None)
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
    (instagram_business_manage_comments / Live): Graph devuelve [] sin
    comments-read aunque el comentario exista. Aquí no se decide el OK;
    el llamador usa comments_count del media. No inventamos textos.
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


def insights_total(metricas, since, until):
    """Pide metric_type=total_value. Si el lote falla, reintenta de una en una.

    Asi una metrica que Graph ya no acepta no deja el bloque entero vacio.
    Devuelve (filas, errores) con errores[nombre] = cuerpo de error.
    """
    if not metricas:
        return [], {}
    r = get(
        "{}/insights".format(USER),
        metric=",".join(metricas),
        metric_type="total_value",
        period="day",
        since=since,
        until=until,
    )
    if "error" not in r:
        data = r.get("data") or []
        return (data if isinstance(data, list) else []), {}
    if len(metricas) == 1:
        return [], {metricas[0]: cuerpo_error(r)}
    filas, errores = [], {}
    for metrica in metricas:
        f, e = insights_total([metrica], since, until)
        filas.extend(f)
        errores.update(e)
        pausa(PAUSA_TV)
    return filas, errores


def registrar_omisiones(nombre, pedidas, filas):
    vinieron = {fila.get("name") for fila in filas if isinstance(fila, dict)}
    faltan = [m for m in pedidas if m not in vinieron]
    if not faltan:
        return
    partes = [anotar_metrica(m, m) for m in faltan]
    metric_errores[nombre] = {
        "message": "la API no devolvio (sin error explicito): {}".format("; ".join(partes))
    }


# ---------- la cuenta ----------
# No llamar a /me: el token de pagina resuelve /me a la Page de Facebook.
cuenta = get(
    USER,
    fields="username,followers_count,follows_count,media_count,biography,profile_picture_url",
)
if codigo_error(cuenta) is not None:
    cuenta_min = get(USER, fields="username,followers_count,follows_count,media_count")
    if "error" not in cuenta_min:
        cuenta_min["campos_extra_error"] = cuerpo_error(cuenta)
        cuenta = cuenta_min
if codigo_error(cuenta) == 190:
    print("::error::FB_PAGE_TOKEN rechazado por Graph (error 190)")
    print(motivo_api(cuenta))
    sys.exit(1)

hoy = datetime.date.today()
desde = int(
    datetime.datetime.combine(hoy - datetime.timedelta(days=29), datetime.time()).timestamp()
)
hasta = int(datetime.datetime.combine(hoy, datetime.time()).timestamp())

cuenta_insights = {}
cuenta_errores = {}
metric_errores = {}

# reach sigue siendo serie temporal (period=day). views solo existe como total_value:
# pedida junto a reach, Meta la omite en silencio. website_clicks y profile_views
# ya no estan en el catalogo; se prueban en LEGACY_TV, nunca dentro de este request.
r = get("{}/insights".format(USER), metric="reach", period="day", since=desde, until=hasta)
if "error" in r:
    cuenta_errores["serie_diaria"] = r["error"]
else:
    data = r.get("data", r)
    cuenta_insights["serie_diaria"] = data
    if isinstance(data, list):
        registrar_omisiones("serie_diaria", ["reach"], data)
pausa(PAUSA)

filas_30, errores_30 = insights_total(TOTALES_30D, desde, hasta)
if filas_30:
    cuenta_insights["totales_30d"] = filas_30
    registrar_omisiones("totales_30d", TOTALES_30D, filas_30)
elif errores_30:
    cuenta_errores["totales_30d"] = errores_30
for metrica, err in errores_30.items():
    metric_errores["total_" + metrica] = {
        "message": anotar_metrica(metrica, motivo_api({"error": err}))
    }
pausa(PAUSA)

# Legacy aparte: si responden, se anexan a totales_30d (el panel lee esos nombres).
# Si no, no se inventa un 0 ni se rellena con profile_links_taps.
legacy_bloqueadas = {}
for metrica in LEGACY_TV:
    filas, errores = insights_total([metrica], desde, hasta)
    err = errores.get(metrica)
    if filas and not err:
        cuenta_insights.setdefault("totales_30d", []).extend(filas)
        pausa(PAUSA_TV)
        continue
    if err:
        motivo = anotar_metrica(metrica, motivo_api({"error": err}))
        code = err.get("code")
    else:
        motivo = anotar_metrica(
            metrica, "la API no devolvio {} (data vacia, sin error)".format(metrica)
        )
        code = None
    metric_errores["total_" + metrica] = {"message": motivo}
    # 100 = metrica invalida; omision sin error = no esta en la respuesta. No repetir 29 dias.
    if code in (10, 100, 190) or code is None:
        legacy_bloqueadas[metrica] = motivo
    pausa(PAUSA_TV)

# ---------- serie diaria total_value (una llamada por metrica y dia) ----------
# Convencion de fecha = la de reach en serie_cuenta (fecha = end_time[:10] de Meta).
# Comprobado con los datos: el valor con end_time D T07:00Z es el dia calendario PT D
# (America/Los_Angeles): la cuenta tuvo reach>0 el primer dia con posts (08-30 PT).
PT = ZoneInfo("America/Los_Angeles")
METRICAS_DIARIAS_TV = ["views", "website_clicks", "profile_views"]
DIAS_BACKFILL = 29      # Meta: consultas de insights de cuenta hasta 30 dias atras por ventana
DIAS_REFRESCO = 3       # los ultimos dias se re-piden siempre (Meta ajusta cifras ~48h)


VERSION_TV = 3          # v1: ventana de 2 dias; v2: guardaba el dia PT en curso (parcial)


def ventana_dia(fecha):
    # Meta incluye todo dia cuyo end_time cae en [since, until]. Con since en la medianoche
    # exacta entraban 2 dias (comprobado: la suma diaria era el doble de totales_30d).
    # since = medianoche + 1 s deja solo el dia con end_time `fin` (= dia PT `fecha`).
    fin = datetime.datetime.combine(fecha, datetime.time(), tzinfo=PT)
    ini = datetime.datetime.combine(fecha - datetime.timedelta(days=1), datetime.time(), tzinfo=PT)
    return int(ini.timestamp()) + 1, int(fin.timestamp())


def metrica_total_dia(metrica, fecha):
    """Devuelve (valor, motivo). valor None => motivo explica por que."""
    ini, fin = ventana_dia(fecha)
    r = get(
        "{}/insights".format(USER),
        metric=metrica, metric_type="total_value", period="day", since=ini, until=fin,
    )
    if "error" in r:
        return None, anotar_metrica(metrica, motivo_api(r))
    for fila in r.get("data") or []:
        if fila.get("name") == metrica:
            tv = fila.get("total_value")
            if isinstance(tv, dict) and tv.get("value") is not None:
                return tv.get("value"), None
            return None, anotar_metrica(metrica, "la API devolvio {} sin total_value".format(metrica))
    return None, anotar_metrica(
        metrica, "la API no devolvio {} (data vacia, sin error)".format(metrica)
    )


serie_previa = {}
if os.path.exists(SERIE_PATH):
    try:
        with open(SERIE_PATH, encoding="utf-8") as f:
            _s = json.load(f) or {}
        # Solo se reaprovechan valores calculados con la version actual de la ventana.
        if (_s.get("metricas_diarias") or {}).get("version_tv") == VERSION_TV:
            for d in _s.get("dias", []):
                if isinstance(d, dict) and d.get("fecha"):
                    serie_previa[d["fecha"]] = d
    except Exception:
        serie_previa = {}

fecha_hoy_pt = datetime.datetime.now(PT).date()   # dia PT en curso: aun incompleto
fecha_hoy_utc = datetime.datetime.now(datetime.timezone.utc).date()
diarias_tv = {}          # {fecha_iso: {metrica: valor}}
diarias_tv_motivos = {}  # {fecha_iso: {metrica: motivo}}
metrica_bloqueada = {}   # permiso, token o metrica inexistente: no insistir cada dia
metrica_bloqueada.update(legacy_bloqueadas)
llamadas_tv = 0
for fecha in sorted({fecha_hoy_pt, fecha_hoy_utc}):
    if fecha >= fecha_hoy_pt:
        # No se guarda un parcial como si fuera el dia completo; se rellena en el siguiente run.
        diarias_tv_motivos[fecha.isoformat()] = {
            m: metrica_bloqueada.get(m) or (
                "dia en curso en America/Los_Angeles (Meta): se completa en el siguiente run"
            )
            for m in METRICAS_DIARIAS_TV
        }
for delta in range(DIAS_BACKFILL, 0, -1):
    fecha = fecha_hoy_pt - datetime.timedelta(days=delta)
    iso = fecha.isoformat()
    previa = serie_previa.get(iso, {})
    for metrica in METRICAS_DIARIAS_TV:
        if delta > DIAS_REFRESCO and previa.get(metrica) is not None:
            continue
        if metrica in metrica_bloqueada:
            diarias_tv_motivos.setdefault(iso, {})[metrica] = metrica_bloqueada[metrica]
            continue
        valor, motivo = metrica_total_dia(metrica, fecha)
        llamadas_tv += 1
        if motivo is None:
            diarias_tv.setdefault(iso, {})[metrica] = valor
        else:
            diarias_tv_motivos.setdefault(iso, {})[metrica] = motivo
            metric_errores.setdefault("diaria_" + metrica, {"message": motivo, "fecha": iso})
            if (
                motivo.startswith("API error code=10 ")
                or motivo.startswith("API error code=100 ")
                or motivo.startswith("API error code=190 ")
            ):
                metrica_bloqueada[metrica] = motivo
        pausa(PAUSA_TV)

# ---------- las publicaciones ----------
# media_url y thumbnail_url siguen en el nodo. Si Graph los rechaza, se reintenta
# con los campos que graph_diario.py ya lee con este token.
media = get(
    "{}/media".format(USER),
    fields="id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count,media_url,thumbnail_url",
    limit=50,
)
if "error" in media:
    media_min = get(
        "{}/media".format(USER),
        fields="id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count",
        limit=50,
    )
    if "error" not in media_min:
        metric_errores["media_fields"] = {"message": motivo_api(media)}
        media = media_min

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
    # likes/comments de insights a veces no aplican al tipo de pieza. El conjunto
    # que graph_diario ya usa con este token conserva views, reach, saved y shares.
    if "error" in ins:
        m["insights_error_base"] = ins["error"]
        ins = get("{}/insights".format(m["id"]), metric=BASE_MIN)
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

    # Regla 29/09/2026: primer_comentario_ok sigue a comments_count del media.
    # POST que publicó deja comments_count>0. GET /comments vacío (sin
    # comments-read) no es fallo y no hace falta el prefijo "Ficha y precio".
    # Si comments_count no viene, queda false: no se inventa el conteo.
    cc = m.get("comments_count")
    if isinstance(cc, bool) or not isinstance(cc, int):
        m["primer_comentario_ok"] = False
        m["primer_comentario_nota"] = "comments_count ausente; no se marca OK"
    elif cc > 0:
        m["primer_comentario_ok"] = True
    else:
        m["primer_comentario_ok"] = False

    texto_ok = (not comentarios_err) and primer_comentario_ok(comentarios, username)
    if m["primer_comentario_ok"] and not texto_ok:
        m["primer_comentario_texto"] = (
            "no verificado: GET /comments vacío o sin texto del autor; OK por comments_count>0"
        )
    if not comentarios and isinstance(cc, int) and not isinstance(cc, bool) and cc > 0:
        m["comentarios_discrepancia"] = {
            "comments_count": cc,
            "comentarios_n": 0,
            "nota": "GET /comments vacío con comments_count>0; OK por comments_count, texto no verificado",
        }

    publicaciones.append(m)
    pausa(PAUSA)

ahora = datetime.datetime.now(datetime.timezone.utc)
salida = {
    "generado": ahora.isoformat(),
    "api": API,
    "login": "facebook",
    "ig_user_id": USER,
    "cuenta": cuenta,
    "cuenta_insights": cuenta_insights,
    "cuenta_insights_errores": cuenta_errores,
    "metric_errores": metric_errores,
    "publicaciones": publicaciones,
}

os.makedirs(os.path.dirname(ESTADISTICAS_PATH) or ".", exist_ok=True)
salida = redactar(salida)
with open(ESTADISTICAS_PATH, "w", encoding="utf-8") as f:
    json.dump(salida, f, ensure_ascii=False, indent=2)

# ---------- serie_cuenta.json (append/upsert por dia) ----------
serie = cargar_serie()
if (serie.get("metricas_diarias") or {}).get("version_tv") != VERSION_TV:
    # Valores diarios total_value de una version anterior (ventana erronea): se descartan
    # para que no sobrevivan si el recalculo falla. reach/followers no se tocan.
    for d in serie["dias"]:
        for k in METRICAS_DIARIAS_TV:
            if k in d:
                d[k] = None
reach_map = serie_a_mapa(cuenta_insights.get("serie_diaria"), "reach")
fechas = set(reach_map) | set(diarias_tv) | set(diarias_tv_motivos)
fecha_run = ahora.date().isoformat()
fechas.add(fecha_run)

followers_hoy = None
if isinstance(cuenta, dict) and isinstance(cuenta.get("followers_count"), int):
    followers_hoy = cuenta["followers_count"]

for fecha in sorted(fechas):
    tv = diarias_tv.get(fecha, {})
    fila = {
        "fecha": fecha,
        "followers_count": followers_hoy if fecha == fecha_run else None,
        "profile_views": tv.get("profile_views"),
        "website_clicks": tv.get("website_clicks"),
        "views": tv.get("views"),
        "reach": reach_map.get(fecha),
    }
    motivos = dict(diarias_tv_motivos.get(fecha, {}))
    if motivos:
        fila["motivos_null"] = motivos
    # No inventar: si no hay ningun dato util (todo null salvo fecha), igual upsert
    # para poder rellenar reach historico; followers solo el dia del run.
    upsert_dia(serie["dias"], fila)

serie["dias"].sort(key=lambda d: d.get("fecha") or "")
for d in serie["dias"]:
    d.setdefault("views", None)
serie["generado"] = ahora.isoformat()
serie["metricas_diarias"] = {
    "reach": "insights period=day (serie temporal) en graph.facebook.com/v26.0",
    "views": "insights metric_type=total_value period=day, una llamada por dia",
    "website_clicks,profile_views": (
        "se piden igual, pero graph.facebook.com ya no las lista (v21+, 8 ene 2025). "
        "Si no vienen quedan null en motivos_null. profile_links_taps no se copia a website_clicks"
    ),
    "convencion_fecha": "fecha D = end_time[:10] de Meta = dia calendario America/Los_Angeles D; el dia PT en curso queda null (motivos_null)",
    "version_tv": VERSION_TV,
}
serie["api"] = API
serie["login"] = "facebook"
serie["ig_user_id"] = USER
# TT omitido: Overview oficial no trae followers netos diarios fiables.

os.makedirs(os.path.dirname(SERIE_PATH) or ".", exist_ok=True)
serie = redactar(serie)
with open(SERIE_PATH, "w", encoding="utf-8") as f:
    json.dump(serie, f, ensure_ascii=False, indent=2)

print(json.dumps(cuenta, ensure_ascii=False))
print("bloques de cuenta que funcionan:", list(cuenta_insights))
print("bloques de cuenta que fallan:", list(cuenta_errores))
print("metric_errores:", metric_errores)
print("{} publicaciones".format(len(publicaciones)))
print("serie_cuenta dias:", len(serie["dias"]), "llamadas total_value diarias:", llamadas_tv)
for d in serie["dias"][-5:]:
    print("  serie", json.dumps(d, ensure_ascii=False))
for p in publicaciones:
    print(
        " ",
        p["timestamp"][:10],
        "primer_comentario_ok=",
        p.get("primer_comentario_ok"),
        json.dumps(p.get("insights", {}), ensure_ascii=False),
    )
