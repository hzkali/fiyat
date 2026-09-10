import re
import time
import logging
from datetime import date

import requests
from bs4 import BeautifulSoup

import database

BASE_URL = "https://fonksiyonel.tr"
PRODUCTS_API = f"{BASE_URL}/products.json"
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


if __name__ == "__main__":
    count = run_scrape()
    print(f"\n✓ {count} ürün kaydedildi.")
