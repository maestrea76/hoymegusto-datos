"""Utilidades comunes de las automatizaciones que despiertan al bot.

- Hora de Madrid y "puerta" de horario de verano/invierno: cada workflow programa
  dos crons UTC (uno vale en CEST y otro en CET); solo sigue el que corresponde a
  la hora local pedida. El retraso de GitHub no afecta: se mira el cron que disparó
  (github.event.schedule), no la hora real de arranque.
- Aviso al bot: abre un issue con etiqueta «aviso-bot» asignado a maestrea76
  (GITHUB_TOKEN, permissions issues: write). Con SECO=true solo escribe en el log.
  Repo público: el issue nunca lleva texto de DMs, tokens ni datos privados.
- Graph API con el Page token (FB_PAGE_TOKEN). Nunca se imprimen tokens ni paging.
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
GRAPH = "https://graph.facebook.com/v26.0/"
IG_ID = "17841430322145064"
PAGE_ID = "1349934388202783"
IG_USERNAME = "hoymegusto"


def ahora_madrid():
    return datetime.datetime.now(MADRID)


def puerta_horaria(horas_locales):
    """True si el run debe seguir. En schedule, comprueba que el cron UTC que lo
    disparó corresponde (con el desfase actual de Madrid) a una de horas_locales."""
    evento = os.environ.get("GITHUB_EVENT_NAME", "")
    cron = os.environ.get("CRON_SCHEDULE", "").strip()
    if evento != "schedule" or not cron:
        print(f"Evento {evento or 'local'}: sin puerta horaria")
        return True
    minuto, hora = cron.split()[:2]
    desfase = int(ahora_madrid().utcoffset().total_seconds() // 3600)
    local = (int(hora) + desfase) % 24
    ok = local in horas_locales
    print(f"Cron '{cron}' -> {local:02d}:{int(minuto):02d} Madrid (UTC+{desfase}); "
          f"{'sigue' if ok else 'no toca en este horario, termina'}")
    return ok


def _limpiar(o):
    """Quita 'paging' (lleva el token en next/previous) de cualquier respuesta."""
    if isinstance(o, dict):
        return {k: _limpiar(v) for k, v in o.items() if k != "paging"}
    if isinstance(o, list):
        return [_limpiar(x) for x in o]
    return o


class TokenCaducado(Exception):
    pass


def graph_get(path, errores=None, **params):
    token = os.environ.get("FB_PAGE_TOKEN", "").strip()
    if not token:
        raise SystemExit("Falta el secreto FB_PAGE_TOKEN")
    params["access_token"] = token
    url = GRAPH + path + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return _limpiar(json.load(r))
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}").get("error", {})
        except Exception:
            err = {"message": f"HTTP {e.code}"}
        err = {k: err.get(k) for k in ("message", "type", "code", "error_subcode")}
        if err.get("code") == 190:
            raise TokenCaducado(err.get("message"))
        if errores is not None:
            p = {k: v for k, v in params.items() if k != "access_token"}
            errores.append({"path": path, "params": p, "error": err})
        return None
    except Exception as e:  # red, timeout...
        if errores is not None:
            errores.append({"path": path, "error": {"message": str(e)[:200]}})
        return None


REPO_API = "https://api.github.com/repos/"
ETIQUETA = "aviso-bot"
ASIGNADO = "maestrea76"


def _gh(metodo, ruta, datos=None):
    """Llamada a la API de GitHub con GITHUB_TOKEN. Devuelve (status, json)."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "maestrea76/hoymegusto-datos")
    if not token:
        raise RuntimeError("falta GITHUB_TOKEN")
    req = urllib.request.Request(
        REPO_API + repo + ruta, method=metodo,
        data=None if datos is None else json.dumps(datos, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None


def _issue_abierto(titulo):
    st, issues = _gh("GET", f"/issues?state=open&labels={ETIQUETA}&per_page=100")
    if st != 200:
        raise RuntimeError(f"no se pudieron listar los issues (HTTP {st})")
    for i in issues or []:
        if i.get("title") == titulo and "pull_request" not in i:
            return i
    return None


def avisar_bot(tipo, detalle, fecha=None, comentar_si_existe=False):
    """Despierta al bot abriendo un issue «aviso-bot: <tipo> <fecha>» con etiqueta
    aviso-bot, asignado a maestrea76 y cuerpo JSON {"tipo", "detalle"}.
    El repo es PÚBLICO: detalle no puede llevar texto de DMs, tokens ni datos privados.

    Devuelve "creado", "duplicado" (ya hay uno abierto igual), "comentado"
    (ya había uno abierto y se le añade un comentario con el nuevo detalle),
    "seco" (SECO=true: no escribe nada) o None si falló.
    Se considera entregado si devuelve creado, duplicado o comentado."""
    fecha = fecha or ahora_madrid().date().isoformat()
    titulo = f"aviso-bot: {tipo} {fecha}"
    cuerpo = json.dumps({"tipo": tipo, "detalle": detalle}, ensure_ascii=False, indent=1)
    seco = os.environ.get("SECO", "").strip().lower() in ("1", "true", "yes", "si", "sí")
    print(f"Aviso «{titulo}»:")
    print(cuerpo)
    try:
        existente = _issue_abierto(titulo)
        if existente:
            if not comentar_si_existe:
                print(f"Ya hay un issue abierto igual (#{existente['number']}); no se abre otro")
                return "seco" if seco else "duplicado"
            if seco:
                print(f"SECO: habría comentado en el issue abierto #{existente['number']}")
                return "seco"
            st, _ = _gh("POST", f"/issues/{existente['number']}/comments",
                        {"body": "Nuevos elementos:\n```json\n" + cuerpo + "\n```"})
            if st == 201:
                print(f"Añadido como comentario al issue abierto #{existente['number']}")
                return "comentado"
            print(f"::warning::No se pudo comentar en #{existente['number']} (HTTP {st})")
            return None
        if seco:
            print("SECO: no se crea el issue")
            return "seco"
        st, _ = _gh("GET", f"/labels/{ETIQUETA}")
        if st == 404:
            st, _ = _gh("POST", "/labels", {"name": ETIQUETA, "color": "5319e7",
                                            "description": "Aviso automático que despierta al bot"})
            print(f"Etiqueta {ETIQUETA} creada (HTTP {st})")
        st, issue = _gh("POST", "/issues", {"title": titulo, "body": "```json\n" + cuerpo + "\n```",
                                           "labels": [ETIQUETA], "assignees": [ASIGNADO]})
        if st == 201:
            asignados = [a["login"] for a in issue.get("assignees", [])]
            print(f"Issue #{issue['number']} creado: {issue['html_url']} (asignado a {asignados})")
            if ASIGNADO not in asignados:
                print(f"::warning::El issue se creó pero sin asignar a {ASIGNADO}")
            return "creado"
        print(f"::warning::No se pudo crear el issue (HTTP {st}): {(issue or {}).get('message')}")
    except Exception as e:
        print(f"::warning::Fallo al avisar al bot: {e}")
    return None


ENTREGADO = ("creado", "duplicado", "comentado")


def marcar_error_enviado():
    open("/tmp/aviso_error_enviado", "w").write("1")


if __name__ == "__main__":
    # CLI: python comun.py TIPO 'JSON_DETALLE'  (sale con 1 si no se pudo avisar)
    tipo = sys.argv[1]
    detalle = json.loads(sys.argv[2]) if len(sys.argv) > 2 else None
    r = avisar_bot(tipo, detalle)
    sys.exit(0 if r in ENTREGADO + ("seco",) else 1)
