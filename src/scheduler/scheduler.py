"""APScheduler-based screening scheduler for IDX market hours.

Runs scoring pipeline at configured times on market days (Mon-Fri).
- Job overlap prevention via threading.Lock (non-blocking acquire)
- Automatic weekend skip via CronTrigger day_of_week='mon-fri'
- One retry after 5 minutes on failure (no double-retry)
- Graceful shutdown waits for active job to complete
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

import math

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config.settings import (
    BASE_DIR,
    SCHEDULE_PAGI,
    SCHEDULE_WEEKLY_TIME,
    SCREEN_SCHEDULE,
    SCHEDULE_SIANG,
    SCHEDULE_SORE,
    TICKERS,
    TIMEZONE,
)

log = logging.getLogger(__name__)

RESULTS_FILE = str(BASE_DIR / "results_cache.json")
STATUS_FILE = str(BASE_DIR / "screen_status.json")

_scheduler: BackgroundScheduler | None = None
_job_lock = threading.Lock()

_RETRY_DELAY_MINUTES = 5
_TZ = pytz.timezone(TIMEZONE)


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

def is_market_day() -> bool:
    """True if today is Monday-Friday in WIB."""
    return datetime.now(_TZ).weekday() < 5


def _parse_time(t: str) -> tuple[int, int]:
    """Parse 'HH:MM' → (hour, minute)."""
    h, m = t.strip().split(":")
    return int(h), int(m)


def _json_default(obj):
    """Handle non-serializable types and NaN → null."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return str(obj)


def _save_status(status: str, progress: int, message: str) -> None:
    """Atomic write to screen_status.json."""
    data = {
        "status": status,
        "progress": int(progress),
        "message": message,
        "log": [],
        "updated_at": datetime.now().isoformat(),
    }
    tmp = STATUS_FILE + ".tmp"
    try:
        Path(STATUS_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, STATUS_FILE)
    except Exception as exc:
        log.warning("Failed to write status: %s", exc)
        try:
            os.remove(tmp)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════
#  Screening job
# ═══════════════════════════════════════════════════════════════

def run_screening_job(is_retry: bool = False,
                      ignore_market_day: bool = False) -> bool:
    """Run one full screening cycle: merge → score → save.

    ignore_market_day: True untuk jadwal mingguan Sabtu (bukan hari bursa
    secara sengaja — memakai harga penutupan Jumat).
    Returns True on success, False on skip/failure.
    Overlap-safe: if a previous job is still running, this call is skipped.
    """
    if not is_market_day() and not is_retry and not ignore_market_day:
        log.info("Bukan hari bursa — screening di-skip")
        return False

    if not _job_lock.acquire(blocking=False):
        log.warning("Job overlap — screening sebelumnya masih berjalan, skip")
        return False

    ts = datetime.now(_TZ).strftime("%Y-%m-%d %H:%M")
    log.info("[%s] Screening otomatis dimulai%s", ts, " (retry)" if is_retry else "")
    _save_status("running", 0, "Memulai screening otomatis...")

    try:
        from src.data.merger import get_all_merged
        from src.data.universe import get_universe
        from src.analysis.scorer import score_all

        tickers = get_universe()
        _save_status("running", 10, f"Fetching & merging {len(tickers)} saham...")
        all_merged = get_all_merged(tickers)

        _save_status("running", 60, "Scoring...")
        scored = score_all(all_merged)

        if not scored:
            raise RuntimeError("Scoring returned empty results")

        # Build combined result list (merger fields + scorer fields)
        results_list: list[dict] = []
        for ticker, merged in all_merged.items():
            score_data = scored.get(ticker, {})
            price = merged.get("price")
            entry = {
                "ticker": ticker,
                "name": merged.get("name", ""),
                "sector": merged.get("sector", "Unknown"),
                "sub_sector": merged.get("sub_sector", ""),
                "price": price,
                "harga_lot": int(price * 100) if price else 0,
                "pe": merged.get("pe"),
                "pbv": merged.get("pbv"),
                "roe": merged.get("roe"),
                "der": merged.get("der"),
                "net_profit_margin": merged.get("net_profit_margin"),
                "market_cap": merged.get("market_cap"),
                "yield_ttm": merged.get("yield_ttm"),
                "div_streak": merged.get("div_streak"),
                "div_amount_ttm": merged.get("div_amount_ttm"),
                "revenue_cagr_3y": merged.get("revenue_cagr_3y"),
                "earnings_cagr_3y": merged.get("earnings_cagr_3y"),
            }
            entry.update(score_data)
            results_list.append(entry)

        results_list.sort(key=lambda r: r.get("skor_total", 0), reverse=True)

        # Save to results_cache.json (atomic write)
        _save_status("running", 90, "Menyimpan hasil...")
        cache = {
            "generated_at": datetime.now().isoformat(),
            "total": len(results_list),
            "data": results_list,
        }
        cache_path = Path(RESULTS_FILE)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(cache_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, default=_json_default)
        os.replace(tmp, RESULTS_FILE)

        buy_count = sum(
            1 for r in results_list if r.get("label") in ("STRONG BUY", "BUY")
        )
        msg = f"Selesai! {len(results_list)} saham, {buy_count} BUY"
        _save_status("done", 100, msg)
        log.info("[%s] %s", ts, msg)
        return True

    except Exception as exc:
        log.error("Screening failed: %s", exc, exc_info=True)
        _save_status("error", 0, str(exc)[:200])

        if not is_retry and _scheduler is not None:
            retry_time = datetime.now(_TZ) + timedelta(minutes=_RETRY_DELAY_MINUTES)
            log.info("Retry dijadwalkan pada %s", retry_time.strftime("%H:%M"))
            _scheduler.add_job(
                run_screening_job,
                trigger="date",
                run_date=retry_time,
                kwargs={"is_retry": True,
                        "ignore_market_day": ignore_market_day},
                id="screening_retry",
                replace_existing=True,
            )
        return False
    finally:
        _job_lock.release()


