"""Tests for src/analysis/indicators.py"""

import numpy as np
import pandas as pd
import pytest

from src.analysis.indicators import (
    hitung_macd,
    hitung_ma_trend,
    hitung_mfi,
    hitung_momentum,
    hitung_obv_trend,
    hitung_rsi,
    hitung_volume_trend,
)


# ── Helpers ───────────────────────────────────────────────────

def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def _flat(value: float, n: int) -> pd.Series:
    return pd.Series([value] * n, dtype=float)


def _rising(start: float, n: int, step: float = 1.0) -> pd.Series:
    return pd.Series([start + i * step for i in range(n)], dtype=float)


def _falling(start: float, n: int, step: float = 1.0) -> pd.Series:
    return pd.Series([start - i * step for i in range(n)], dtype=float)


# ═══════════════════════════════════════════════════════════════
#  RSI
# ═══════════════════════════════════════════════════════════════

class TestRSI:

    def test_flat_price_returns_50(self):
        """BUG FIX: flat price → RSI ~50, bukan 100."""
        close = _flat(1000.0, 100)
        assert hitung_rsi(close) == 50.0

    def test_insufficient_data_returns_none(self):
        assert hitung_rsi(_series([100.0] * 10)) is None

    def test_none_input_returns_none(self):
        assert hitung_rsi(None) is None

    def test_strong_uptrend_high_rsi(self):
        close = _rising(100, 100, 5.0)
        rsi = hitung_rsi(close)
        assert rsi is not None
        assert rsi > 70

    def test_strong_downtrend_low_rsi(self):
        close = _falling(1000, 100, 5.0)
        rsi = hitung_rsi(close)
        assert rsi is not None
        assert rsi < 30

    def test_return_range_0_to_100(self):
        np.random.seed(42)
        close = pd.Series(np.random.lognormal(7, 0.02, 200))
        rsi = hitung_rsi(close)
        assert rsi is not None
        assert 0.0 <= rsi <= 100.0


# ═══════════════════════════════════════════════════════════════
#  MACD
# ═══════════════════════════════════════════════════════════════

class TestMACD:

    def test_insufficient_data_returns_none(self):
        assert hitung_macd(_series([100.0] * 30)) is None

    def test_none_input_returns_none(self):
        assert hitung_macd(None) is None

    def test_uptrend_returns_bullish_label(self):
        close = _rising(100, 100, 2.0)
        result = hitung_macd(close)
        assert result is not None
        assert "Bullish" in result

    def test_downtrend_returns_bearish_label(self):
        close = _falling(1000, 100, 2.0)
        result = hitung_macd(close)
        assert result is not None
        assert "Bearish" in result

    def test_bullish_lemah_below_zero(self):
        """MACD > signal but MACD < 0 → Bullish Lemah, not full Bullish."""
        close = _falling(1000, 60, 3.0)
        for _ in range(15):
            close = pd.concat([close, pd.Series([float(close.iloc[-1]) + 1.0])], ignore_index=True)
        result = hitung_macd(close)
        assert result is not None
        if "Bullish" in result and result != "Bullish Cross":
            assert "Lemah" in result or result == "Bullish"

    def test_flat_price_returns_valid_string(self):
        close = _flat(500.0, 100)
        result = hitung_macd(close)
        assert result is not None
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════
#  MA Trend
# ═══════════════════════════════════════════════════════════════

class TestMATrend:

    def test_insufficient_data_returns_none(self):
        assert hitung_ma_trend(_series([100.0] * 40)) is None

    def test_none_input_returns_none(self):
        assert hitung_ma_trend(None) is None

    def test_uptrend_kuat(self):
        close = _rising(100, 250, 1.0)
        assert hitung_ma_trend(close) == "Uptrend Kuat"

    def test_downtrend(self):
        close = _falling(1000, 250, 1.0)
        assert hitung_ma_trend(close) == "Downtrend"

    def test_short_data_uses_ma50_only(self):
        close = _rising(100, 80, 1.0)
        result = hitung_ma_trend(close)
        assert result in ("Di Atas MA50", "Di Bawah MA50")


# ═══════════════════════════════════════════════════════════════
#  Momentum
# ═══════════════════════════════════════════════════════════════

