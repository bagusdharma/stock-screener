"""IDX unofficial JSON API fetcher — emiten profile & price data.

Fetches from idx.co.id hidden JSON endpoints:
- Company profile: name, sector, sub-sector, shares outstanding
- Trading summary: latest closing price, volume, value

Uses curl_cffi for browser emulation to avoid WAF blocks.
Falls back to standard requests if curl_cffi unavailable.
"""

import logging
import time
from datetime import datetime, timedelta
from threading import Lock

log = logging.getLogger(__name__)

# ── HTTP client (prefer curl_cffi for browser emulation) ────
try:
    from curl_cffi import requests as _http

    _USE_CURL_CFFI = True
except ImportError:
    import requests as _http  # type: ignore[no-redef]

    _USE_CURL_CFFI = False
    log.info("curl_cffi not installed, using requests (may be blocked by IDX WAF)")

from src.config.settings import IDX_IC_SECTORS, SEKTOR

# ── Endpoints (migrated from /umbraco/Surface/StockData → /primary/ListedCompany)
_BASE = "https://www.idx.co.id/primary/ListedCompany"
_PROFILE_URL = f"{_BASE}/GetCompanyProfiles"
_TRADING_URL = f"{_BASE}/GetTradingInfoSS"

# ── Request config ───────────────────────────────────────────
_TIMEOUT = 15
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0
_MIN_REQUEST_GAP = 1.5

# ── Cache ────────────────────────────────────────────────────
_PROFILE_TTL = timedelta(hours=24)
_PRICE_TTL = timedelta(minutes=30)

_profile_cache: dict[str, tuple[dict, datetime]] = {}
_price_cache: dict[str, tuple[dict, datetime]] = {}
_cache_lock = Lock()

# ── Rate limiting ────────────────────────────────────────────
_last_request_at = 0.0
_rate_lock = Lock()


# ── Helpers ──────────────────────────────────────────────────

def _to_bare_code(ticker: str) -> str:
    """BBCA.JK → BBCA, bbca.jk → BBCA"""
    return ticker.replace(".JK", "").replace(".jk", "").strip().upper()


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _enforce_rate_limit():
    global _last_request_at
    with _rate_lock:
        now = time.monotonic()
        gap = now - _last_request_at
        if gap < _MIN_REQUEST_GAP:
            time.sleep(_MIN_REQUEST_GAP - gap)
        _last_request_at = time.monotonic()


def _get_cached(cache: dict, key: str, ttl: timedelta) -> dict | None:
    with _cache_lock:
        entry = cache.get(key)
        if entry is not None:
            data, ts = entry
            if datetime.now() - ts < ttl:
                return data
            del cache[key]
    return None


def _set_cache(cache: dict, key: str, data: dict):
    with _cache_lock:
        cache[key] = (data, datetime.now())


def _clear_all_caches():
    with _cache_lock:
        _profile_cache.clear()
        _price_cache.clear()


# ── Circuit breaker ─────────────────────────────────────────
_CB_THRESHOLD = 5
_CB_COOLDOWN = 300.0
_cb_failures = 0
_cb_open_at = 0.0
_cb_lock = Lock()


def _circuit_open() -> bool:
    with _cb_lock:
        if _cb_failures < _CB_THRESHOLD:
            return False
        if time.monotonic() - _cb_open_at > _CB_COOLDOWN:
            return False
        return True


def _cb_record_success():
    global _cb_failures, _cb_open_at
    with _cb_lock:
        _cb_failures = 0
        _cb_open_at = 0.0


def _cb_record_failure():
    global _cb_failures, _cb_open_at
    with _cb_lock:
        _cb_failures += 1
        if _cb_failures >= _CB_THRESHOLD:
            _cb_open_at = time.monotonic()
            log.warning(
                "IDX API circuit breaker OPEN after %d failures — "
                "retry in %ds", _cb_failures, int(_CB_COOLDOWN),
            )


# ── HTTP layer ───────────────────────────────────────────────

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    "Referer": "https://www.idx.co.id/",
}