# ═══════════════════════════════════════════════════════════════
#  Scheduler lifecycle
# ═══════════════════════════════════════════════════════════════

def start_scheduler() -> BackgroundScheduler:
    """Start APScheduler sesuai SCREEN_SCHEDULE di settings.

    "weekly" (default): 1 job — Sabtu SCHEDULE_WEEKLY_TIME WIB, memakai
      harga penutupan Jumat. Hemat 15x utk free-tier VM; fundamental
      (80% bobot skor) memang hanya berubah kuartalan.
    "daily": mode lama — 3 job Sen-Jum (pagi/siang/sore).

    Returns the BackgroundScheduler instance.
    """
    global _scheduler

    if _scheduler is not None:
        log.warning("Scheduler already running")
        return _scheduler

    _scheduler = BackgroundScheduler(timezone=TIMEZONE)

    if SCREEN_SCHEDULE == "off":
        # Deploy Render + GitHub Actions: penjadwalan dilakukan GA,
        # scheduler internal tidak membuat job apa pun.
        log.info("SCREEN_SCHEDULE=off — scheduler internal tanpa job "
                 "(screening dikelola GitHub Actions)")
        _scheduler.start()
        return _scheduler

    if SCREEN_SCHEDULE == "daily":
        jobs_config = [
            ("screening_pagi", SCHEDULE_PAGI, "Screening Pagi"),
            ("screening_siang", SCHEDULE_SIANG, "Screening Siang"),
            ("screening_sore", SCHEDULE_SORE, "Screening Sore"),
        ]
        for job_id, time_str, name in jobs_config:
            h, m = _parse_time(time_str)
            _scheduler.add_job(
                run_screening_job,
                CronTrigger(
                    hour=h,
                    minute=m,
                    day_of_week="mon-fri",
                    timezone=TIMEZONE,
                ),
                id=job_id,
                name=name,
                replace_existing=True,
            )
    else:  # weekly (default)
        h, m = _parse_time(SCHEDULE_WEEKLY_TIME)
        _scheduler.add_job(
            run_screening_job,
            CronTrigger(
                hour=h,
                minute=m,
                day_of_week="sat",
                timezone=TIMEZONE,
            ),
            kwargs={"ignore_market_day": True},
            id="screening_mingguan",
            name="Screening Mingguan (Sabtu, harga tutup Jumat)",
            replace_existing=True,
        )

    _scheduler.start()

    for j in _scheduler.get_jobs():
        log.info("Scheduled: %s → next run %s", j.id, j.next_run_time)

    return _scheduler


def stop_scheduler() -> None:
    """Shut down the scheduler without blocking.

    Uses wait=False because during Ctrl+C shutdown the yfinance executor
    is already shut down, so any running screening job would produce
    garbage data anyway.  Blocking here just prevents the process from
    exiting in a timely manner.
    """
    global _scheduler

    if _scheduler is None:
        return

    log.info("Stopping scheduler...")
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("Scheduler stopped")
