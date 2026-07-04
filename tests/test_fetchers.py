import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from src.data.fetcher_idx_xlsx import (
    DataSourceError,
    STANDARD_COLUMNS,
    parse_idx_xlsx,
    get_idx_fundamental,
)


def _make_test_xlsx(tmp_path: Path, rows: list[dict]) -> Path:
    """Helper: write a minimal XLSX with given rows."""
    df = pd.DataFrame(rows)
    out = tmp_path / "test.xlsx"
    df.to_excel(out, index=False, engine="openpyxl")
    return out


class TestParseIdxXlsx:

    def test_kolom_standar(self, tmp_path):
        """DataFrame hasil parse punya semua kolom wajib."""
        xlsx = _make_test_xlsx(tmp_path, [
            {"kode_emiten": "BBCA", "per": 15.0, "pbv": 4.0, "eps": 500,
             "der": 5.5, "roe": 20.0, "div_yield": 0.03,
             "current_ratio": 1.2, "tato": 0.8, "npm": 25.0,
             "market_cap": 1_000_000_000_000},
        ])
        df = parse_idx_xlsx(xlsx)
        for col in STANDARD_COLUMNS:
            assert col in df.columns, f"Kolom '{col}' tidak ada di hasil parse"

    def test_ticker_format(self, tmp_path):
        """Semua ticker berformat XXXX.JK."""
        xlsx = _make_test_xlsx(tmp_path, [
            {"kode_emiten": "BBCA", "per": 15},
            {"kode_emiten": "BBRI.JK", "per": 10},
        ])
        df = parse_idx_xlsx(xlsx)
        for ticker in df["ticker"]:
            assert ticker.endswith(".JK"), f"Ticker '{ticker}' tidak berformat .JK"

    def test_nilai_none_bukan_crash(self, tmp_path):
        """Nilai tidak valid → None, bukan exception."""
        xlsx = _make_test_xlsx(tmp_path, [
            {"kode_emiten": "BBCA", "per": "INVALID", "pbv": "", "eps": None,
             "der": "N/A", "roe": "abc"},
        ])
        df = parse_idx_xlsx(xlsx)
        assert not df.empty or df.empty  # no crash is the test
        # Numeric columns with bad values should be NaN (pandas None)
        if not df.empty:
            assert pd.isna(df["pe"].iloc[0])
            assert pd.isna(df["der"].iloc[0])
            assert pd.isna(df["roe"].iloc[0])

    def test_filter_hanya_tickers_valid(self, tmp_path):
        """Hanya ticker yang ada di settings.TICKERS yang lolos."""
        xlsx = _make_test_xlsx(tmp_path, [
            {"kode_emiten": "BBCA", "per": 15},
            {"kode_emiten": "XXXX", "per": 10},  # not in TICKERS
        ])
        df = parse_idx_xlsx(xlsx)
        tickers = df["ticker"].tolist()
        assert "BBCA.JK" in tickers
        assert "XXXX.JK" not in tickers


class TestGetIdxFundamental:

    def test_pakai_cache(self, tmp_path):
        """Cache valid → tidak download ulang."""
        cache_df = pd.DataFrame([{
            "ticker": "BBCA.JK", "pe": 15.0, "pbv": 4.0, "eps": 500.0,
            "der": 5.5, "roe": 20.0, "dividend_yield": 0.03,
            "current_ratio": 1.2, "asset_turnover": 0.8,
            "net_profit_margin": 25.0, "market_cap": 1e12,
            "last_updated": datetime.now(),
        }])
        cache_file = tmp_path / "idx_fundamental.parquet"
        cache_df.to_parquet(cache_file, index=False)

        with patch(
            "src.data.fetcher_idx_xlsx._PARQUET_CACHE", cache_file
        ), patch(
            "src.data.fetcher_idx_xlsx.download_idx_xlsx"
        ) as mock_dl:
            result = get_idx_fundamental()
            mock_dl.assert_not_called()
            assert len(result) == 1
            assert result["ticker"].iloc[0] == "BBCA.JK"

    def test_fallback_bulan_lalu(self, tmp_path):
        """Bulan ini gagal → coba bulan lalu."""
        call_count = {"n": 0}

        def fake_download(year, month):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise DataSourceError("bulan ini gagal")
            xlsx = _make_test_xlsx(tmp_path, [
                {"kode_emiten": "BBRI", "per": 10},
            ])
            return xlsx

        cache_file = tmp_path / "idx_fundamental.parquet"

        with patch(
            "src.data.fetcher_idx_xlsx._PARQUET_CACHE", cache_file
        ), patch(
            "src.data.fetcher_idx_xlsx.download_idx_xlsx",
            side_effect=fake_download,
        ):
            result = get_idx_fundamental(force_refresh=True)
            assert call_count["n"] == 2
            assert "BBRI.JK" in result["ticker"].values

    def test_semua_gagal_raise_error(self, tmp_path):
        """Semua bulan gagal → raise DataSourceError."""
        cache_file = tmp_path / "nonexistent.parquet"

        with patch(
            "src.data.fetcher_idx_xlsx._PARQUET_CACHE", cache_file
        ), patch(
            "src.data.fetcher_idx_xlsx.download_idx_xlsx",
            side_effect=DataSourceError("gagal"),
        ):
            with pytest.raises(DataSourceError, match="Semua sumber"):
                get_idx_fundamental(force_refresh=True)


