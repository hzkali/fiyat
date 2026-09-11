import os
import threading
import logging
from datetime import date
from flask import Flask, render_template, request, redirect, url_for, jsonify
import database
import scraper
import scheduler as sched

log = logging.getLogger(__name__)
app = Flask(__name__)

IS_VERCEL = bool(os.environ.get("VERCEL"))

if not IS_VERCEL and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
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

    product = database.get_product_meta(handle)
    if not product:
        return redirect(url_for("index"))

    return render_template("product.html", product=product, history=history)


@app.route("/scrape")
def trigger_scrape():
    def _run():
        try:
            scraper.run_scrape_all()
        except Exception as exc:
            log.error("Scrape hatası: %s", exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return redirect(url_for("index"))


@app.route("/karsilastir")
def compare():
    database.init_db()
    all_rows = database.get_comparison()
    only_diff = request.args.get("fark") == "1"
    rows = [r for r in all_rows if r["differs"]] if only_diff else all_rows
    diff_count = sum(1 for r in all_rows if r["differs"])

    return render_template(
        "compare.html",
        rows=rows,
        total_matched=len(all_rows),
        diff_count=diff_count,
        only_diff=only_diff,
    )


@app.route("/api/products")
def api_products():
    snap_date = request.args.get("date") or str(date.today())
    return jsonify(database.get_daily_products(snap_date))


@app.route("/api/status")
def api_status():
    last = database.get_last_scrape_time()
    return jsonify({"last_scrape": last, "today": str(date.today())})


if __name__ == "__main__":
    database.init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
