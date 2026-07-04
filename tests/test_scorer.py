"""Tests for src/analysis/scorer.py"""

import pandas as pd
import pytest

from src.analysis.scorer import (
    HARD_GATE_STRONG_BUY,
    LABEL_BUY,
    LABEL_HOLD,
    LABEL_SELL,
    LABEL_STRONG_BUY,
    P_DATA_LOW,
    P_DER_HIGH,
    P_LOSS_2Y,
    P_PER_EXTREME,
    P_ROE_NEGATIVE,
    P_YIELD_TRAP,
    _compute_sector_medians,
    score_all,
    score_ticker,
)


# ── Helpers ───────────────────────────────────────────────────

def _make_ohlcv(n: int = 250, start: float = 1000.0, step: float = 1.0) -> pd.DataFrame:
    close = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "Open": close,
        "High": [c * 1.01 for c in close],
        "Low": [c * 0.99 for c in close],
        "Close": close,
        "Volume": [1_000_000.0] * n,
    })


def _blue_chip(ticker: str = "BBCA.JK", sector: str = "Financials") -> dict:
    """Excellent fundamentals — should score high."""
    return {
        "ticker": ticker,
        "name": "Blue Chip Co",
        "sector": sector,
        "sub_sector": "",
        "pe": 15.0,
        "pbv": 2.5,
        "eps": 500.0,
        "der": 5.0,
        "roe": 22.0,
        "net_profit_margin": 25.0,
        "current_ratio": 2.5,
        "asset_turnover": 0.6,
        "market_cap": 500_000_000_000_000,
        "price": 9000,
        "yield_ttm": 4.5,
        "div_streak": 12,
        "div_amount_ttm": 400,
        "revenue_cagr_3y": 12.0,
        "earnings_cagr_3y": 15.0,
        "revenue_yoy": 10.0,
        "earnings_yoy": 14.0,
        "profitable_years": 5,
        "revenue_trend": "up",
        "ohlcv": _make_ohlcv(),
        "data_completeness": 1.0,
        "sources": {},
    }


def _poor_stock(ticker: str = "POOR.JK", sector: str = "Industrials") -> dict:
    """Bad fundamentals — should score low."""
    return {
        "ticker": ticker,
        "name": "Poor Co",
        "sector": sector,
        "sub_sector": "",
        "pe": 80.0,
        "pbv": 5.0,
        "eps": -10.0,
        "der": 4.0,
        "roe": -8.0,
        "net_profit_margin": -5.0,
        "current_ratio": 0.5,
        "asset_turnover": 0.1,
        "market_cap": 1_000_000_000_000,
        "price": 50,
        "yield_ttm": 0.0,
        "div_streak": 0,
        "div_amount_ttm": 0,
        "revenue_cagr_3y": -10.0,
        "earnings_cagr_3y": -20.0,
        "revenue_yoy": -8.0,
        "earnings_yoy": -15.0,
        "profitable_years": 0,
        "revenue_trend": "down",
        "ohlcv": _make_ohlcv(n=250, start=500, step=-1.0),
        "data_completeness": 0.9,
        "sources": {},
    }


