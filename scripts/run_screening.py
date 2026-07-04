"""Screening penuh untuk GitHub Actions (dan manual CLI).

Dipanggil oleh .github/workflows/screening.yml 3x sehari (Sen-Jum).
Menulis results_cache.json + screen_status.json di root repo — workflow
kemudian meng-commit file tsb sehingga Vercel & bot (Render) membaca
data terbaru via raw.githubusercontent.com.

Jalankan manual: python scripts/run_screening.py
"""

import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import logging

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("run_screening")


def _json_default(obj):
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return str(obj)


def _save_status(status: str, progress: int, message: str, logs: list):
    from src.bot.formatters import now_wib
    data = {
        "status": status,
        "progress": int(progress),
        "message": message,
        "log": logs[-20:],
        "updated_at": now_wib().isoformat(),
    }
    tmp = "screen_status.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, "screen_status.json")


def main() -> int:
    from src.data.universe import get_universe
    from src.data.merger import get_all_merged, shutdown_merger
    from src.data.fetcher_yfinance import shutdown_executor
    from src.analysis.scorer import score_all
    from src.bot.formatters import now_wib

    t0 = time.time()
    logs: list[str] = []
    tickers = get_universe()
    log.info("Universe: %d ticker", len(tickers))
    _save_status("running", 5, f"Fetching {len(tickers)} saham...", logs)

    def _prog(done, total, ticker):
        if done % 25 == 0 or done == total:
            line = f"[{time.strftime('%H:%M:%S')}] [{done}/{total}] {ticker}"
            logs.append(line)
            print(line, flush=True)
            _save_status("running", 5 + int(done / total * 55),
                         f"Fetching {done}/{total} — {ticker}", logs)

    try:
        all_merged = get_all_merged(tickers, force_refresh=True,
                                    on_progress=_prog)

        # Guard anti-data-sampah: kalau mayoritas ticker tanpa harga
        # (Yahoo rate limit / sumber down), JANGAN timpa hasil lama —
        # exit 1 → step commit di workflow tidak jalan.
        with_price = sum(1 for m in all_merged.values() if m.get("price"))
        coverage = with_price / max(len(all_merged), 1)
        if coverage < 0.7:
            raise RuntimeError(
                f"Harga hanya ada utk {with_price}/{len(all_merged)} ticker "
                f"({coverage:.0%} < 70%) — kemungkinan rate limit; "
                "hasil TIDAK disimpan agar cache lama tetap utuh")
        log.info("Price coverage: %d/%d (%.0f%%)",
                 with_price, len(all_merged), coverage * 100)

        _save_status("running", 60, "Scoring...", logs)
        scored = score_all(all_merged)
        if not scored:
            raise RuntimeError("Scoring returned empty results")

        results_list = []
        for ticker, merged in all_merged.items():
            entry = {
                "ticker": ticker,
                "name": merged.get("name", ""),
                "sector": merged.get("sector", "Unknown"),
                "sub_sector": merged.get("sub_sector", ""),
                "price": merged.get("price"),
                "harga_lot": int(merged["price"] * 100) if merged.get("price") else 0,
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
            entry.update(scored.get(ticker, {}))
            entry.pop("ohlcv", None)
            results_list.append(entry)

        results_list.sort(key=lambda r: r.get("skor_total", 0), reverse=True)
        cache = {
            "generated_at": now_wib().isoformat(),
            "total": len(results_list),
            "data": results_list,
        }
        tmp = "results_cache.json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2,
                      default=_json_default)
        os.replace(tmp, "results_cache.json")

        buy = sum(1 for r in results_list
                  if r.get("label") in ("STRONG BUY", "BUY"))
        _save_status("done", 100,
                     f"Selesai! {len(results_list)} saham, {buy} BUY", logs)
        log.info("SELESAI %d saham (%d BUY) dalam %.0fs",
                 len(results_list), buy, time.time() - t0)
        return 0
    except Exception as exc:
        log.error("Screening gagal: %s", exc, exc_info=True)
        _save_status("error", 0, str(exc)[:150], logs)
        return 1
    finally:
        shutdown_merger()
        shutdown_executor()


if __name__ == "__main__":
    sys.exit(main())