# ════════════════════════════════════════════════════════════
#  Yahoo Finance fetcher tests (all mocked — no internet)
# ════════════════════════════════════════════════════════════

from src.data.fetcher_yfinance import (
    fetch_yf_fundamental,
    fetch_yf_dividends,
    fetch_yf_ohlcv,
    get_yf_data,
)


class TestFetchYfFundamental:

    def test_der_normalisasi(self):
        """DER > 20 from Yahoo → divided by 100."""
        fake_info = {
            "regularMarketPrice": 5000,
            "debtToEquity": 235.0,  # Yahoo sends percent
            "returnOnEquity": 0.18,
            "profitMargins": 0.25,
            "priceToBook": 4.0,
            "trailingPE": 15.0,
            "trailingEps": 300,
            "marketCap": 1e12,
        }
        with patch(
            "src.data.fetcher_yfinance._safe_info", return_value=fake_info
        ):
            result = fetch_yf_fundamental("BBCA.JK")
            assert result["der"] == pytest.approx(2.35, rel=1e-3)

    def test_roe_normalisasi(self):
        """ROE decimal (0.18) → converted to 18.0."""
        fake_info = {
            "regularMarketPrice": 5000,
            "returnOnEquity": 0.18,
            "debtToEquity": 5.0,
            "profitMargins": 25.0,
            "priceToBook": 4.0,
            "trailingPE": 15.0,
            "trailingEps": 300,
            "marketCap": 1e12,
        }
        with patch(
            "src.data.fetcher_yfinance._safe_info", return_value=fake_info
        ):
            result = fetch_yf_fundamental("BBCA.JK")
            assert result["roe"] == pytest.approx(18.0, rel=1e-3)


class TestFetchYfDividends:

    def test_streak_consecutive(self):
        """Gap in dividend years → streak stops at gap."""
        dates = pd.to_datetime(["2024-06-01", "2023-06-01", "2021-06-01"])
        divs = pd.Series([100.0, 100.0, 100.0], index=dates)
        with patch(
            "src.data.fetcher_yfinance._safe_dividends", return_value=divs
        ):
            result = fetch_yf_dividends("TEST.JK", price=5000)
            assert result["div_streak"] == 2  # 2024,2023 consecutive; 2021 gap

    def test_yield_trap(self):
        """Yield > 20% → yield_ttm = None (threshold naik dari 10% via OOS-I1:
        bank IDX legitimate bisa yield 8-15%)."""
        now = pd.Timestamp.now()
        dates = pd.to_datetime([now - pd.DateOffset(months=3)])
        divs = pd.Series([1250.0], index=dates)
        with patch(
            "src.data.fetcher_yfinance._safe_dividends", return_value=divs
        ):
            result = fetch_yf_dividends("TEST.JK", price=5000)
            # 1250/5000 = 25% → yield trap
            assert result["yield_ttm"] is None

    def test_yield_tinggi_tapi_wajar_tidak_kena_trap(self):
        """Yield 15% (bank IDX high-yield) TIDAK boleh di-trap."""
        now = pd.Timestamp.now()
        dates = pd.to_datetime([now - pd.DateOffset(months=3)])
        divs = pd.Series([750.0], index=dates)
        with patch(
            "src.data.fetcher_yfinance._safe_dividends", return_value=divs
        ):
            result = fetch_yf_dividends("TEST.JK", price=5000)
            # 750/5000 = 15% → dipertahankan (decimal 0.15)
            assert result["yield_ttm"] == 0.15

    def test_window_normalisasi(self):
        """15-month window → normalised to 12 months."""
        now = pd.Timestamp.now()
        dates = pd.to_datetime([now - pd.DateOffset(months=13)])
        divs = pd.Series([150.0], index=dates)
        with patch(
            "src.data.fetcher_yfinance._safe_dividends", return_value=divs
        ):
            result = fetch_yf_dividends("TEST.JK", price=5000)
            expected_amount = 150.0 / 15 * 12
            assert result["div_amount_ttm"] == pytest.approx(expected_amount, rel=1e-2)


