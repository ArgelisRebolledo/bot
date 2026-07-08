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


def buscar_ofertas():
    """Busca productos con descuento en ML México."""
    token = obtener_token_ml()
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (compatible; OfertasBot/1.0)",
        "Accept": "application/json",
    }

    # Endpoints alternativos para encontrar ofertas
    endpoints = [
        # Productos con tag de buen precio
        ("https://api.mercadolibre.com/sites/MLM/search",
         {"tag": "good_price", "sort": "best_match", "limit": 50}),
        # Destacados del día
        ("https://api.mercadolibre.com/sites/MLM/search",
         {"tag": "best_seller", "sort": "best_match", "limit": 50}),
    ]

    productos = []
    for url, params in endpoints:
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            productos.extend(resp.json().get("results", []))
            log.info(f"  ✓ {len(resp.json().get('results', []))} productos de {params.get('tag', url)}")
        except Exception as e:
            log.warning(f"Error en endpoint {params}: {e}")

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
