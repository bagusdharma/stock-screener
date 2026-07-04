"""Scoring engine — assigns 0-100 score per ticker from merged data.

Components (total 100):
  A. Kualitas Bisnis    40 pts
  B. Dividen            20 pts
  C. Growth             20 pts
  D. Valuasi            15 pts
  E. Teknikal            5 pts

Penalties and hard gates applied after subtotal.
All thresholds sourced from settings.py — no magic numbers.
"""

from __future__ import annotations

import logging
from statistics import median

import pandas as pd

from src.analysis.indicators import (
    hitung_macd,
    hitung_ma_trend,
    hitung_rsi,
    hitung_volume_trend,
)
from src.config.settings import (
    IDX_IC_SECTORS,
    SEKTOR,
    SKOR_BUY,
    SKOR_HOLD,
    SKOR_STRONG_BUY,
)

log = logging.getLogger(__name__)

# ── Scoring weights ───────────────────────────────────────────

W_KUALITAS = 40
W_DIVIDEN = 20
W_GROWTH = 20
W_VALUASI = 15
W_TEKNIKAL = 5

# ── Sub-component weights ─────────────────────────────────────

# A. Kualitas
W_ROE = 12
W_NPM = 10
W_DER = 8
W_CR = 5
W_AT = 5

# B. Dividen
W_YIELD = 10
W_STREAK = 10

# C. Growth
W_REV_CAGR = 7
W_EARN_CAGR = 7
W_CONSISTENCY = 6

# D. Valuasi
W_PER = 8
W_PBV = 7

# E. Teknikal
W_MA_MACD = 3
W_RSI_VOL = 2

# ── Penalty values ────────────────────────────────────────────

P_ROE_NEGATIVE = -10
P_LOSS_2Y = -10
P_REV_DECLINE_2Y = -5
P_DER_HIGH = -8
P_YIELD_TRAP = -5
P_PER_EXTREME = -5
P_DATA_LOW = -15

# ── Thresholds ────────────────────────────────────────────────

YIELD_TRAP_THRESHOLD = 10.0
DER_HIGH_NON_BANK = 3.0
DER_HIGH_BANK = 10.0
PER_EXTREME = 50.0
DATA_LOW_THRESHOLD = 0.70
HARD_GATE_ROE_MIN = 15.0
HARD_GATE_DER_MAX_NON_BANK = 2.0
HARD_GATE_STRONG_BUY = SKOR_STRONG_BUY

# ── Kalibrasi skala tampilan (gaya IBD Composite Rating) ─────
#
# Skor mentah punya plafon realistis ~72-80 karena kriteria poin-penuh antar
# komponen saling trade-off (deep value <=0.5x median sektor vs growth CAGR
# >=15% vs yield >=6% tidak mungkin terpenuhi bersamaan) — terbukti dari data:
# max skor 176 saham = 72, STRONG BUY (>=75 lama) tidak pernah tercapai.
# Screener komersial (IBD Composite Rating, Zacks) menormalisasi skor final
# ke persentil sehingga saham terbaik universe mendekati 99.
#
# Remap piecewise-linear di bawah MEMPERTAHANKAN RANKING dan memetakan batas
# label lama ke skala baru: raw 50->70 (HOLD), raw 63->85 (BUY),
# raw ~69->90 (STRONG BUY ~ top 3% universe), raw 80->97.
# Skor mentah tetap disimpan sebagai `skor_raw` untuk transparansi.

_CAL_ANCHORS = [(0, 0), (30, 45), (50, 70), (63, 85), (72, 93), (80, 97), (100, 100)]


def _calibrate(raw: float) -> int:
    for (x1, y1), (x2, y2) in zip(_CAL_ANCHORS, _CAL_ANCHORS[1:]):
        if raw <= x2:
            return round(y1 + (raw - x1) * (y2 - y1) / (x2 - x1))
    return 100


# ── Labels ────────────────────────────────────────────────────

LABEL_STRONG_BUY = "STRONG BUY"
LABEL_BUY = "BUY"
LABEL_HOLD = "HOLD"
LABEL_SELL = "JUAL"


def _label(skor: int) -> str:
    if skor >= HARD_GATE_STRONG_BUY:
        return LABEL_STRONG_BUY
    if skor >= SKOR_BUY:
        return LABEL_BUY
    if skor >= SKOR_HOLD:
        return LABEL_HOLD
    return LABEL_SELL


