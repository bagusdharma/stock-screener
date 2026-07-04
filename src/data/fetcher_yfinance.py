"""Yahoo Finance data fetcher — fallback source for IDX stocks.

Fixes from old run.py:
- BUG-009: shared ThreadPoolExecutor (not per-call)
- BUG-003: DER normalisation threshold 20 (not 5)
- BUG-004: ROE decimal vs percent detection
- BUG-001: div_streak as consecutive years (not nunique)
- BUG-002: yield TTM strict 12-month window with 15-month normalisation
- FORMULA-05: profit margin decimal detection
- Added: CAGR 3-year for revenue & earnings
"""

import contextlib
import io
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

# ── Shared executor — FIX BUG-009 ─────────────────────────────
# Old code created a new ThreadPoolExecutor per call (560x for 140 tickers).
# One shared executor with 4 workers handles all timeout-wrapped calls.
_executor = ThreadPoolExecutor(max_workers=4)
_is_shutdown = False

FETCH_TIMEOUT = 15


def shutdown_executor() -> None:
    """Explicitly shut down the shared executor.

    Called from main.py during graceful shutdown to prevent
    atexit race condition with daemon threads still submitting work.
    Uses cancel_futures=True to abort queued work immediately.
    """
    global _is_shutdown
    _is_shutdown = True
    try:
        _executor.shutdown(wait=False, cancel_futures=True)
        log.info("yfinance executor shut down")
    except Exception:
        pass


# ── Helper ─────────────────────────────────────────────────────

@contextlib.contextmanager
def _silent():
    """Suppress stderr noise from yfinance.

    JANGAN bungkus yield dengan try/except-yield-lagi: contextmanager yang
    yield dua kali saat exception menghasilkan RuntimeError "generator didn't
    stop after throw()" DAN menelan exception aslinya (root cause hilangnya
    seluruh fundamental ICBP/SIDO di log 2026-06-30).
    """
    with contextlib.redirect_stderr(io.StringIO()):
        yield


def _run_with_timeout(func, timeout=FETCH_TIMEOUT, retries=1):
    """Run func in the shared executor with a time limit.

    Transient error (network flake, dll) di-retry `retries` kali.
    Returns None on timeout, shutdown, or exception — caller decides fallback.
    """
    if _is_shutdown:
        return None
    last_exc = None
    for attempt in range(retries + 1):
        try:
            future = _executor.submit(func)
        except RuntimeError:
            log.debug("yfinance executor already shut down, skipping call")
            return None
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            log.warning("yfinance call timed out after %ds", timeout)
            return None
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                log.debug("yfinance call failed (attempt %d), retry: %s",
                          attempt + 1, exc)
                continue
    log.warning("yfinance call failed after %d attempts: %s",
                retries + 1, last_exc)
    return None


def _safe_info(ticker: str) -> dict:
    def _get():
        with _silent():
            result = yf.Ticker(ticker).info
            return result if isinstance(result, dict) else {}
    return _run_with_timeout(_get) or {}


def _safe_download(ticker: str, period: str = "2y") -> pd.DataFrame:
    def _get():
        with _silent():
            df = yf.download(
                ticker, period=period, interval="1d",
                auto_adjust=True, progress=False, threads=False,
            )
            return df if df is not None else pd.DataFrame()
    result = _run_with_timeout(_get)
    return result if isinstance(result, pd.DataFrame) else pd.DataFrame()


def _safe_dividends(ticker: str) -> pd.Series:
    def _get():
        with _silent():
            result = yf.Ticker(ticker).dividends
            return result if isinstance(result, pd.Series) else pd.Series(dtype=float)
    result = _run_with_timeout(_get)
    return result if isinstance(result, pd.Series) else pd.Series(dtype=float)


def _safe_financials(ticker: str) -> pd.DataFrame:
    def _get():
        with _silent():
            t = yf.Ticker(ticker)
            try:
                fin = t.income_stmt
            except AttributeError:
                fin = t.financials
            return fin if isinstance(fin, pd.DataFrame) else pd.DataFrame()
    result = _run_with_timeout(_get, timeout=10)
    return result if isinstance(result, pd.DataFrame) else pd.DataFrame()


