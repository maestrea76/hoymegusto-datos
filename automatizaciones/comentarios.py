"""Comentarios y DMs nuevos de @hoymegusto (IG + página de FB) sin pasar por el bot.

- Comentarios (y respuestas) de las publicaciones de IG y FB de los últimos 14 días.
- DMs de IG (conversaciones actualizadas en los últimos 14 días). Los DMs de FB
  necesitan pages_messaging, que el token no tiene.
- Ignora lo escrito por @hoymegusto / la página y el spam evidente.
- Ids ya vistos en datos/comentarios-vistos.json. Solo se marcan como vistos
  cuando el bot ha recibido el aviso (HTTP 2xx); si no hay webhook o falla, se
  reintentan en la siguiente pasada.
Si hay algo nuevo, avisa con tipo "comentarios": lista de {plataforma, tipo, autor,
texto, enlace, id, fecha}. En el log público, el texto de los DMs se oculta.
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comun

# 13:00 y 21:00 Madrid -> crons 11/19 UTC (CEST) y 12/20 UTC (CET)
if not comun.puerta_horaria({13, 21}):
    sys.exit(0)

VISTOS = "datos/comentarios-vistos.json"
DIAS = 14
SPAM = re.compile(
    r"(promo(te|tion)?\b|collab|colaboraci[oó]n pagada|brand ambassador|embajador(a)? de marca|"
    r"send (us )?(a )?(pic|dm)|dm (us|me)|check (my|our) (bio|profile|page)|"
    r"\bcrypto|bitcoin|forex|invest(ment)?|ganar dinero|gana dinero|"
    r"free followers|seguidores gratis|followers? (cheap|baratos)|onlyfans|"
    r"t\.me/|wa\.me/|bit\.ly/|https?://)",
    re.I,
)


def es_spam(texto):
    return bool(texto) and bool(SPAM.search(texto))


ahora = datetime.datetime.now(datetime.timezone.utc)
desde = ahora - datetime.timedelta(days=DIAS)
vistos = json.load(open(VISTOS))
for k in ("ig_comments", "fb_comments", "ig_dms"):
    vistos.setdefault(k, [])
ya = {k: set(vistos[k]) for k in ("ig_comments", "fb_comments", "ig_dms")}
errores = []
nuevos = []          # elementos para el bot
claves = []          # (lista_vistos, id) para marcar tras enviar
ignorados = {"propios": 0, "spam": 0}


def ts(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")


def considerar(lista, item, autor_propio, texto):
    if item["id"] in ya[lista]:
        return
    if autor_propio:
        ignorados["propios"] += 1
        claves.append((lista, item["id"]))  # se marcan también para no revisarlos más
        return
    if es_spam(texto):
        ignorados["spam"] += 1
        claves.append((lista, item["id"]))
        return
    nuevos.append(item)
    claves.append((lista, item["id"]))


try:
    # --- IG: comentarios ---
    r = comun.graph_get(f"{comun.IG_ID}/media", errores,
                        fields="id,timestamp,permalink,comments_count", limit=50)
    for m in (r or {}).get("data", []):
        if ts(m["timestamp"]) < desde or not m.get("comments_count"):
            continue
        c = comun.graph_get(f"{m['id']}/comments", errores, limit=100,
                            fields="id,text,username,timestamp,from,replies{id,text,username,timestamp,from}")
        for com in (c or {}).get("data", []):
            for x in [com] + (com.get("replies") or {}).get("data", []):
                autor = x.get("username") or (x.get("from") or {}).get("username") or "?"
                considerar("ig_comments", {
                    "plataforma": "instagram", "tipo": "comentario", "autor": autor,
                    "texto": x.get("text", ""), "enlace": m["permalink"],
                    "id": x["id"], "fecha": x.get("timestamp"),
                }, autor == comun.IG_USERNAME, x.get("text", ""))

    # --- FB: comentarios de las publicaciones de la página ---
    r = comun.graph_get(f"{comun.PAGE_ID}/posts", errores,
                        fields="id,created_time,permalink_url", limit=50)
    for p in (r or {}).get("data", []):
        if ts(p["created_time"]) < desde:
            continue
        c = comun.graph_get(f"{p['id']}/comments", errores, limit=100, filter="stream",
                            fields="id,message,from,created_time,permalink_url")
        for x in (c or {}).get("data", []):
            de = x.get("from") or {}
            considerar("fb_comments", {
                "plataforma": "facebook", "tipo": "comentario",
                "autor": de.get("name") or "(sin nombre: Meta no lo da)",
                "texto": x.get("message", ""),
                "enlace": x.get("permalink_url") or p.get("permalink_url"),
                "id": x["id"], "fecha": x.get("created_time"),
            }, de.get("id") == comun.PAGE_ID, x.get("message", ""))

    # --- IG: DMs ---
    r = comun.graph_get(f"{comun.PAGE_ID}/conversations", errores, platform="instagram", limit=25,
                        fields="id,updated_time,messages.limit(20){id,from,message,created_time}")
    if r is None:
        print("DMs de IG no disponibles con este token (ver errores)")
    for conv in (r or {}).get("data", []):
        if ts(conv["updated_time"]) < desde:
            continue
        for msg in (conv.get("messages") or {}).get("data", []):
            if ts(msg["created_time"]) < desde:
                continue
            autor = (msg.get("from") or {}).get("username") or "?"
            considerar("ig_dms", {
                "plataforma": "instagram", "tipo": "dm", "autor": autor,
                "texto": msg.get("message") or "(sin texto: adjunto o reacción)",
                "enlace": "https://www.instagram.com/direct/inbox/",
                "id": msg["id"], "fecha": msg.get("created_time"),
            }, autor == comun.IG_USERNAME, msg.get("message", ""))
except comun.TokenCaducado as e:
    print("::error::Token de Graph caducado o inválido (error 190)")
    comun.avisar_bot("error", {"origen": "comentarios", "codigo": 190,
                               "mensaje": "FB_PAGE_TOKEN rechazado por Graph (error 190)"})
    comun.marcar_error_enviado()
    sys.exit(1)

print(f"Nuevos: {len(nuevos)} | ignorados propios: {ignorados['propios']} | spam: {ignorados['spam']} | "
      f"errores API: {len(errores)}")
for e in errores:
    print("  error API:", json.dumps(e, ensure_ascii=False))

enviado = True
if nuevos:
    nuevos.sort(key=lambda x: x.get("fecha") or "")
    log = [dict(x, texto=f"[DM oculto en el log público: {len(x['texto'])} caracteres]")
           if x["tipo"] == "dm" else x for x in nuevos]
    enviado = comun.avisar_bot("comentarios", nuevos, log_detalle=log)
else:
    print("Nada nuevo. No se avisa al bot.")

if claves and enviado:
    for lista, i in claves:
        if i not in ya[lista]:
            vistos[lista].append(i)
    vistos["ultima_revision"] = comun.ahora_madrid().isoformat(timespec="seconds")
    json.dump(vistos, open(VISTOS, "w"), ensure_ascii=False, indent=1)
    print(f"Marcados como vistos: {len(claves)}")
elif claves:
    print(f"{len(claves)} ids sin marcar como vistos (aviso no entregado); se reintentan en la próxima pasada")