def _sub_label(label: str, skor: int, d: dict, tek: dict) -> str:
    """Context-aware sub-label explaining WHY hold or sell."""
    if label == LABEL_HOLD:
        reasons: list[str] = []
        dy = _safe_float(d.get("yield_ttm"))
        mom = tek.get("momentum")
        ma = tek.get("ma")
        rsi = tek.get("rsi")
        if dy >= 4:
            reasons.append("dividen menarik")
        if mom is not None and mom > 5:
            reasons.append("momentum positif")
        if ma in ("Uptrend Kuat", "Di Atas MA50"):
            reasons.append("tren naik")
        if rsi is not None and rsi < 35:
            reasons.append("RSI oversold")
        if reasons:
            return f"HOLD — {', '.join(reasons[:2])}"
        return "HOLD — belum ada katalis"

    if label == LABEL_SELL:
        roe = _safe_float(d.get("roe"))
        profitable_years = int(_safe_float(d.get("profitable_years")))
        dc = _safe_float(d.get("data_completeness"), 1.0)
        if dc < 0.5:
            return "JUAL — data tidak cukup"
        if roe < 0 or profitable_years <= 0:
            return "JUAL — fundamental buruk"
        if skor < 45:  # setara raw <30 di skala kalibrasi
            return "JUAL — banyak red flag"
        return "JUAL — skor rendah"

    return ""