def _extract_financial_row(df: pd.DataFrame, labels: list) -> list:
    """Find a row in income statement — tries multiple labels (yfinance versions differ)."""
    for label in labels:
        if label in df.index:
            row = df.loc[label].dropna()
            vals = []
            for v in row.values:
                try:
                    fv = float(v)
                    if fv == fv:  # skip NaN
                        vals.append(fv)
                except (TypeError, ValueError):
                    pass
            if vals:
                return vals
    return []


# ── 5.2 — Fundamental ─────────────────────────────────────────

def fetch_yf_fundamental(ticker: str) -> dict:
    """Fetch fundamental data for one ticker from Yahoo Finance.

    Returns dict with standardised keys. All values float or None.
    """
    info = _safe_info(ticker)

    price = None
    for key in ("regularMarketPrice", "currentPrice"):
        raw = info.get(key)
        if raw is not None:
            try:
                price = float(raw)
            except (TypeError, ValueError):
                pass
            if price and price > 0:
                break
            price = None

    if not price or price <= 0:
        log.warning("%s: harga tidak tersedia, skip fundamental", ticker)
        return {k: None for k in [
            "pe", "pbv", "eps", "der", "roe", "net_profit_margin",
            "current_ratio", "asset_turnover", "market_cap",
            "dividend_yield", "price",
        ]}

    # DER — FIX BUG-003: threshold 20 (not 5)
    der = None
    der_raw = info.get("debtToEquity")
    if der_raw is not None:
        try:
            der_raw = float(der_raw)
            if der_raw > 20:
                der = der_raw / 100
            else:
                der = der_raw
        except (TypeError, ValueError):
            pass

    # ROE — FIX BUG-004: detect decimal vs percent
    roe = None
    roe_raw = info.get("returnOnEquity")
    if roe_raw is not None:
        try:
            roe_raw = float(roe_raw)
            if abs(roe_raw) <= 2:
                roe = roe_raw * 100
            else:
                roe = roe_raw
            roe = max(-100.0, min(100.0, roe))
        except (TypeError, ValueError):
            pass

    # Profit margin — FIX FORMULA-05: detect decimal vs percent
    pm = None
    pm_raw = info.get("profitMargins")
    if pm_raw is not None:
        try:
            pm_raw = float(pm_raw)
            if abs(pm_raw) <= 1:
                pm = pm_raw * 100
            else:
                pm = pm_raw
        except (TypeError, ValueError):
            pass

    # PBV — cap at 50x (Yahoo data often absurd for IDX)
    pbv = None
    pbv_raw = info.get("priceToBook")
    if pbv_raw is not None:
        try:
            pbv_raw = float(pbv_raw)
            if -50 <= pbv_raw <= 50:
                pbv = pbv_raw
        except (TypeError, ValueError):
            pass

    # PER — cap at 200x, negative = rugi → None
    pe = None
    pe_raw = info.get("trailingPE")
    if pe_raw is not None:
        try:
            pe_raw = float(pe_raw)
            if 0 < pe_raw <= 200:
                pe = pe_raw
        except (TypeError, ValueError):
            pass

    def _safe_float(key):
        val = info.get(key)
        if val is None:
            return None
        try:
            f = float(val)
            return f if f == f else None  # NaN check
        except (TypeError, ValueError):
            return None

    result = {
        "pe": pe,
        "pbv": pbv,
        "eps": _safe_float("trailingEps"),
        "der": der,
        "roe": roe,
        "net_profit_margin": pm,
        "current_ratio": _safe_float("currentRatio"),
        "asset_turnover": None,  # not in yfinance .info
        "market_cap": _safe_float("marketCap"),
        "dividend_yield": None,  # calculated in fetch_yf_dividends
        "price": price,
        "sector_yf": info.get("sector"),  # untuk pengecualian field N/A bank
    }

    # Field yang WAJAR kosong tidak dihitung sebagai missing:
    # - dividend_yield: selalu None di tahap ini (diisi fetch_yf_dividends)
    # - bank/financials: current_ratio & asset_turnover tidak berlaku untuk
    #   model bisnis bank, dan DER bank sering null di Yahoo (bukan red flag)
    skip = {"dividend_yield", "sector_yf"}
    if result["sector_yf"] == "Financial Services":
        skip |= {"current_ratio", "asset_turnover", "der"}
    none_count = sum(1 for k, v in result.items()
                     if v is None and k not in skip)
    if none_count > 3:
        log.warning("%s: %d field fundamental kosong dari Yahoo (di luar N/A wajar)",
                    ticker, none_count)

    return result


