"""Indikator teknikal untuk IDX Stock Screener.

Semua fungsi menerima pd.Series (close, high, low, volume).
Tidak ada data-fetching di file ini — data disupply dari fetcher layer.

Return convention:
  - float indicators: return None jika data tidak cukup
  - string indicators: return None jika data tidak cukup
  - Caller (scorer) bertanggung jawab handle None
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ── Minimum data constants ────────────────────────────────────

MIN_RSI = 15       # period + 1
MIN_MACD = 35      # EMA26 + EMA9
MIN_MA50 = 50
MIN_MA200 = 200
MIN_MFI = 15       # period + 1
MIN_OBV = 30
MIN_VOL_TREND = 20


# ═══════════════════════════════════════════════════════════════
#  RSI — Wilder's Smoothing (standar TradingView / MT5)
# ═══════════════════════════════════════════════════════════════

def hitung_rsi(close: pd.Series, period: int = 14) -> float | None:
    """RSI dengan Wilder's Smoothing. Return 0.0–100.0, atau None jika data < period+1."""
    if close is None or len(close) < period + 1:
        return None
    try:
        delta = close.diff().dropna()
        gain = delta.clip(lower=0)
        loss = (-delta.clip(upper=0))
        avg_gain = gain.ewm(com=period - 1, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period, adjust=False).mean()
        lg = float(avg_gain.iloc[-1])
        ll = float(avg_loss.iloc[-1])
        if lg == 0 and ll == 0:
            return 50.0
        if ll == 0:
            return 100.0
        if lg == 0:
            return 0.0
        rsi = 100.0 - (100.0 / (1.0 + lg / ll))
        if np.isnan(rsi) or np.isinf(rsi):
            return None
        return round(rsi, 1)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  MACD — EMA12, EMA26, Signal EMA9
# ═══════════════════════════════════════════════════════════════

def hitung_macd(close: pd.Series) -> str | None:
    """MACD standar 12/26/9. Return label string, atau None jika data < 35."""
    if close is None or len(close) < MIN_MACD:
        return None
    try:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        mn = float(macd.iloc[-1])
        mp = float(macd.iloc[-2])
        sn = float(signal.iloc[-1])
        sp = float(signal.iloc[-2])
        if np.isnan(mn) or np.isnan(sn):
            return None
        if mp < sp and mn > sn:
            return "Bullish Cross"
        if mp > sp and mn < sn:
            return "Bearish Cross"
        if mn > sn and mn > 0:
            return "Bullish"
        if mn > sn:
            return "Bullish Lemah"
        if mn < sn and mn < 0:
            return "Bearish"
        return "Bearish Lemah"
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  MA Trend — SMA50 vs SMA200
# ═══════════════════════════════════════════════════════════════

def hitung_ma_trend(close: pd.Series) -> str | None:
    """SMA50 vs SMA200 trend classification. Return None jika data < 50."""
    if close is None or len(close) < MIN_MA50:
        return None
    try:
        harga = float(close.iloc[-1])
        ma50 = float(close.tail(50).mean())
        if np.isnan(harga) or np.isnan(ma50):
            return None
        if len(close) >= MIN_MA200:
            ma200 = float(close.tail(200).mean())
            if np.isnan(ma200):
                return None
            if harga > ma50 and ma50 > ma200:
                return "Uptrend Kuat"
            if harga > ma50:
                return "Di Atas MA50"
            if harga > ma200:
                return "Di Atas MA200"
            return "Downtrend"
        return "Di Atas MA50" if harga > ma50 else "Di Bawah MA50"
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  Momentum — % perubahan harga dalam N hari
# ═══════════════════════════════════════════════════════════════

def hitung_momentum(close: pd.Series, hari: int = 63) -> float | None:
    """Return % price change over N trading days. None jika data < hari."""
    if close is None or len(close) < hari:
        return None
    try:
        kini = float(close.iloc[-1])
        lalu = float(close.iloc[-hari])
        if np.isnan(kini) or np.isnan(lalu) or lalu <= 0:
            return None
        return round((kini / lalu - 1) * 100, 1)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  Volume Trend — konfirmasi sinyal harga via volume