class TestGetYfData:

    def test_tidak_crash_jika_satu_gagal(self):
        """If fetch_yf_ohlcv fails → dict still returns, ohlcv=None."""
        fake_info = {
            "regularMarketPrice": 5000,
            "debtToEquity": 100.0,
            "returnOnEquity": 0.15,
            "profitMargins": 0.20,
            "priceToBook": 3.0,
            "trailingPE": 12.0,
            "trailingEps": 400,
            "marketCap": 5e11,
        }
        with patch(
            "src.data.fetcher_yfinance._safe_info", return_value=fake_info
        ), patch(
            "src.data.fetcher_yfinance._safe_download",
            side_effect=Exception("network error"),
        ), patch(
            "src.data.fetcher_yfinance._safe_dividends",
            return_value=pd.Series(dtype=float),
        ), patch(
            "src.data.fetcher_yfinance._safe_financials",
            return_value=pd.DataFrame(),
        ):
            result = get_yf_data("TEST.JK")
            assert result["ticker"] == "TEST.JK"
            assert result["ohlcv"] is None
            assert result["fundamental"] is not None
            assert result["fundamental"]["price"] == 5000
            assert 0.0 <= result["data_completeness"] <= 1.0


# ════════════════════════════════════════════════════════════
#  IDX API fetcher tests (all mocked — no internet)
# ════════════════════════════════════════════════════════════

from src.data.fetcher_idx_api import (
    get_idx_price_profile,
    _normalize_sector,
    _to_bare_code,
    _clear_all_caches,
)
import src.data.fetcher_idx_api as _idx_api_mod


@pytest.fixture(autouse=True)
def _reset_idx_cache():
    """Clear IDX API caches and circuit breaker before each test."""
    _clear_all_caches()
    _idx_api_mod._cb_failures = 0
    _idx_api_mod._cb_open_at = 0.0
    yield
    _clear_all_caches()
    _idx_api_mod._cb_failures = 0
    _idx_api_mod._cb_open_at = 0.0


def _fake_securities_response(code="BBCA", name="Bank Central Asia Tbk.",
                               sector="Keuangan", sub_sector="Bank"):
    """Build a fake GetCompanyProfiles JSON response."""
    return {
        "draw": 0,
        "recordsTotal": 1,
        "recordsFiltered": 1,
        "data": [{
            "KodeEmiten": code,
            "NamaEmiten": name,
            "Sektor": sector,
            "SubSektor": sub_sector,
            "PapanPencatatan": "Utama",
            "TanggalPencatatan": "2000-05-31T00:00:00",
        }],
    }


def _fake_summary_response(code="BBCA", close=9875, volume=45_000_000,
                            value=446_000_000_000, listed_shares=24_655_010_000):
    """Build a fake GetTradingInfoSS JSON response."""
    return {
        "KodeEmiten": code,
        "replies": [{
            "No": 1,
            "StockCode": code,
            "StockName": "Bank Central Asia Tbk.",
            "Date": "2026-06-26T00:00:00",
            "Previous": 9850,
            "OpenPrice": 9850,
            "FirstTrade": 9850,
            "High": 9900,
            "Low": 9825,
            "Close": close,
            "Change": 25,
            "Volume": volume,
            "Value": value,
            "Frequency": 12345,
            "ListedShares": listed_shares,
        }],
    }