def _request_json(url: str, params: dict) -> dict | None:
    """HTTP GET with retry, timeout, rate limiting, and circuit breaker.

    Returns parsed JSON dict, or None on any failure.
    Never raises.
    """
    if _circuit_open():
        return None

    for attempt in range(1, _MAX_RETRIES + 1):
        _enforce_rate_limit()
        try:
            kwargs = {
                "params": params,
                "headers": _HEADERS,
                "timeout": _TIMEOUT,
            }
            if _USE_CURL_CFFI:
                kwargs["impersonate"] = "chrome"

            resp = _http.get(url, **kwargs)

            if resp.status_code == 200:
                try:
                    result = resp.json()
                    _cb_record_success()
                    return result
                except (ValueError, TypeError) as exc:
                    log.warning("IDX API: invalid JSON from %s: %s", url, exc)
                    _cb_record_failure()
                    return None

            if resp.status_code in (429, 503):
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                log.warning(
                    "IDX API %d on attempt %d/%d, retry in %.1fs",
                    resp.status_code, attempt, _MAX_RETRIES, delay,
                )
                time.sleep(delay)
                continue

            log.warning("IDX API returned %d for %s", resp.status_code, url)
            _cb_record_failure()
            return None

        except Exception as exc:
            if attempt < _MAX_RETRIES:
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                log.warning(
                    "IDX API error attempt %d/%d: %s, retry in %.1fs",
                    attempt, _MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)
            else:
                log.error("IDX API failed after %d attempts: %s", _MAX_RETRIES, exc)
                _cb_record_failure()

    return None


# ── Sector mapping ───────────────────────────────────────────

_SECTOR_ALIASES: list[tuple[str, str]] = [
    ("coal mining", "Energy"),
    ("oil & gas", "Energy"),
    ("coal", "Energy"),
    ("oil", "Energy"),
    ("gas production", "Energy"),
    ("finance", "Financials"),
    ("financial", "Financials"),
    ("banking", "Financials"),
    ("bank", "Financials"),
    ("property", "Property & Real Estate"),
    ("real estate", "Property & Real Estate"),
    ("consumer goods", "Consumer Non-Cyclical"),
    ("consumer staples", "Consumer Non-Cyclical"),
    ("consumer discretionary", "Consumer Cyclical"),
    ("trade", "Consumer Cyclical"),
    ("mining", "Basic Materials"),
    ("chemical", "Basic Materials"),
    ("cement", "Basic Materials"),
    ("metal", "Basic Materials"),
    ("basic industry", "Basic Materials"),
    ("industrial", "Industrials"),
    ("pharma", "Healthcare"),
    ("health", "Healthcare"),
    ("telco", "Infrastructure"),
    ("telecom", "Infrastructure"),
    ("telecommunication", "Infrastructure"),
    ("construction", "Infrastructure"),
    ("transport", "Transportation & Logistic"),
    ("logistics", "Transportation & Logistic"),
    ("media", "Technology"),
    ("information technology", "Technology"),
]


_SECTOR_MAP_ID: dict[str, str] = {
    "Keuangan": "Financials",
    "Energi": "Energy",
    "Barang Baku": "Basic Materials",
    "Properti & Real Estat": "Property & Real Estate",
    "Barang Konsumen Primer": "Consumer Non-Cyclical",
    "Barang Konsumen Non-Primer": "Consumer Cyclical",
    "Infrastruktur": "Infrastructure",
    "Kesehatan": "Healthcare",
    "Perindustrian": "Industrials",
    "Teknologi": "Technology",
    "Transportasi & Logistik": "Transportation & Logistic",
}


def _normalize_sector(api_sector: str) -> str:
    """Map IDX API sector string to one of 11 IDX-IC official sectors.

    Handles both Indonesian (new API) and English (legacy) sector names.
    """
    if not api_sector or not api_sector.strip():
        return "Unknown"

    s = api_sector.strip()

    if s in IDX_IC_SECTORS:
        return s

    if s in _SECTOR_MAP_ID:
        return _SECTOR_MAP_ID[s]

    s_lower = s.lower()
    for official in IDX_IC_SECTORS:
        if official.lower() == s_lower:
            return official

    for alias, mapped in _SECTOR_ALIASES:
        if alias in s_lower:
            return mapped

    log.warning("Unknown IDX sector: '%s'", api_sector)
    return "Unknown"


# ── Profile fetch ────────────────────────────────────────────

