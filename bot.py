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


def scraper_get(url):
    """Hace una petición a través de ScraperAPI para evitar bloqueos de IP."""
    resp = requests.get(
        "https://api.scraperapi.com",
        params={"api_key": SCRAPER_API_KEY, "url": url},
        timeout=70,
    )
    resp.raise_for_status()
    return resp.json()


def buscar_ofertas():
    """Busca productos con descuento en ML México vía ScraperAPI."""
    token = obtener_token_ml()

    busquedas = [
        f"https://api.mercadolibre.com/sites/MLM/search?q=electronica&limit=20&sort=best_match&Authorization=Bearer%20{token}",
        f"https://api.mercadolibre.com/sites/MLM/search?q=celular&limit=20&sort=best_match&Authorization=Bearer%20{token}",
        f"https://api.mercadolibre.com/sites/MLM/search?q=hogar&limit=20&sort=best_match&Authorization=Bearer%20{token}",
        f"https://api.mercadolibre.com/sites/MLM/search?q=ropa&limit=20&sort=best_match&Authorization=Bearer%20{token}",
        f"https://api.mercadolibre.com/sites/MLM/search?q=deporte&limit=20&sort=best_match&Authorization=Bearer%20{token}",
    ]

    # ScraperAPI no soporta headers personalizados en plan gratuito,
    # así que usamos el endpoint público sin auth (suficiente para búsqueda básica)
    busquedas_publicas = [
        "https://api.mercadolibre.com/sites/MLM/search?q=electronica&limit=20&sort=best_match",
        "https://api.mercadolibre.com/sites/MLM/search?q=celular+smartphone&limit=20&sort=best_match",
        "https://api.mercadolibre.com/sites/MLM/search?q=hogar+cocina&limit=20&sort=best_match",
        "https://api.mercadolibre.com/sites/MLM/search?q=ropa+moda&limit=20&sort=best_match",
        "https://api.mercadolibre.com/sites/MLM/search?q=deporte+fitness&limit=20&sort=best_match",
    ]

    productos = []
    for url in busquedas_publicas:
        try:
            data = scraper_get(url)
            resultados = data.get("results", [])
            productos.extend(resultados)
            log.info(f"  ✓ {len(resultados)} productos de {url.split('q=')[1].split('&')[0]}")
        except Exception as e:
            log.warning(f"Error buscando: {e}")

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
