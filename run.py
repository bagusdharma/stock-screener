"""
run.py — IDX Stock Screener v5
================================
Fix v5:
  - Suppress HTTP 404 noise dari yfinance internal (contextlib + logging)
  - Ticker bermasalah sudah dihapus dari config.py
  - Semua bug v4 sudah termasuk (RSI Wilder, DER=0, Python 3.7 compat)
"""

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import schedule
import time
import sys
import os
import io
import logging
import warnings
import contextlib
import concurrent.futures
from datetime import datetime

# ════════════════════════════════════════════════════════════
#  SUPPRESS NOISE DARI YFINANCE
#  yfinance mencetak HTTP 404 ke stderr via logging.WARNING.
#  Kita set ke CRITICAL agar hanya error fatal yang muncul.
# ════════════════════════════════════════════════════════════
for _lib in ["yfinance", "urllib3", "requests", "peewee", "multitasking"]:
    logging.getLogger(_lib).setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")


from config import (
    BOT_TOKEN, CHAT_ID, BUDGET,
    JUMLAH_REKOMENDASI, TICKERS,
    SKOR_STRONG_BUY, SKOR_BUY, SKOR_HOLD,
)

VALID_TICKERS_FILE = "valid_tickers.txt"

def _sprint(msg: str):
    """Print ke stdout dengan aman — tidak crash kalau stdout=None (Task Scheduler)."""
    try:
        if sys.stdout is not None:
            print(msg, end="", flush=True)
    except Exception:
        pass




# ════════════════════════════════════════════════════════════
#  HELPER: Bungkus semua panggilan yfinance agar noise
#  yang ke stderr juga tersuppress (double protection)
# ════════════════════════════════════════════════════════════

FETCH_TIMEOUT = 15  # detik — skip ticker kalau Yahoo Finance tidak respond


@contextlib.contextmanager
def _silent():
    """Suppress stderr selama blok ini berjalan."""
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            yield
    except Exception:
        yield


