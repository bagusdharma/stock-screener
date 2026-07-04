"""Data merger — single source of truth for each ticker.

Reconciles data from three fetchers with strict priority:
  IDX XLSX (fundamental) → IDX API (price/profile) → Yahoo Finance (fallback)

Conflict rule: if IDX XLSX and Yahoo differ >5% on the same metric,
use IDX value and log the conflict to cache/conflict_log.jsonl.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Event, Lock

import pandas as pd

from src.config.settings import CACHE_DIR, CONFLICT_THRESHOLD, SEKTOR

log = logging.getLogger(__name__)

_CONFLICT_LOG = Path(CACHE_DIR) / "conflict_log.jsonl"
_conflict_lock = Lock()

_shutdown_event = Event()
_active_pool: ThreadPoolExecutor | None = None
_pool_lock = Lock()

# ── Knob lingkungan utk runner IP-shared (GitHub Actions) ──────────
# FETCH_MAX_WORKERS  ticker paralel (default 4; GA: 1 — burst memicu 429 Yahoo)
# FETCH_DELAY        jeda detik antar ticker (default 0; GA: 2)
# DISABLE_IDX_API=1  lewati IDX API total (GA: Cloudflare IDX blok IP datacenter,
#                    HTTP 403 — field khas IDX diisi snapshot-fill)
_FETCH_WORKERS = max(1, int(os.getenv("FETCH_MAX_WORKERS", "4")))
_FETCH_DELAY = max(0.0, float(os.getenv("FETCH_DELAY", "0")))
_DISABLE_IDX_API = os.getenv("DISABLE_IDX_API", "0") == "1"

# Fields where IDX XLSX and Yahoo Finance overlap and can conflict
_OVERLAP_FIELDS = ["pe", "pbv", "der", "roe", "net_profit_margin", "market_cap"]

# ── Snapshot anti-data-hilang (FIX CRITICAL 2026-07-04) ─────────
# Saat run besar (957 ticker) Yahoo bisa throttle → field acak jadi None →
# skor blue chip ambruk (kasus nyata: BBCA roe=None → skor 69 → JUAL).
# Solusi: simpan nilai sehat per field per run; run berikutnya, field yang
# None diisi dari snapshot selama umurnya <= 14 hari (ditandai stale_fields).
_SNAPSHOT_FILE = None  # diinisialisasi lazy (butuh BASE_DIR)
_SNAPSHOT_MAX_AGE_DAYS = 14
_SNAP_FIELDS = [
    "name", "sector", "sub_sector", "pe", "pbv", "eps", "der", "roe",
    "net_profit_margin", "current_ratio", "asset_turnover", "market_cap",
    "yield_ttm", "div_streak", "div_amount_ttm",
    "revenue_cagr_3y", "earnings_cagr_3y", "profitable_years", "revenue_trend",
]


def _snapshot_path():
    global _SNAPSHOT_FILE
    if _SNAPSHOT_FILE is None:
        from src.config.settings import BASE_DIR
        _SNAPSHOT_FILE = BASE_DIR / "merged_snapshot.json"
    return _SNAPSHOT_FILE


def _load_snapshot() -> dict:
    """{ticker: {"fields": {...}, "fresh_at": {field: iso}}} atau {}."""
    import json
    try:
        with open(_snapshot_path(), encoding="utf-8") as f:
            snap = json.load(f)
        return snap if isinstance(snap, dict) else {}
    except (OSError, ValueError):
        return {}


def _snap_age_ok(iso: str | None) -> bool:
    if not iso:
        return False
    try:
        age = datetime.now() - datetime.fromisoformat(iso)
        return age.days <= _SNAPSHOT_MAX_AGE_DAYS
    except (ValueError, TypeError):
        return False


def _fill_from_snapshot(merged: dict, snap_entry: dict) -> list[str]:
    """Isi field None dari snapshot (yang masih segar). Return nama field terisi."""
    filled: list[str] = []
    fields = snap_entry.get("fields", {})
    fresh_at = snap_entry.get("fresh_at", {})
    for f in _SNAP_FIELDS:
        cur = merged.get(f)
        is_missing = cur is None or (f == "sector" and cur == "Unknown") \
            or (f in ("name", "sub_sector", "revenue_trend") and cur == "")
        if is_missing and fields.get(f) is not None and _snap_age_ok(fresh_at.get(f)):
            merged[f] = fields[f]
            filled.append(f)
    return filled


def _save_snapshot(all_merged: dict, old_snap: dict) -> None:
    """Simpan nilai SEGAR run ini; field yang tadi diisi dari snapshot
    dicarry dengan timestamp lamanya (kadaluarsa alami di 14 hari)."""
    import json
    now_iso = datetime.now().isoformat()
    snap: dict = {}
    for ticker, m in all_merged.items():
        stale = set(m.get("stale_fields", []))
        old = old_snap.get(ticker, {})
        fields: dict = {}
        fresh_at: dict = {}
        for f in _SNAP_FIELDS:
            v = m.get(f)
            if v is None:
                continue
            if isinstance(v, float) and v != v:  # NaN
                continue
            if f in stale:  # carry-forward: pertahankan timestamp lama
                fields[f] = v
                fresh_at[f] = old.get("fresh_at", {}).get(f)
            else:
                fields[f] = v
                fresh_at[f] = now_iso
        if fields:
            snap[ticker] = {"fields": fields, "fresh_at": fresh_at}
    try:
        tmp = str(_snapshot_path()) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        import os
        os.replace(tmp, _snapshot_path())
        log.info("Snapshot merged tersimpan: %d ticker", len(snap))
    except Exception as exc:
        log.warning("Gagal simpan snapshot: %s", exc)


_COMPLETENESS_ALWAYS = [
    "pe", "pbv", "eps", "roe", "net_profit_margin", "market_cap", "price",
    "yield_ttm", "div_streak", "revenue_cagr_3y", "earnings_cagr_3y",
    "profitable_years", "ohlcv",
]
_COMPLETENESS_NONBANK = ["der", "current_ratio", "asset_turnover"]


def _recount_completeness(m: dict) -> float:
    """Hitung ulang data_completeness setelah snapshot-fill.

    Cermin dari kalkulasi inline di _merge_one (field 'sector' dihitung
    hadir bila bukan Unknown).
    """
    fields = list(_COMPLETENESS_ALWAYS)
    if m.get("sector") != "Financials":
        fields += _COMPLETENESS_NONBANK
    present = sum(1 for f in fields if m.get(f) is not None)
    total = len(fields) + 1  # +1 utk sector
    if m.get("sector") not in (None, "Unknown"):
        present += 1
    return round(present / total, 3)

# All fields the scorer needs — used for data_completeness calculation
_SCORER_FIELDS = [
    "pe", "pbv", "eps", "der", "roe", "net_profit_margin",
    "current_ratio", "asset_turnover", "market_cap", "price",
    "yield_ttm", "div_streak",
    "revenue_cagr_3y", "earnings_cagr_3y", "profitable_years",
    "ohlcv", "sector",
]


# ── Helpers ──────────────────────────────────────────────────

def _clean_row(row_dict: dict) -> dict:
    """Convert a pandas row dict: drop last_updated, NaN → None."""
    clean = {}
    for k, v in row_dict.items():
        if k == "last_updated":
            continue
        if isinstance(v, float) and v != v:
            clean[k] = None
        else:
            clean[k] = v
    return clean


# ── Source adapters (mockable seams) ─────────────────────────

def _get_idx_xlsx_row(ticker: str) -> dict | None:
    """Get one ticker's row from IDX XLSX DataFrame. Returns dict or None."""
    try:
        from src.data.fetcher_idx_xlsx import get_idx_fundamental
        df = get_idx_fundamental()
        match = df.loc[df["ticker"] == ticker]
        if match.empty:
            return None
        return _clean_row(match.iloc[0].to_dict())
    except Exception as exc:
        log.warning("%s: IDX XLSX fetch failed: %s", ticker, exc)
        return None


