import threading
import logging
from datetime import date
from flask import Flask, render_template, request, redirect, url_for, jsonify
import database
import scraper
import scheduler as sched

log = logging.getLogger(__name__)
app = Flask(__name__)

# Arka planda günlük scheduler'ı başlat (sadece main process'te)
import os
if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
    # production veya non-reloader ortamda doğrudan başlat
    _sched_thread = threading.Thread(target=sched.schedule_loop, daemon=True, name="DailyScheduler")
    _sched_thread.start()


def _apply_filters(products: list, filter_: str, type_: str, sort: str) -> list:
    if filter_ == "available":
        products = [p for p in products if p["available"]]
    elif filter_ == "discount":
        products = [p for p in products if p.get("compare_price") and p["compare_price"] > p["price"]]

    if type_:
        products = [p for p in products if p.get("product_type") == type_]

    if sort == "price_asc":
        products = sorted(products, key=lambda x: x["price"] or 0)
    elif sort == "price_desc":
        products = sorted(products, key=lambda x: x["price"] or 0, reverse=True)
    elif sort == "name":
        products = sorted(products, key=lambda x: x["title"])
    elif sort == "discount":
        def discount_rate(p):
            if p.get("compare_price") and p["compare_price"] > 0:
                return (p["compare_price"] - p["price"]) / p["compare_price"]
            return 0
        products = sorted(products, key=discount_rate, reverse=True)

    return products


@app.route("/")
def index():
    database.init_db()
    snap_date = request.args.get("date") or str(date.today())
    filter_ = request.args.get("filter", "")
    type_ = request.args.get("type", "")
    sort = request.args.get("sort", "")

    products = database.get_daily_products(snap_date)
    products = _apply_filters(products, filter_, type_, sort)

    categories = sorted(set(
        p["product_type"] for p in database.get_daily_products(snap_date)
        if p.get("product_type")
    ))

    dates = database.get_available_dates()
    return render_template(
        "index.html",
        products=products,
        snap_date=snap_date,
        dates=dates,
        categories=categories,
    )


@app.route("/urun/<handle>")
def product_detail(handle):
    database.init_db()
    history = database.get_price_history(handle)
    if not history:
        return redirect(url_for("index"))

    # grab latest snapshot's metadata
    conn = database.get_conn()
    row = conn.execute("""
        SELECT p.title, p.vendor, p.product_type, p.image_url, p.url,
               s.description
        FROM products p
        JOIN snapshots s ON s.product_id = p.id
        WHERE p.handle = ?
        ORDER BY s.snap_date DESC LIMIT 1
    """, (handle,)).fetchone()
    conn.close()

    if not row:
        return redirect(url_for("index"))

    product = dict(row)
    return render_template("product.html", product=product, history=history)


@app.route("/scrape")
def trigger_scrape():
    """Manually trigger a scrape in a background thread."""
    def _run():
        try:
            scraper.run_scrape()
        except Exception as exc:
            log.error("Scrape hatası: %s", exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return redirect(url_for("index"))


@app.route("/api/products")
def api_products():
    """JSON API — today's products."""
    snap_date = request.args.get("date") or str(date.today())
    return jsonify(database.get_daily_products(snap_date))


@app.route("/api/status")
def api_status():
    last = database.get_last_scrape_time()
    return jsonify({"last_scrape": last, "today": str(date.today())})


if __name__ == "__main__":
    database.init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
