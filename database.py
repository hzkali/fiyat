import sqlite3
import os
from datetime import date

DB_PATH = os.path.join(os.path.dirname(__file__), "fiyat.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            shopify_id  TEXT UNIQUE NOT NULL,
            handle      TEXT NOT NULL,
            title       TEXT NOT NULL,
            vendor      TEXT,
            product_type TEXT,
            image_url   TEXT,
            url         TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id  INTEGER NOT NULL REFERENCES products(id),
            snap_date   TEXT NOT NULL,
            price       REAL,
            compare_price REAL,
            available   INTEGER,
            description TEXT,
            UNIQUE(product_id, snap_date)
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_date ON snapshots(snap_date);
    """)
    conn.commit()
    conn.close()


def upsert_product(shopify_id, handle, title, vendor, product_type, image_url, url):
    conn = get_conn()
    conn.execute("""
        INSERT INTO products (shopify_id, handle, title, vendor, product_type, image_url, url)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shopify_id) DO UPDATE SET
            title        = excluded.title,
            vendor       = excluded.vendor,
            product_type = excluded.product_type,
            image_url    = excluded.image_url,
            url          = excluded.url
    """, (shopify_id, handle, title, vendor, product_type, image_url, url))
    row = conn.execute("SELECT id FROM products WHERE shopify_id = ?", (shopify_id,)).fetchone()
    conn.commit()
    conn.close()
    return row["id"]


def upsert_snapshot(product_id, snap_date, price, compare_price, available, description):
    conn = get_conn()
    conn.execute("""
        INSERT INTO snapshots (product_id, snap_date, price, compare_price, available, description)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id, snap_date) DO UPDATE SET
            price         = excluded.price,
            compare_price = excluded.compare_price,
            available     = excluded.available,
            description   = excluded.description
    """, (product_id, str(snap_date), price, compare_price, int(available), description))
    conn.commit()
    conn.close()


def get_daily_products(snap_date=None):
    """Return all products with their snapshot for a given date."""
    if snap_date is None:
        snap_date = str(date.today())
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            p.title, p.vendor, p.product_type, p.image_url, p.url, p.handle,
            s.price, s.compare_price, s.available, s.description, s.snap_date
        FROM snapshots s
        JOIN products p ON p.id = s.product_id
        WHERE s.snap_date = ?
        ORDER BY s.available DESC, p.title ASC
    """, (snap_date,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_available_dates():
    """Return distinct snapshot dates, newest first."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT DISTINCT snap_date FROM snapshots ORDER BY snap_date DESC LIMIT 30
    """).fetchall()
    conn.close()
    return [r["snap_date"] for r in rows]


def get_price_history(handle):
    """Return price history for a product handle."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.snap_date, s.price, s.compare_price, s.available
        FROM snapshots s
        JOIN products p ON p.id = s.product_id
        WHERE p.handle = ?
        ORDER BY s.snap_date ASC
    """, (handle,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_last_scrape_time():
    conn = get_conn()
    row = conn.execute("""
        SELECT snap_date FROM snapshots ORDER BY rowid DESC LIMIT 1
    """).fetchone()
    conn.close()
    return row["snap_date"] if row else None