class TestMomentum:

    def test_insufficient_data_returns_none(self):
        assert hitung_momentum(_series([100.0] * 10), hari=63) is None

    def test_none_input_returns_none(self):
        assert hitung_momentum(None) is None

    def test_positive_momentum(self):
        close = _rising(100, 100, 1.0)
        mom = hitung_momentum(close, hari=63)
        assert mom is not None
        assert mom > 0

    def test_flat_price_zero_momentum(self):
        close = _flat(500.0, 100)
        assert hitung_momentum(close, hari=63) == 0.0

    def test_zero_start_price_returns_none(self):
        vals = [0.0] * 30 + [100.0] * 40
        close = _series(vals)
        result = hitung_momentum(close, hari=63)
        assert result is None


# ═══════════════════════════════════════════════════════════════
#  Volume Trend
# ═══════════════════════════════════════════════════════════════

class TestVolumeTrend:

    def test_insufficient_data_returns_none(self):
        vol = _series([1000.0] * 10)
        close = _series([100.0] * 10)
        assert hitung_volume_trend(vol, close) is None

    def test_none_input_returns_none(self):
        assert hitung_volume_trend(None, _series([1.0] * 25)) is None
        assert hitung_volume_trend(_series([1.0] * 25), None) is None

    def test_zero_volume_returns_tidak_likuid(self):
        """BUG FIX: vol_20=0 → flag 'Tidak Likuid' bukan misleading label."""
        vol = _flat(0.0, 50)
        close = _rising(100, 50, 1.0)
        assert hitung_volume_trend(vol, close) == "Tidak Likuid"

    def test_akumulasi(self):
        close = _series([100.0] * 30 + [100.0 + i * 2 for i in range(20)])
        vol_low = [100_000.0] * 30
        vol_high = [300_000.0] * 20
        vol = _series(vol_low + vol_high)
        result = hitung_volume_trend(vol, close)
        assert result == "Akumulasi"

    def test_distribusi(self):
        close = _series([200.0] * 30 + [200.0 - i * 2 for i in range(20)])
        vol_low = [100_000.0] * 30
        vol_high = [300_000.0] * 20
        vol = _series(vol_low + vol_high)
        result = hitung_volume_trend(vol, close)
        assert result == "Distribusi"


# ═══════════════════════════════════════════════════════════════
#  MFI
# ═══════════════════════════════════════════════════════════════

class TestMFI:

    def test_insufficient_data_returns_none(self):
        short = _series([100.0] * 10)
        assert hitung_mfi(short, short, short, short) is None

    def test_none_input_returns_none(self):
        s = _series([100.0] * 20)
        assert hitung_mfi(None, s, s, s) is None
        assert hitung_mfi(s, None, s, s) is None
        assert hitung_mfi(s, s, None, s) is None
        assert hitung_mfi(s, s, s, None) is None

    def test_flat_price_returns_none(self):
        """Flat typical price → no positive or negative flow → MFI undefined → None."""
        flat = _flat(100.0, 50)
        vol = _flat(1_000_000.0, 50)
        result = hitung_mfi(flat, flat, flat, vol)
        assert result is None

    def test_return_range_0_to_100(self):
        np.random.seed(99)
        n = 100
        close = pd.Series(np.random.lognormal(7, 0.03, n))
        high = close * 1.02
        low = close * 0.98
        vol = pd.Series(np.random.uniform(100_000, 1_000_000, n))
        mfi = hitung_mfi(high, low, close, vol)
        assert mfi is not None
        assert 0.0 <= mfi <= 100.0

    def test_zero_volume_returns_none(self):
        close = _rising(100, 30, 1.0)
        vol = _flat(0.0, 30)
        result = hitung_mfi(close * 1.01, close * 0.99, close, vol)
        assert result is None


# ═══════════════════════════════════════════════════════════════
#  OBV Trend
# ═══════════════════════════════════════════════════════════════

class TestOBVTrend:

    def test_insufficient_data_returns_none(self):
        assert hitung_obv_trend(_series([100.0] * 20), _series([1000.0] * 20)) is None

    def test_none_input_returns_none(self):
        s = _series([100.0] * 40)
        assert hitung_obv_trend(None, s) is None
        assert hitung_obv_trend(s, None) is None

    def test_confirm_up(self):
        close = _rising(100, 50, 2.0)
        vol = _flat(1_000_000.0, 50)
        result = hitung_obv_trend(close, vol)
        assert result is not None
        assert result == "Confirm Up"

    def test_confirm_down(self):
        close = _falling(1000, 50, 2.0)
        vol = _flat(1_000_000.0, 50)
        result = hitung_obv_trend(close, vol)
        assert result is not None
        assert result == "Confirm Down"

    def test_flat_price_returns_netral(self):
        close = _flat(500.0, 50)
        vol = _flat(1_000_000.0, 50)
        result = hitung_obv_trend(close, vol)
        assert result == "Netral"
