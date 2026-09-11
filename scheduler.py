"""
Günlük scraping scheduler — APScheduler gerektirmez.
Her gün sabah 08:00'de otomatik olarak scraper'ı çalıştırır.

Kullanım:
    python3 scheduler.py           # Sürekli çalıştır (daemon mod)
    python3 scheduler.py --now     # Sadece şimdi bir kez çalıştır
"""
import sys
import time
import logging
import threading
from datetime import datetime, date
import scraper
import database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Her gün çalışacak saat (24h)
SCHEDULE_HOUR = 8
SCHEDULE_MINUTE = 0


def run_job():
    log.info("=== Günlük scraping başladı ===")
    try:
        count = scraper.run_scrape_all()
        log.info("=== Scraping bitti: %d ürün ===", count)
    except Exception as exc:
        log.error("Scraping başarısız: %s", exc)


def seconds_until_next_run() -> float:
    """Bir sonraki 08:00'e kadar kaç saniye kaldığını hesaplar."""
    now = datetime.now()
    target = now.replace(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, second=0, microsecond=0)
    if now >= target:
        # Bugünkü saat geçmiş, yarına planla
        from datetime import timedelta
        target += timedelta(days=1)
    return (target - now).total_seconds()


def schedule_loop():
    """Sonsuz döngü: her gün 08:00'de job çalıştırır."""
    last_run_date = None

    while True:
        today = date.today()
        now = datetime.now()

        # Bugün henüz çalışmadıysa ve saat 08:00'i geçtiyse hemen çalıştır
        if last_run_date != today and now.hour >= SCHEDULE_HOUR:
            run_job()
            last_run_date = today

        wait = seconds_until_next_run()
        log.info(
            "Sonraki çalışma: %02d:%02d — %.0f saniye sonra",
            SCHEDULE_HOUR, SCHEDULE_MINUTE, wait,
        )
        time.sleep(min(wait, 3600))  # En fazla 1 saat bekle, sonra tekrar kontrol et


def start_background_scheduler():
    """Flask uygulamasıyla birlikte arka planda başlatmak için."""
    t = threading.Thread(target=schedule_loop, daemon=True, name="DailyScheduler")
    t.start()
    log.info("Arka plan scheduler başlatıldı")
    return t


def main():
    database.init_db()

    if "--now" in sys.argv:
        run_job()
        return

    # İlk başlatmada hemen bir kez çalıştır
    log.info("Başlangıç scrapin'i yapılıyor…")
    run_job()

    log.info("Scheduler başlatıldı — her gün %02d:%02d'de çalışacak", SCHEDULE_HOUR, SCHEDULE_MINUTE)
    schedule_loop()


if __name__ == "__main__":
    main()
