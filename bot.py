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
ML_AFFILIATE_TAG  = os.environ.get("ML_AFFILIATE_TAG", "heycharalco")
SCRAPER_API_KEY   = os.environ.get("SCRAPER_API_KEY", "")

MIN_DISCOUNT     = int(os.environ.get("MIN_DISCOUNT", "20"))
POSTS_PER_RUN    = int(os.environ.get("POSTS_PER_RUN", "5"))
INTERVAL_HOURS   = int(os.environ.get("INTERVAL_HOURS", "6"))

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

publicados = set()  # evitar duplicados en la misma sesión

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OfertasBot/1.0)"}


# ─── Fuentes de ofertas ───────────────────────────────────────────────────────

def buscar_url_ml(titulo):
    """Busca el producto en ML usando el título y retorna el primer resultado."""
    try:
        # Limpiar título: quitar "Mercado Libre: " del inicio
        query = re.sub(r'^mercado libre\s*:\s*', '', titulo, flags=re.I).strip()
        query_encoded = requests.utils.quote(query)
        search_url = f"https://listado.mercadolibre.com.mx/{query_encoded}"

        resp = requests.get(
            "https://api.scraperapi.com",
            params={
                "api_key": SCRAPER_API_KEY,
                "url":     search_url,
                "render":  "true",
                "wait":    "3000",
            },
            timeout=70,
        )
        html = resp.text

        # Buscar el primer link de producto en resultados de ML
        match = re.search(
            r'href=["\'](https?://(?:articulo|www)\.mercadolibre\.com\.mx/[^"\']{20,})["\']',
            html
        )
        if not match:
            match = re.search(
                r'"permalink"\s*:\s*"(https?://[^"]*mercadolibre\.com\.mx/[^"]{20,})"',
                html
            )
        if not match:
            match = re.search(
                r'(https?://articulo\.mercadolibre\.com\.mx/MLM[^"\s\'<>]{10,})',
                html
            )
        if match:
            url = match.group(1).strip().rstrip(".,)")
            log.info(f"  ✓ ML URL encontrada: {url[:80]}")
            return url

        log.warning(f"  Sin resultados ML para: {query[:50]}")
    except Exception as e:
        log.warning(f"  Error buscando en ML: {e}")
    return None


def desde_promodescuentos():
    """Busca deals de MercadoLibre en promodescuentos.com."""
    # Intentar página de tienda ML o RSS general filtrando por ML en título
    fuentes = [
        "https://www.promodescuentos.com/ofertas/tiendas/mercado-libre",
        "https://www.promodescuentos.com/rss/ofertas",
    ]

    # Primero intentar RSS y filtrar por "Mercado Libre" en título
    try:
        resp = requests.get(fuentes[1], headers=HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        log.info(f"  RSS: {len(items)} items totales")

        productos = []
        for item in items:
            titulo = item.findtext("title", "")
            pds_link = item.findtext("link", "")
            desc   = item.findtext("description", "") or ""

            # Filtrar por tienda ML
            if "mercado libre" not in titulo.lower() and "mercadolibre" not in desc.lower():
                continue

            log.info(f"  Deal ML encontrado: {titulo[:60]}")

            # Extraer precio
            precio_m  = re.search(r'\$\s?([\d,]+(?:\.\d+)?)', desc + " " + titulo)
            precio    = float(precio_m.group(1).replace(",", "")) if precio_m else 0

            # Extraer descuento
            pct_m     = re.search(r'(\d+)\s*%', desc + " " + titulo)
            descuento = int(pct_m.group(1)) if pct_m else 0

            # Buscar URL real del producto en ML
            ml_url = buscar_url_ml(titulo) or pds_link
            mlm_id = re.search(r'MLM-?\d+', ml_url)
            item_id = mlm_id.group(0) if mlm_id else pds_link[-20:]

            original = precio / (1 - descuento / 100) if descuento > 0 and precio > 0 else 0
            productos.append({
                "id":             item_id,
                "title":          titulo,
                "price":          precio,
                "original_price": original,
                "permalink":      ml_url,
                "currency_id":    "MXN",
            })

        log.info(f"  ✓ {len(productos)} deals de ML encontrados")
        return productos

    except Exception as e:
        log.warning(f"  Error promodescuentos: {e}")
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
