"""Lee las estadisticas de @hoymegusto en la API de Instagram y las deja en datos/estadisticas.json.

Se ejecuta en GitHub Actions, que si tiene salida a internet hacia graph.instagram.com.
El token vive en el secreto IG_TOKEN del repositorio y nunca se escribe en el fichero.
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


cuenta = get(USER, fields="username,followers_count,follows_count,media_count")
media = get(
    "{}/media".format(USER),
    fields="id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count",
    limit=50,
)

publicaciones = []
for m in media.get("data", []):
    ins = get(
        "{}/insights".format(m["id"]),
        metric="views,reach,likes,comments,shares,saved,total_interactions",
    )
    valores = {}
    for fila in ins.get("data", []):
        try:
            valores[fila["name"]] = fila["values"][0]["value"]
        except Exception:
            pass
    m["insights"] = valores
    if "error" in ins:
        m["insights_error"] = ins["error"]
    publicaciones.append(m)
    time.sleep(1)

salida = {
    "generado": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "cuenta": cuenta,
    "publicaciones": publicaciones,
}

os.makedirs("datos", exist_ok=True)
with open("datos/estadisticas.json", "w", encoding="utf-8") as f:
    json.dump(salida, f, ensure_ascii=False, indent=2)

print(json.dumps(cuenta, ensure_ascii=False))
print("{} publicaciones".format(len(publicaciones)))
if publicaciones:
    print(json.dumps(publicaciones[0].get("insights", {}), ensure_ascii=False))
    if "insights_error" in publicaciones[0]:
        print("ERROR insights:", json.dumps(publicaciones[0]["insights_error"], ensure_ascii=False))