def _safe_float(val, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        f = float(val)
        if f != f:
            return default
        return f
    except (TypeError, ValueError):
        return default


def _is_bank(sector: str) -> bool:
    return sector == "Financials"


# ═══════════════════════════════════════════════════════════════
#  Sector medians — computed dynamically per scoring run
# ═══════════════════════════════════════════════════════════════

def _compute_sector_medians(
    all_data: dict[str, dict],
) -> dict[str, dict[str, float]]:
    """Compute median PER, PBV, ROE per IDX-IC sector from all merged data."""
    buckets: dict[str, dict[str, list[float]]] = {}
    for sector in IDX_IC_SECTORS:
        buckets[sector] = {"pe": [], "pbv": [], "roe": []}

    for d in all_data.values():
        sector = d.get("sector", "Unknown")
        if sector not in buckets:
            continue
        for field in ("pe", "pbv", "roe"):
            val = d.get(field)
            if val is not None:
                try:
                    f = float(val)
                    if f == f and f != 0:
                        buckets[sector][field].append(f)
                except (TypeError, ValueError):
                    pass

    medians: dict[str, dict[str, float]] = {}
    for sector, fields in buckets.items():
        medians[sector] = {}
        for field, vals in fields.items():
            medians[sector][field] = median(vals) if vals else 0.0
    return medians


def _roe_percentile_rank(roe: float, sector: str,
                         all_data: dict[str, dict]) -> float:
    """Percentile rank of ROE within its sector. Returns 0.0–1.0."""
    sector_roes = []
    for d in all_data.values():
        if d.get("sector") == sector:
            r = d.get("roe")
            if r is not None:
                try:
                    f = float(r)
                    if f == f:
                        sector_roes.append(f)
                except (TypeError, ValueError):
                    pass
    if not sector_roes or len(sector_roes) < 2:
        return 0.5
    below = sum(1 for r in sector_roes if r < roe)
    return below / len(sector_roes)


# ═══════════════════════════════════════════════════════════════
#  Component A — Kualitas Bisnis (40 pts)
# ═══════════════════════════════════════════════════════════════

def _score_kualitas(d: dict, roe_pctile: float) -> tuple[int, list[str]]:
    skor = 0
    alasan: list[str] = []

    # ROE percentile rank (12 pts)
    if roe_pctile >= 0.80:
        skor += W_ROE
    elif roe_pctile >= 0.60:
        skor += 9
    elif roe_pctile >= 0.40:
        skor += 6
    elif roe_pctile >= 0.20:
        skor += 3

    roe = _safe_float(d.get("roe"))
    if roe > 0:
        if roe_pctile >= 0.60:
            top_pct = max(1, round((1 - roe_pctile) * 100))
            alasan.append(f"ROE {roe:.1f}% — termasuk {top_pct}% teratas di sektornya")
        else:
            alasan.append(f"ROE {roe:.1f}%")

    # Net Profit Margin (10 pts)
    npm = _safe_float(d.get("net_profit_margin"))
    if npm >= 20:
        skor += W_NPM
    elif npm >= 15:
        skor += 8
    elif npm >= 10:
        skor += 6
    elif npm >= 5:
        skor += 4
    elif npm >= 0:
        skor += 2

    # DER sector-adjusted (8 pts)
    der_raw = d.get("der")
    der = _safe_float(der_raw)
    sector = d.get("sector", "Unknown")
    if _is_bank(sector) and der_raw is None:
        # Banks often have null DER from Yahoo Finance — high leverage is their
        # business model, not a red flag. Give full points when data is missing.
        skor += W_DER
    elif der > 0:
        if _is_bank(sector):
            if der <= 6:
                skor += W_DER
            elif der <= 8:
                skor += 6
            elif der <= DER_HIGH_BANK:
                skor += 4
        else:
            if der <= 0.5:
                skor += W_DER
            elif der <= 1.0:
                skor += 6
            elif der <= 1.5:
                skor += 4
            elif der <= 2.0:
                skor += 2

    # Current Ratio (5 pts)
    cr = _safe_float(d.get("current_ratio"))
    if cr >= 2.0:
        skor += W_CR
    elif cr >= 1.5:
        skor += 4
    elif cr >= 1.0:
        skor += 2

    # Asset Turnover (5 pts)
    at = _safe_float(d.get("asset_turnover"))
    if at >= 1.0:
        skor += W_AT
    elif at >= 0.5:
        skor += 3
    elif at >= 0.2:
        skor += 1

    # Bank: current ratio & asset turnover TIDAK berlaku untuk model bisnis
    # bank — tanpa normalisasi ini bank kehilangan 10/40 poin secara
    # struktural (BBCA dkk tidak akan pernah bisa skor tinggi).
    if _is_bank(d.get("sector", "")):
        max_bank = W_KUALITAS - W_CR - W_AT  # 30
        skor = round(min(skor, max_bank) * W_KUALITAS / max_bank)

    return skor, alasan


# ═══════════════════════════════════════════════════════════════
#  Component B — Dividen (20 pts)
# ═══════════════════════════════════════════════════════════════

def _score_dividen(d: dict) -> tuple[int, list[str]]:
    skor = 0
    alasan: list[str] = []

    # Yield TTM capped at 10% for scoring (10 pts)
    raw_yield = _safe_float(d.get("yield_ttm"))
    y = min(raw_yield, YIELD_TRAP_THRESHOLD)
    if y >= 6:
        skor += W_YIELD
    elif y >= 4:
        skor += 7
    elif y >= 2:
        skor += 4
    elif y > 0:
        skor += 1
    if raw_yield > 0:
        # Tampilkan NOMINAL rupiah — itu yang dipahami user, bukan persen
        amt = _safe_float(d.get("div_amount_ttm"))
        if amt <= 0:
            price = _safe_float(d.get("price"))
            amt = raw_yield / 100 * price if price > 0 else 0.0
        if amt > 0:
            alasan.append(
                f"Dividen ±Rp {amt:,.0f}/lembar setahun (yield {raw_yield:.1f}%)"
                .replace(",", "."))
        else:
            alasan.append(f"Yield TTM {raw_yield:.1f}%")

    # Streak consecutive years (10 pts)
    streak = int(_safe_float(d.get("div_streak")))
    if streak >= 10:
        skor += W_STREAK
    elif streak >= 7:
        skor += 8
    elif streak >= 5:
        skor += 6
    elif streak >= 3:
        skor += 4
    elif streak >= 1:
        skor += 2
    if streak > 0:
        alasan.append(f"Dividen {streak} tahun berturut")

    return skor, alasan


# ═══════════════════════════════════════════════════════════════
#  Component C — Growth (20 pts)
# ═══════════════════════════════════════════════════════════════

def _score_growth(d: dict) -> tuple[int, list[str]]:
    skor = 0
    alasan: list[str] = []

    # Revenue CAGR 3Y (7 pts)
    rev_cagr = _safe_float(d.get("revenue_cagr_3y"))
    if rev_cagr >= 15:
        skor += W_REV_CAGR
    elif rev_cagr >= 10:
        skor += 5
    elif rev_cagr >= 5:
        skor += 3
    elif rev_cagr > 0:
        skor += 1
    if rev_cagr != 0:
        alasan.append(f"Rev CAGR 3Y {rev_cagr:.1f}%")

    # Earnings CAGR 3Y (7 pts)
    earn_cagr = _safe_float(d.get("earnings_cagr_3y"))
    if earn_cagr >= 15:
        skor += W_EARN_CAGR
    elif earn_cagr >= 10:
        skor += 5
    elif earn_cagr >= 5:
        skor += 3
    elif earn_cagr > 0:
        skor += 1

    # Multi-year consistency (6 pts)
    profitable_years = int(_safe_float(d.get("profitable_years")))
    revenue_trend = d.get("revenue_trend", "")
    if profitable_years >= 5 and revenue_trend == "growing":
        skor += W_CONSISTENCY
    elif profitable_years >= 4:
        skor += 4
    elif profitable_years >= 3:
        skor += 2

    return skor, alasan


# ═══════════════════════════════════════════════════════════════
#  Component D — Valuasi (15 pts)
# ═══════════════════════════════════════════════════════════════

def _score_valuasi(
    d: dict,
    sector_median_pe: float,
    sector_median_pbv: float,
) -> tuple[int, list[str]]:
    skor = 0
    alasan: list[str] = []

    # PER vs sector median (8 pts)
    pe = _safe_float(d.get("pe"))
    if pe > 0 and sector_median_pe > 0:
        ratio = pe / sector_median_pe
        if ratio <= 0.5:
            skor += W_PER
        elif ratio <= 0.75:
            skor += 6
        elif ratio <= 1.0:
            skor += 4
        elif ratio <= 1.25:
            skor += 2
        alasan.append(f"PER {pe:.1f}x (median sektor {sector_median_pe:.1f}x)")

    # PBV vs sector median (7 pts)
    pbv = _safe_float(d.get("pbv"))
    if pbv > 0 and sector_median_pbv > 0:
        ratio = pbv / sector_median_pbv
        if ratio <= 0.5:
            skor += W_PBV
        elif ratio <= 0.75:
            skor += 5
        elif ratio <= 1.0:
            skor += 3
        elif ratio <= 1.25:
            skor += 1

    return skor, alasan


# ═══════════════════════════════════════════════════════════════
#  Component E — Teknikal (5 pts, confirmation only)
# ═══════════════════════════════════════════════════════════════

def _score_teknikal(d: dict) -> tuple[int, list[str]]:
    skor = 0
    alasan: list[str] = []

    ohlcv = d.get("ohlcv")
    if not isinstance(ohlcv, pd.DataFrame) or ohlcv.empty:
        return skor, alasan

    close = ohlcv["Close"] if "Close" in ohlcv.columns else None
    volume = ohlcv["Volume"] if "Volume" in ohlcv.columns else None

    # MA Trend + MACD (3 pts)
    ma = hitung_ma_trend(close) if close is not None else None
    macd = hitung_macd(close) if close is not None else None

    ma_bullish = ma in ("Uptrend Kuat", "Di Atas MA50")
    macd_bullish = macd in ("Bullish Cross", "Bullish")

    if ma_bullish and macd_bullish:
        skor += W_MA_MACD
    elif ma_bullish or macd_bullish:
        skor += 1

    if ma:
        alasan.append(f"MA: {ma}")
    if macd:
        alasan.append(f"MACD: {macd}")

    # RSI + Volume (2 pts)
    rsi = hitung_rsi(close) if close is not None else None
    vol_trend = hitung_volume_trend(volume, close) if volume is not None and close is not None else None

    rsi_buy = rsi is not None and 30 <= rsi <= 60
    vol_good = vol_trend in ("Akumulasi", "Konfirmasi")

    if rsi_buy and vol_good:
        skor += W_RSI_VOL
    elif rsi_buy or vol_good:
        skor += 1

    return skor, alasan


# ═══════════════════════════════════════════════════════════════
#  Penalties
# ═══════════════════════════════════════════════════════════════

def _compute_penalties(d: dict) -> tuple[int, list[str]]:
    total = 0
    detail: list[str] = []

    roe = _safe_float(d.get("roe"))
    sector = d.get("sector", "Unknown")
    der = _safe_float(d.get("der"))
    raw_yield = _safe_float(d.get("yield_ttm"))
    pe = _safe_float(d.get("pe"))
    profitable_years = int(_safe_float(d.get("profitable_years")))
    data_completeness = _safe_float(d.get("data_completeness"), 1.0)
    rev_cagr = _safe_float(d.get("revenue_cagr_3y"))
    revenue_trend = d.get("revenue_trend", "")

    # P1: ROE negatif → -10
    if roe < 0:
        total += P_ROE_NEGATIVE
        detail.append(f"ROE negatif ({roe:.1f}%): {P_ROE_NEGATIVE}")

    # P2: Rugi 2 tahun berturut → -10
    if profitable_years <= 0:
        total += P_LOSS_2Y
        detail.append(f"Rugi berturut-turut: {P_LOSS_2Y}")

    # P3: Revenue turun 2+ tahun → -5
    if revenue_trend == "declining" or rev_cagr < -5:
        total += P_REV_DECLINE_2Y
        detail.append(f"Revenue turun: {P_REV_DECLINE_2Y}")

    # P4: DER > 3x non-bank → -8
    if not _is_bank(sector) and der > DER_HIGH_NON_BANK:
        total += P_DER_HIGH
        detail.append(f"DER tinggi {der:.1f}x: {P_DER_HIGH}")

    # P5: Yield trap (>10%) → -5
    # Exempt banks with long dividend track record — high yield is normal
    # during market corrections, not a trap signal.
    div_streak = int(_safe_float(d.get("div_streak")))
    is_yield_trap_exempt = _is_bank(sector) and div_streak >= 10
    if raw_yield > YIELD_TRAP_THRESHOLD and not is_yield_trap_exempt:
        total += P_YIELD_TRAP
        detail.append(f"Yield trap ({raw_yield:.1f}%): {P_YIELD_TRAP}")

    # P6: PER > 50x → -5
    if pe > PER_EXTREME:
        total += P_PER_EXTREME
        detail.append(f"PER extreme ({pe:.1f}x): {P_PER_EXTREME}")

    # P7: Data < 70% lengkap → -15
    if data_completeness < DATA_LOW_THRESHOLD:
        total += P_DATA_LOW
        detail.append(f"Data rendah ({data_completeness*100:.0f}%): {P_DATA_LOW}")

    return total, detail


# ═══════════════════════════════════════════════════════════════
#  Hard Gate — STRONG BUY requires passing all gates
# ═══════════════════════════════════════════════════════════════

def _apply_hard_gate(skor: int, d: dict) -> tuple[int, list[str]]:
    """Jika skor (skala tampilan) >= SKOR_STRONG_BUY tapi ada gate yang gagal,
    cap di SKOR_STRONG_BUY - 1 dan jelaskan alasannya."""
    if skor < HARD_GATE_STRONG_BUY:
        return skor, []

    gate_fails: list[str] = []
    roe = _safe_float(d.get("roe"))
    der = _safe_float(d.get("der"))
    sector = d.get("sector", "Unknown")
    profitable_years = int(_safe_float(d.get("profitable_years")))
    rev_cagr = _safe_float(d.get("revenue_cagr_3y"))
    revenue_trend = d.get("revenue_trend", "")
    data_completeness = _safe_float(d.get("data_completeness"), 1.0)

    if roe < HARD_GATE_ROE_MIN:
        gate_fails.append(f"ROE {roe:.1f}% < {HARD_GATE_ROE_MIN}%")

    if profitable_years <= 0:
        gate_fails.append("Rugi dalam 2 tahun terakhir")

    if not _is_bank(sector) and der > HARD_GATE_DER_MAX_NON_BANK:
        gate_fails.append(f"DER {der:.1f}x > {HARD_GATE_DER_MAX_NON_BANK}x")

    if revenue_trend == "declining" or rev_cagr < -5:
        gate_fails.append("Revenue turun 2 tahun berturut")

    if data_completeness < DATA_LOW_THRESHOLD:
        gate_fails.append(f"Data {data_completeness*100:.0f}% < {DATA_LOW_THRESHOLD*100:.0f}%")

    if gate_fails:
        return HARD_GATE_STRONG_BUY - 1, [
            f"Hard gate: {g}" for g in gate_fails
        ]
    return skor, []


# ═══════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════

def score_ticker(
    merged: dict,
    sector_medians: dict[str, dict[str, float]],
    all_data: dict[str, dict],
) -> dict:
    """Score one ticker from merged data.

    Args:
        merged: output from merger.py for this ticker
        sector_medians: pre-computed median PER/PBV/ROE per sector
        all_data: full dataset (needed for ROE percentile rank)

    Returns dict with keys:
        skor_total, label, komponen{A-E}, penalti_total, penalti_detail,
        alasan[], data_completeness, teknikal{rsi, macd, ma, vol_trend}
    """
    sector = merged.get("sector", "Unknown")
    sm = sector_medians.get(sector, {"pe": 0.0, "pbv": 0.0, "roe": 0.0})

    roe = _safe_float(merged.get("roe"))
    roe_pctile = _roe_percentile_rank(roe, sector, all_data)

    # Score components
    s_kualitas, a_kualitas = _score_kualitas(merged, roe_pctile)
    s_dividen, a_dividen = _score_dividen(merged)
    s_growth, a_growth = _score_growth(merged)
    s_valuasi, a_valuasi = _score_valuasi(merged, sm["pe"], sm["pbv"])
    s_teknikal, a_teknikal = _score_teknikal(merged)

    subtotal = s_kualitas + s_dividen + s_growth + s_valuasi + s_teknikal

    # Penalties
    penalti_total, penalti_detail = _compute_penalties(merged)

    skor_raw = subtotal + penalti_total
    skor_clamped = max(0, min(100, skor_raw))

    # Kalibrasi ke skala tampilan (ranking tidak berubah)
    skor_display = _calibrate(skor_clamped)

    # Hard gate (bekerja di skala tampilan: cap di bawah SKOR_STRONG_BUY)
    skor_final, gate_alasan = _apply_hard_gate(skor_display, merged)

    alasan = a_kualitas + a_dividen + a_growth + a_valuasi + a_teknikal
    if penalti_detail:
        alasan.extend(penalti_detail)
    if gate_alasan:
        alasan.extend(gate_alasan)

    label = _label(skor_final)

    # Extract technical indicators for display
    ohlcv = merged.get("ohlcv")
    close = None
    volume = None
    if isinstance(ohlcv, pd.DataFrame) and not ohlcv.empty:
        close = ohlcv.get("Close")
        volume = ohlcv.get("Volume")

    from src.analysis.indicators import hitung_momentum, hitung_mfi, hitung_obv_trend

    rsi_val = hitung_rsi(close) if close is not None else None
    macd_val = hitung_macd(close) if close is not None else None
    ma_val = hitung_ma_trend(close) if close is not None else None
    vol_val = hitung_volume_trend(volume, close) if volume is not None and close is not None else None
    mom_val = hitung_momentum(close) if close is not None else None
    mfi_val = hitung_mfi(
        ohlcv["High"], ohlcv["Low"], close, volume
    ) if isinstance(ohlcv, pd.DataFrame) and {"High", "Low", "Close", "Volume"}.issubset(ohlcv.columns) else None
    obv_val = hitung_obv_trend(close, volume) if close is not None and volume is not None else None

    tek_dict = {
        "rsi": rsi_val,
        "macd": macd_val,
        "ma": ma_val,
        "vol_trend": vol_val,
        "momentum": mom_val,
        "mfi": mfi_val,
        "obv": obv_val,
    }

    sl = _sub_label(label, skor_final, merged, tek_dict)

    return {
        "ticker": merged.get("ticker", ""),
        "skor_total": skor_final,
        "skor_raw": skor_clamped,
        "label": label,
        "sub_label": sl,
        "komponen": {
            "A_kualitas": s_kualitas,
            "B_dividen": s_dividen,
            "C_growth": s_growth,
            "D_valuasi": s_valuasi,
            "E_teknikal": s_teknikal,
        },
        "penalti_total": penalti_total,
        "penalti_detail": penalti_detail,
        "alasan": alasan,
        "data_completeness": _safe_float(merged.get("data_completeness"), 0.0),
        "teknikal": tek_dict,
    }


def score_all(all_merged: dict[str, dict]) -> dict[str, dict]:
    """Score all tickers. Computes sector medians dynamically first.

    Args:
        all_merged: {ticker: merged_dict} from merger.get_all_merged()

    Returns:
        {ticker: score_result_dict}
    """
    sector_medians = _compute_sector_medians(all_merged)

    results = {}
    for ticker, merged in all_merged.items():
        try:
            results[ticker] = score_ticker(merged, sector_medians, all_merged)
        except Exception as exc:
            log.error("%s: scoring crashed: %s", ticker, exc)
            results[ticker] = {
                "ticker": ticker,
                "skor_total": 0,
                "label": LABEL_SELL,
                "sub_label": "JUAL — scoring error",
                "komponen": {
                    "A_kualitas": 0,
                    "B_dividen": 0,
                    "C_growth": 0,
                    "D_valuasi": 0,
                    "E_teknikal": 0,
                },
                "penalti_total": 0,
                "penalti_detail": [],
                "alasan": [f"Scoring error: {exc}"],
                "data_completeness": 0.0,
                "teknikal": {
                    "rsi": None, "macd": None, "ma": None,
                    "vol_trend": None, "momentum": None,
                    "mfi": None, "obv": None,
                },
            }
    return results