# ═══════════════════════════════════════════════════════════════

def hitung_volume_trend(volume: pd.Series, close: pd.Series) -> str | None:
    """Analisis volume vs price action.

    Return labels: "Akumulasi", "Konfirmasi", "Distribusi",
                   "Lemah", "Tidak Likuid", "Normal".
    Return None jika data < 20 hari.
    """
    if volume is None or close is None or len(volume) < MIN_VOL_TREND or len(close) < MIN_VOL_TREND:
        return None
    try:
        vol_20 = float(volume.tail(20).mean())
        if vol_20 <= 0:
            return "Tidak Likuid"

        vol_50 = float(volume.tail(50).mean()) if len(volume) >= 50 else float(volume.mean())
        if vol_50 <= 0:
            return "Tidak Likuid"

        close_20_ago = float(close.iloc[-20])
        if close_20_ago <= 0 or np.isnan(close_20_ago):
            return None
        price_chg = float(close.iloc[-1]) / close_20_ago - 1

        vol_ratio = vol_20 / vol_50

        if price_chg > 0.02 and vol_ratio > 1.3:
            return "Akumulasi"
        if price_chg > 0 and vol_ratio >= 0.9:
            return "Konfirmasi"
        if price_chg < -0.02 and vol_ratio > 1.3:
            return "Distribusi"
        if price_chg > 0.02 and vol_ratio < 0.7:
            return "Lemah"
        return "Normal"
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  MFI — Money Flow Index ("RSI berbasis volume")
# ═══════════════════════════════════════════════════════════════

def hitung_mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 14,
) -> float | None:
    """MFI standar. Return 0.0–100.0, atau None jika data < period+1."""
    if (
        high is None or low is None or close is None or volume is None
        or len(high) < period + 1
        or len(low) < period + 1
        or len(close) < period + 1
        or len(volume) < period + 1
    ):
        return None
    try:
        tp = (high + low + close) / 3.0
        raw_mf = tp * volume
        tp_diff = tp.diff()

        pos_mf = raw_mf.where(tp_diff > 0, 0.0)
        neg_mf = raw_mf.where(tp_diff < 0, 0.0)

        pos_sum = pos_mf.rolling(window=period, min_periods=period).sum()
        neg_sum = neg_mf.rolling(window=period, min_periods=period).sum()

        neg_safe = neg_sum.replace(0, np.nan)
        mfr = pos_sum / neg_safe
        mfi = 100.0 - (100.0 / (1.0 + mfr))

        val = float(mfi.iloc[-1])
        if np.isnan(val) or np.isinf(val):
            return None
        return round(max(0.0, min(100.0, val)), 1)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  OBV Trend — On-Balance Volume divergence detection
# ═══════════════════════════════════════════════════════════════

def hitung_obv_trend(close: pd.Series, volume: pd.Series) -> str | None:
    """OBV trend & divergence. Return label, atau None jika data < 30."""
    if close is None or volume is None or len(close) < MIN_OBV or len(volume) < MIN_OBV:
        return None
    try:
        price_diff = close.diff()
        direction = price_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        obv = (volume * direction).cumsum()

        obv_now = float(obv.iloc[-1])
        obv_20 = float(obv.iloc[-20])
        price_now = float(close.iloc[-1])
        price_20 = float(close.iloc[-20])

        if np.isnan(obv_now) or np.isnan(price_now):
            return None

        obv_pct = (obv_now - obv_20) / abs(obv_20) * 100 if abs(obv_20) > 0 else 0
        price_pct = (price_now - price_20) / price_20 * 100 if price_20 > 0 else 0

        obv_up = obv_pct > 3
        obv_down = obv_pct < -3
        price_up = price_pct > 2
        price_down = price_pct < -2

        if obv_up and price_down:
            return "Bullish Div"
        if obv_down and price_up:
            return "Bearish Div"
        if obv_up and price_up:
            return "Confirm Up"
        if obv_down and price_down:
            return "Confirm Down"
        return "Netral"
    except Exception:
        return None
