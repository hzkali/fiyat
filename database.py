import os
import sqlite3
from datetime import date

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql://", 1)

USE_PG = bool(_DATABASE_URL)
PH = "%s" if USE_PG else "?"

_IS_VERCEL = bool(os.environ.get("VERCEL"))
_DB_PATH = "/tmp/fiyat.db" if _IS_VERCEL else os.path.join(os.path.dirname(__file__), "fiyat.db")


def get_conn():
    if USE_PG:
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(_DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ex(conn, sql, params=()):
    if USE_PG:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur
    return conn.execute(sql, params)


def init_db():
    conn = get_conn()
    if USE_PG:
        for stmt in [
            """CREATE TABLE IF NOT EXISTS products (
                id           SERIAL PRIMARY KEY,
                shopify_id   TEXT UNIQUE NOT NULL,
                handle       TEXT NOT NULL,
                title        TEXT NOT NULL,
                vendor       TEXT,
                product_type TEXT,
                image_url    TEXT,
                url          TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS snapshots (
                id            SERIAL PRIMARY KEY,
                product_id    INTEGER NOT NULL REFERENCES products(id),
                snap_date     TEXT NOT NULL,
                price         REAL,
                compare_price REAL,
                available     INTEGER,
                description   TEXT,
                UNIQUE(product_id, snap_date)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_date ON snapshots(snap_date)",
        ]:
            _ex(conn, stmt)
    else:
        conn.cursor().executescript("""
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
    _ex(conn, f"""
        INSERT INTO products (shopify_id, handle, title, vendor, product_type, image_url, url)
        VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH})
        ON CONFLICT(shopify_id) DO UPDATE SET
            title        = excluded.title,
            vendor       = excluded.vendor,
            product_type = excluded.product_type,
            image_url    = excluded.image_url,
            url          = excluded.url
    """, (shopify_id, handle, title, vendor, product_type, image_url, url))
    row = _ex(conn, f"SELECT id FROM products WHERE shopify_id = {PH}", (shopify_id,)).fetchone()
    conn.commit()
    conn.close()
    return dict(row)["id"]


def upsert_snapshot(product_id, snap_date, price, compare_price, available, description):
    conn = get_conn()
    _ex(conn, f"""
        INSERT INTO snapshots (product_id, snap_date, price, compare_price, available, description)
        VALUES ({PH},{PH},{PH},{PH},{PH},{PH})
        ON CONFLICT(product_id, snap_date) DO UPDATE SET
            price         = excluded.price,
            compare_price = excluded.compare_price,
            available     = excluded.available,
            description   = excluded.description
    """, (product_id, str(snap_date), price, compare_price, int(available), description))
    conn.commit()
    conn.close()


def get_daily_products(snap_date=None):
    if snap_date is None:
        snap_date = str(date.today())
    conn = get_conn()
    rows = _ex(conn, f"""
        SELECT
            p.title, p.vendor, p.product_type, p.image_url, p.url, p.handle,
            s.price, s.compare_price, s.available, s.description, s.snap_date
        FROM snapshots s
        JOIN products p ON p.id = s.product_id
        WHERE s.snap_date = {PH}
        ORDER BY s.available DESC, p.title ASC
    """, (snap_date,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_available_dates():
    conn = get_conn()
    rows = _ex(conn, """
        SELECT DISTINCT snap_date FROM snapshots ORDER BY snap_date DESC LIMIT 30
    """).fetchall()
    conn.close()
    return [dict(r)["snap_date"] for r in rows]


def get_price_history(handle):
    conn = get_conn()
    rows = _ex(conn, f"""
        SELECT s.snap_date, s.price, s.compare_price, s.available
        FROM snapshots s
        JOIN products p ON p.id = s.product_id
        WHERE p.handle = {PH}
        ORDER BY s.snap_date ASC
    """, (handle,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_product_meta(handle):
    conn = get_conn()
    row = _ex(conn, f"""
        SELECT p.title, p.vendor, p.product_type, p.image_url, p.url,
               s.description
        FROM products p
        JOIN snapshots s ON s.product_id = p.id
        WHERE p.handle = {PH}
        ORDER BY s.snap_date DESC LIMIT 1
    """, (handle,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_last_scrape_time():
    conn = get_conn()
    row = _ex(conn, """
        SELECT snap_date FROM snapshots ORDER BY id DESC LIMIT 1
    """).fetchone()
    conn.close()
    return dict(row)["snap_date"] if row else None
