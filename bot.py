import os
import time
import logging
import requests
from datetime import datetime

# ─── Configuración (se leen de las variables de entorno en Railway) ───────────
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHANNEL  = os.environ.get("TELEGRAM_CHANNEL", "@ofertasMexiCanal")
ML_APP_ID         = os.environ["ML_APP_ID"]
ML_SECRET         = os.environ["ML_SECRET"]
ML_AFFILIATE_TAG  = os.environ.get("ML_AFFILIATE_TAG", "heycharalco")
SCRAPER_API_KEY   = os.environ["SCRAPER_API_KEY"]

MIN_DISCOUNT      = int(os.environ.get("MIN_DISCOUNT", "25"))   # % mínimo de descuento
POSTS_PER_RUN     = int(os.environ.get("POSTS_PER_RUN", "5"))   # cuántas ofertas publicar por corrida
INTERVAL_HOURS    = int(os.environ.get("INTERVAL_HOURS", "6"))  # cada cuántas horas correr

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s"
)
log = logging.getLogger(__name__)

# Evitar publicar el mismo producto dos veces en la misma sesión
publicados = set()


# ─── MercadoLibre ─────────────────────────────────────────────────────────────

def obtener_token_ml():
    """Autenticación con ML usando Client Credentials."""
    resp = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     ML_APP_ID,
            "client_secret": ML_SECRET,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def scraper_get_html(url):
    """Obtiene HTML de una página a través de ScraperAPI con render JS."""
    resp = requests.get(
        "https://api.scraperapi.com",
        params={
            "api_key": SCRAPER_API_KEY,
            "url": url,
            "render": "true",
            "country_code": "mx",
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text


def buscar_ofertas():
    """Extrae productos con descuento de la página de ofertas de ML."""
    import re
    import json

    url = "https://www.mercadolibre.com.mx/ofertas"
    log.info("  Obteniendo página de ofertas de ML...")

    try:
        html = scraper_get_html(url)
    except Exception as e:
        log.error(f"Error obteniendo página: {e}")
        return []

    # ML embebe datos de productos como JSON en el HTML
    productos = []
    patrones = [
        r'"price":\s*(\d+\.?\d*)',
        r'"original_price":\s*(\d+\.?\d*)',
    ]

    # Buscar bloques JSON de productos embebidos
    bloques = re.findall(r'\{[^{}]*"title"[^{}]*"price"[^{}]*"permalink"[^{}]*\}', html)
    for bloque in bloques:
        try:
            p = json.loads(bloque)
            if p.get("title") and p.get("price") and p.get("permalink"):
                productos.append(p)
        except Exception:
            pass

    # Si no encontró con el método anterior, buscar estructura alternativa
    if not productos:
        matches = re.findall(
            r'"title":"([^"]+)"[^}]*"price":(\d+\.?\d*)[^}]*"original_price":(\d+\.?\d*)[^}]*"permalink":"([^"]+)"',
            html
        )
        for title, price, original, permalink in matches:
            productos.append({
                "title": title,
                "price": float(price),
                "original_price": float(original),
                "permalink": permalink,
                "id": permalink.split("-_JM")[0].split("/")[-1],
                "currency_id": "MXN",
            })

    log.info(f"  {len(productos)} productos encontrados en página de ofertas")
    return productos


def link_afiliado(url_producto):
    """Agrega parámetros de seguimiento de afiliado al URL."""
    sep = "&" if "?" in url_producto else "?"
    return (
        f"{url_producto}{sep}"
        f"matt_tool={ML_AFFILIATE_TAG}"
        f"&matt_medium=affiliate"
        f"&matt_content=telegrambot"
    )


def calcular_descuento(producto):
    precio_actual   = producto.get("price", 0)
    precio_original = producto.get("original_price") or 0
    if precio_original > precio_actual > 0:
        return round((1 - precio_actual / precio_original) * 100)
    return 0


def formatear_mensaje(producto):
    titulo   = producto["title"]
    precio   = producto["price"]
    original = producto.get("original_price") or precio
    descuento = calcular_descuento(producto)
    moneda   = producto.get("currency_id", "MXN")
    url      = link_afiliado(producto["permalink"])

    lineas = [f"🔥 *{titulo}*\n"]

    if descuento > 0:
        lineas.append(f"~~${original:,.0f}~~ → *${precio:,.0f} {moneda}*")
        lineas.append(f"✅ *{descuento}% de descuento*\n")
    else:
        lineas.append(f"*${precio:,.0f} {moneda}*\n")

    lineas.append(f"👉 [Ver oferta en MercadoLibre]({url})")
    return "\n".join(lineas)


# ─── Telegram ─────────────────────────────────────────────────────────────────

def enviar_telegram(texto):
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id":                  TELEGRAM_CHANNEL,
            "text":                     texto,
            "parse_mode":               "Markdown",
            "disable_web_page_preview": False,
        },
        timeout=10,
    )
    resp.raise_for_status()


# ─── Lógica principal ─────────────────────────────────────────────────────────

def correr():
    log.info(f"▶ Iniciando búsqueda de ofertas — {datetime.now().strftime('%H:%M %d/%m/%Y')}")

    try:
        productos = buscar_ofertas()

        log.info(f"  {len(productos)} productos encontrados en total")

        # Filtrar: descuento mínimo y no publicados antes
        ofertas = [
            p for p in productos
            if calcular_descuento(p) >= MIN_DISCOUNT
            and p["id"] not in publicados
        ]

        # Ordenar por mayor descuento primero
        ofertas.sort(key=calcular_descuento, reverse=True)
        log.info(f"  {len(ofertas)} ofertas con ≥{MIN_DISCOUNT}% de descuento")

        publicadas = 0
        for oferta in ofertas[:POSTS_PER_RUN]:
            try:
                mensaje = formatear_mensaje(oferta)
                enviar_telegram(mensaje)
                publicados.add(oferta["id"])
                publicadas += 1
                log.info(f"  ✓ Publicado: {oferta['title'][:50]}")
                time.sleep(3)  # pausa para no saturar Telegram
            except Exception as e:
                log.error(f"  ✗ Error publicando {oferta['id']}: {e}")

        log.info(f"✅ {publicadas} ofertas publicadas")

    except Exception as e:
        log.error(f"❌ Error general: {e}")


def main():
    log.info("🤖 Bot de ofertas iniciado")
    correr()

    while True:
        log.info(f"💤 Esperando {INTERVAL_HOURS} horas...")
        time.sleep(INTERVAL_HOURS * 3600)
        correr()


if __name__ == "__main__":
    main()