class TestGetIdxPriceProfile:

    def test_success_full_data(self):
        """Both API calls succeed → complete dict with all fields."""
        with patch(
            "src.data.fetcher_idx_api._request_json",
            side_effect=[
                _fake_securities_response(),
                _fake_summary_response(),
            ],
        ):
            result = get_idx_price_profile("BBCA.JK")

        assert result is not None
        assert result["ticker"] == "BBCA.JK"
        assert result["name"] == "Bank Central Asia Tbk."
        assert result["sector"] == "Financials"
        assert result["price"] == 9875
        assert result["volume"] == 45_000_000
        assert result["shares_outstanding"] == 24_655_010_000
        expected_mcap = 9875 * 24_655_010_000
        assert result["market_cap"] == expected_mcap

    def test_api_down_returns_none(self):
        """Both API calls fail → returns None, no crash."""
        with patch(
            "src.data.fetcher_idx_api._request_json",
            return_value=None,
        ):
            result = get_idx_price_profile("BBCA.JK")

        assert result is None

    def test_partial_data_price_unavailable(self):
        """Profile works but price fails → dict with price=None."""
        with patch(
            "src.data.fetcher_idx_api._request_json",
            side_effect=[
                _fake_securities_response(),
                None,
            ],
        ):
            result = get_idx_price_profile("BBCA.JK")

        assert result is not None
        assert result["name"] == "Bank Central Asia Tbk."
        assert result["price"] is None
        assert result["market_cap"] is None
        assert result["shares_outstanding"] is None

    def test_ticker_not_in_response(self):
        """API returns data but our ticker is not in it."""
        wrong_response = _fake_securities_response(code="TLKM")
        with patch(
            "src.data.fetcher_idx_api._request_json",
            side_effect=[wrong_response, None],
        ):
            result = get_idx_price_profile("BBCA.JK")

        assert result is None or result.get("name") == ""


    def test_price_caching(self):
        """Second call for same ticker uses price cache — no extra API call."""
        call_count = {"n": 0}
        responses = [
            _fake_securities_response(),
            _fake_summary_response(),
        ]

        def tracked_request(url, params):
            call_count["n"] += 1
            if call_count["n"] <= len(responses):
                return responses[call_count["n"] - 1]
            return None

        with patch(
            "src.data.fetcher_idx_api._request_json",
            side_effect=tracked_request,
        ):
            result1 = get_idx_price_profile("BBCA.JK")
            calls_after_first = call_count["n"]

            result2 = get_idx_price_profile("BBCA.JK")
            calls_after_second = call_count["n"]

        assert result1["price"] == 9875
        assert result2["price"] == 9875
        assert calls_after_second == calls_after_first


class TestNormalizeSector:

    def test_exact_match(self):
        assert _normalize_sector("Financials") == "Financials"
        assert _normalize_sector("Energy") == "Energy"

    def test_case_insensitive(self):
        assert _normalize_sector("FINANCIALS") == "Financials"
        assert _normalize_sector("energy") == "Energy"

    def test_alias_mapping(self):
        assert _normalize_sector("Finance") == "Financials"
        assert _normalize_sector("Banking") == "Financials"
        assert _normalize_sector("Coal Mining") == "Energy"
        assert _normalize_sector("Real Estate") == "Property & Real Estate"

    def test_indonesian_sector_names(self):
        assert _normalize_sector("Keuangan") == "Financials"
        assert _normalize_sector("Energi") == "Energy"
        assert _normalize_sector("Kesehatan") == "Healthcare"
        assert _normalize_sector("Teknologi") == "Technology"

    def test_unknown_returns_unknown(self):
        assert _normalize_sector("") == "Unknown"
        assert _normalize_sector("SomethingNew") == "Unknown"


class TestToBareCode:

    def test_strips_jk_suffix(self):
        assert _to_bare_code("BBCA.JK") == "BBCA"

    def test_already_bare(self):
        assert _to_bare_code("BBCA") == "BBCA"

    def test_lowercase_normalized(self):
        assert _to_bare_code("bbca.jk") == "BBCA"


# ════════════════════════════════════════════════════════════
#  Merger tests (all sources mocked — no internet)
# ════════════════════════════════════════════════════════════

