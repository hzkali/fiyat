import re
import time
import logging
from datetime import date

import requests
from bs4 import BeautifulSoup

import database

BASE_URL = "https://fonksiyonel.tr"
PRODUCTS_API = f"{BASE_URL}/products.json"

VITANICA_BASE_URL = "https://www.vitanica.tr"
VITANICA_API = f"{VITANICA_BASE_URL}/wp-json/wc/store/v1/products"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _clean_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace from body_html."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def _fetch_page(page: int, limit: int = 250) -> list:
    params = {"limit": limit, "page": page}
    try:
        r = requests.get(PRODUCTS_API, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json().get("products", [])
    except Exception as exc:
        log.error("Sayfa %d çekilemedi: %s", page, exc)
        return []


def fetch_all_products() -> list:
    """Fetch every product from the Shopify API (all pages)."""
    all_products = []
    page = 1
    while True:
        log.info("Sayfa %d çekiliyor…", page)
        products = _fetch_page(page)
        if not products:
            break
        all_products.extend(products)
        log.info("  %d ürün alındı (toplam: %d)", len(products), len(all_products))
        if len(products) < 250:
            break
        page += 1
        time.sleep(0.5)
    return all_products


def _parse_product(p: dict) -> dict:
    """Extract the fields we care about from a raw Shopify product dict."""
    variant = p.get("variants", [{}])[0]
    images = p.get("images", [])
    image_url = ""
    if images:
        src = images[0].get("src", "")
        # ensure https
        image_url = src if src.startswith("http") else f"https:{src}"

    price_str = variant.get("price", "0") or "0"
    compare_str = variant.get("compare_at_price") or None

    return {
        "shopify_id": str(p["id"]),
        "handle": p.get("handle", ""),
        "title": p.get("title", ""),
        "vendor": p.get("vendor", ""),
        "product_type": p.get("product_type", ""),
        "image_url": image_url,
        "url": f"{BASE_URL}/products/{p.get('handle', '')}",
        "price": float(price_str),
        "compare_price": float(compare_str) if compare_str else None,
        "available": variant.get("available", False),
        "description": _clean_html(p.get("body_html", "")),
    }


def run_scrape():
    """Main entry point: fetch → parse → persist."""
    log.info("Scraping başlatıldı")
    database.init_db()
    raw = fetch_all_products()
    if not raw:
        log.warning("Hiç ürün bulunamadı, işlem sonlandırılıyor")
        return 0

    snap_date = date.today()
    saved = 0
    for p in raw:
        try:
            parsed = _parse_product(p)
            product_id = database.upsert_product(
                parsed["shopify_id"],
                parsed["handle"],
                parsed["title"],
                parsed["vendor"],
                parsed["product_type"],
                parsed["image_url"],
                parsed["url"],
            )
            database.upsert_snapshot(
                product_id,
                snap_date,
                parsed["price"],
                parsed["compare_price"],
                parsed["available"],
                parsed["description"],
            )
            saved += 1
        except Exception as exc:
            log.error("Ürün kaydedilemedi (%s): %s", p.get("handle"), exc)

    log.info("Scraping tamamlandı: %d ürün kaydedildi", saved)
    return saved


def _fetch_vitanica_page(page: int, per_page: int = 100) -> list:
    params = {"per_page": per_page, "page": page}
    try:
        r = requests.get(VITANICA_API, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("Vitanica sayfa %d çekilemedi: %s", page, exc)
        return []


def fetch_all_vitanica_products() -> list:
    """Fetch every product from the Vitanica WooCommerce Store API (all pages)."""
    all_products = []
    page = 1
    while True:
        log.info("Vitanica sayfa %d çekiliyor…", page)
        products = _fetch_vitanica_page(page)
        if not products:
            break
        all_products.extend(products)
        log.info("  %d ürün alındı (toplam: %d)", len(products), len(all_products))
        if len(products) < 100:
            break
        page += 1
        time.sleep(0.5)
    return all_products


def _parse_vitanica_product(p: dict) -> dict:
    """Extract the fields we care about from a raw WooCommerce Store API product dict."""
    images = p.get("images", [])
    image_url = images[0].get("src", "") if images else ""

    prices = p.get("prices", {})
    divisor = 10 ** prices.get("currency_minor_unit", 2)

    def _to_amount(v):
        return float(v) / divisor if v not in (None, "") else None

    price = _to_amount(prices.get("price")) or 0.0
    regular_price = _to_amount(prices.get("regular_price"))
    compare_price = regular_price if (p.get("on_sale") and regular_price and regular_price > price) else None

    brands = p.get("brands") or []
    categories = p.get("categories") or []

    return {
        "shopify_id": f"vitanica-{p['id']}",
        "handle": p.get("slug", ""),
        "title": p.get("name", ""),
        "vendor": brands[0]["name"] if brands else "",
        "product_type": categories[0]["name"] if categories else "",
        "image_url": image_url,
        "url": p.get("permalink") or f"{VITANICA_BASE_URL}/?p={p.get('id')}",
        "price": price,
        "compare_price": compare_price,
        "available": bool(p.get("is_in_stock", False)),
        "description": _clean_html(p.get("short_description") or p.get("description") or ""),
    }


def run_scrape_vitanica():
    """Vitanica.tr için: fetch → parse → persist."""
    log.info("Vitanica scraping başlatıldı")
    database.init_db()
    raw = fetch_all_vitanica_products()
    if not raw:
        log.warning("Vitanica: hiç ürün bulunamadı, işlem sonlandırılıyor")
        return 0

    snap_date = date.today()
    saved = 0
    for p in raw:
        try:
            parsed = _parse_vitanica_product(p)
            product_id = database.upsert_product(
                parsed["shopify_id"],
                parsed["handle"],
                parsed["title"],
                parsed["vendor"],
                parsed["product_type"],
                parsed["image_url"],
                parsed["url"],
                site="vitanica",
            )
            database.upsert_snapshot(
                product_id,
                snap_date,
                parsed["price"],
                parsed["compare_price"],
                parsed["available"],
                parsed["description"],
            )
            saved += 1
        except Exception as exc:
            log.error("Vitanica ürün kaydedilemedi (%s): %s", p.get("slug"), exc)

    log.info("Vitanica scraping tamamlandı: %d ürün kaydedildi", saved)
    return saved


def run_scrape_all():
    """fonksiyonel.tr ve vitanica.tr'yi sırayla scrape eder."""
    return run_scrape() + run_scrape_vitanica()


if __name__ == "__main__":
    count = run_scrape_all()
    print(f"\n✓ {count} ürün kaydedildi.")
