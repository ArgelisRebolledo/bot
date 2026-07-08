import os
import re
import time
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

# ─── Configuración ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@ofertasMexiCanal")
ML_APP_ID        = os.environ["ML_APP_ID"]
ML_SECRET        = os.environ["ML_SECRET"]
ML_AFFILIATE_TAG = os.environ.get("ML_AFFILIATE_TAG", "heycharalco")

MIN_DISCOUNT     = int(os.environ.get("MIN_DISCOUNT", "20"))
POSTS_PER_RUN    = int(os.environ.get("POSTS_PER_RUN", "5"))
INTERVAL_HOURS   = int(os.environ.get("INTERVAL_HOURS", "6"))

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

publicados = set()  # evitar duplicados en la misma sesión

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OfertasBot/1.0)"}


# ─── Fuentes de ofertas ───────────────────────────────────────────────────────

def desde_promodescuentos():
    """Lee el RSS de promodescuentos.com y filtra deals de MercadoLibre."""
    urls = [
        "https://www.promodescuentos.com/rss/deals",
        "https://www.promodescuentos.com/rss/ofertas",
    ]
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            items = root.findall(".//item")
            log.info(f"  promodescuentos: {len(items)} items en feed")

            # Debug: mostrar primeros 3 items
            for i, item in enumerate(items[:3]):
                t = item.findtext("title", "")
                l = item.findtext("link", "")
                d = (item.findtext("description", "") or "")[:200]
                log.info(f"  [item {i}] titulo={t!r} link={l!r} desc={d!r}")

            productos = []
            for item in items:
                titulo = item.findtext("title", "")
                link   = item.findtext("link", "")
                desc   = item.findtext("description", "") or ""
                todo   = titulo + " " + link + " " + desc

                if "mercadolibre" not in todo.lower():
                    continue

                # Extraer % descuento
                pct = re.search(r'(\d+)\s*%\s*(?:off|de\s*desc|desc)', todo, re.I)
                descuento = int(pct.group(1)) if pct else 0

                # Extraer precio actual
                precio_m = re.search(r'\$\s?([\d,]+(?:\.\d+)?)', todo)
                precio   = float(precio_m.group(1).replace(",", "")) if precio_m else 0

                # Encontrar link directo de ML
                ml_url_m = re.search(r'https?://[^\s"<>]*mercadolibre\.com\.mx[^\s"<>]*', todo)
                ml_url   = ml_url_m.group(0).rstrip(".,)") if ml_url_m else link

                # ID único
                mlm_id = re.search(r'MLM-?\d+', ml_url)
                item_id = mlm_id.group(0) if mlm_id else ml_url[-30:]

                if descuento >= MIN_DISCOUNT:
                    original = precio / (1 - descuento / 100) if descuento > 0 and precio > 0 else 0
                    productos.append({
                        "id":             item_id,
                        "title":          titulo,
                        "price":          precio,
                        "original_price": original,
                        "permalink":      ml_url,
                        "currency_id":    "MXN",
                    })

            if productos:
                log.info(f"  ✓ {len(productos)} deals de ML con ≥{MIN_DISCOUNT}% descuento")
                return productos

        except Exception as e:
            log.warning(f"  Error en {url}: {e}")

    return []


def desde_ml_deals():
    """Intento secundario: página de cupones/deals de ML (sin JS)."""
    try:
        resp = requests.get(
            "https://www.mercadolibre.com.mx/ofertas",
            headers={**HEADERS, "Accept-Language": "es-MX"},
            timeout=15,
        )
        resp.raise_for_status()
        html = resp.text

        # Buscar JSON embebido con datos de productos
        match = re.search(r'"items"\s*:\s*(\[.*?\])\s*[,}]', html, re.S)
        if not match:
            log.info("  ML deals: no se encontraron productos en HTML")
            return []

        import json
        items = json.loads(match.group(1))
        productos = []
        for it in items:
            precio    = it.get("price") or it.get("sale_price", 0)
            original  = it.get("original_price", 0)
            permalink = it.get("permalink") or it.get("url", "")
            titulo    = it.get("title", "")
            if precio and permalink and titulo:
                productos.append({
                    "id":             it.get("id", permalink[-20:]),
                    "title":          titulo,
                    "price":          float(precio),
                    "original_price": float(original),
                    "permalink":      permalink,
                    "currency_id":    "MXN",
                })
        log.info(f"  ML deals directos: {len(productos)} productos")
        return productos

    except Exception as e:
        log.warning(f"  Error ML deals: {e}")
        return []


def buscar_ofertas():
    productos = desde_promodescuentos()
    if not productos:
        productos = desde_ml_deals()
    return productos


# ─── Links y formato ──────────────────────────────────────────────────────────

def link_afiliado(url):
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}matt_tool={ML_AFFILIATE_TAG}&matt_medium=affiliate&matt_content=tgbot"


def calcular_descuento(p):
    precio    = p.get("price", 0)
    original  = p.get("original_price") or 0
    if original > precio > 0:
        return round((1 - precio / original) * 100)
    return 0


def formatear_mensaje(p):
    titulo    = p["title"]
    precio    = p["price"]
    original  = p.get("original_price") or precio
    descuento = calcular_descuento(p)
    url       = link_afiliado(p["permalink"])

    lineas = [f"🔥 *{titulo}*\n"]
    if descuento > 0:
        lineas.append(f"~~${original:,.0f}~~ → *${precio:,.0f} MXN*")
        lineas.append(f"✅ *{descuento}% de descuento*\n")
    else:
        lineas.append(f"*${precio:,.0f} MXN*\n")
    lineas.append(f"👉 [Ver oferta en MercadoLibre]({url})")
    return "\n".join(lineas)


# ─── Telegram ─────────────────────────────────────────────────────────────────

def enviar_telegram(texto):
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id":   TELEGRAM_CHANNEL,
            "text":      texto,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        },
        timeout=10,
    )
    resp.raise_for_status()


# ─── Principal ────────────────────────────────────────────────────────────────

def correr():
    log.info(f"▶ Buscando ofertas — {datetime.now().strftime('%H:%M %d/%m/%Y')}")
    try:
        productos = buscar_ofertas()
        log.info(f"  {len(productos)} productos encontrados")

        ofertas = [
            p for p in productos
            if calcular_descuento(p) >= MIN_DISCOUNT and p["id"] not in publicados
        ]
        ofertas.sort(key=calcular_descuento, reverse=True)
        log.info(f"  {len(ofertas)} nuevas con ≥{MIN_DISCOUNT}% descuento")

        publicadas = 0
        for oferta in ofertas[:POSTS_PER_RUN]:
            try:
                enviar_telegram(formatear_mensaje(oferta))
                publicados.add(oferta["id"])
                publicadas += 1
                log.info(f"  ✓ {oferta['title'][:60]}")
                time.sleep(3)
            except Exception as e:
                log.error(f"  ✗ Error: {e}")

        log.info(f"✅ {publicadas} ofertas publicadas")

    except Exception as e:
        log.error(f"❌ Error general: {e}")


def main():
    log.info("🤖 Bot iniciado")
    correr()
    while True:
        log.info(f"💤 Próxima corrida en {INTERVAL_HOURS}h")
        time.sleep(INTERVAL_HOURS * 3600)
        correr()


if __name__ == "__main__":
    main()