from src.data.merger import get_merged_data, get_all_merged

# ── Fake data builders ────────────────────────────────────────

def _fake_idx_xlsx_df(ticker="BBCA.JK", pe=15.0, pbv=4.0, roe=20.0,
                       der=5.5, npm=25.0, cr=1.2, at=0.8, eps=500.0,
                       div_yield=0.03, mcap=1e12):
    """Build a minimal IDX XLSX DataFrame for one ticker."""
    from datetime import datetime
    return pd.DataFrame([{
        "ticker": ticker, "pe": pe, "pbv": pbv, "eps": eps,
        "der": der, "roe": roe, "dividend_yield": div_yield,
        "current_ratio": cr, "asset_turnover": at,
        "net_profit_margin": npm, "market_cap": mcap,
        "last_updated": datetime.now(),
    }])


def _fake_yf_data(price=9875, pe=14.5, pbv=3.8, roe=19.0, der=5.2,
                   npm=24.0, cr=1.1, at=None, mcap=9.5e11, eps=480,
                   yield_ttm=0.028, div_streak=5, div_amount=280,
                   rev_cagr=0.08, earn_cagr=0.12, rev_yoy=0.10,
                   earn_yoy=0.15, prof_years=4, rev_trend="growing",
                   has_ohlcv=True):
    """Build a fake get_yf_data result."""
    ohlcv = None
    if has_ohlcv:
        import numpy as np
        idx = pd.date_range("2023-01-01", periods=100, freq="B")
        ohlcv = {
            "close": pd.Series(np.random.uniform(9000, 10000, 100), index=idx),
            "high": pd.Series(np.random.uniform(9500, 10500, 100), index=idx),
            "low": pd.Series(np.random.uniform(8500, 9500, 100), index=idx),
            "volume": pd.Series(np.random.uniform(1e7, 5e7, 100), index=idx),
        }
    return {
        "ticker": "BBCA.JK",
        "fundamental": {
            "pe": pe, "pbv": pbv, "eps": eps, "der": der, "roe": roe,
            "net_profit_margin": npm, "current_ratio": cr,
            "asset_turnover": at, "market_cap": mcap,
            "dividend_yield": None, "price": price,
        },
        "ohlcv": ohlcv,
        "dividends": {
            "yield_ttm": yield_ttm, "div_streak": div_streak,
            "div_amount_ttm": div_amount,
        },
        "growth": {
            "revenue_cagr_3y": rev_cagr, "earnings_cagr_3y": earn_cagr,
            "revenue_yoy": rev_yoy, "earnings_yoy": earn_yoy,
            "profitable_years": prof_years, "revenue_trend": rev_trend,
        },
        "data_completeness": 0.85,
    }


def _fake_idx_api(price=9900, name="Bank Central Asia Tbk.",
                   sector="Financials", sub="Bank", mcap=2.4e14,
                   volume=45000000, shares=24655010000):
    """Build a fake get_idx_price_profile result."""
    return {
        "ticker": "BBCA.JK", "name": name, "sector": sector,
        "sub_sector": sub, "price": price, "market_cap": mcap,
        "volume": volume, "shares_outstanding": shares,
        "price_date": "2025-06-27",
    }


class TestMergerReconciliation:

    def test_idx_xlsx_priority_over_yahoo(self):
        """IDX XLSX values win over Yahoo Finance for overlapping fields."""
        with patch(
            "src.data.merger._get_idx_xlsx_row",
            return_value={"pe": 15.0, "pbv": 4.0, "roe": 20.0, "der": 5.5,
                          "net_profit_margin": 25.0, "current_ratio": 1.2,
                          "asset_turnover": 0.8, "eps": 500.0,
                          "dividend_yield": 0.03, "market_cap": 1e12},
        ), patch(
            "src.data.merger._get_idx_api",
            return_value=_fake_idx_api(),
        ), patch(
            "src.data.merger._get_yf",
            return_value=_fake_yf_data(pe=14.5, roe=19.0),
        ):
            result = get_merged_data("BBCA.JK")

        assert result["pe"] == 15.0      # IDX wins over Yahoo 14.5
        assert result["roe"] == 20.0     # IDX wins over Yahoo 19.0
        assert result["price"] == 9900   # IDX API wins for price

    def test_yahoo_fallback_when_idx_missing(self):
        """IDX XLSX returns None → Yahoo Finance fills in."""
        with patch(
            "src.data.merger._get_idx_xlsx_row",
            return_value=None,
        ), patch(
            "src.data.merger._get_idx_api",
            return_value=None,
        ), patch(
            "src.data.merger._get_yf",
            return_value=_fake_yf_data(pe=14.5, roe=19.0, price=9875),
        ):
            result = get_merged_data("BBCA.JK")

        assert result["pe"] == 14.5
        assert result["roe"] == 19.0
        assert result["price"] == 9875