def _run_with_timeout(func, timeout=FETCH_TIMEOUT):
    """
    Jalankan func() dengan batas waktu.
    Kalau > timeout detik tidak selesai → return None (skip ticker).
    Mencegah script hang selamanya saat Yahoo Finance lambat/tidak respond.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(func)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return None
        except Exception as e:
            raise e


def _safe_info(ticker: str) -> dict:
    """Ambil .info dengan timeout. Return {} kalau gagal/timeout."""
    def _get():
        with _silent():
            result = yf.Ticker(ticker).info
            return result if isinstance(result, dict) else {}
    try:
        result = _run_with_timeout(_get)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _safe_download(ticker: str, period: str = "2y") -> pd.DataFrame:
    """Download historis harga dengan timeout. Return DataFrame kosong kalau gagal."""
    def _get():
        with _silent():
            df = yf.download(
                ticker, period=period, interval="1d",
                auto_adjust=True, progress=False, threads=False,
            )
            return df if df is not None else pd.DataFrame()
    try:
        result = _run_with_timeout(_get)
        return result if isinstance(result, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _safe_dividends(ticker: str) -> pd.Series:
    """Ambil dividen dengan timeout. Return Series kosong kalau gagal."""
    def _get():
        with _silent():
            result = yf.Ticker(ticker).dividends
            return result if isinstance(result, pd.Series) else pd.Series(dtype=float)
    try:
        result = _run_with_timeout(_get)
        return result if isinstance(result, pd.Series) else pd.Series(dtype=float)
    except Exception:
        return pd.Series(dtype=float)


def _safe_financials(ticker: str) -> pd.DataFrame:
    """Ambil income statement tahunan dengan timeout. Return DataFrame kosong kalau gagal."""
    def _get():
        with _silent():
            t = yf.Ticker(ticker)
            # income_stmt (yfinance >=0.2.x), fallback ke financials (older)
            try:
                fin = t.income_stmt
            except AttributeError:
                fin = t.financials
            return fin if isinstance(fin, pd.DataFrame) else pd.DataFrame()
    try:
        result = _run_with_timeout(_get, timeout=10)
        return result if isinstance(result, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _extract_financial_row(df: pd.DataFrame, labels: list) -> list:
    """Cari row di income statement — coba beberapa label (beda versi yfinance)."""
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


def _default_growth() -> dict:
    return {
        "rev_growth_yoy": 0.0,
        "earn_growth_yoy": 0.0,
        "growth_years_positive": 0,
        "revenue_trend": "unknown",
    }


def fetch_growth_data(ticker: str) -> dict:
    """
    Analisis pertumbuhan revenue & earnings multi-year dari income statement.

    Return dict:
      - rev_growth_yoy:  float (desimal, 0.15 = 15%)
      - earn_growth_yoy: float
      - growth_years_positive: int (berapa tahun berturut-turut earnings naik)
      - revenue_trend: str ("growing" / "flat" / "declining" / "unknown")

    Data diambil dari income_stmt (annual), kolom = tahun (newest first).
    Typical: 4 tahun data (2025, 2024, 2023, 2022).
    """
    try:
        fin = _safe_financials(ticker)
        if fin is None or fin.empty:
            return _default_growth()

        # yfinance label bervariasi antar versi
        rev_labels = ["Total Revenue", "TotalRevenue", "Revenue", "Operating Revenue"]
        ni_labels  = ["Net Income", "NetIncome", "Net Income Common Stockholders",
                      "Net Income Common Stock Holders"]

        revenues    = _extract_financial_row(fin, rev_labels)
        net_incomes = _extract_financial_row(fin, ni_labels)

        # YoY revenue growth (index 0 = newest)
        rev_growth = 0.0
        if len(revenues) >= 2 and abs(revenues[1]) > 0:
            rev_growth = (revenues[0] - revenues[1]) / abs(revenues[1])

        # YoY earnings growth
        earn_growth = 0.0
        if len(net_incomes) >= 2 and abs(net_incomes[1]) > 0:
            earn_growth = (net_incomes[0] - net_incomes[1]) / abs(net_incomes[1])

        # Berapa tahun berturut-turut earnings naik (newest → oldest)
        positive_years = 0
        for j in range(len(net_incomes) - 1):
            if net_incomes[j] > net_incomes[j + 1] and net_incomes[j] > 0:
                positive_years += 1
            else:
                break

        # Revenue trend: 3+ tahun data → bisa lihat tren
        if len(revenues) >= 3:
            if revenues[0] > revenues[1] > revenues[2]:
                rev_trend = "growing"
            elif revenues[0] < revenues[1] < revenues[2]:
                rev_trend = "declining"
            else:
                rev_trend = "flat"
        elif len(revenues) >= 2:
            rev_trend = "growing" if revenues[0] > revenues[1] else (
                "declining" if revenues[0] < revenues[1] else "flat"
            )
        else:
            rev_trend = "unknown"

        # Sanity caps: growth > 500% atau < -100% kemungkinan data error
        rev_growth  = max(min(rev_growth, 5.0), -1.0)
        earn_growth = max(min(earn_growth, 5.0), -1.0)

        return {
            "rev_growth_yoy":       rev_growth,
            "earn_growth_yoy":      earn_growth,
            "growth_years_positive": positive_years,
            "revenue_trend":        rev_trend,
        }
    except Exception:
        return _default_growth()


# ════════════════════════════════════════════════════════════
#  BAGIAN 1 — VALIDASI TICKER
# ════════════════════════════════════════════════════════════

CACHE_MAX_HARI = 30  # cache kadaluarsa setelah 30 hari


def validasi_semua_ticker(force: bool = False, progress_cb=None) -> list:
    """
    Cek tiap ticker ke Yahoo Finance. Simpan ke cache.
    Cache otomatis kadaluarsa setelah CACHE_MAX_HARI hari.
    404 tidak muncul di terminal (sudah di-suppress).
    """
    import time as _time

    def _vcb(msg):
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    cache_ada    = os.path.exists(VALID_TICKERS_FILE)
    cache_expire = False

    if cache_ada and not force:
        usia_hari = (_time.time() - os.path.getmtime(VALID_TICKERS_FILE)) / 86400
        if usia_hari > CACHE_MAX_HARI:
            cache_expire = True
            print(f"  🔄 Cache {usia_hari:.0f} hari (> {CACHE_MAX_HARI} hari) — re-validasi...")

    if cache_ada and not force and not cache_expire:
        with open(VALID_TICKERS_FILE) as f:
            valid = [line.strip() for line in f if line.strip()]
        usia = (_time.time() - os.path.getmtime(VALID_TICKERS_FILE)) / 86400
        print(f"  📋 Cache: {len(valid)} ticker valid (umur {usia:.0f}/{CACHE_MAX_HARI} hari)")
        return valid

    print(f"\n  🔍 Validasi {len(TICKERS)} ticker...")
    print(f"     404 dan ticker tidak ditemukan akan di-skip otomatis\n")

    valid, invalid = [], []
    total = len(TICKERS)

    for i, ticker in enumerate(TICKERS, 1):
        pct  = int((i / total) * 30)
        bar  = "█" * pct + "░" * (30 - pct)
        kode = ticker.replace(".JK", "")
        _sprint(f"\r  [{bar}] {i:03d}/{total}  {kode:<8}")

        # Progress callback supaya bot.py bisa update status
        if i % 5 == 0 or i == total:
            _vcb(f"Validasi [{i}/{total}] {kode}")

        # _safe_info: tidak akan print 404 ke terminal
        info  = _safe_info(ticker)
        harga = info.get("regularMarketPrice") or info.get("currentPrice") or 0

        try:
            harga = float(harga)
        except (TypeError, ValueError):
            harga = 0.0

        if harga > 0:
            valid.append(ticker)
            _sprint(" ✓\n")
        else:
            invalid.append(ticker)
            _sprint(" ✗\n")

        time.sleep(0.4)

    with open(VALID_TICKERS_FILE, "w") as f:
        f.write("\n".join(valid))

    print(f"\n  ✅ Valid  : {len(valid)} ticker")
    print(f"  ❌ Skip   : {len(invalid)} ticker")
    if invalid:
        kodes = [t.replace(".JK","") for t in invalid]
        print(f"     → {', '.join(kodes)}")
    print(f"  📁 Cache  : {VALID_TICKERS_FILE}\n")
    return valid


# ════════════════════════════════════════════════════════════
#  BAGIAN 2 — FETCH DATA
# ════════════════════════════════════════════════════════════

def fetch_info(ticker: str) -> dict:
    """
    Ambil fundamental + harga dari Yahoo Finance (tanpa noise).
    Raise ValueError jika harga tidak tersedia.

    Catatan normalisasi:
    - DER     : Yahoo Finance kirim dalam persen (45.5 = 0.455x), kita bagi 100
    - ROE     : Yahoo Finance kirim dalam desimal (0.185 = 18.5%), kita simpan apa adanya
    - div_yield: TIDAK diambil dari Yahoo Finance — dihitung manual di fetch_div_info()
                 karena Yahoo Finance sering kirim nilai yang salah untuk saham IDX
    - PBV     : Yahoo Finance sering return nilai absurd (>1000x) untuk saham IDX
                yang sudah stock split/aksi korporasi — di-cap di 50x sebagai sanity check
    """
    info  = _safe_info(ticker)
    harga = float(info.get("regularMarketPrice") or info.get("currentPrice") or 0)

    if harga <= 0:
        raise ValueError(f"Harga {ticker} tidak tersedia")

    # ── DER normalisasi ────────────────────────────────────
    # Yahoo Finance kirim debtToEquity dalam persen:
    #   BBCA: 571.7  → 571.7/100 = 5.717x ✓
    #   UNVR: 235.0  → 235.0/100 = 2.35x  ✓
    # Threshold > 5 untuk bedakan "sudah rasio" vs "dalam persen"
    der_raw = float(info.get("debtToEquity") or 0)
    if der_raw > 5:
        der = der_raw / 100     # dalam persen → ubah ke rasio
    else:
        der = der_raw           # sudah dalam bentuk rasio

    # ── PBV sanity check ──────────────────────────────────
    # Yahoo Finance sering return PBV absurd untuk saham IDX (contoh:
    # BYAN 117771x, ADRO 13647x, CUAN 248333x). Ini terjadi karena:
    # - Book value per share belum di-adjust setelah stock split
    # - Corporate action (spin-off, merger) belum tercermin
    # - Data error dari provider
    # PBV > 50x sangat tidak wajar bahkan untuk growth stock Indonesia.
    # Set ke 0 (unknown) agar tidak merusak median & scoring.
    pbv_raw = float(info.get("priceToBook") or 0)
    if pbv_raw > 50 or pbv_raw < -50:
        pbv_raw = 0.0  # treat as unknown data

    # ── PER sanity check ──────────────────────────────────
    # PER > 200 biasanya data error atau perusahaan baru listing
    # PER < 0 artinya rugi — set ke 0 (dihandle terpisah di scoring)
    per_raw = float(info.get("trailingPE") or 0)
    if per_raw > 200 or per_raw < 0:
        per_raw = 0.0

    return {
        "harga":           harga,
        "nama":            str(info.get("longName")         or ticker.replace(".JK","")),
        "sektor":          str(info.get("sector")           or "-"),
        "per":             per_raw,
        "pbv":             pbv_raw,
        "roe":             float(info.get("returnOnEquity") or 0),  # desimal: 0.185
        "der":             der,
        "div_yield":       0.0,   # placeholder — diisi fetch_div_info() di scoring loop
        "earnings_growth": float(info.get("earningsGrowth") or 0),
        "revenue_growth":  float(info.get("revenueGrowth")  or 0),
        "profit_margin":   float(info.get("profitMargins")  or 0),
        "mkt_cap":         float(info.get("marketCap")      or 0),
    }


def fetch_history(ticker: str) -> tuple:
    """
    Ambil historis harga 2 tahun (tanpa noise).
    Return: (close, high, low, volume) — 4 pd.Series.
    High & Low dibutuhkan untuk Money Flow Index (MFI).
    """
    empty = pd.Series(dtype=float)
    df = _safe_download(ticker, period="2y")

    if df.empty:
        return empty, empty, empty, empty

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close  = df["Close"].squeeze().dropna().astype(float)  if "Close"  in df.columns else empty
    high   = df["High"].squeeze().dropna().astype(float)   if "High"   in df.columns else empty
    low    = df["Low"].squeeze().dropna().astype(float)    if "Low"    in df.columns else empty
    volume = df["Volume"].squeeze().dropna().astype(float) if "Volume" in df.columns else empty
    return close, high, low, volume


def fetch_div_info(ticker: str, harga: float) -> tuple:
    """
    Hitung dividend yield dan jumlah tahun dari riwayat dividen AKTUAL.

    Return: (div_yield: float, div_tahun: int)

    KENAPA TIDAK PAKAI dividendYield dari Yahoo Finance?
    Yahoo Finance sering mengembalikan nilai yang tidak akurat untuk saham IDX.
    Contoh: SSIA actual yield 0.9%, tapi Yahoo bisa return 0.96
    (yang ditampilkan jadi 96% karena kita kalikan 100).

    Solusi: hitung sendiri = total dividen trailing / harga saham sekarang
    Ini adalah cara perhitungan yang benar dan dipakai analis profesional.

    v6 FIX: Window diperlebar ke 18 bulan untuk menangkap dividen tahunan
    yang jadwalnya bergeser (contoh: TLKM bayar dividen Juni, kalau window
    tepat 12 bulan dan hari ini Juni juga, dividen bisa terlewat karena
    selisih hari). Lalu dinormalisasi ke 12 bulan.
    Fallback: jika TTM kosong tapi ada riwayat, hitung dari dividen terakhir.
    """
    try:
        divs = _safe_dividends(ticker)
        if divs is None or len(divs) == 0:
            return 0.0, 0

        # Jumlah tahun yang pernah bayar dividen
        div_tahun = int(divs.index.year.nunique())

        if harga <= 0:
            return 0.0, div_tahun

        # ── Normalize timezone ────────────────────────────
        # yfinance kadang return tz-aware, kadang tidak
        try:
            idx = divs.index
            if hasattr(idx, 'tz') and idx.tz is not None:
                now = pd.Timestamp.now(tz='UTC')
                divs_clean = divs.copy()
                divs_clean.index = idx.tz_convert('UTC')
            else:
                now = pd.Timestamp.now()
                divs_clean = divs.copy()
                if hasattr(idx, 'tz_localize'):
                    divs_clean.index = idx.tz_localize(None)
        except Exception:
            now = pd.Timestamp.now()
            divs_clean = divs.copy()
            try:
                divs_clean.index = divs_clean.index.tz_localize(None)
            except Exception:
                pass

        # ── Strategi 1: Window 18 bulan, normalisasi ke 12 ─
        # Pakai 18 bulan untuk menangkap dividen tahunan yang jadwalnya
        # bergeser. Lalu bagi total dengan (bulan_aktual/12) untuk normalisasi.
        batas_18bln = now - pd.DateOffset(months=18)
        batas_12bln = now - pd.DateOffset(months=12)

        mask_18 = divs_clean.index >= batas_18bln
        mask_12 = divs_clean.index >= batas_12bln

        divs_18bln = divs_clean[mask_18]
        divs_12bln = divs_clean[mask_12]

        total_div = 0.0
        if len(divs_12bln) > 0:
            # Ada dividen dalam 12 bulan — pakai langsung
            total_div = float(divs_12bln.sum())
        elif len(divs_18bln) > 0:
            # Tidak ada dalam 12 bulan, tapi ada dalam 18 bulan
            # Ini menangkap kasus TLKM yang dividennya pas di batas window
            total_div = float(divs_18bln.sum())
            # Normalisasi: kalau dividen ada di bulan 13-18, anggap tahunan
            # (tidak perlu skala karena ini dividen annual yang bergeser)

        # ── Strategi 2: Fallback dari rata-rata 3 tahun terakhir ──
        if total_div <= 0 and div_tahun >= 3:
            batas_3thn = now - pd.DateOffset(years=3)
            divs_3thn = divs_clean[divs_clean.index >= batas_3thn]
            if len(divs_3thn) > 0:
                # Rata-rata dividen per tahun dari 3 tahun terakhir
                tahun_unik = divs_3thn.index.year.nunique()
                if tahun_unik > 0:
                    total_div = float(divs_3thn.sum()) / tahun_unik

        if total_div <= 0:
            return 0.0, div_tahun

        yield_calc = total_div / harga

        # ── Sanity check ──────────────────────────────────
        # Yield > 25% sangat tidak wajar untuk saham IDX
        # Kemungkinan data error dari Yahoo Finance
        if yield_calc <= 0 or yield_calc > 0.25:
            return 0.0, div_tahun

        return round(yield_calc, 6), div_tahun

    except Exception:
        return 0.0, 0


# ════════════════════════════════════════════════════════════
#  BAGIAN 3 — INDIKATOR TEKNIKAL
# ════════════════════════════════════════════════════════════

def hitung_rsi(close: pd.Series, period: int = 14) -> float:
    """
    RSI dengan Wilder's Smoothing — standar TradingView/MT5.
    alpha = 1/period → EWM com = period-1.
    Selalu return float 0.0-100.0.
    """
    if len(close) < period + 1:
        return 0.0
    try:
        delta    = close.diff().dropna()
        gain     = delta.clip(lower=0)
        loss     = (-delta.clip(upper=0))
        avg_gain = gain.ewm(com=period-1, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(com=period-1, min_periods=period, adjust=False).mean()
        lg = float(avg_gain.iloc[-1])
        ll = float(avg_loss.iloc[-1])
        if ll == 0: return 100.0
        if lg == 0: return 0.0
        rsi = 100.0 - (100.0 / (1.0 + lg / ll))
        if np.isnan(rsi) or np.isinf(rsi): return 0.0
        return round(rsi, 1)
    except Exception:
        return 0.0


def hitung_macd(close: pd.Series) -> str:
    """
    MACD standar: EMA12 - EMA26, Signal = EMA9(MACD).
    Selalu return str.
    """
    if len(close) < 35:
        return "-"
    try:
        macd   = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        signal = macd.ewm(span=9, adjust=False).mean()
        mn, mp = float(macd.iloc[-1]),   float(macd.iloc[-2])
        sn, sp = float(signal.iloc[-1]), float(signal.iloc[-2])
        if mp < sp and mn > sn: return "Bullish Cross ✓"
        if mp > sp and mn < sn: return "Bearish Cross ✗"
        if mn > sn and sn > 0:  return "Bullish"
        if mn > sn:             return "Bullish Lemah"
        return "Bearish"
    except Exception:
        return "-"


def hitung_ma_trend(close: pd.Series) -> str:
    """SMA50 vs SMA200. Selalu return str."""
    if len(close) < 50:
        return "-"
    try:
        harga = float(close.iloc[-1])
        ma50  = float(close.tail(50).mean())
        if len(close) >= 200:
            ma200 = float(close.tail(200).mean())
            if harga > ma50 and ma50 > ma200: return "Uptrend Kuat"
            if harga > ma50:                  return "Di Atas MA50"
            if harga > ma200:                 return "Di Atas MA200"
            return "Downtrend"
        return "Di Atas MA50" if harga > ma50 else "Di Bawah MA50"
    except Exception:
        return "-"


def hitung_momentum(close: pd.Series, hari: int = 63) -> float:
    """Return % perubahan harga 3 bulan. Selalu return float."""
    if len(close) < hari:
        return 0.0
    try:
        kini = float(close.iloc[-1])
        lalu = float(close.iloc[-hari])
        if lalu <= 0: return 0.0
        return round((kini / lalu - 1) * 100, 1)
    except Exception:
        return 0.0


def hitung_volume_trend(volume: pd.Series, close: pd.Series) -> str:
    """
    Analisis volume — konfirmasi sinyal harga.
    Referensi: On Balance Volume (Joe Granville), Volume Price Analysis (Anna Coulling).

    Prinsip utama:
    - Harga naik + volume naik signifikan = Akumulasi (smart money masuk)
    - Harga naik + volume normal          = Konfirmasi tren
    - Harga turun + volume naik           = Distribusi (smart money keluar)
    - Volume rendah saat rally             = Tren lemah, waspadai reversal

    Return: str — "Akumulasi", "Konfirmasi", "Distribusi", "Lemah", "Normal", "-"
    """
    if volume is None or len(volume) < 20 or len(close) < 20:
        return "-"
    try:
        vol_20 = float(volume.tail(20).mean())
        vol_50 = float(volume.tail(50).mean()) if len(volume) >= 50 else float(volume.mean())

        if vol_50 <= 0:
            return "-"

        price_chg = float(close.iloc[-1] / close.iloc[-20] - 1) if float(close.iloc[-20]) > 0 else 0.0
        vol_ratio = vol_20 / vol_50

        if price_chg > 0.02 and vol_ratio > 1.3:
            return "Akumulasi"       # Harga naik + volume spike → smart money masuk
        elif price_chg > 0 and vol_ratio >= 0.9:
            return "Konfirmasi"      # Harga naik + volume normal/up → tren sehat
        elif price_chg < -0.02 and vol_ratio > 1.3:
            return "Distribusi"      # Harga turun + volume spike → smart money keluar
        elif price_chg > 0.02 and vol_ratio < 0.7:
            return "Lemah"           # Harga naik tapi volume kering → tren rapuh
        else:
            return "Normal"
    except Exception:
        return "-"


def hitung_mfi(high: pd.Series, low: pd.Series, close: pd.Series,
               volume: pd.Series, period: int = 14) -> float:
    """
    Money Flow Index — "RSI berbasis volume".
    Mengukur tekanan beli vs jual berdasarkan typical price × volume.

    Interpretasi:
    - MFI < 20  = oversold + uang masuk besar → AKUMULASI KUAT (smart money masuk)
    - MFI < 40  = zona beli, arus uang mulai masuk
    - MFI > 80  = overbought + uang keluar → DISTRIBUSI (smart money keluar)
    - MFI 40-80 = netral

    Referensi: Gene Quong & Avrum Soudack (1989), Investopedia MFI.
    Data: typical_price = (High + Low + Close) / 3, dari Yahoo Finance.

    Return: float 0.0–100.0 (default 50.0 kalau data tidak cukup)
    """
    if (len(high) < period + 1 or len(low) < period + 1
            or len(close) < period + 1 or len(volume) < period + 1):
        return 50.0
    try:
        # Typical price = (High + Low + Close) / 3
        tp = (high + low + close) / 3.0
        raw_mf = tp * volume  # raw money flow

        tp_diff = tp.diff()

        # Positive money flow = raw_mf pada hari typical price naik
        pos_mf = raw_mf.where(tp_diff > 0, 0.0)
        neg_mf = raw_mf.where(tp_diff < 0, 0.0)

        pos_sum = pos_mf.rolling(window=period, min_periods=period).sum()
        neg_sum = neg_mf.rolling(window=period, min_periods=period).sum()

        # Money Flow Ratio & MFI
        # Hindari division by zero
        neg_safe = neg_sum.replace(0, np.nan)
        mfr = pos_sum / neg_safe
        mfi = 100.0 - (100.0 / (1.0 + mfr))

        val = float(mfi.iloc[-1])
        if np.isnan(val) or np.isinf(val):
            return 50.0
        return round(max(0.0, min(100.0, val)), 1)
    except Exception:
        return 50.0


def hitung_obv_trend(close: pd.Series, volume: pd.Series) -> str:
    """
    On-Balance Volume (OBV) Trend — deteksi akumulasi/distribusi diam-diam.

    OBV = kumulatif volume: +volume di hari harga naik, -volume di hari harga turun.
    Divergence antara OBV dan harga = sinyal kuat:

    - "Bullish Div"  = OBV naik tapi harga turun → SMART MONEY MASUK diam-diam
                       Ini yang terjadi sebelum saham "terbang" — institusi akumulasi
                       sementara retail panik jual.
    - "Bearish Div"  = OBV turun tapi harga naik → SMART MONEY KELUAR
                       Harga naik tapi bukan karena beli kuat — waspadai jatuh.
    - "Confirm Up"   = OBV naik + harga naik → tren naik solid
    - "Confirm Down"  = OBV turun + harga turun → tren turun solid
    - "-"            = data tidak cukup

    Referensi: Joe Granville "New Key to Stock Market Profits" (1963).
    """
    if len(close) < 30 or len(volume) < 30:
        return "-"
    try:
        # Hitung OBV
        price_diff = close.diff()
        direction = price_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        obv = (volume * direction).cumsum()

        # Bandingkan tren 20 hari terakhir
        obv_now  = float(obv.iloc[-1])
        obv_20   = float(obv.iloc[-20])
        price_now = float(close.iloc[-1])
        price_20  = float(close.iloc[-20])

        obv_pct   = (obv_now - obv_20) / abs(obv_20) * 100 if abs(obv_20) > 0 else 0
        price_pct = (price_now - price_20) / price_20 * 100 if price_20 > 0 else 0

        # Threshold: perubahan signifikan = > 3%
        obv_up   = obv_pct > 3
        obv_down = obv_pct < -3
        price_up   = price_pct > 2
        price_down = price_pct < -2

        if obv_up and price_down:
            return "Bullish Div"    # Smart money masuk saat harga turun
        elif obv_down and price_up:
            return "Bearish Div"    # Smart money keluar saat harga naik
        elif obv_up and price_up:
            return "Confirm Up"     # Tren naik dikonfirmasi volume
        elif obv_down and price_down:
            return "Confirm Down"   # Tren turun dikonfirmasi volume
        else:
            return "Netral"
    except Exception:
        return "-"


# ════════════════════════════════════════════════════════════
#  BAGIAN 4 — SCORING (maks 100 poin)
#
#  v7: Rebalance dengan komponen GROWTH + DOWNTREND PENALTY.
#  Investor ritel butuh: (1) growth mendatang bagus, (2) dividen aman & besar,
#  (3) harga TIDAK dalam tren turun.
#
#  Kualitas(36): ROE(12) + DER(8) + Laba(4) + PM(6) + MktCap(6)
#  Dividen(20) : Konsistensi(10) + Yield(10)
#  Valuasi(10) : PER(5) + PBV(5)
#  Growth(14)  : RevGrowth(4) + EarnGrowth(4) + MultiYear(6)
#  Teknikal(20): MA(5) + RSI(5) + MACD(4) + Vol(3) + Mom(3)
#  Penalti     : Downtrend tanpa reversal(−3) + Momentum tanpa reversal(−2)
#  Bonus       : Buy-the-dip (downtrend + 2 sinyal reversal)(+2)
#
#  CATATAN PENTING:
#  - Recovery growth di-cap: revenue flat tapi earnings spike > 50%
#    hanya dapat partial credit (2/4), bukan full.
#  - Downtrend SMART penalty: saham downtrend TANPA sinyal reversal
#    dihukum −3. Tapi kalau ada MACD bullish/golden cross, RSI buy zone,
#    atau volume akumulasi → penalty dihapus atau jadi BONUS +2.
#    Ini menangkap peluang "buy the dip" blue-chip.
# ════════════════════════════════════════════════════════════

def hitung_skor(info: dict, close: pd.Series, div_tahun: int,
                median_per: float, median_pbv: float,
                volume: pd.Series = None,
                high: pd.Series = None,
                low: pd.Series = None,
                sector_median_per: float = None,
                sector_median_pbv: float = None,
                growth_data: dict = None) -> dict:
    """
    Return dict dengan keys: skor, status, alasan, rsi, macd, ma, mom, roe_pct,
                              mfi, obv
    Semua tipe konsisten — tidak pernah campur tuple/None.

    high, low: pd.Series dari fetch_history() — dibutuhkan untuk MFI.
    growth_data: dict dari fetch_growth_data() — revenue/earnings multi-year.
    """
    div_tahun = int(div_tahun) if div_tahun is not None else 0
    skor, alasan = 0, []
    if growth_data is None:
        growth_data = _default_growth()

    # Pilih median yang tepat: sektor kalau tersedia, fallback ke market
    val_median_per = sector_median_per if sector_median_per and sector_median_per > 0 else median_per
    val_median_pbv = sector_median_pbv if sector_median_pbv and sector_median_pbv > 0 else median_pbv

    # ── A. KUALITAS (36 poin) ──────────────────────────────
    # ROE (12)
    roe     = float(info.get("roe", 0) or 0)
    roe_pct = roe * 100 if abs(roe) <= 2 else roe
    roe_pct = min(roe_pct, 100.0) if roe_pct > 0 else max(roe_pct, -100.0)
    if roe_pct >= 20:   skor += 12; alasan.append(f"✓ Profitabilitas sangat kuat (ROE {roe_pct:.1f}%)")
    elif roe_pct >= 15: skor += 9;  alasan.append(f"✓ Profitabilitas baik (ROE {roe_pct:.1f}%)")
    elif roe_pct >= 10: skor += 6;  alasan.append(f"✓ Profitabilitas cukup (ROE {roe_pct:.1f}%)")
    elif roe_pct >= 5:  skor += 3
    elif roe_pct < 0:   alasan.append(f"⚠ ROE negatif ({roe_pct:.1f}%) — perusahaan merugi")

    # DER (8)
    der = float(info.get("der", 0) or 0)
    sektor = str(info.get("sektor", "")).lower()
    is_financial = "financial" in sektor or "bank" in sektor

    if is_financial:
        if der == 0:
            skor += 6
            alasan.append("✓ Bank — DER tinggi wajar (leverage = bisnis utama)")
        elif der <= 7:    skor += 8; alasan.append(f"✓ DER wajar untuk bank ({der:.2f}x)")
        elif der <= 10:   skor += 5; alasan.append(f"✓ DER cukup untuk bank ({der:.2f}x)")
        else:             alasan.append(f"⚠ DER tinggi bahkan untuk bank ({der:.2f}x)")
    else:
        if der == 0:
            pass  # data tidak tersedia — 0 poin tanpa penalti
        elif der <= 0.5: skor += 8; alasan.append(f"✓ Utang sangat rendah (DER {der:.2f}x)")
        elif der <= 1.0: skor += 6; alasan.append(f"✓ Utang sehat (DER {der:.2f}x)")
        elif der <= 2.0: skor += 3
        else:            alasan.append(f"⚠ Utang tinggi (DER {der:.2f}x)")

    # Laba bersih positif (4) — basic profitability check
    pm     = float(info.get("profit_margin", 0) or 0)
    pm_pct = pm * 100 if abs(pm) <= 1 else pm
    if pm_pct > 0:
        skor += 4  # perusahaan profitable
    elif pm_pct < -5:
        alasan.append(f"⚠ Perusahaan merugi (margin {pm_pct:.0f}%)")

    # Profit Margin (6)
    if pm_pct > 20:   skor += 6; alasan.append(f"✓ Margin laba tebal ({pm_pct:.0f}%)")
    elif pm_pct > 10: skor += 4; alasan.append(f"✓ Margin laba sehat ({pm_pct:.0f}%)")
    elif pm_pct > 5:  skor += 2
    elif pm_pct > 0:  skor += 1

    # Market Cap (6)
    mkt_cap = float(info.get("mkt_cap", 0) or 0)
    if mkt_cap >= 100e12:  skor += 6; alasan.append("✓ Mega-cap blue-chip terpercaya")
    elif mkt_cap >= 50e12: skor += 5; alasan.append("✓ Large-cap, likuiditas tinggi")
    elif mkt_cap >= 10e12: skor += 4
    elif mkt_cap >= 1e12:  skor += 2
    elif mkt_cap > 0:      skor += 1

    # ── B. DIVIDEN (20 poin) ───────────────────────────────
    if div_tahun >= 5:   skor += 10; alasan.append(f"✓ Dividen konsisten {div_tahun} tahun berturut-turut")
    elif div_tahun >= 3: skor += 7;  alasan.append(f"✓ Dividen rutin {div_tahun} tahun")
    elif div_tahun >= 1: skor += 3;  alasan.append(f"✓ Ada riwayat dividen ({div_tahun} tahun)")

    dy = float(info.get("div_yield", 0) or 0)
    if dy >= 0.08:   skor += 10; alasan.append(f"✓ Yield dividen sangat tinggi ({dy*100:.1f}%)")
    elif dy >= 0.05: skor += 8;  alasan.append(f"✓ Yield dividen menarik ({dy*100:.1f}%)")
    elif dy >= 0.03: skor += 5;  alasan.append(f"✓ Ada dividen ({dy*100:.1f}%)")
    elif dy > 0:     skor += 2

    # ── C. VALUASI (10 poin) ───────────────────────────────
    per = float(info.get("per", 0) or 0)
    pbv = float(info.get("pbv", 0) or 0)

    skor_valuasi = 0

    # PER (5)
    if per > 0 and val_median_per > 0:
        if per <= val_median_per * 0.6:   skor_valuasi += 5; alasan.append(f"✓ Valuasi sangat murah (PER {per:.1f}x vs median sektor {val_median_per:.1f}x)")
        elif per <= val_median_per:       skor_valuasi += 3; alasan.append(f"✓ Valuasi wajar (PER {per:.1f}x vs median sektor {val_median_per:.1f}x)")
        elif per <= val_median_per * 1.5: skor_valuasi += 1

    # PBV (5) — PBV=0 berarti unknown → skip
    if pbv > 0 and val_median_pbv > 0:
        if pbv <= 1.0:                    skor_valuasi += 5; alasan.append(f"✓ Harga di bawah nilai buku (PBV {pbv:.2f}x)!")
        elif pbv <= val_median_pbv * 0.7: skor_valuasi += 4; alasan.append(f"✓ PBV murah ({pbv:.2f}x vs median sektor {val_median_pbv:.1f}x)")
        elif pbv <= val_median_pbv:       skor_valuasi += 2; alasan.append(f"✓ PBV di bawah median ({pbv:.2f}x)")
        elif pbv <= val_median_pbv * 1.5: skor_valuasi += 1

    # Quality premium floor (min 3/10)
    is_quality = (
        mkt_cap >= 50e12
        and roe_pct >= 12
        and div_tahun >= 5
    )
    if is_quality and skor_valuasi < 3:
        skor_valuasi = 3
        alasan.append("✓ Valuasi premium wajar (quality blue-chip)")

    skor += skor_valuasi

    # ── D. GROWTH (14 poin) ────────────────────────────────
    # Komponen paling penting untuk membedakan growth stock vs value trap.
    # Saham murah + stagnan (BJTM, GJTL) akan kehilangan poin di sini.
    # Saham premium + tumbuh (BBCA, BMRI) justru mendapat bonus.
    skor_growth = 0

    # Ambil data growth dulu (dipakai di beberapa tempat di bawah)
    gyp = growth_data.get("growth_years_positive", 0)
    rev_trend = growth_data.get("revenue_trend", "unknown")

    # Revenue Growth YoY (4)
    rg = growth_data.get("rev_growth_yoy", 0)
    rg_pct = rg * 100 if abs(rg) <= 2 else rg
    rg_pct = max(min(rg_pct, 500.0), -100.0)
    if rg_pct > 15:    skor_growth += 4; alasan.append(f"✓ Revenue tumbuh kuat ({rg_pct:.0f}% YoY)")
    elif rg_pct > 5:   skor_growth += 3; alasan.append(f"✓ Revenue tumbuh ({rg_pct:.0f}% YoY)")
    elif rg_pct > 0:   skor_growth += 2
    elif rg_pct < -5:  alasan.append(f"⚠ Revenue menurun ({rg_pct:.0f}% YoY)")

    # Earnings Growth YoY (4)
    eg = growth_data.get("earn_growth_yoy", 0)
    eg_pct = eg * 100 if abs(eg) <= 2 else eg
    eg_pct = max(min(eg_pct, 500.0), -100.0)

    # Cap recovery growth: revenue flat/declining tapi earnings spike > 50%
    # → kemungkinan besar recovery dari base rendah, bukan growth nyata.
    # Contoh: UNVR earnings -60% lalu +127% → masih di bawah level awal.
    is_recovery = (rev_trend in ("flat", "declining") and eg_pct > 50)

    if is_recovery:
        skor_growth += 2  # hanya partial credit
        alasan.append(f"⚠ Laba +{eg_pct:.0f}% tapi revenue {rev_trend} — recovery, bukan growth")
    elif eg_pct > 20:    skor_growth += 4; alasan.append(f"✓ Laba tumbuh pesat ({eg_pct:.0f}% YoY)")
    elif eg_pct > 10:  skor_growth += 3; alasan.append(f"✓ Laba tumbuh ({eg_pct:.0f}% YoY)")
    elif eg_pct > 0:   skor_growth += 2
    elif eg_pct < -10: alasan.append(f"⚠ Laba turun tajam ({eg_pct:.0f}% YoY)")

    # Multi-year consistency (6)
    # Berapa tahun berturut-turut earnings naik — ini yang membedakan
    # growth stock konsisten vs one-time spike
    if gyp >= 3:
        skor_growth += 6
        alasan.append(f"✓ Pertumbuhan konsisten {gyp} tahun berturut-turut")
    elif gyp == 2:
        skor_growth += 4
        alasan.append(f"✓ Pertumbuhan 2 tahun berturut-turut")
    elif gyp == 1:
        skor_growth += 2

    # Bonus/penalti revenue trend (within multi-year allocation)
    if rev_trend == "declining" and skor_growth > 0:
        skor_growth = max(skor_growth - 2, 0)
        alasan.append("⚠ Revenue tren menurun — pertumbuhan tidak sustainable")

    skor += skor_growth

    # ── D. TEKNIKAL (20 poin) ──────────────────────────────
    # Referensi teknikal analisis profesional:
    #   - John Murphy "Technical Analysis of the Financial Markets"
    #   - Alexander Elder "Trading for a Living" (Triple Screen)
    #   - Joe Granville (On Balance Volume)
    #   - Anna Coulling (Volume Price Analysis)
    #   - Ellen May, Ryan Filbert (teknikal saham Indonesia)
    #
    # Distribusi: MA(5) + RSI(5) + MACD(4) + Volume(3) + Momentum(3) = 20
    # + Flow Analysis: MFI + OBV (info + reversal signals, tidak punya poin sendiri)
    rsi_val, macd_val, ma_val, mom_val, vol_val = 0.0, "-", "-", 0.0, "-"
    mfi_val, obv_val = 50.0, "-"

    if len(close) >= 50:
        # ── MA Trend (5 poin) — tren jangka panjang ────────
        ma_val = hitung_ma_trend(close)
        if ma_val == "Uptrend Kuat":    skor += 5; alasan.append("✓ Tren bullish kuat (harga > MA50 > MA200)")
        elif ma_val == "Di Atas MA50":  skor += 3
        elif ma_val == "Di Atas MA200": skor += 1
        else: alasan.append("⚠ Harga di bawah MA50 & MA200 — tren melemah")

        # ── RSI (5 poin) — zona beli/jual ──────────────────
        rsi_val = hitung_rsi(close)
        if rsi_val == 100.0:
            skor += 2; alasan.append("⚠ RSI ekstrem (100) — waspadai koreksi")
        elif rsi_val > 0:
            if 30 <= rsi_val <= 50:   skor += 5; alasan.append(f"✓ RSI zona beli ideal ({rsi_val:.0f})")
            elif 50 < rsi_val <= 60:  skor += 4
            elif rsi_val < 30:        skor += 3; alasan.append(f"⚠ RSI oversold ({rsi_val:.0f}) — potensi reversal")
            elif rsi_val <= 70:       skor += 2
            else: alasan.append(f"⚠ RSI overbought ({rsi_val:.0f})")

        # ── MACD (4 poin) — sinyal beli/jual ───────────────
        macd_val = hitung_macd(close)
        if macd_val == "Bullish Cross ✓":  skor += 4; alasan.append("✓ MACD golden cross — sinyal beli kuat")
        elif macd_val == "Bullish":        skor += 3; alasan.append("✓ MACD bullish")
        elif macd_val == "Bullish Lemah":  skor += 2
        elif macd_val == "Bearish Cross ✗": alasan.append("⚠ MACD death cross — sinyal jual")
        elif macd_val == "Bearish":        alasan.append("⚠ MACD bearish")

        # ── Volume Trend (3 poin) — konfirmasi smart money ─
        vol_val = hitung_volume_trend(volume, close)
        if vol_val == "Akumulasi":     skor += 3; alasan.append("✓ Volume akumulasi — smart money masuk")
        elif vol_val == "Konfirmasi":  skor += 2; alasan.append("✓ Volume mendukung tren")
        elif vol_val == "Lemah":       alasan.append("⚠ Volume kering saat rally — tren rapuh")
        elif vol_val == "Distribusi":  alasan.append("⚠ Volume distribusi — waspadai penurunan")

        # ── MFI — Money Flow Index (info + reversal signal) ──
        # MFI = RSI berbasis volume. Mendeteksi arus uang masuk/keluar.
        # Tidak diberi poin tersendiri — digunakan sebagai:
        # 1. Sinyal reversal di buy-the-dip (MFI < 25 = uang masuk kuat)
        # 2. Warning distribusi (MFI > 80 = uang keluar)
        mfi_val = 50.0
        if high is not None and low is not None and len(high) >= 15 and len(low) >= 15:
            mfi_val = hitung_mfi(high, low, close, volume)

        if mfi_val < 20:
            alasan.append(f"💰 MFI oversold ({mfi_val:.0f}) — arus uang masuk kuat")
        elif mfi_val < 40:
            alasan.append(f"💰 MFI zona beli ({mfi_val:.0f}) — arus uang mulai masuk")
        elif mfi_val > 80:
            alasan.append(f"⚠ MFI overbought ({mfi_val:.0f}) — arus uang keluar, waspadai koreksi")

        # ── OBV — On-Balance Volume Trend (info + reversal signal) ──
        # Deteksi smart money yang akumulasi/distribusi DIAM-DIAM.
        # OBV naik + harga turun = smart money masuk sebelum harga naik.
        obv_val = hitung_obv_trend(close, volume)

        if obv_val == "Bullish Div":
            alasan.append("💰 OBV bullish divergence — smart money akumulasi diam-diam!")
        elif obv_val == "Bearish Div":
            alasan.append("⚠ OBV bearish divergence — smart money keluar diam-diam")

        # ── Momentum 3 Bulan (3 poin) ──────────────────────
        mom_val = hitung_momentum(close)
        if mom_val > 15:   skor += 3; alasan.append(f"✓ Momentum kuat (+{mom_val:.1f}%)")
        elif mom_val > 5:  skor += 2; alasan.append(f"✓ Momentum positif (+{mom_val:.1f}%)")
        elif mom_val > 0:  skor += 1
        else: alasan.append(f"⚠ Harga turun 3 bulan ({mom_val:.1f}%)")

        # ── Penalti/Bonus Downtrend — Buy the Dip Logic ────
        # Saham blue-chip turun BUKAN selalu buruk — justru bisa jadi
        # peluang beli kalau ada sinyal teknikal reversal.
        # Contoh: BBRI turun tapi MACD golden cross → potensi rebound.
        #
        # 5 sinyal reversal yang dicek:
        # 1. MACD golden cross / bullish
        # 2. RSI buy zone (30-50)
        # 3. Volume akumulasi
        # 4. MFI < 25 (arus uang masuk kuat)
        # 5. OBV bullish divergence (smart money akumulasi)
        #
        # Logika:
        # - 2+ sinyal → BONUS +2 (buy the dip!)
        # - 1 sinyal  → netral (pantau)
        # - 0 sinyal  → PENALTI -3 (hindari)
        if ma_val == "Downtrend":
            # Hitung berapa sinyal reversal yang ada
            reversal_signals = 0
            reversal_detail = []

            if macd_val in ("Bullish Cross ✓", "Bullish"):
                reversal_signals += 1
                reversal_detail.append("MACD bullish")
            if 30 <= rsi_val <= 50:
                reversal_signals += 1
                reversal_detail.append(f"RSI buy zone ({rsi_val:.0f})")
            if vol_val == "Akumulasi":
                reversal_signals += 1
                reversal_detail.append("volume akumulasi")
            if mfi_val < 25:
                reversal_signals += 1
                reversal_detail.append(f"MFI oversold ({mfi_val:.0f})")
            if obv_val == "Bullish Div":
                reversal_signals += 1
                reversal_detail.append("OBV smart money masuk")

            if reversal_signals >= 2:
                # Strong buy-the-dip: downtrend + multiple reversal signals
                skor += 2
                sig_str = " + ".join(reversal_detail)
                alasan.append(f"⚡ Buy the dip! Downtrend + {sig_str} → potensi rebound (+2)")
            elif reversal_signals == 1:
                # Ada 1 sinyal reversal → netral, tidak dihukum tapi tidak diberi bonus
                sig_str = reversal_detail[0]
                alasan.append(f"⚡ Downtrend tapi {sig_str} — pantau potensi reversal")
            else:
                # Downtrend tanpa sinyal reversal → hindari
                skor -= 3
                alasan.append("✗ Downtrend tanpa sinyal reversal (−3)")

        # Penalti momentum turun tajam (>10% dalam 3 bulan)
        # Tapi kalau ada sinyal reversal (MACD bullish), jangan hukum
        if mom_val < -10:
            has_macd_reversal = macd_val in ("Bullish Cross ✓", "Bullish")
            if has_macd_reversal:
                alasan.append(f"⚡ Harga turun {mom_val:.1f}% tapi MACD mulai berbalik")
            else:
                skor -= 2
                alasan.append(f"✗ Momentum turun tajam {mom_val:.1f}% tanpa pembalikan (−2)")

    # Floor: skor minimum 0 (penalti bisa bikin negatif)
    skor = max(skor, 0)

    if skor >= SKOR_STRONG_BUY: status = "STRONG BUY"
    elif skor >= SKOR_BUY:      status = "BUY"
    elif skor >= SKOR_HOLD:     status = "HOLD"
    else:                        status = "PERINGATAN"

    return {
        "skor": int(skor), "status": status, "alasan": alasan,
        "rsi": rsi_val, "macd": macd_val, "ma": ma_val, "mom": mom_val,
        "vol": vol_val, "mfi": mfi_val, "obv": obv_val,
        "roe_pct": round(roe_pct, 1),
    }


# ════════════════════════════════════════════════════════════
#  BAGIAN 5 — TELEGRAM
# ════════════════════════════════════════════════════════════

def kirim_telegram(pesan: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    MAX = 4000

    def _send(text):
        try:
            r = requests.post(url, data={
                "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"
            }, timeout=15)
            return r.status_code == 200
        except Exception as e:
            print(f"  ⚠ Telegram: {e}")
            return False

    if len(pesan) <= MAX:
        return _send(pesan)

    bags, curr = [], ""
    for line in pesan.split("\n"):
        if len(curr) + len(line) + 1 > MAX:
            bags.append(curr); curr = line
        else:
            curr += ("\n" if curr else "") + line
    if curr: bags.append(curr)

    ok = True
    for i, part in enumerate(bags, 1):
        if not _send(f"📄 Bagian {i}/{len(bags)}\n\n{part}"): ok = False
        time.sleep(1)
    return ok


def format_dan_kirim(hasil: list):
    now    = datetime.now().strftime("%A, %d %B %Y %H:%M")
    strong = [r for r in hasil if r["status"] == "STRONG BUY"]
    buy    = [r for r in hasil if r["status"] == "BUY"]
    hold   = [r for r in hasil if r["status"] == "HOLD"]
    warn   = [r for r in hasil if r["status"] == "PERINGATAN"]
    bisa   = [r for r in hasil if r["bisa_dibeli"]]

    kirim_telegram("\n".join([
        "📊 <b>IDX STOCK SCREENER — LAPORAN MINGGUAN</b>",
        f"📅 {now}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Budget    : <b>Rp {BUDGET:,.0f}</b>",
        f"🔍 Dianalisis: <b>{len(hasil)} saham IDX</b>",
        "",
        "<b>Hasil:</b>",
        f"🟢🟢 STRONG BUY : {len(strong)}",
        f"🟢   BUY        : {len(buy)}",
        f"🟡   HOLD       : {len(hold)}",
        f"⚠️   PERINGATAN : {len(warn)}",
        f"💰   Bisa dibeli: {len(bisa)}",
    ]))
    time.sleep(1.5)

    top = sorted(
        [r for r in bisa if r["status"] in ("STRONG BUY","BUY","HOLD")],
        key=lambda x: x["skor"], reverse=True,
    )[:JUMLAH_REKOMENDASI]

    if top:
        lines = [f"🏆 <b>TOP {JUMLAH_REKOMENDASI} REKOMENDASI BULAN INI</b>", ""]
        for i, r in enumerate(top, 1):
            em  = "🟢🟢" if r["status"]=="STRONG BUY" else "🟢" if r["status"]=="BUY" else "🟡"
            dy  = f"{r['div_yield']*100:.1f}%" if r["div_yield"] > 0 else "–"
            rsi = f"{r['rsi']:.0f}" if r["rsi"] > 0 else "–"
            lines += [
                f"{i}. {em} <b>{r['ticker']}</b> — {r['status']}",
                f"   📌 {r['nama'][:45]}",
                f"   🏢 {r['sektor']}",
                f"   💵 Rp {r['harga']:,.0f}/lembar · 1 lot = Rp {r['harga_lot']:,.0f}",
                f"   📦 Rp {BUDGET:,.0f} → bisa beli {r['lot_maks']} lot",
                f"   📊 <b>Skor: {r['skor']}/100</b>",
                f"   📈 PER:{r['per']:.1f}x · PBV:{r['pbv']:.2f}x · ROE:{r['roe_pct']:.1f}%",
                f"   💰 Dividen: {dy} ({r['div_tahun']} th) · DER:{r['der']:.2f}x",
                f"   📉 RSI:{rsi} · MACD:{r['macd']} · Momentum:{r['mom']:+.1f}%",
                "", "   <b>Kenapa layak beli?</b>",
            ]
            for a in r["alasan"][:4]:
                lines.append(f"   {a}")
            lines += ["", "   👉 Cocok untuk akumulasi bertahap.", "──────────────────────", ""]
        kirim_telegram("\n".join(lines))
        time.sleep(1.5)

    ok = kirim_telegram(
        "⚠️ <i>Bukan rekomendasi investasi resmi. "
        "Data dari Yahoo Finance. Selalu DYOR sebelum membeli.</i>"
    )
    return ok


# ════════════════════════════════════════════════════════════
#  BAGIAN 6 — MAIN SCREENER
# ════════════════════════════════════════════════════════════

def run_screener(kirim_tg: bool = True, progress_cb=None) -> list:
    def _cb(msg):
        """Kirim progress ke callback (untuk logging ke file)."""
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    print("=" * 58)
    print("  IDX STOCK SCREENER v5")
    print(f"  {datetime.now().strftime('%d %B %Y %H:%M')}")
    print(f"  Budget: Rp {BUDGET:,.0f}")
    print("=" * 58)

    valid_tickers = validasi_semua_ticker(progress_cb=progress_cb)
    _cb(f"Validasi selesai: {len(valid_tickers)} ticker valid")
    if not valid_tickers:
        print("  ❌ Tidak ada ticker valid. Cek koneksi.")
        return []

    print(f"\n  Fetching {len(valid_tickers)} saham dari Yahoo Finance...")
    print("  (Estimasi: 3-5 menit)\n")

    semua_info, semua_close, semua_volume, semua_growth = {}, {}, {}, {}
    semua_high, semua_low = {}, {}
    per_list, pbv_list = [], []
    total = len(valid_tickers)

    for i, ticker in enumerate(valid_tickers, 1):
        kode = ticker.replace(".JK","")
        pct  = int((i / total) * 28)
        bar  = "█" * pct + "░" * (28 - pct)
        _sprint(f"\r  [{bar}] {i:03d}/{total}  {kode:<8}")

        try:
            info     = fetch_info(ticker)
            close, high, low, volume = fetch_history(ticker)
            gdata    = fetch_growth_data(ticker)
            semua_info[ticker]   = info
            semua_close[ticker]  = close
            semua_high[ticker]   = high
            semua_low[ticker]    = low
            semua_volume[ticker] = volume
            semua_growth[ticker] = gdata
            if info["per"] > 0: per_list.append(info["per"])
            if info["pbv"] > 0: pbv_list.append(info["pbv"])
            _sprint(" ✓\n")
            rg = gdata.get("rev_growth_yoy", 0) * 100
            eg = gdata.get("earn_growth_yoy", 0) * 100
            _cb(f"[{i:03d}/{total}] {kode:<8} ✓  Rp {info['harga']:,.0f}  PER:{info['per']:.1f}  RevG:{rg:+.0f}%  EarnG:{eg:+.0f}%")
        except Exception as e:
            _sprint(f" ✗\n")
            _cb(f"[{i:03d}/{total}] {kode:<8} ✗  skip ({str(e)[:40]})")

        time.sleep(0.3)

    if not semua_info:
        msg = "Screening gagal: tidak ada data dari Yahoo Finance."
        print(f"\n  \u274c {msg}")
        if kirim_tg:
            kirim_telegram(f"\u26a0\ufe0f <b>IDX Screener ERROR</b>\n\n{msg}\n\nCek koneksi internet.")
        raise RuntimeError(msg)

    median_per = round(float(pd.Series(per_list).median()), 1) if per_list else 15.0
    median_pbv = round(float(pd.Series(pbv_list).median()), 1) if pbv_list else 2.0

    # ── Hitung median PER/PBV per grup sektor ─────────────
    # Bank/financial vs non-financial punya range valuasi yang sangat berbeda.
    # Bank besar Indonesia (BBCA, BBRI, BMRI): PER 7-15x, PBV 1-3x
    # Tambang/commodity: PER 3-8x, PBV 0.3-1.5x
    # Membandingkan lintas sektor tidak apple-to-apple.
    #
    # Grup: financial, energy, consumer, industrial, other
    sector_per = {}  # {"financial": [7.2, 13.3, ...], ...}
    sector_pbv = {}
    for ticker, info in semua_info.items():
        sektor = str(info.get("sektor", "")).lower()
        # Simplifikasi ke grup besar
        if "financial" in sektor or "bank" in sektor:
            grp = "financial"
        elif "energy" in sektor:
            grp = "energy"
        elif "consumer" in sektor:
            grp = "consumer"
        elif "basic" in sektor or "material" in sektor:
            grp = "materials"
        else:
            grp = "other"

        if info["per"] > 0:
            sector_per.setdefault(grp, []).append(info["per"])
        if info["pbv"] > 0:
            sector_pbv.setdefault(grp, []).append(info["pbv"])

    # Hitung median per grup
    sector_median_per = {}
    sector_median_pbv = {}
    for grp in set(list(sector_per.keys()) + list(sector_pbv.keys())):
        if grp in sector_per and len(sector_per[grp]) >= 3:
            sector_median_per[grp] = round(float(pd.Series(sector_per[grp]).median()), 1)
        if grp in sector_pbv and len(sector_pbv[grp]) >= 3:
            sector_median_pbv[grp] = round(float(pd.Series(sector_pbv[grp]).median()), 1)

    _cb(f"Fetch selesai: {len(semua_info)} saham berhasil diambil")
    print(f"\n  Data OK   : {len(semua_info)} saham")
    print(f"  Median PER: {median_per}x | PBV: {median_pbv}x")
    for grp in sorted(sector_median_per.keys()):
        sm_per = sector_median_per.get(grp, '-')
        sm_pbv = sector_median_pbv.get(grp, '-')
        print(f"    {grp:12s}: PER {sm_per}x | PBV {sm_pbv}x")
    print("\n  Menghitung skor...\n")

    hasil  = []
    total2 = len(semua_info)

    for i, (ticker, info) in enumerate(semua_info.items(), 1):
        kode = ticker.replace(".JK","")
        _sprint(f"\r  [{i:03d}/{total2}] {kode:<10}")


        # Hitung div_yield dan div_tahun dari riwayat aktual
        # (bukan dari dividendYield Yahoo Finance yang sering salah)
        div_yield_calc, div_tahun = fetch_div_info(ticker, info["harga"])
        time.sleep(0.2)

        # Inject div_yield yang akurat ke info sebelum scoring
        info_scoring = dict(info)
        info_scoring["div_yield"] = div_yield_calc

        # Tentukan grup sektor untuk median yang tepat
        sektor_lower = str(info.get("sektor", "")).lower()
        if "financial" in sektor_lower or "bank" in sektor_lower:
            grp = "financial"
        elif "energy" in sektor_lower:
            grp = "energy"
        elif "consumer" in sektor_lower:
            grp = "consumer"
        elif "basic" in sektor_lower or "material" in sektor_lower:
            grp = "materials"
        else:
            grp = "other"

        s_med_per = sector_median_per.get(grp)
        s_med_pbv = sector_median_pbv.get(grp)

        close  = semua_close.get(ticker, pd.Series(dtype=float))
        high   = semua_high.get(ticker, pd.Series(dtype=float))
        low    = semua_low.get(ticker, pd.Series(dtype=float))
        volume = semua_volume.get(ticker, pd.Series(dtype=float))
        gdata  = semua_growth.get(ticker, _default_growth())
        sk     = hitung_skor(info_scoring, close, div_tahun, median_per, median_pbv,
                             volume=volume,
                             high=high,
                             low=low,
                             sector_median_per=s_med_per,
                             sector_median_pbv=s_med_pbv,
                             growth_data=gdata)
        _cb(f"[{i:03d}/{total2}] {kode:<8} — Skor:{sk['skor']:3d} {sk['status']:<12} RSI:{sk['rsi']:.0f} MFI:{sk['mfi']:.0f} MA:{sk['ma']}")

        harga     = info["harga"]
        harga_lot = int(harga * 100)

        base = {
            "ticker":     kode,
            "nama":       info["nama"],
            "sektor":     info["sektor"],
            "harga":      harga,
            "harga_lot":  harga_lot,
            "bisa_dibeli":harga_lot <= BUDGET,
            "lot_maks":   int(BUDGET // harga_lot) if harga_lot > 0 else 0,
            "per":        info["per"],
            "pbv":        info["pbv"],
            "roe_pct":    sk["roe_pct"],
            "der":        info["der"],
            "div_yield":  div_yield_calc,   # dari riwayat aktual, bukan Yahoo Finance
            "div_tahun":  div_tahun,
            "skor":       sk["skor"],
            "status":     sk["status"],
            "alasan":     sk["alasan"],
            "rsi":        sk["rsi"],
            "macd":       sk["macd"],
            "ma":         sk["ma"],
            "mom":        sk["mom"],
            "vol":        sk["vol"],
            "mfi":        sk["mfi"],
            "obv":        sk["obv"],
            "rev_growth": round(gdata.get("rev_growth_yoy", 0) * 100, 1),
            "earn_growth": round(gdata.get("earn_growth_yoy", 0) * 100, 1),
            "growth_years": gdata.get("growth_years_positive", 0),
            "rev_trend":  gdata.get("revenue_trend", "unknown"),
        }
        hasil.append(base)

    hasil.sort(key=lambda x: x["skor"], reverse=True)
    _cb(f"Scoring selesai: {len(hasil)} saham dianalisis")
    print("\n")

    strong = [r for r in hasil if r["status"] == "STRONG BUY"]
    buy    = [r for r in hasil if r["status"] == "BUY"]
    hold   = [r for r in hasil if r["status"] == "HOLD"]
    warn   = [r for r in hasil if r["status"] == "PERINGATAN"]
    bisa   = [r for r in hasil if r["bisa_dibeli"]]

    print(f"{'='*58}")
    print("  HASIL")
    print(f"{'='*58}")
    print(f"  🟢🟢 STRONG BUY : {len(strong)}")
    print(f"  🟢   BUY        : {len(buy)}")
    print(f"  🟡   HOLD       : {len(hold)}")
    print(f"  ⚠️   PERINGATAN : {len(warn)}")
    print(f"  💰   Bisa dibeli: {len(bisa)} (budget Rp {BUDGET:,.0f})")

    top = sorted(
        [r for r in bisa if r["status"] in ("STRONG BUY","BUY","HOLD")],
        key=lambda x: x["skor"], reverse=True,
    )[:JUMLAH_REKOMENDASI]

    print(f"\n  TOP {JUMLAH_REKOMENDASI} REKOMENDASI:")
    for i, r in enumerate(top, 1):
        em  = "🟢🟢" if r["status"]=="STRONG BUY" else "🟢" if r["status"]=="BUY" else "🟡"
        dy  = f"{r['div_yield']*100:.1f}%" if r["div_yield"] > 0 else "–"
        rsi = f"{r['rsi']:.0f}" if r["rsi"] > 0 else "–"
        print(f"\n  {i}. {em} {r['ticker']} — {r['status']} ({r['skor']}/100)")
        print(f"     {r['nama']}")
        print(f"     Rp {r['harga']:,.0f}/lembar · 1 lot=Rp {r['harga_lot']:,.0f} · {r['lot_maks']} lot")
        print(f"     PER:{r['per']:.1f}x PBV:{r['pbv']:.2f}x ROE:{r['roe_pct']:.1f}% Div:{dy} RSI:{rsi}")
        for a in r["alasan"][:3]: print(f"     {a}")

    # Simpan CSV
    rows = []
    for r in hasil:
        row = {k: v for k, v in r.items() if k != "alasan"}
        row["alasan"] = " | ".join(r["alasan"])
        rows.append(row)
    df  = pd.DataFrame(rows)
    ts  = datetime.now().strftime("%Y%m%d_%H%M")
    csv = f"hasil_{ts}.csv"
    df.to_csv(csv, index=False, encoding="utf-8-sig")
    _cb(f"CSV disimpan: {csv}")
    print(f"\n  ✅ Disimpan: {csv}")

    if kirim_tg:
        print("  📨 Mengirim ke Telegram...")
        tg_ok = format_dan_kirim(hasil)
        if tg_ok:
            print("  ✅ Laporan terkirim ke Telegram!\n")
        else:
            print("  ⚠ Telegram gagal — cek BOT_TOKEN & CHAT_ID di config.py\n")

    return hasil


# ════════════════════════════════════════════════════════════
#  BAGIAN 7 — SCHEDULER MINGGUAN
# ════════════════════════════════════════════════════════════

def weekly_job():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n[{ts}] ⏰ Weekly screening dimulai...")
    with open("scheduler.log", "a", encoding="utf-8") as f:
        f.write(f"[{ts}] Mulai\n")
    try:
        run_screener(kirim_tg=True)
        with open("scheduler.log", "a", encoding="utf-8") as f:
            f.write(f"[{ts}] Selesai ✅\n")
    except Exception as e:
        import traceback
        with open("scheduler.log", "a", encoding="utf-8") as f:
            f.write(f"[{ts}] ERROR: {e}\n{traceback.format_exc()}\n")
        print(f"  ❌ Error: {e}")


# ════════════════════════════════════════════════════════════
#  MENU
# ════════════════════════════════════════════════════════════

def menu():
    print("\n" + "="*55)
    print("  IDX STOCK SCREENER v5")
    print("="*55)
    print("  1. Jalankan screening + kirim Telegram")
    print("  2. Jalankan screening (tanpa Telegram)")
    print("  3. Validasi ulang ticker (hapus cache)")
    print("  4. Aktifkan scheduler (Senin 09:00 otomatis)")
    print("  0. Keluar")

    p = input("\n  Pilih: ").strip()

    if p == "1":
        run_screener(kirim_tg=True)
    elif p == "2":
        run_screener(kirim_tg=False)
    elif p == "3":
        if os.path.exists(VALID_TICKERS_FILE):
            os.remove(VALID_TICKERS_FILE)
            print("  🗑 Cache dihapus")
        validasi_semua_ticker(force=True)
        print("  ✅ Validasi selesai.")
        menu()   # kembali ke menu utama
    elif p == "4":
        print("\n" + "="*55)
        print("  SCHEDULER — Setiap Senin 09:00")
        print("="*55)
        print("\n  ✅ Aktif. JANGAN tutup terminal ini.")
        print("  📋 Log: scheduler.log | 🛑 Stop: Ctrl+C\n")

        # Jalankan sekali sekarang — scheduler tetap aktif meski gagal
        try:
            run_screener(kirim_tg=True)
        except Exception as e:
            print(f"  ⚠ Run pertama error: {e}")
            print("  Scheduler tetap aktif — akan coba lagi Senin depan.\n")
            with open("scheduler.log", "a", encoding="utf-8") as _f:
                _f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Run pertama ERROR: {e}\n")

        schedule.every().monday.at("09:00").do(weekly_job)
        while True:
            schedule.run_pending()
            nxt = schedule.next_run()
            if nxt:
                sisa = nxt - datetime.now()
                h = int(sisa.total_seconds() // 3600)
                m = int((sisa.total_seconds() % 3600) // 60)
                _sprint(f"\r  ⏳ Berikutnya: {nxt.strftime('%A %d %b %H:%M')} (lagi {h}j {m}m)   ")
                
            time.sleep(60)
    elif p == "0":
        print("\n  Selesai.")
    else:
        print("  Tidak valid.")
        menu()


if __name__ == "__main__":
    menu()