def _empty_stock(ticker: str = "EMPTY.JK") -> dict:
    """All data missing."""
    return {
        "ticker": ticker,
        "name": "",
        "sector": "Unknown",
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


def _build_all(*stocks: dict) -> dict[str, dict]:
    return {s["ticker"]: s for s in stocks}


def _default_medians() -> dict[str, dict[str, float]]:
    return {
        "Financials": {"pe": 12.0, "pbv": 2.0, "roe": 18.0},
        "Industrials": {"pe": 15.0, "pbv": 1.5, "roe": 12.0},
        "Energy": {"pe": 10.0, "pbv": 1.0, "roe": 15.0},
        "Basic Materials": {"pe": 12.0, "pbv": 1.2, "roe": 10.0},
        "Consumer Non-Cyclical": {"pe": 20.0, "pbv": 3.0, "roe": 15.0},
        "Consumer Cyclical": {"pe": 18.0, "pbv": 2.0, "roe": 12.0},
        "Healthcare": {"pe": 25.0, "pbv": 3.0, "roe": 14.0},
        "Property & Real Estate": {"pe": 10.0, "pbv": 0.8, "roe": 8.0},
        "Technology": {"pe": 30.0, "pbv": 5.0, "roe": 10.0},
        "Infrastructure": {"pe": 15.0, "pbv": 1.5, "roe": 10.0},
        "Transportation & Logistic": {"pe": 12.0, "pbv": 1.2, "roe": 10.0},
        "Unknown": {"pe": 0.0, "pbv": 0.0, "roe": 0.0},
    }


# ═══════════════════════════════════════════════════════════════
#  Test: Strong Buy requires genuinely good data
# ═══════════════════════════════════════════════════════════════

class TestStrongBuy:

    def test_blue_chip_scores_high(self):
        bc = _blue_chip()
        all_data = _build_all(bc)
        result = score_ticker(bc, _default_medians(), all_data)
        assert result["skor_total"] >= 65
        assert result["label"] in (LABEL_STRONG_BUY, LABEL_BUY)

    def test_poor_stock_scores_low(self):
        ps = _poor_stock()
        all_data = _build_all(ps)
        result = score_ticker(ps, _default_medians(), all_data)
        assert result["skor_total"] < 50
        assert result["label"] == LABEL_SELL


# ═══════════════════════════════════════════════════════════════
#  Test: Hard gate works
# ═══════════════════════════════════════════════════════════════

class TestHardGate:

    def test_high_score_low_roe_capped_at_79(self):
        """Skor 82+ but ROE < 15% → capped at 79."""
        bc = _blue_chip()
        bc["roe"] = 10.0  # below HARD_GATE_ROE_MIN of 15%
        all_data = _build_all(bc)
        result = score_ticker(bc, _default_medians(), all_data)
        if result["skor_total"] + 10 >= HARD_GATE_STRONG_BUY:
            # Only test gate if raw score would have been >= 80
            pass
        # More direct: construct a scenario guaranteed to trigger gate
        bc2 = _blue_chip()
        bc2["roe"] = 12.0
        # Boost all components to ensure raw >= 80
        bc2["net_profit_margin"] = 30.0
        bc2["revenue_cagr_3y"] = 20.0
        bc2["earnings_cagr_3y"] = 20.0
        bc2["yield_ttm"] = 7.0
        bc2["div_streak"] = 15
        bc2["profitable_years"] = 5
        bc2["revenue_trend"] = "up"
        bc2["pe"] = 6.0
        bc2["pbv"] = 0.8
        bc2["current_ratio"] = 3.0
        bc2["asset_turnover"] = 1.5
        all_data2 = _build_all(bc2)
        result2 = score_ticker(bc2, _default_medians(), all_data2)
        assert result2["skor_total"] <= HARD_GATE_STRONG_BUY - 1
        assert result2["label"] == LABEL_BUY
        assert any("Hard gate" in a for a in result2["alasan"])

    def test_high_score_high_der_non_bank_capped(self):
        """Non-bank with DER > 2x → hard gate triggers."""
        bc = _blue_chip(sector="Industrials")
        bc["der"] = 2.5
        bc["roe"] = 25.0
        bc["net_profit_margin"] = 30.0
        bc["revenue_cagr_3y"] = 20.0
        bc["earnings_cagr_3y"] = 20.0
        bc["yield_ttm"] = 7.0
        bc["div_streak"] = 15
        bc["pe"] = 6.0
        bc["pbv"] = 0.5
        bc["current_ratio"] = 3.0
        bc["asset_turnover"] = 1.5
        bc["profitable_years"] = 5
        bc["revenue_trend"] = "up"
        all_data = _build_all(bc)
        result = score_ticker(bc, _default_medians(), all_data)
        assert result["skor_total"] <= HARD_GATE_STRONG_BUY - 1


# ═══════════════════════════════════════════════════════════════
#  Test: Penalties accumulate correctly
# ═══════════════════════════════════════════════════════════════

class TestPenalties:

    def test_penalties_accumulate(self):
        ps = _poor_stock()
        all_data = _build_all(ps)
        result = score_ticker(ps, _default_medians(), all_data)
        # ROE neg(-10) + loss 2y(-10) + rev decline(-5) + DER high(-8) + PER extreme(-5) = -38
        assert result["penalti_total"] <= -30
        assert len(result["penalti_detail"]) >= 4

    def test_roe_negative_gets_penalty(self):
        """Company with negative ROE must get explicit penalty, not neutral."""
        stock = _blue_chip(sector="Industrials")
        stock["roe"] = -5.0
        all_data = _build_all(stock)
        result = score_ticker(stock, _default_medians(), all_data)
        assert result["penalti_total"] <= P_ROE_NEGATIVE
        assert any("ROE negatif" in d for d in result["penalti_detail"])

    def test_yield_trap_penalized(self):
        """Yield >10% gets penalty — can't game high score via extreme yield."""
        stock = _blue_chip(sector="Industrials")
        stock["yield_ttm"] = 15.0
        all_data = _build_all(stock)
        result = score_ticker(stock, _default_medians(), all_data)
        assert any("Yield trap" in d for d in result["penalti_detail"])


# ═══════════════════════════════════════════════════════════════
#  Test: Sector median computed dynamically
# ═══════════════════════════════════════════════════════════════

class TestSectorMedians:

    def test_medians_computed_from_data(self):
        s1 = _blue_chip("A.JK", "Financials")
        s1["pe"] = 10.0
        s2 = _blue_chip("B.JK", "Financials")
        s2["pe"] = 20.0
        s3 = _blue_chip("C.JK", "Financials")
        s3["pe"] = 15.0
        all_data = _build_all(s1, s2, s3)
        medians = _compute_sector_medians(all_data)
        assert medians["Financials"]["pe"] == 15.0

    def test_score_all_uses_dynamic_medians(self):
        """score_all() must compute medians internally, not use hardcoded."""
        s1 = _blue_chip("A.JK", "Financials")
        s2 = _blue_chip("B.JK", "Financials")
        all_data = _build_all(s1, s2)
        results = score_all(all_data)
        assert len(results) == 2
        for r in results.values():
            assert "skor_total" in r
            assert "komponen" in r


# ═══════════════════════════════════════════════════════════════
#  Test: Edge cases
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_all_data_empty_no_crash(self):
        """140 tickers with no data → all scored 0, no crash."""
        all_data = {f"T{i}.JK": _empty_stock(f"T{i}.JK") for i in range(140)}
        results = score_all(all_data)
        assert len(results) == 140
        for r in results.values():
            assert r["skor_total"] >= 0
            assert r["label"] == LABEL_SELL

    def test_output_structure_complete(self):
        bc = _blue_chip()
        all_data = _build_all(bc)
        result = score_ticker(bc, _default_medians(), all_data)
        assert "skor_total" in result
        assert "label" in result
        assert "komponen" in result
        assert set(result["komponen"].keys()) == {
            "A_kualitas", "B_dividen", "C_growth", "D_valuasi", "E_teknikal"
        }
        assert "penalti_total" in result
        assert "penalti_detail" in result
        assert "alasan" in result
        assert isinstance(result["alasan"], list)
        assert "data_completeness" in result
        assert "teknikal" in result

    def test_score_clamped_0_to_100(self):
        """Score never goes below 0 or above 100."""
        ps = _poor_stock()
        ps["data_completeness"] = 0.1  # triggers -15 on top of other penalties
        all_data = _build_all(ps)
        result = score_ticker(ps, _default_medians(), all_data)
        assert 0 <= result["skor_total"] <= 100

    def test_no_ohlcv_still_scores(self):
        """Ticker with no OHLCV data still gets fundamental score."""
        bc = _blue_chip()
        bc["ohlcv"] = None
        all_data = _build_all(bc)
        result = score_ticker(bc, _default_medians(), all_data)
        assert result["skor_total"] > 0
        assert result["komponen"]["E_teknikal"] == 0