class TestMergerConflict:

    def test_conflict_detected_and_logged(self, tmp_path):
        """IDX vs Yahoo differ >5% → use IDX, log conflict."""
        conflict_log = tmp_path / "conflict_log.jsonl"
        with patch(
            "src.data.merger._get_idx_xlsx_row",
            return_value={"pe": 15.0, "pbv": 4.0, "roe": 20.0, "der": 5.5,
                          "net_profit_margin": 25.0, "current_ratio": 1.2,
                          "asset_turnover": 0.8, "eps": 500.0,
                          "dividend_yield": 0.03, "market_cap": 1e12},
        ), patch(
            "src.data.merger._get_idx_api",
            return_value=_fake_idx_api(),
        ), patch(
            "src.data.merger._get_yf",
            return_value=_fake_yf_data(pe=10.0, roe=12.0),  # >5% diff
        ), patch(
            "src.data.merger._CONFLICT_LOG", conflict_log,
        ):
            result = get_merged_data("BBCA.JK")

        assert result["pe"] == 15.0  # IDX wins
        assert result["roe"] == 20.0  # IDX wins
        assert conflict_log.exists()
        import json
        lines = conflict_log.read_text().strip().split("\n")
        assert len(lines) >= 2  # pe and roe both conflict
        entry = json.loads(lines[0])
        assert "ticker" in entry
        assert "diff_pct" in entry


class TestMergerBatch:

    def test_batch_survives_one_ticker_failure(self):
        """One ticker crashes → others still succeed."""
        call_count = {"n": 0}

        def fake_yf(ticker):
            call_count["n"] += 1
            if ticker == "FAIL.JK":
                raise ConnectionError("timeout")
            return _fake_yf_data()

        with patch(
            "src.data.merger._get_idx_xlsx_row", return_value=None,
        ), patch(
            "src.data.merger._get_idx_api", return_value=None,
        ), patch(
            "src.data.merger._get_yf", side_effect=fake_yf,
        ):
            results = get_all_merged(["BBCA.JK", "FAIL.JK", "BBRI.JK"])

        assert "BBCA.JK" in results
        assert "FAIL.JK" in results
        assert "BBRI.JK" in results
        assert results["BBCA.JK"]["price"] == 9875
        assert results["BBRI.JK"]["price"] == 9875
        # FAIL.JK still returns a dict, just with None values
        assert results["FAIL.JK"]["price"] is None


class TestMergerCompleteness:

    def test_data_completeness_calculation(self):
        """Full data → high completeness; no data → low completeness."""
        with patch(
            "src.data.merger._get_idx_xlsx_row",
            return_value={"pe": 15.0, "pbv": 4.0, "roe": 20.0, "der": 5.5,
                          "net_profit_margin": 25.0, "current_ratio": 1.2,
                          "asset_turnover": 0.8, "eps": 500.0,
                          "dividend_yield": 0.03, "market_cap": 1e12},
        ), patch(
            "src.data.merger._get_idx_api",
            return_value=_fake_idx_api(),
        ), patch(
            "src.data.merger._get_yf",
            return_value=_fake_yf_data(),
        ):
            full = get_merged_data("BBCA.JK")

        with patch(
            "src.data.merger._get_idx_xlsx_row", return_value=None,
        ), patch(
            "src.data.merger._get_idx_api", return_value=None,
        ), patch(
            "src.data.merger._get_yf", return_value=None,
        ):
            empty = get_merged_data("XXXX.JK")

        assert full["data_completeness"] >= 0.8
        assert empty["data_completeness"] == 0.0
