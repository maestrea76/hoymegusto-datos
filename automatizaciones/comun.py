"""Utilidades comunes de las automatizaciones que despiertan al bot.

- Hora de Madrid y "puerta" de horario de verano/invierno: cada workflow programa
  dos crons UTC (uno vale en CEST y otro en CET); solo sigue el que corresponde a
  la hora local pedida. El retraso de GitHub no afecta: se mira el cron que disparó
  (github.event.schedule), no la hora real de arranque.
- Aviso al bot por webhook (POST JSON, timeout 8 s, un solo intento). Si faltan
  BOT_WEBHOOK_URL / BOT_WEBHOOK_KEY, escribe en el log lo que habría enviado.
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


def avisar_bot(tipo, detalle, log_detalle=None):
    """Despierta al bot. Devuelve True solo si el webhook respondió 2xx.
    log_detalle: versión del detalle para el log (el repo es público)."""
    payload = {"tipo": tipo, "detalle": detalle}
    visible = {"tipo": tipo, "detalle": detalle if log_detalle is None else log_detalle}
    url = os.environ.get("BOT_WEBHOOK_URL", "").strip()
    key = os.environ.get("BOT_WEBHOOK_KEY", "").strip()
    if not url or not key:
        print("SIN WEBHOOK (faltan BOT_WEBHOOK_URL/BOT_WEBHOOK_KEY). Habría enviado:")
        print(json.dumps(visible, ensure_ascii=False, indent=1))
        return False
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "X-Automation-Key": key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            print(f"Aviso '{tipo}' enviado al bot: HTTP {r.status}")
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        print(f"::warning::El webhook respondió HTTP {e.code} al aviso '{tipo}'")
    except Exception as e:
        print(f"::warning::No se pudo enviar el aviso '{tipo}': {type(e).__name__}")
    return False


def marcar_error_enviado():
    open("/tmp/aviso_error_enviado", "w").write("1")


if __name__ == "__main__":
    # CLI: python comun.py TIPO 'JSON_DETALLE'
    tipo = sys.argv[1]
    detalle = json.loads(sys.argv[2]) if len(sys.argv) > 2 else None
    avisar_bot(tipo, detalle)