# ── 5.3 — OHLCV ───────────────────────────────────────────────

def fetch_yf_ohlcv(ticker: str, period: str = "2y") -> dict | None:
    """Download historical OHLCV for technical indicators.

    Returns dict with close/high/low/volume as pd.Series.
    Returns None if data < 50 rows.
    """
    yf_logger = logging.getLogger("yfinance")
    prev_level = yf_logger.level
    yf_logger.setLevel(logging.CRITICAL)

    try:
        df = _safe_download(ticker, period=period)
    finally:
        yf_logger.setLevel(prev_level)

    if df.empty:
        log.warning("%s: OHLCV download returned empty", ticker)
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    empty = pd.Series(dtype=float)
    close = df["Close"].squeeze().dropna().astype(float) if "Close" in df.columns else empty
    high = df["High"].squeeze().dropna().astype(float) if "High" in df.columns else empty
    low = df["Low"].squeeze().dropna().astype(float) if "Low" in df.columns else empty
    volume = df["Volume"].squeeze().dropna().astype(float) if "Volume" in df.columns else empty

    if len(close) < 50:
        log.warning("%s: OHLCV only %d rows (need 50+)", ticker, len(close))
        return None

    return {
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
    }


# ── 5.4 — Dividends (FIXED) ───────────────────────────────────

def fetch_yf_dividends(ticker: str, price: float = 0.0) -> dict | None:
    """Fetch dividend data and calculate yield/streak.

    Fixes:
    - BUG-001: div_streak = consecutive years (not nunique)
    - BUG-002: strict 12-month TTM window, 15-month normalised fallback
    - Yield trap: yield > 10% → None + warning
    """
    divs = _safe_dividends(ticker)
    if divs is None or len(divs) == 0:
        return {"yield_ttm": 0.0, "div_streak": 0, "div_amount_ttm": 0.0}

    # Normalise timezone
    try:
        idx = divs.index
        if hasattr(idx, "tz") and idx.tz is not None:
            now = pd.Timestamp.now(tz="UTC")
            divs = divs.copy()
            divs.index = idx.tz_convert("UTC")
        else:
            now = pd.Timestamp.now()
            divs = divs.copy()
            if hasattr(idx, "tz_localize"):
                try:
                    divs.index = idx.tz_localize(None)
                except TypeError:
                    pass
    except Exception:
        now = pd.Timestamp.now()
        divs = divs.copy()
        try:
            divs.index = divs.index.tz_localize(None)
        except Exception:
            pass

    # FIX BUG-001: div_streak = consecutive years counting backward from most recent
    years_with_div = sorted(divs.index.year.unique(), reverse=True)
    if years_with_div:
        div_streak = 1
        for i in range(len(years_with_div) - 1):
            if years_with_div[i] - years_with_div[i + 1] == 1:
                div_streak += 1
            else:
                break
    else:
        div_streak = 0

    # ── FIX FATAL 2026-07-04: baris dividen korup dari Yahoo ─────
    # Yahoo kadang punya baris yang TIDAK ter-adjust stock split (tanpa
    # catatan split di feed-nya) atau dividen spesial one-off raksasa.
    # Kasus nyata: YUPI 2025-07-07 Rp 187.26 di samping pembayaran normal
    # Rp 35 & Rp 16 → TTM 238.9 padahal riil 51.68 (verified vs Stockbit).
    # Buang baris dari kalkulasi TTM bila DUA-DUANYA terpenuhi:
    #   (1) nilai > 3.5x median pembayaran 24 bulan terakhir (min 3 baris)
    #   (2) baris tunggal itu saja > 8% dari harga sekarang
    # Kalibrasi anti-false-positive: ITMG (final jumbo 2245 sah, harga
    # 22525) lolos karena hanya 2.0x median. Streak tahun TIDAK terpengaruh.
    divs_ttm = divs
    try:
        recent = divs[divs.index >= (now - pd.DateOffset(months=24))]
        if len(recent) >= 3 and price > 0:
            med = float(recent.median())
            if med > 0:
                suspect = (divs > 3.5 * med) & (divs / price > 0.08)
                if bool(suspect.any()):
                    for dt, v in divs[suspect].items():
                        log.warning(
                            "%s: baris dividen anomali DIBUANG dari TTM: "
                            "%s Rp %.2f (%.1fx median Rp %.2f, %.1f%% dari "
                            "harga) — kemungkinan unadjusted split / spesial",
                            ticker, dt.date(), v, v / med, med,
                            v / price * 100,
                        )
                    divs_ttm = divs[~suspect]
    except Exception as exc:
        log.warning("%s: filter dividen anomali gagal, pakai data mentah: %s",
                    ticker, exc)

    # FIX BUG-002: strict 12-month TTM, fallback 15-month normalised
    batas_12m = now - pd.DateOffset(months=12)
    batas_15m = now - pd.DateOffset(months=15)

    divs_12m = divs_ttm[divs_ttm.index >= batas_12m]
    divs_15m = divs_ttm[divs_ttm.index >= batas_15m]

    div_amount_ttm = 0.0
    if len(divs_12m) > 0:
        div_amount_ttm = float(divs_12m.sum())
    elif len(divs_15m) > 0:
        raw_15m = float(divs_15m.sum())
        div_amount_ttm = raw_15m / 15 * 12
        log.info("%s: no div in 12m, normalised 15m (%.0f → %.0f)", ticker, raw_15m, div_amount_ttm)

    if div_amount_ttm <= 0 or price <= 0:
        return {
            "yield_ttm": 0.0,
            "div_streak": div_streak,
            "div_amount_ttm": div_amount_ttm,
        }

    yield_ttm = div_amount_ttm / price

    # Yield trap: > 20% is almost certainly a data error for IDX
    # (many blue chip banks legitimately yield 8-15%)
    if yield_ttm > 0.20:
        log.warning(
            "%s: Potential yield trap — yield %.1f%% > 20%%, setting to None",
            ticker, yield_ttm * 100,
        )
        yield_ttm = None

    return {
        "yield_ttm": yield_ttm,
        "div_streak": div_streak,
        "div_amount_ttm": round(div_amount_ttm, 2),
    }


