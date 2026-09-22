"""Briefing semanal de viralizacion para el panel.

Lee solo JSON locales (sin Graph). Escribe datos/diagnostico_semanal.json.
Pensado para el martes 10:00 Europe/Madrid: el Action corre a las 09:00
Madrid para que a las 10:00 el jefe ya lo vea en la web.

No inventa nulls. Promo != organico. Umbrales fijos de cuenta temprana.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

def tz_madrid():
    try:
        return ZoneInfo("Europe/Madrid")
    except Exception:
        return dt.timezone(dt.timedelta(hours=2), name="UTC+2")


MADRID = tz_madrid()
ROOT = Path(__file__).resolve().parent
DATOS = ROOT / "datos"
OUT = DATOS / "diagnostico_semanal.json"
MIN_N = 50
TT_PROFILE_META = 0.01


def load(path: Path):
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def num(v):
    if v is None or v is False:
        return None
    if v == 0:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pct(n, d):
    if n is None or d in (None, 0):
        return None
    return n / d


def pick(obj, keys):
    if not obj:
        return None
    for k in keys:
        if obj.get(k) is not None:
            return obj[k]
    return None


def flat_insights(block):
    out = {}
    if not block:
        return out
    rows = block if isinstance(block, list) else block.get("data") or []
    for row in rows:
        name = row.get("name")
        if not name:
            continue
        if "total_value" in row and isinstance(row["total_value"], dict):
            out[name] = row["total_value"].get("value")
        else:
            vals = row.get("values") or []
            if vals:
                out[name] = vals[0].get("value")
    return out


def title_from_caption(c):
    if not c:
        return "Sin pie"
    line = next((s.strip() for s in c.split("\n") if s.strip()), c)
    line = " ".join(line.split())
    return line[:72] + ("…" if len(line) > 72 else "")


def parse_posts(ig):
    posts = []
    for p in ig.get("publicaciones") or []:
        ins = p.get("insights") or {}
        views = num(pick(ins, ["views", "plays"]))
        shares = num(ins.get("shares"))
        saved = num(pick(ins, ["saved", "saves"]))
        avg = num(ins.get("segundos_medios"))
        if avg is None and num(ins.get("ig_reels_avg_watch_time")) is not None:
            avg = num(ins.get("ig_reels_avg_watch_time")) / 1000.0
        posts.append(
            {
                "when": (p.get("timestamp") or "")[:10],
                "ts": p.get("timestamp"),
                "kind": p.get("media_product_type") or p.get("media_type") or "POST",
                "title": title_from_caption(p.get("caption")),
                "permalink": p.get("permalink"),
                "views": views,
                "shares": shares,
                "saved": saved,
                "avg": avg,
                "primer": p.get("primer_comentario_ok"),
            }
        )
    return posts


def in_days(iso, ref: dt.datetime, n: int):
    if not iso:
        return False
    raw = iso if len(iso) > 10 else iso + "T12:00:00"
    try:
        t = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=dt.timezone.utc)
    delta = (ref.astimezone(dt.timezone.utc) - t.astimezone(dt.timezone.utc)).total_seconds()
    return 0 <= delta <= n * 86400


def serie_delta(dias, field, days):
    rows = [d for d in dias if num(d.get(field)) is not None and d.get("fecha")]
    if not rows:
        return None, "null en serie"
    last = rows[-1]
    last_t = dt.date.fromisoformat(last["fecha"])
    cutoff = last_t - dt.timedelta(days=days)
    prev = None
    for d in reversed(rows):
        if dt.date.fromisoformat(d["fecha"]) <= cutoff:
            prev = d
            break
    if not prev or prev["fecha"] == last["fecha"]:
        return None, f"serie <{days}d"
    return num(last[field]) - num(prev[field]), None


def next_tuesday_10(now: dt.datetime) -> dt.datetime:
    # 0=lunes … 1=martes
    days = (1 - now.weekday()) % 7
    target = (now + dt.timedelta(days=days)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    if days == 0 and now >= target:
        target += dt.timedelta(days=7)
    return target


def diagnose(ig, posts, tt, ov, cola, serie, now):
    c = ig.get("cuenta") or {}
    ti = flat_insights((ig.get("cuenta_insights") or {}).get("totales_30d")) or flat_insights(
        ig.get("cuenta_insights")
    )
    clicks = num(ti.get("website_clicks"))
    pviews = num(ti.get("profile_views"))
    views = sum(p["views"] or 0 for p in posts)
    shares = sum(p["shares"] or 0 for p in posts)
    saves = sum(p["saved"] or 0 for p in posts)
    followers = num(pick(c, ["followers_count", "seguidores", "followers"]))
    low = sum(1 for p in posts if (p["views"] or 0) < 50)
    items = []
    pills = []

    if clicks == 0 and pviews and pviews > 0:
        items.append(
            {
                "sev": "bad",
                "t": "Cero clics al enlace",
                "d": f"{int(pviews)} visitas al perfil y website_clicks = 0. Story con sticker de enlace en cada publicación; CTA de bio en el segundo 1.",
            }
        )
        pills.append(["Conversión", "bad"])
    elif clicks and clicks > 0:
        pills.append(["Conversión", "ok"])
    else:
        pills.append(["Conversión", "ns"])

    tt_views = num((ov or {}).get("totals", {}).get("views"))
    tt_prof = num((ov or {}).get("totals", {}).get("profile_views"))
    tt_ratio = pct(tt_prof, tt_views)
    if tt_ratio is not None and tt_ratio < TT_PROFILE_META:
        items.append(
            {
                "sev": "bad",
                "t": "TikTok no lleva al perfil",
                "d": f"Views→perfil {100*tt_ratio:.2f}% (meta ≥1%). El núcleo orgánico no pide el follow; las promo inflan views.",
            }
        )
        pills.append(["Views→perfil TT", "bad"])
    elif tt_ratio is not None:
        pills.append(["Views→perfil TT", "ok"])
    else:
        pills.append(["Views→perfil TT", "ns"])

    if posts and low / len(posts) >= 0.5:
        items.append(
            {
                "sev": "bad",
                "t": "El algoritmo no empuja en Instagram",
                "d": f"{low} de {len(posts)} piezas bajo 50 views. Cambia el gancho (texto en pantalla, sin intro); no toques el producto.",
            }
        )
        pills.append(["Distribución IG", "bad"])
    else:
        pills.append(["Distribución IG", "warn" if posts else "ns"])

    save_acc = pct(saves, views)
    if save_acc is not None and save_acc < 0.004:
        items.append(
            {
                "sev": "warn",
                "t": "No hay bucle viral",
                "d": f"{int(saves)} guardados y {int(shares)} compartidos ({100*save_acc:.2f}% save). En belleza manda el save; se comparte la pega, no el elogio.",
            }
        )
        pills.append(["Viral", "bad"])
    else:
        pills.append(["Viral", "ok" if save_acc and save_acc >= 0.01 else "warn"])

    videos = (tt or {}).get("videos") or []
    nucleo = [v for v in videos if v.get("etiqueta") == "nucleo"]
    promos = [v for v in videos if v.get("etiqueta") == "promo" or v.get("promo")]
    if promos and nucleo:
        med = ((tt or {}).get("summary") or {}).get("median_nucleo_organic")
        items.append(
            {
                "sev": "warn",
                "t": "No leas el pico de TikTok como orgánico",
                "d": f"{len(promos)} piezas promo y {len(nucleo)} de núcleo. Mediana orgánica {med} views. Las decisiones de formato salen del núcleo.",
            }
        )

    sin_prim = sum(1 for p in posts if p.get("primer") is False)
    if sin_prim:
        items.append(
            {
                "sev": "warn",
                "t": "Falta el primer comentario",
                "d": f"{sin_prim} de {len(posts)} pubs con primer_comentario_ok = false. Es regla de publish Graph, no de métrica.",
            }
        )

    views_day_err = (ig.get("metric_errores") or {}).get("views_day")
    if views_day_err:
        items.append(
            {
                "sev": "ns",
                "t": "Meta no manda views/day",
                "d": views_day_err.get("message")
                or "serie_diaria solo trae reach. No inventar views diarios.",
            }
        )

    if not items:
        items.append(
            {
                "sev": "ok",
                "t": "Sin alertas duras",
                "d": "Revisa top piezas y cadencia. Sigue sin inventar números que no estén en el JSON.",
            }
        )

    best_share = sorted(posts, key=lambda p: p["shares"] or 0, reverse=True)
    best_share = best_share[0] if best_share else None
    acciones = [
        {
            "k": "01 · Clics",
            "t": "Cada Reel/carrusel sale con Story de enlace. Sin eso, website_clicks se queda en 0.",
        }
    ]
    if best_share and (best_share.get("shares") or 0) > 0:
        acciones.append(
            {
                "k": "02 · Formato",
                "t": f"Repite el gancho de «{best_share['title']}» ({int(best_share['shares'])} shares). La pega va en el segundo 1–3.",
            }
        )
    else:
        acciones.append(
            {
                "k": "02 · Gancho",
                "t": "Tarjeta de primer segundo con la pega, no con la marca. Watch < 5 s = el vídeo no existe para el algoritmo.",
            }
        )
    acciones.append(
        {
            "k": "03 · Cadencia TT",
            "t": "Objetivo ~2/día. El núcleo orgánico es la referencia; promo y outlier se etiquetan aparte.",
        }
    )
    if sin_prim:
        acciones[0] = {
            "k": "01 · Clics + 1er comentario",
            "t": "Story de enlace en cada pub Y primer comentario obligatorio en Graph. Hoy el 1er comentario falla en todas.",
        }

    dias = (serie or {}).get("dias") or []
    d7, n7 = serie_delta(dias, "followers_count", 7)
    d30, n30 = serie_delta(dias, "followers_count", 30)
    tt_gen = (tt or {}).get("generado")
    try:
        tt_ref = (
            dt.datetime.fromisoformat(str(tt_gen).replace("Z", "+00:00"))
            if tt_gen
            else now
        )
    except ValueError:
        tt_ref = now
    if tt_ref.tzinfo is None:
        tt_ref = tt_ref.replace(tzinfo=dt.timezone.utc)
    tt_week = [v for v in videos if in_days(v.get("fecha"), tt_ref, 7)]
    ig_ref = now
    ig_week = [p for p in posts if in_days(p.get("ts") or p.get("when"), ig_ref, 7)]
    cars = [
        p
        for p in ig_week
        if "CAROUSEL" in str(p["kind"]).upper() or p["kind"] == "CAROUSEL_ALBUM"
    ]
    reels = [
        p
        for p in ig_week
        if "REEL" in str(p["kind"]).upper() or p["kind"] == "VIDEO"
    ]
    conteos = (cola or {}).get("conteos") or {}
    best = sorted(posts, key=lambda p: p["views"] or 0, reverse=True)
    best = best[0] if best else None
    worst = items[0]
    iso_w = now.isocalendar()
    proxima = next_tuesday_10(now)
    return {
        "generado": now.isoformat(),
        "semana": f"{iso_w.year}-W{iso_w.week:02d}",
        "revision": {
            "cuando": "martes 10:00 Europe/Madrid",
            "proxima": proxima.isoformat(),
            "siguiente_ciclo": (proxima + dt.timedelta(days=7)).isoformat(),
        },
        "headline": worst["t"],
        "lead": worst["d"],
        "sev": worst["sev"],
        "pills": pills,
        "problemas": items,
        "acciones": acciones,
        "kpis": {
            "followers_ig": followers,
            "delta_7d": d7,
            "delta_7d_nota": n7,
            "delta_30d": d30,
            "delta_30d_nota": n30,
            "website_clicks": clicks,
            "profile_views": pviews,
            "views_ig_piezas": views,
            "tt_views": tt_views,
            "tt_profile_views": tt_prof,
            "tt_views_perfil": tt_ratio,
            "ig_bajo_50": f"{low}/{len(posts)}" if posts else None,
            "primer_comentario_ok": f"{sum(1 for p in posts if p.get('primer') is True)}/{len(posts)}"
            if posts
            else None,
            "cola_pendientes": conteos.get("pendientes"),
            "cola_publicados": conteos.get("publicados"),
            "tt_cadencia_7d": f"{len(tt_week)}/14",
            "ig_carruseles_7d": len(cars),
            "ig_reels_7d": len(reels),
            "mejor_reel": {
                "title": best["title"],
                "views": best["views"],
                "avg": best["avg"],
            }
            if best
            else None,
        },
        "fuentes": {
            "estadisticas": bool(ig),
            "serie_cuenta": bool(serie),
            "cola": bool(cola),
            "tiktok": bool(tt),
            "overview": bool(ov),
            "tt_generado": (tt or {}).get("generado"),
            "tt_content_import": (tt or {}).get("content_import"),
        },
        "regla": "no inventar nulls; umbrales fijos; promo ≠ orgánico; manda API/CSV no la parrilla",
    }


def main():
    # Opcional: REQUIRE_MADRID_HOUR="9" o "9-12" (rango para retrasos de Actions).
    # El workflow ya no lo envía: el cron martes UTC basta.
    hour = os.environ.get("REQUIRE_MADRID_HOUR", "").strip()
    now = dt.datetime.now(MADRID)
    if hour:
        if now.weekday() != 1:
            print(f"skip: hoy no es martes ({now.isoformat()})")
            return 0
        if "-" in hour:
            lo, hi = hour.split("-", 1)
            allowed = range(int(lo), int(hi) + 1)
        else:
            allowed = (int(hour),)
        if now.hour not in allowed:
            print(f"skip: ahora {now.isoformat()} fuera de horas Madrid {hour}")
            return 0

    ig = load(DATOS / "estadisticas.json")
    if not ig:
        print("falta datos/estadisticas.json", file=sys.stderr)
        return 1
    serie = load(DATOS / "serie_cuenta.json")
    cola = load(DATOS / "cola.json")
    tt = load(DATOS / "tiktok.json") or load(DATOS / "tiktok" / "tiktok.json")
    ov = load(DATOS / "tiktok" / "tiktok_overview_latest.json")
    posts = parse_posts(ig)
    briefing = diagnose(ig, posts, tt, ov, cola, serie, now)
    DATOS.mkdir(exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(briefing, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("escrito", OUT)
    print(briefing["semana"], briefing["headline"])
    print("proxima revision", briefing["revision"]["proxima"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