def _get_idx_api(ticker: str) -> dict | None:
    """Get price/profile from IDX API. Returns dict or None."""
    if _DISABLE_IDX_API:
        return None
    try:
        from src.data.fetcher_idx_api import get_idx_price_profile
        return get_idx_price_profile(ticker)
    except Exception as exc:
        log.warning("%s: IDX API fetch failed: %s", ticker, exc)
        return None


def _get_yf(ticker: str) -> dict | None:
    """Get all Yahoo Finance data. Returns dict or None."""
    try:
        from src.data.fetcher_yfinance import get_yf_data
        return get_yf_data(ticker)
    except Exception as exc:
        log.warning("%s: Yahoo Finance fetch failed: %s", ticker, exc)
        return None


# ── Conflict logging ─────────────────────────────────────────

def _log_conflict(ticker: str, field: str,
                  idx_val: float, yf_val: float, diff_pct: float):
    """Append one conflict entry to cache/conflict_log.jsonl (thread-safe)."""
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "ticker": ticker,
            "field": field,
            "idx_value": idx_val,
            "yahoo_value": yf_val,
            "diff_pct": round(diff_pct * 100, 2),
            "chosen": "idx_xlsx",
        }
        with _conflict_lock:
            _CONFLICT_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(_CONFLICT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        log.error("Failed to write conflict log: %s", exc)


# ── Core merge logic ─────────────────────────────────────────

def _pick(idx_val, yf_val, ticker: str, field: str) -> tuple:
    """Pick the best value with priority IDX > Yahoo.

    Returns (value, source_tag).
    If both present and differ >5%, logs conflict and picks IDX.
    """
    if idx_val is not None and yf_val is not None:
        if field in _OVERLAP_FIELDS:
            try:
                i, y = float(idx_val), float(yf_val)
                if abs(i) > 0:
                    diff = abs(i - y) / abs(i)
                    if diff > CONFLICT_THRESHOLD:
                        log.warning(
                            "%s: %s conflict — IDX=%.4g Yahoo=%.4g (%.1f%%)",
                            ticker, field, i, y, diff * 100,
                        )
                        _log_conflict(ticker, field, i, y, diff)
            except (TypeError, ValueError):
                pass
        return idx_val, "idx_xlsx"

    if idx_val is not None:
        return idx_val, "idx_xlsx"
    if yf_val is not None:
        return yf_val, "yahoo"
    return None, "none"


def _merge_one(ticker: str, idx_row: dict | None,
               idx_api: dict | None, yf: dict | None) -> dict:
    """Merge data from all three sources for one ticker."""
    idx = idx_row or {}
    api = idx_api or {}
    yf_fund = (yf or {}).get("fundamental") or {}
    yf_div = (yf or {}).get("dividends") or {}
    yf_growth = (yf or {}).get("growth") or {}
    yf_ohlcv = (yf or {}).get("ohlcv")

    sources: dict[str, str] = {}

    # ── Price: IDX API → Yahoo ────────────────────────────────
    price = api.get("price")
    if price and price > 0:
        sources["price"] = "idx_api"
    else:
        price = yf_fund.get("price")
        sources["price"] = "yahoo" if price else "none"

    # ── Fundamental: IDX XLSX → Yahoo ─────────────────────────
    fund_fields = ["pe", "pbv", "eps", "der", "roe",
                   "net_profit_margin", "current_ratio", "asset_turnover"]
    merged = {}
    for f in fund_fields:
        val, src = _pick(idx.get(f), yf_fund.get(f), ticker, f)
        merged[f] = val
        sources[f] = src

    # ── Market cap: IDX XLSX → IDX API → Yahoo ────────────────
    mcap = idx.get("market_cap")
    if mcap and mcap > 0:
        sources["market_cap"] = "idx_xlsx"
    else:
        mcap = api.get("market_cap")
        if mcap and mcap > 0:
            sources["market_cap"] = "idx_api"
        else:
            mcap = yf_fund.get("market_cap")
            sources["market_cap"] = "yahoo" if mcap else "none"

    # Check XLSX vs Yahoo conflict for market_cap
    idx_mcap = idx.get("market_cap")
    yf_mcap = yf_fund.get("market_cap")
    if idx_mcap and yf_mcap and idx_mcap > 0:
        try:
            diff = abs(float(idx_mcap) - float(yf_mcap)) / abs(float(idx_mcap))
            if diff > CONFLICT_THRESHOLD:
                log.warning(
                    "%s: market_cap conflict — IDX=%.4g Yahoo=%.4g (%.1f%%)",
                    ticker, idx_mcap, yf_mcap, diff * 100,
                )
                _log_conflict(ticker, "market_cap", float(idx_mcap),
                              float(yf_mcap), diff)
        except (TypeError, ValueError):
            pass

    # ── Profile: IDX API → settings fallback ──────────────────
    name = api.get("name", "")
    sector = api.get("sector") or SEKTOR.get(
        ticker.replace(".JK", ""), "Unknown"
    )
    sub_sector = api.get("sub_sector", "")

    # ── Dividend: Yahoo Finance (only source with streak) ─────
    yield_ttm = yf_div.get("yield_ttm")
    # Yahoo returns yield as decimal fraction (0.05 = 5%);
    # scorer and formatters expect percentage (5.0 = 5%).
    if yield_ttm is not None and 0 < yield_ttm < 1.0:
        yield_ttm = yield_ttm * 100
    div_streak = yf_div.get("div_streak") if yf_div else None
    div_amount_ttm = yf_div.get("div_amount_ttm") if yf_div else None

    # IDX XLSX dividend_yield as fallback for yield_ttm
    if yield_ttm is None or yield_ttm == 0:
        xlsx_yield = idx.get("dividend_yield")
        if xlsx_yield and xlsx_yield > 0:
            yield_ttm = xlsx_yield
            sources["yield_ttm"] = "idx_xlsx"
        else:
            sources["yield_ttm"] = "none"
    else:
        sources["yield_ttm"] = "yahoo"

    # ── Growth: Yahoo Finance only ────────────────────────────
    revenue_cagr_3y = yf_growth.get("revenue_cagr_3y")
    earnings_cagr_3y = yf_growth.get("earnings_cagr_3y")
    revenue_yoy = yf_growth.get("revenue_yoy")
    earnings_yoy = yf_growth.get("earnings_yoy")
    profitable_years = yf_growth.get("profitable_years")
    revenue_trend = yf_growth.get("revenue_trend")

    # Yahoo returns CAGR/YoY as decimal fraction (0.15 = 15%);
    # scorer expects percentage (15.0 = 15%).
    if revenue_cagr_3y is not None and -1.5 <= revenue_cagr_3y <= 6.0:
        revenue_cagr_3y = revenue_cagr_3y * 100
    if earnings_cagr_3y is not None and -1.5 <= earnings_cagr_3y <= 6.0:
        earnings_cagr_3y = earnings_cagr_3y * 100
    if revenue_yoy is not None and -1.5 <= revenue_yoy <= 6.0:
        revenue_yoy = revenue_yoy * 100
    if earnings_yoy is not None and -1.5 <= earnings_yoy <= 6.0:
        earnings_yoy = earnings_yoy * 100

    # ── OHLCV: Yahoo Finance only ─────────────────────────────
    # Fetcher returns dict of Series (lowercase keys).
    # Scorer expects pd.DataFrame with capitalized columns.
    ohlcv = None
    if isinstance(yf_ohlcv, dict):
        try:
            key_map = {"close": "Close", "high": "High",
                       "low": "Low", "volume": "Volume"}
            df_data = {}
            for src, dst in key_map.items():
                if src in yf_ohlcv and isinstance(yf_ohlcv[src], pd.Series):
                    df_data[dst] = yf_ohlcv[src]
            if df_data:
                ohlcv = pd.DataFrame(df_data)
        except Exception as exc:
            log.warning("%s: OHLCV dict→DataFrame failed: %s", ticker, exc)
    elif isinstance(yf_ohlcv, pd.DataFrame):
        ohlcv = yf_ohlcv

    # ── Price validation against OHLCV last close ──────────────
    # Catch stale / pre-stock-split prices from .info endpoint
    if price and ohlcv is not None and "Close" in ohlcv.columns and len(ohlcv) > 0:
        try:
            last_close = float(ohlcv["Close"].iloc[-1])
            if last_close > 0:
                ratio = price / last_close
                if ratio > 2.0 or ratio < 0.5:
                    log.warning(
                        "%s: price mismatch — source=%.0f ohlcv_close=%.0f "
                        "(ratio=%.1fx), using OHLCV close",
                        ticker, price, last_close, ratio,
                    )
                    price = last_close
                    sources["price"] = "ohlcv_corrected"
        except (TypeError, ValueError, IndexError):
            pass

    # ── Data completeness ─────────────────────────────────────
    # Banks structurally lack der, current_ratio, asset_turnover from Yahoo
    # Finance — exclude these to avoid unfair completeness penalty.
    is_bank_sector = sector == "Financials"
    check_vals = {
        "pe": merged.get("pe"),
        "pbv": merged.get("pbv"),
        "eps": merged.get("eps"),
        "roe": merged.get("roe"),
        "net_profit_margin": merged.get("net_profit_margin"),
        "market_cap": mcap,
        "price": price,
        "yield_ttm": yield_ttm,
        "div_streak": div_streak,
        "revenue_cagr_3y": revenue_cagr_3y,
        "earnings_cagr_3y": earnings_cagr_3y,
        "profitable_years": profitable_years,
        "ohlcv": ohlcv,
        "sector": sector if sector != "Unknown" else None,
    }
    if not is_bank_sector:
        check_vals["der"] = merged.get("der")
        check_vals["current_ratio"] = merged.get("current_ratio")
        check_vals["asset_turnover"] = merged.get("asset_turnover")
    total = len(check_vals)
    present = sum(1 for v in check_vals.values() if v is not None)
    data_completeness = round(present / total, 3) if total > 0 else 0.0

    if data_completeness < 0.3:
        log.error(
            "%s: data_completeness=%.1f%% — critically low",
            ticker, data_completeness * 100,
        )
    elif data_completeness < 0.5:
        log.warning(
            "%s: data_completeness=%.1f%% — low coverage",
            ticker, data_completeness * 100,
        )

    return {
        "ticker": ticker,
        "name": name,
        "sector": sector,
        "sub_sector": sub_sector,
        # Fundamental
        "pe": merged.get("pe"),
        "pbv": merged.get("pbv"),
        "eps": merged.get("eps"),
        "der": merged.get("der"),
        "roe": merged.get("roe"),
        "net_profit_margin": merged.get("net_profit_margin"),
        "current_ratio": merged.get("current_ratio"),
        "asset_turnover": merged.get("asset_turnover"),
        "market_cap": mcap,
        "price": price,
        # Dividend
        "yield_ttm": yield_ttm,
        "div_streak": div_streak,
        "div_amount_ttm": div_amount_ttm,
        # Growth
        "revenue_cagr_3y": revenue_cagr_3y,
        "earnings_cagr_3y": earnings_cagr_3y,
        "revenue_yoy": revenue_yoy,
        "earnings_yoy": earnings_yoy,
        "profitable_years": profitable_years,
        "revenue_trend": revenue_trend,
        # Technical
        "ohlcv": ohlcv,
        # Meta
        "data_completeness": data_completeness,
        "sources": sources,
    }


# ── Public API ────────────────────────────────────────────────

def get_merged_data(ticker: str) -> dict:
    """Merge data from all sources for one ticker.

    Priority: IDX XLSX → IDX API → Yahoo Finance.
    Never crashes — returns dict with None fields on failure.
    """
    idx_row = _get_idx_xlsx_row(ticker)
    idx_api = _get_idx_api(ticker)
    yf = _get_yf(ticker)
    return _merge_one(ticker, idx_row, idx_api, yf)


def get_all_merged(tickers: list[str],
                   force_refresh: bool = False,
                   on_progress: "Callable[[int, int, str], None] | None" = None,
                   ) -> dict[str, dict]:
    """Batch merge for multiple tickers with concurrent fetching.

    Uses ThreadPoolExecutor for Yahoo Finance calls (slow, external).
    IDX API rate-limits itself internally.
    One failed ticker does not block others.

    Args:
        force_refresh: if True, invalidate all caches before fetching.
        on_progress: callback(done_count, total, last_ticker) called per ticker.
    """
    if force_refresh:
        try:
            from src.data.fetcher_idx_api import _clear_all_caches
            _clear_all_caches()
            log.info("IDX API caches cleared (force_refresh)")
        except Exception as exc:
            log.warning("Failed to clear IDX API caches: %s", exc)

    global _active_pool

    if _FETCH_WORKERS != 4 or _FETCH_DELAY or _DISABLE_IDX_API:
        log.info("Mode throttle: workers=%d delay=%.1fs idx_api=%s",
                 _FETCH_WORKERS, _FETCH_DELAY,
                 "OFF" if _DISABLE_IDX_API else "on")

    _shutdown_event.clear()
    results: dict[str, dict] = {}
    total = len(tickers)
    done_count = 0

    # IDX XLSX disabled — endpoint returns HTML instead of XLSX.
    # Fundamental data sourced from IDX API + Yahoo Finance fallback.

    # Concurrent fetch: IDX API + Yahoo Finance per ticker
    def _fetch_and_merge(ticker: str) -> tuple[str, dict]:
        if _shutdown_event.is_set():
            raise InterruptedError("shutdown")
        idx_api = _get_idx_api(ticker)
        if _shutdown_event.is_set():
            raise InterruptedError("shutdown")
        yf = _get_yf(ticker)
        if _shutdown_event.is_set():
            raise InterruptedError("shutdown")
        merged = _merge_one(ticker, None, idx_api, yf)
        if _FETCH_DELAY:
            time.sleep(_FETCH_DELAY)
        return ticker, merged

    pool = ThreadPoolExecutor(max_workers=_FETCH_WORKERS)
    with _pool_lock:
        _active_pool = pool

    try:
        futures = {
            pool.submit(_fetch_and_merge, t): t for t in tickers
        }
        for future in as_completed(futures):
            if _shutdown_event.is_set():
                break
            ticker = futures[future]
            try:
                _, merged = future.result(timeout=120)
                results[ticker] = merged
            except InterruptedError:
                results[ticker] = _empty_merged(ticker)
            except Exception as exc:
                log.error("%s: merge pipeline crashed: %s", ticker, exc)
                results[ticker] = _empty_merged(ticker)
            done_count += 1
            if on_progress:
                try:
                    on_progress(done_count, total, ticker.replace(".JK", ""))
                except Exception:
                    pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        with _pool_lock:
            _active_pool = None

    # ── Snapshot fill & save (anti data-hilang) ──────────────
    try:
        old_snap = _load_snapshot()
        n_filled = 0
        for ticker, merged in results.items():
            snap_entry = old_snap.get(ticker)
            if not snap_entry:
                continue
            filled = _fill_from_snapshot(merged, snap_entry)
            if filled:
                merged["stale_fields"] = filled
                merged["data_completeness"] = _recount_completeness(merged)
                n_filled += 1
                log.info("%s: %d field diisi dari snapshot run sebelumnya: %s",
                         ticker, len(filled), ", ".join(filled))
        if n_filled:
            log.warning("Snapshot-fill aktif utk %d ticker — sumber data "
                        "sempat gagal parsial di run ini", n_filled)
        _save_snapshot(results, old_snap)
    except Exception as exc:
        log.warning("Snapshot fill/save gagal (non-fatal): %s", exc)

    return results


def shutdown_merger() -> None:
    """Signal merger to stop and cancel in-flight work."""
    _shutdown_event.set()
    with _pool_lock:
        if _active_pool is not None:
            try:
                _active_pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
    log.info("Merger shutdown signalled")


def _empty_merged(ticker: str) -> dict:
    """Return a valid but empty merged dict for a ticker that failed."""
    code = ticker.replace(".JK", "")
    return {
        "ticker": ticker,
        "name": "",
        "sector": SEKTOR.get(code, "Unknown"),
        "sub_sector": "",
        "pe": None, "pbv": None, "eps": None, "der": None,
        "roe": None, "net_profit_margin": None,
        "current_ratio": None, "asset_turnover": None,
        "market_cap": None, "price": None,
        "yield_ttm": None, "div_streak": None,
        "div_amount_ttm": None,
        "revenue_cagr_3y": None, "earnings_cagr_3y": None,
        "revenue_yoy": None, "earnings_yoy": None,
        "profitable_years": None, "revenue_trend": None,
        "ohlcv": None,
        "data_completeness": 0.0,
        "sources": {},
    }