# ── 5.5 — Growth & Financials ─────────────────────────────────

def fetch_yf_growth(ticker: str) -> dict | None:
    """Fetch income statement and calculate growth metrics.

    Returns dict with CAGR, YoY, profitable_years, revenue_trend.
    Returns None if data insufficient.
    """
    fin = _safe_financials(ticker)
    if fin is None or fin.empty:
        return None

    rev_labels = ["Total Revenue", "TotalRevenue", "Revenue", "Operating Revenue"]
    ni_labels = ["Net Income", "NetIncome", "Net Income Common Stockholders",
                 "Net Income Common Stock Holders"]

    revenues = _extract_financial_row(fin, rev_labels)
    net_incomes = _extract_financial_row(fin, ni_labels)

    if len(revenues) < 2 and len(net_incomes) < 2:
        log.warning("%s: insufficient financial data for growth", ticker)
        return None

    # YoY revenue growth (index 0 = newest)
    revenue_yoy = None
    if len(revenues) >= 2 and abs(revenues[1]) > 0:
        revenue_yoy = (revenues[0] - revenues[1]) / abs(revenues[1])
        revenue_yoy = max(-1.0, min(5.0, revenue_yoy))

    # YoY earnings growth
    earnings_yoy = None
    if len(net_incomes) >= 2 and abs(net_incomes[1]) > 0:
        earnings_yoy = (net_incomes[0] - net_incomes[1]) / abs(net_incomes[1])
        earnings_yoy = max(-1.0, min(5.0, earnings_yoy))

    # CAGR 3-year: (end/start)^(1/n) - 1
    revenue_cagr_3y = None
    if len(revenues) >= 4 and revenues[3] > 0 and revenues[0] > 0:
        ratio = revenues[0] / revenues[3]
        if ratio > 0:
            revenue_cagr_3y = ratio ** (1.0 / 3.0) - 1.0
            revenue_cagr_3y = max(-1.0, min(5.0, revenue_cagr_3y))

    earnings_cagr_3y = None
    if len(net_incomes) >= 4 and net_incomes[3] > 0 and net_incomes[0] > 0:
        ratio = net_incomes[0] / net_incomes[3]
        if ratio > 0:
            earnings_cagr_3y = ratio ** (1.0 / 3.0) - 1.0
            earnings_cagr_3y = max(-1.0, min(5.0, earnings_cagr_3y))

    # profitable_years: consecutive streak from newest
    profitable_years = 0
    for ni in net_incomes:
        if ni > 0:
            profitable_years += 1
        else:
            break

    # revenue_trend: need 3+ years
    if len(revenues) >= 3:
        if revenues[0] > revenues[1] > revenues[2]:
            revenue_trend = "growing"
        elif revenues[0] < revenues[1] < revenues[2]:
            revenue_trend = "declining"
        else:
            revenue_trend = "flat"
    elif len(revenues) >= 2:
        if revenues[0] > revenues[1]:
            revenue_trend = "growing"
        elif revenues[0] < revenues[1]:
            revenue_trend = "declining"
        else:
            revenue_trend = "flat"
    else:
        revenue_trend = "unknown"

    return {
        "revenue_cagr_3y": revenue_cagr_3y,
        "earnings_cagr_3y": earnings_cagr_3y,
        "revenue_yoy": revenue_yoy,
        "earnings_yoy": earnings_yoy,
        "profitable_years": profitable_years,
        "revenue_trend": revenue_trend,
    }


