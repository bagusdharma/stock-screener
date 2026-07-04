"""Tests for src/bot/formatters.py — fokus bug truncation pesan Telegram.

Jaminan yang diuji:
1. split_long_message: SEMUA part <= limit dalam segala kondisi
   (blok normal, blok tunggal raksasa, baris tunggal raksasa).
2. Tidak ada konten yang hilang setelah split (selain whitespace pemisah).
3. format_stock_detail / format_status TIDAK memotong konten
   (splitting adalah tanggung jawab _send_long, bukan formatter).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.bot.formatters import (
    _SAFE_MAX,
    format_status,
    format_stock_detail,
    split_long_message,
)


def _content_chars(s: str) -> str:
    """Konten tanpa whitespace — untuk cek tidak ada karakter hilang."""
    return "".join(s.split())


# ── split_long_message ────────────────────────────────────────

def test_split_short_text_single_part():
    text = "pesan pendek"
    assert split_long_message(text) == [text]


def test_split_normal_blocks_all_parts_under_limit():
    block = "BARIS SAHAM " * 30  # ~360 char per blok
    text = "\n\n".join(block for _ in range(20))  # ~7200 char total
    parts = split_long_message(text)
    assert len(parts) >= 2
    for p in parts:
        assert len(p) <= _SAFE_MAX, f"part {len(p)} char > limit {_SAFE_MAX}"
    assert _content_chars("".join(parts)) == _content_chars(text)


def test_split_single_giant_block_no_double_newline():
    """Blok tunggal >limit tanpa \\n\\n — dulu lolos utuh >4096 (ditolak API)."""
    line = "1. BBCA skor 95 STRONG BUY harga 9000"
    text = "\n".join(f"{line} #{i}" for i in range(250))  # ~10000 char, tanpa \n\n
    assert len(text) > _SAFE_MAX
    parts = split_long_message(text)
    assert len(parts) >= 2
    for p in parts:
        assert len(p) <= _SAFE_MAX, f"part {len(p)} char > limit {_SAFE_MAX}"
    assert _content_chars("".join(parts)) == _content_chars(text)


def test_split_single_giant_line_hard_chunked():
    """Kasus ekstrem: satu baris tunggal >limit harus di-hard-chunk."""
    text = "X" * (_SAFE_MAX * 2 + 137)
    parts = split_long_message(text)
    for p in parts:
        assert len(p) <= _SAFE_MAX
    assert "".join(parts).count("X") == len(text)


def test_split_mixed_normal_and_giant_blocks():
    small = "blok kecil"
    giant = "\n".join("baris panjang di dalam blok raksasa" * 3 for _ in range(200))
    text = small + "\n\n" + giant + "\n\n" + small
    parts = split_long_message(text)
    for p in parts:
        assert len(p) <= _SAFE_MAX
    assert _content_chars("".join(parts)) == _content_chars(text)


# ── formatter tidak boleh memotong konten ─────────────────────

def _stock_with_long_alasan() -> dict:
    return {
        "ticker": "BBCA.JK",
        "name": "Bank Central Asia",
        "skor_total": 88,
        "label": "STRONG BUY",
        "price": 9000,
        "harga_lot": 900_000,
        "pe": 22.5, "pbv": 4.1, "roe": 21.0, "der": 0.8,
        "net_profit_margin": 45.2,
        "yield_ttm": 2.9,
        "div_streak": 10,
        "teknikal": {"rsi": 55, "macd": "Bullish", "ma": "Uptrend Kuat",
                     "momentum": 4.2, "mfi": 60, "obv": "Naik"},
        "komponen": {"A_kualitas": 36, "B_dividen": 15, "C_growth": 16,
                     "D_valuasi": 12, "E_teknikal": 4},
        "alasan": [f"Alasan panjang nomor {i}: " + "detail analisis " * 40
                   for i in range(8)],
    }


def test_format_stock_detail_never_truncates():
    out = format_stock_detail(_stock_with_long_alasan(), budget=1_000_000)
    assert "[... pesan dipotong]" not in out
    # Semua 8 alasan (maks yang ditampilkan) harus utuh
    assert "Alasan panjang nomor 7" in out


def test_format_status_running_never_truncates():
    status = {
        "status": "running",
        "message": "Fetching 100/170 — BBCA.JK",
        "progress": 60,
        "updated_at": None,
        "log": [f"[12:00:{i:02d}] [{i}/170] TICKER{i}.JK — " + "x" * 500
                for i in range(8)],
    }
    out = format_status(status)
    assert "[... pesan dipotong]" not in out


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
