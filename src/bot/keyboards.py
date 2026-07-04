"""Inline keyboard builders for Telegram bot.

No business logic — only UI layout.
All callback_data strings must match handlers.py on_button router.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Top Rekomendasi", callback_data="rekomendasi"),
            InlineKeyboardButton("💰 Top Dividen", callback_data="dividen"),
        ],
        [
            InlineKeyboardButton("🔍 Bandingkan Budget", callback_data="bandingkan"),
            InlineKeyboardButton("⚠️ Watchlist", callback_data="watchlist"),
        ],
        [
            InlineKeyboardButton("🔄 Screening Baru", callback_data="screen"),
        ],
        [
            InlineKeyboardButton("📈 Status", callback_data="status"),
            InlineKeyboardButton("⚙️ Budget", callback_data="set_budget"),
            InlineKeyboardButton("🔢 Jumlah", callback_data="set_jumlah"),
        ],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀ Kembali ke Menu", callback_data="menu")],
    ])


def budget_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Rp 500.000", callback_data="setup_budget_500000"),
            InlineKeyboardButton("Rp 1.000.000", callback_data="setup_budget_1000000"),
        ],
        [
            InlineKeyboardButton("Rp 2.000.000", callback_data="setup_budget_2000000"),
            InlineKeyboardButton("Rp 5.000.000", callback_data="setup_budget_5000000"),
        ],
        [
            InlineKeyboardButton(
                "✏️ Ketik manual → /budget [nominal]",
                callback_data="setup_budget_manual",
            ),
        ],
    ])


def bandingkan_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("100rb", callback_data="cmp_budget_100000"),
            InlineKeyboardButton("250rb", callback_data="cmp_budget_250000"),
            InlineKeyboardButton("500rb", callback_data="cmp_budget_500000"),
        ],
        [
            InlineKeyboardButton("1 jt", callback_data="cmp_budget_1000000"),
            InlineKeyboardButton("2 jt", callback_data="cmp_budget_2000000"),
            InlineKeyboardButton("5 jt", callback_data="cmp_budget_5000000"),
        ],
        [
            InlineKeyboardButton("10 jt", callback_data="cmp_budget_10000000"),
            InlineKeyboardButton("📊 Semua Level", callback_data="cmp_budget_all"),
        ],
        [InlineKeyboardButton("◀ Kembali ke Menu", callback_data="menu")],
    ])


def jumlah_keyboard(current: int) -> InlineKeyboardMarkup:
    options = [3, 5, 10, 15, 20]
    buttons = []
    for n in options:
        label = f"✓ {n}" if n == current else str(n)
        buttons.append(InlineKeyboardButton(label, callback_data=f"jumlah_{n}"))
    return InlineKeyboardMarkup([
        buttons,
        [InlineKeyboardButton("◀ Kembali", callback_data="menu")],
    ])


def detail_buttons(stocks: list[dict], per_row: int = 2) -> InlineKeyboardMarkup:
    """Build detail buttons for a list of scored stocks."""
    from src.bot.formatters import status_emoji, _display_ticker
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for s in stocks:
        raw_ticker = s.get("ticker", "")
        display = _display_ticker(raw_ticker)
        skor = s.get("skor_total", s.get("skor", 0))
        label_status = s.get("label", s.get("status", ""))
        btn = InlineKeyboardButton(
            f"{status_emoji(label_status)} {display} ({skor})",
            callback_data=f"detail_{raw_ticker}",
        )
        row.append(btn)
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("◀ Kembali", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def dividen_detail_buttons(stocks: list[dict], per_row: int = 2) -> InlineKeyboardMarkup:
    from src.bot.formatters import _display_ticker
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for s in stocks:
        raw_ticker = s.get("ticker", "")
        display = _display_ticker(raw_ticker)
        y = s.get("yield_ttm", s.get("div_yield", 0)) or 0
        btn = InlineKeyboardButton(
            f"💰 {display} ({y:.1f}%)",
            callback_data=f"detail_{raw_ticker}",
        )
        row.append(btn)
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("◀ Kembali", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def status_keyboard(status: str) -> InlineKeyboardMarkup:
    if status == "running":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh Status", callback_data="status")],
            [InlineKeyboardButton("◀ Kembali", callback_data="menu")],
        ])
    if status == "done":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Lihat Rekomendasi", callback_data="rekomendasi")],
            [InlineKeyboardButton("◀ Kembali", callback_data="menu")],
        ])
    if status == "error":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Coba Lagi", callback_data="screen")],
            [InlineKeyboardButton("◀ Kembali", callback_data="menu")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Mulai Screening", callback_data="screen")],
        [InlineKeyboardButton("◀ Kembali", callback_data="menu")],
    ])


def cmp_nav_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Budget lain", callback_data="bandingkan"),
            InlineKeyboardButton("◀ Menu", callback_data="menu"),
        ],
    ])
