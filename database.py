import os
import re
import sqlite3
from datetime import date

# Öncelik sırası: Vercel Postgres → Supabase/genel DATABASE_URL
_DATABASE_URL = (
    os.environ.get("POSTGRES_URL_NON_POOLING")
    or os.environ.get("POSTGRES_URL")
    or os.environ.get("DATABASE_URL", "")
)
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql://", 1)
# Supabase bağlantı stringine sslmode yoksa ekle
if _DATABASE_URL and "sslmode" not in _DATABASE_URL:
    sep = "&" if "?" in _DATABASE_URL else "?"
    _DATABASE_URL += f"{sep}sslmode=require"

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
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS site TEXT NOT NULL DEFAULT 'fonksiyonel'",
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
        try:
            conn.execute("ALTER TABLE products ADD COLUMN site TEXT NOT NULL DEFAULT 'fonksiyonel'")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def upsert_product(shopify_id, handle, title, vendor, product_type, image_url, url, site="fonksiyonel"):
    conn = get_conn()
    _ex(conn, f"""
        INSERT INTO products (shopify_id, handle, title, vendor, product_type, image_url, url, site)
        VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH})
        ON CONFLICT(shopify_id) DO UPDATE SET
            title        = excluded.title,
            vendor       = excluded.vendor,
            product_type = excluded.product_type,
            image_url    = excluded.image_url,
            url          = excluded.url,
            site         = excluded.site
    """, (shopify_id, handle, title, vendor, product_type, image_url, url, site))
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


def get_daily_products(snap_date=None, site="fonksiyonel"):
    if snap_date is None:
        snap_date = str(date.today())
    conn = get_conn()
    rows = _ex(conn, f"""
        SELECT
            p.title, p.vendor, p.product_type, p.image_url, p.url, p.handle,
            s.price, s.compare_price, s.available, s.description, s.snap_date
        FROM snapshots s
        JOIN products p ON p.id = s.product_id
        WHERE s.snap_date = {PH} AND p.site = {PH}
        ORDER BY s.available DESC, p.title ASC
    """, (snap_date, site)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").casefold()).strip()


def get_latest_products_by_site(site: str) -> list:
    """Her ürün için (siteye göre) en güncel snapshot'ı döndürür."""
    conn = get_conn()
    rows = _ex(conn, f"""
        SELECT p.title, p.vendor, p.product_type, p.image_url, p.url, p.handle,
               s.price, s.compare_price, s.available, s.snap_date
        FROM products p
        JOIN snapshots s ON s.product_id = p.id
        WHERE p.site = {PH}
          AND s.snap_date = (
              SELECT MAX(s2.snap_date) FROM snapshots s2 WHERE s2.product_id = p.id
          )
    """, (site,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_comparison() -> list:
    """fonksiyonel.tr ve vitanica.tr'deki ürünleri başlığa göre eşleştirip fiyatları karşılaştırır."""
    fonksiyonel = get_latest_products_by_site("fonksiyonel")
    vitanica = get_latest_products_by_site("vitanica")

    vitanica_by_title = {}
    for v in vitanica:
        vitanica_by_title.setdefault(_normalize_title(v["title"]), v)

    rows = []
    for f in fonksiyonel:
        v = vitanica_by_title.get(_normalize_title(f["title"]))
        if not v:
            continue
        diff = round((f["price"] or 0) - (v["price"] or 0), 2)
        rows.append({
            "title": f["title"],
            "image_url": f["image_url"] or v["image_url"],
            "f_price": f["price"], "f_compare_price": f["compare_price"],
            "f_url": f["url"], "f_available": f["available"], "f_date": f["snap_date"],
            "v_price": v["price"], "v_compare_price": v["compare_price"],
            "v_url": v["url"], "v_available": v["available"], "v_date": v["snap_date"],
            "diff": diff,
            "differs": abs(diff) > 0.01,
        })

    rows.sort(key=lambda r: (not r["differs"], r["title"]))
    return rows


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
