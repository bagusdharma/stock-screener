import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from src.config.settings import (
    CACHE_DIR,
    IDX_XLSX_CACHE_DAYS,
    IDX_XLSX_URL,
    TICKERS,
)

try:
    from curl_cffi import requests as http_client
    _USE_CURL_CFFI = True
except ImportError:
    import requests as http_client
    _USE_CURL_CFFI = False

log = logging.getLogger(__name__)

STANDARD_COLUMNS = [
    "ticker",
    "pe",
    "pbv",
    "eps",
    "der",
    "roe",
    "dividend_yield",
    "current_ratio",
    "asset_turnover",
    "net_profit_margin",
    "market_cap",
    "last_updated",
]

_XLSX_DIR = Path(CACHE_DIR) / "idx_xlsx"
_PARQUET_CACHE = Path(CACHE_DIR) / "idx_fundamental.parquet"

_MAX_RETRIES = 2
_RETRY_DELAY = 3
_REQUEST_TIMEOUT = 20
_MONTHS_LOOKBACK = 6

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/octet-stream,*/*"
    ),
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.idx.co.id/id/data-pasar/laporan-keuangan/",
    "Origin": "https://www.idx.co.id",
}

_COLUMN_MAP = {
    "kode_emiten": "ticker",
    "kode": "ticker",
    "ticker": "ticker",
    "pe_ratio": "pe",
    "per": "pe",
    "p/e": "pe",
    "pbv": "pbv",
    "p/bv": "pbv",
    "price_to_bv": "pbv",
    "eps": "eps",
    "earning_per_share": "eps",
    "der": "der",
    "debt_to_equity": "der",
    "roe": "roe",
    "return_on_equity": "roe",
    "dividend_yield": "dividend_yield",
    "div_yield": "dividend_yield",
    "yield": "dividend_yield",
    "current_ratio": "current_ratio",
    "cr": "current_ratio",
    "asset_turnover": "asset_turnover",
    "tato": "asset_turnover",
    "total_asset_turnover": "asset_turnover",
    "net_profit_margin": "net_profit_margin",
    "npm": "net_profit_margin",
    "profit_margin": "net_profit_margin",
    "market_cap": "market_cap",
    "market_capitalization": "market_cap",
    "mkt_cap": "market_cap",
}


class DataSourceError(Exception):
    pass


class EndpointChangedError(DataSourceError):
    """IDX endpoint no longer returns XLSX — structural change, not transient."""
    pass


def _do_get(url: str, params: dict, timeout: int = _REQUEST_TIMEOUT):
    """HTTP GET with curl_cffi (preferred) or requests fallback."""
    if _USE_CURL_CFFI:
        return http_client.get(
            url, params=params, impersonate="chrome", timeout=timeout,
        )
    session = http_client.Session()
    session.headers.update(_HEADERS)
    return session.get(url, params=params, timeout=timeout)


def download_idx_xlsx(year: int, month: int) -> Path:
    """Download IDX Financial Data & Ratio XLSX for a given month.

    Saves to cache/idx_xlsx/YYYY_MM.xlsx.
    Returns file path on success.
    Raises EndpointChangedError if IDX returns non-XLSX (no retry needed).
    Raises DataSourceError after retries for transient failures.
    """
    _XLSX_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{year:04d}_{month:02d}.xlsx"
    dest = _XLSX_DIR / filename

    if dest.exists():
        age = datetime.now() - datetime.fromtimestamp(dest.stat().st_mtime)
        if age < timedelta(days=IDX_XLSX_CACHE_DAYS):
            log.debug("IDX XLSX cache hit: %s (age %d days)", filename, age.days)
            return dest
        log.debug("IDX XLSX cache expired: %s (age %d days)", filename, age.days)

    params = {"year": year, "month": month, "format": "xlsx"}
    last_err = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = _do_get(IDX_XLSX_URL, params)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "json" in content_type or "text/html" in content_type:
                raise EndpointChangedError(
                    f"IDX returned {content_type} instead of XLSX"
                )

            if len(resp.content) < 1024:
                raise DataSourceError(
                    f"Response too small ({len(resp.content)} bytes)"
                )

            dest.write_bytes(resp.content)
            log.info("IDX XLSX saved: %s (%d bytes)", dest, len(resp.content))
            return dest

        except EndpointChangedError:
            raise

        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY)

    raise DataSourceError(
        f"IDX XLSX {year}-{month:02d} gagal setelah {_MAX_RETRIES} retry: {last_err}"
    )


def parse_idx_xlsx(file_path: Path) -> pd.DataFrame:
    """Parse IDX Financial Data & Ratio XLSX into a standardised DataFrame.

    Returns DataFrame with columns: ticker, pe, pbv, eps, der, roe,
    dividend_yield, current_ratio, asset_turnover, net_profit_margin,
    market_cap, last_updated.

    Invalid values become None instead of raising exceptions.
    """
    try:
        raw = pd.read_excel(file_path, engine="openpyxl")
    except Exception as e:
        raise DataSourceError(f"Gagal membaca XLSX {file_path}: {e}") from e

    raw.columns = [str(c).strip().lower().replace(" ", "_") for c in raw.columns]

    rename = {}
    for raw_col in raw.columns:
        if raw_col in _COLUMN_MAP:
            rename[raw_col] = _COLUMN_MAP[raw_col]
    raw = raw.rename(columns=rename)

    for col in STANDARD_COLUMNS:
        if col not in raw.columns:
            log.warning("IDX XLSX: kolom '%s' tidak ditemukan, diisi None", col)
            raw[col] = None

    numeric_cols = [
        "pe", "pbv", "eps", "der", "roe", "dividend_yield",
        "current_ratio", "asset_turnover", "net_profit_margin", "market_cap",
    ]
    for col in numeric_cols:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")

    if "ticker" in raw.columns and raw["ticker"].notna().any():
        raw["ticker"] = raw["ticker"].astype(str).str.strip().str.upper()
        raw["ticker"] = raw["ticker"].apply(
            lambda t: t if t.endswith(".JK") else f"{t}.JK"
        )

    raw["last_updated"] = datetime.now()

    from src.data.universe import get_universe
    valid_set = set(get_universe())
    df = raw.loc[raw["ticker"].isin(valid_set), STANDARD_COLUMNS].copy()
    df = df.reset_index(drop=True)

    log.info(
        "IDX XLSX parsed: %d rows (dari %d total, %d match TICKERS)",
        len(df), len(raw), len(df),
    )
    return df


def get_idx_fundamental(force_refresh: bool = False) -> pd.DataFrame:
    """Entry point — returns IDX fundamental data as a DataFrame.

    1. Check cache/idx_fundamental.parquet
    2. Cache valid (< IDX_XLSX_CACHE_DAYS) -> load & return
    3. Not valid -> download XLSX, trying up to 6 months back
    4. EndpointChangedError -> bail immediately (structural, not transient)
    5. All fail -> raise DataSourceError
    """
    if not force_refresh and _PARQUET_CACHE.exists():
        age = datetime.now() - datetime.fromtimestamp(
            _PARQUET_CACHE.stat().st_mtime
        )
        if age < timedelta(days=IDX_XLSX_CACHE_DAYS):
            log.debug(
                "IDX fundamental cache hit: parquet (age %d days)", age.days,
            )
            return pd.read_parquet(_PARQUET_CACHE)
        log.debug("IDX fundamental cache expired (age %d days)", age.days)

    now = datetime.now()
    months_to_try = []
    cursor = now
    for _ in range(_MONTHS_LOOKBACK):
        months_to_try.append((cursor.year, cursor.month))
        cursor = cursor.replace(day=1) - timedelta(days=1)

    last_err = None
    for year, month in months_to_try:
        try:
            xlsx_path = download_idx_xlsx(year, month)
            df = parse_idx_xlsx(xlsx_path)
            if df.empty:
                log.warning(
                    "IDX XLSX %d-%02d parsed tapi kosong, coba bulan lain",
                    year, month,
                )
                continue

            _PARQUET_CACHE.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(_PARQUET_CACHE, index=False)
            log.info(
                "IDX fundamental updated: %d rows dari %d-%02d, cache saved",
                len(df), year, month,
            )
            return df

        except EndpointChangedError as e:
            log.warning(
                "IDX XLSX endpoint changed — skipping all months: %s", e,
            )
            raise DataSourceError(
                f"IDX XLSX endpoint no longer provides XLSX: {e}"
            ) from e

        except DataSourceError as e:
            last_err = e
            log.warning("IDX XLSX %d-%02d gagal: %s", year, month, e)
            continue

    raise DataSourceError(
        f"Semua sumber IDX XLSX gagal. Error terakhir: {last_err}"
    )