def _fetch_profile(code: str) -> dict | None:
    """Fetch emiten profile from GetCompanyProfiles endpoint.

    Returns dict with name, sector, sub_sector.
    Returns None if API unavailable or ticker not found.
    """
    cached = _get_cached(_profile_cache, code, _PROFILE_TTL)
    if cached is not None:
        return cached

    data = _request_json(_PROFILE_URL, {"KodeEmiten": code})

    if not data:
        return None

    records = data.get("data")
    if not records or not isinstance(records, list):
        log.warning("IDX API: no profile data for %s", code)
        return None

    rec = records[0]
    resp_code = str(rec.get("KodeEmiten", "")).upper()
    if resp_code != code:
        log.warning("IDX API: requested %s but got %s", code, resp_code)
        return None

    api_sector = str(rec.get("Sektor", "")).strip()
    sector = SEKTOR.get(code) or _normalize_sector(api_sector)

    profile = {
        "name": str(rec.get("NamaEmiten", "")).strip(),
        "sector": sector,
        "sub_sector": str(rec.get("SubSektor", "")).strip(),
        "listing_board": str(rec.get("PapanPencatatan", "")).strip(),
    }

    _set_cache(_profile_cache, code, profile)
    return profile


# ── Price fetch ──────────────────────────────────────────────

def _fetch_price(code: str) -> dict | None:
    """Fetch latest trading data from GetTradingInfoSS endpoint.

    Returns dict with close, volume, value, listed_shares, date.
    Returns None if API unavailable or no valid price.
    """
    cached = _get_cached(_price_cache, code, _PRICE_TTL)
    if cached is not None:
        return cached

    data = _request_json(_TRADING_URL, {"code": code})
    if not data:
        return None

    replies = data.get("replies")
    if not replies or not isinstance(replies, list):
        log.warning("IDX API: no trading data for %s", code)
        return None

    rec = replies[0]
    close = _safe_float(rec.get("Close"))
    if close is None or close <= 0:
        return None

    raw_date = rec.get("Date", "")
    trade_date = raw_date[:10] if raw_date else None

    price_data = {
        "close": close,
        "previous": _safe_float(rec.get("Previous")),
        "open": _safe_float(rec.get("OpenPrice")),
        "high": _safe_float(rec.get("High")),
        "low": _safe_float(rec.get("Low")),
        "volume": _safe_int(rec.get("Volume")),
        "value": _safe_int(rec.get("Value")),
        "frequency": _safe_int(rec.get("Frequency")),
        "listed_shares": _safe_int(rec.get("ListedShares")),
        "date": trade_date,
    }
    _set_cache(_price_cache, code, price_data)
    return price_data


# ── Entry point ──────────────────────────────────────────────

def fetch_all_idx_tickers() -> list[str]:
    """Ambil SEMUA kode emiten saham tercatat di IDX (~950).

    Sumber: GetCompanyProfiles (endpoint yang sama dengan profil per-emiten),
    difilter hanya efek saham (EfekEmiten_Saham). Returns ["AALI.JK", ...]
    urut alfabet, atau [] kalau gagal — caller wajib fallback.
    """
    data = _request_json(_PROFILE_URL, {
        "emitenType": "s", "start": 0, "length": 9999,
    })
    if not isinstance(data, dict):
        return []
    rows = data.get("data") or []
    out: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not row.get("EfekEmiten_Saham"):
            continue
        code = str(row.get("KodeEmiten") or "").strip().upper()
        if len(code) >= 4 and code.isalnum():
            out.add(f"{code}.JK")
    log.info("IDX universe: %d emiten saham", len(out))
    return sorted(out)


def get_idx_price_profile(ticker: str) -> dict | None:
    """Fetch price and profile from IDX unofficial API.

    Returns dict with ticker, name, sector, sub_sector, price,
    market_cap, volume, shares_outstanding, price_date.
    Returns None if IDX API completely unavailable.

    Never crashes — all failures logged and return None.
    """
    code = _to_bare_code(ticker)
    if not code:
        return None

    profile = None
    price_data = None

    try:
        profile = _fetch_profile(code)
    except Exception as exc:
        log.error("%s: _fetch_profile crashed: %s", code, exc)

    try:
        price_data = _fetch_price(code)
    except Exception as exc:
        log.error("%s: _fetch_price crashed: %s", code, exc)

    if profile is None and price_data is None:
        return None

    profile = profile or {}
    price_data = price_data or {}

    close = price_data.get("close")
    shares = price_data.get("listed_shares")
    market_cap = None
    if close and shares and close > 0 and shares > 0:
        market_cap = close * shares

    return {
        "ticker": f"{code}.JK",
        "name": profile.get("name", ""),
        "sector": profile.get("sector", SEKTOR.get(code, "Unknown")),
        "sub_sector": profile.get("sub_sector", ""),
        "price": close,
        "market_cap": market_cap,
        "volume": price_data.get("volume"),
        "shares_outstanding": shares,
        "price_date": price_data.get("date"),
    }