# ── 5.6 — Entry point ─────────────────────────────────────────

# dividend_yield sengaja TIDAK di sini — selalu None di fetch_yf_fundamental
# (diisi oleh fetch_yf_dividends sebagai yield_ttm), jangan deflasi completeness.
_EXPECTED_FIELDS = {
    "fundamental": ["pe", "pbv", "eps", "der", "roe", "net_profit_margin",
                     "current_ratio", "asset_turnover", "market_cap",
                     "price"],
    "dividends": ["yield_ttm", "div_streak", "div_amount_ttm"],
    "growth": ["revenue_cagr_3y", "earnings_cagr_3y", "revenue_yoy",
               "earnings_yoy", "profitable_years", "revenue_trend"],
}


def get_yf_data(ticker: str) -> dict:
    """Entry point — fetch all Yahoo Finance data for one ticker.

    Never crashes. Missing sections filled with None.
    """
    result = {
        "ticker": ticker,
        "fundamental": None,
        "ohlcv": None,
        "dividends": None,
        "growth": None,
        "data_completeness": 0.0,
    }

    # Fundamental
    try:
        result["fundamental"] = fetch_yf_fundamental(ticker)
    except Exception as exc:
        log.error("%s: fetch_yf_fundamental failed: %s", ticker, exc)

    # OHLCV
    try:
        result["ohlcv"] = fetch_yf_ohlcv(ticker)
    except Exception as exc:
        log.error("%s: fetch_yf_ohlcv failed: %s", ticker, exc)

    # Dividends — needs price from fundamental
    try:
        price = 0.0
        if result["fundamental"] and result["fundamental"].get("price"):
            price = result["fundamental"]["price"]
        result["dividends"] = fetch_yf_dividends(ticker, price=price)
    except Exception as exc:
        log.error("%s: fetch_yf_dividends failed: %s", ticker, exc)

    # Growth
    try:
        result["growth"] = fetch_yf_growth(ticker)
    except Exception as exc:
        log.error("%s: fetch_yf_growth failed: %s", ticker, exc)

    # Data completeness — bank: CR/AT/DER tidak berlaku, jangan dihitung
    is_bank = (result.get("fundamental") or {}).get("sector_yf") == "Financial Services"
    na_bank = {"current_ratio", "asset_turnover", "der"}
    total_fields = 0
    non_none = 0
    for section, fields in _EXPECTED_FIELDS.items():
        data = result.get(section)
        for field in fields:
            if is_bank and field in na_bank:
                continue
            total_fields += 1
            if data and data.get(field) is not None:
                non_none += 1
    # OHLCV counts as 1 field (present or not)
    total_fields += 1
    if result["ohlcv"] is not None:
        non_none += 1

    completeness = non_none / total_fields if total_fields > 0 else 0.0
    result["data_completeness"] = round(completeness, 3)

    if completeness < 0.3:
        log.warning(
            "%s: data_completeness=%.1f%% — very low coverage",
            ticker, completeness * 100,
        )

    return result
