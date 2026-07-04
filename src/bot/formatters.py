"""Telegram message formatters.

Receives data dicts from scorer/merger, returns HTML strings.
No business logic — only formatting.

Telegram message limit: 4096 chars. Formatters TIDAK memotong konten —
pesan panjang displit oleh split_long_message() via handlers._send_long().
"""

from __future__ import annotations

import html as html_mod
from datetime import datetime, timedelta, timezone

from src.config.settings import SKOR_BUY, SKOR_HOLD, SKOR_STRONG_BUY

TELEGRAM_MAX = 4096
_SAFE_MAX = 3900

# Aturan project: semua waktu tampil dalam WIB. Offset tetap (WIB tanpa DST)
# supaya tidak butuh tzdata di Windows. Hasil dibuat naive agar konsisten
# dengan timestamp naive yang sudah tersimpan di cache/status.
WIB = timezone(timedelta(hours=7), "WIB")


def now_wib() -> datetime:
    return datetime.now(WIB).replace(tzinfo=None)


def get_lot(r: dict) -> int:
    """Harga 1 lot (Rp) — SATU-SATUNYA implementasi, jangan inline ulang.

    `or 0` menangani harga_lot null di cache lama (`.get("harga_lot", 0)`
    mengembalikan None kalau key ada tapi nilainya null → TypeError di filter).
    """
    lot = r.get("harga_lot") or 0
    if lot == 0:
        price = r.get("price", r.get("harga", 0)) or 0
        lot = int(price * 100)
    return int(lot)


def get_div_ttm(r: dict) -> float:
    """Dividen TTM nominal (Rp per lembar) — yang dipahami user, bukan %.

    Pakai angka aktual `div_amount_ttm` dari fetcher; fallback dihitung dari
    yield × harga (matematis identik: yield_ttm = div_amount_ttm / price).
    """
    v = r.get("div_amount_ttm")
    try:
        if v and float(v) == float(v):
            return float(v)
    except (TypeError, ValueError):
        pass
    dy = r.get("yield_ttm", r.get("div_yield", 0)) or 0
    price = r.get("price", r.get("harga", 0)) or 0
    if dy == dy and dy > 0 and price > 0:
        return dy / 100 * price
    return 0.0

# ── Primitives ────────────────────────────────────────────────


def _display_ticker(ticker: str) -> str:
    """Strip .JK suffix for user-facing display."""
    return ticker.replace(".JK", "") if ticker else "?"


def fmt_rp(v) -> str:
    if not v or v == 0:
        return "–"
    return f"Rp {int(v):,}".replace(",", ".")


def fmt_num(v, d: int = 1) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "–"
    return f"{float(v):.{d}f}"


def status_emoji(label: str) -> str:
    if label == "STRONG BUY":
        return "🟢"
    if label == "BUY":
        return "🔵"
    if label == "HOLD":
        return "🟡"
    return "🔴"


def fmt_timestamp(iso_str: str | None) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(WIB).replace(tzinfo=None)
        ts = f"🕐 Data: {dt.strftime('%d %b %Y %H:%M')}"
        delta_h = (now_wib() - dt).total_seconds() / 3600
        if delta_h >= 48:
            ts += f" · ⚠️ <i>{int(delta_h / 24)} hari lalu</i>"
        elif delta_h >= 12:
            ts += f" · ⚠️ <i>{int(delta_h)} jam lalu</i>"
        return ts
    except (ValueError, TypeError):
        return ""


def _split_oversized_block(block: str, limit: int) -> list[str]:
    """Split satu blok yang melebihi limit menjadi potongan per-baris.

    Baris tunggal yang melebihi limit (kasus ekstrem) di-hard-chunk
    supaya tidak pernah ada potongan > limit.
    """
    chunks: list[str] = []
    current = ""
    for line in block.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = current + ("\n" if current else "") + line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def split_long_message(text: str, limit: int = _SAFE_MAX) -> list[str]:
    """Split text into multiple messages, breaking at stock boundaries.

    Splits on double-newline boundaries to keep individual stock entries intact.
    A single block longer than `limit` is further split per-line (and a single
    line longer than `limit` is hard-chunked), so EVERY returned part is
    guaranteed <= `limit` chars.
    """
    if len(text) <= limit:
        return [text]

    blocks: list[str] = []
    for block in text.split("\n\n"):
        if len(block) <= limit:
            blocks.append(block)
        else:
            blocks.extend(_split_oversized_block(block, limit))

    parts: list[str] = []
    current = ""

    for block in blocks:
        candidate = current + ("\n\n" if current else "") + block
        if len(candidate) > limit and current:
            parts.append(current)
            current = block
        else:
            current = candidate

    if current:
        parts.append(current)

    return parts


def _trend_icon(ma: str | None) -> str:
    if ma == "Uptrend Kuat":
        return "📈Uptrend"
    if ma == "Di Atas MA50":
        return "↗MA50"
    if ma == "Di Atas MA200":
        return "→MA200"
    if ma == "Downtrend":
        return "📉Downtrend"
    return ""


# ── Compact stock line (for list views) ───────────────────────


def format_stock_compact(r: dict, budget: int, rank: int) -> str:
    """4-line compact format for recommendation lists."""
    em = status_emoji(r.get("label", r.get("status", "")))
    ticker = _display_ticker(r.get("ticker", "?"))
    skor = r.get("skor_total", r.get("skor", 0))
    label = r.get("label", r.get("status", ""))
    lot = get_lot(r)
    maks = int(budget // lot) if lot > 0 and lot <= budget else 0

    dttm = get_div_ttm(r)
    dy_str = f"Div Rp{int(dttm)}/lbr" if dttm > 0 else ""

    rsi = r.get("teknikal", {}).get("rsi") if isinstance(r.get("teknikal"), dict) else r.get("rsi")
    rsi_str = f"RSI:{rsi:.0f}" if rsi and rsi > 0 else ""

    ma = r.get("teknikal", {}).get("ma") if isinstance(r.get("teknikal"), dict) else r.get("ma")
    trend = _trend_icon(ma)

    sub = r.get("sub_label", "")
    display_label = sub if sub else label
    line1 = f"{rank}. {em} <b>{ticker}</b> — {display_label} <code>({skor}/100)</code>"
    parts2 = [fmt_rp(r.get("price", r.get("harga"))), f"1 lot = {fmt_rp(lot)}"]
    if maks > 0:
        parts2.append(f"maks {maks} lot")
    line2 = "   " + " · ".join(parts2)

    parts3 = [p for p in [trend, dy_str, rsi_str] if p]
    line3 = "   " + " · ".join(parts3) if parts3 else ""

    alasan = r.get("alasan", [])
    if alasan:
        snippets = [html_mod.escape(str(a))[:40] for a in alasan[:2]]
        line4 = "   💡 " + " · ".join(snippets)
    else:
        line4 = ""

    result = line1 + "\n" + line2
    if line3:
        result += "\n" + line3
    if line4:
        result += "\n" + line4
    return result


# ── Detail stock (single stock full view) ─────────────────────


def format_stock_detail(r: dict, budget: int) -> str:
    """Full detail view for one stock."""
    em = status_emoji(r.get("label", r.get("status", "")))
    ticker = _display_ticker(r.get("ticker", "?"))
    name = html_mod.escape(r.get("name", r.get("nama", ""))[:40])
    skor = r.get("skor_total", r.get("skor", 0))
    label = r.get("label", r.get("status", ""))

    price = r.get("price", r.get("harga", 0)) or 0
    lot = get_lot(r)
    bisa = lot > 0 and lot <= budget
    lot_maks = int(budget // lot) if lot > 0 else 0

    pe = r.get("pe", r.get("per"))
    pbv = r.get("pbv")
    roe = r.get("roe", r.get("roe_pct"))
    der = r.get("der")
    npm = r.get("net_profit_margin")

    dy = r.get("yield_ttm", r.get("div_yield", 0)) or 0
    dy_str = f"{dy:.1f}%" if dy > 0 else "–"
    streak = r.get("div_streak", r.get("div_tahun", 0)) or 0

    tek = r.get("teknikal", {}) if isinstance(r.get("teknikal"), dict) else {}
    rsi = tek.get("rsi", r.get("rsi"))
    macd = tek.get("macd", r.get("macd"))
    ma = tek.get("ma", r.get("ma"))
    mom = tek.get("momentum")
    mfi = tek.get("mfi", r.get("mfi"))
    obv = tek.get("obv", r.get("obv"))

    komp = r.get("komponen", {})

    sub = r.get("sub_label", "")
    display_label = sub if sub else label

    lines = [
        f"{em} <b>{ticker}</b> — {display_label} <code>({skor}/100)</code>",
        f"📌 {name}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "📊 <b>Fundamental</b>",
        f"   PER: <b>{fmt_num(pe)}</b>x · PBV: <b>{fmt_num(pbv, 2)}</b>x · ROE: <b>{fmt_num(roe)}%</b>",
        f"   DER: {fmt_num(der, 2)}x · Margin: {fmt_num(npm)}%",
        "",
        "💰 <b>Dividen</b>",
        (f"   Div TTM: <b>{fmt_rp(int(get_div_ttm(r)))}/lembar</b> "
         f"({fmt_rp(int(get_div_ttm(r) * 100))}/lot per tahun)"
         if get_div_ttm(r) > 0 else "   Div TTM: –"),
        f"   Yield: <b>{dy_str}</b> · Streak: {streak} tahun",
        "",
        "📈 <b>Teknikal</b>",
        f"   MA: {ma or '–'} · MACD: {macd or '–'}",
        f"   RSI: {fmt_num(rsi, 0)} · MFI: {fmt_num(mfi, 0)} · OBV: {obv or '–'}",
    ]
    if mom is not None:
        lines.append(f"   Momentum 3M: {mom:+.1f}%")

    lines.append("")
    lines.append(f"💵 <b>{fmt_rp(price)}</b>/lembar · 1 lot = <b>{fmt_rp(lot)}</b>")
    if bisa:
        lines.append(f"   ✅ Bisa dibeli · maks {lot_maks} lot")
    else:
        lines.append(
            f"   ⚠️ 1 lot = {fmt_rp(lot)} · budget {fmt_rp(budget)} kurang"
        )

    if komp:
        lines.append("")
        lines.append("🧮 <b>Skor Breakdown</b>")
        lines.append(
            f"   Kualitas: {komp.get('A_kualitas', 0)}/40 · "
            f"Dividen: {komp.get('B_dividen', 0)}/20"
        )
        lines.append(
            f"   Growth: {komp.get('C_growth', 0)}/20 · "
            f"Valuasi: {komp.get('D_valuasi', 0)}/15 · "
            f"Teknikal: {komp.get('E_teknikal', 0)}/5"
        )
        raw = r.get("skor_raw")
        if raw is not None:
            lines.append(f"   <i>Skor dasar {raw} → rating {skor} (kalibrasi persentil)</i>")

    penalti = r.get("penalti_total", 0)
    if penalti < 0:
        lines.append(f"   Penalti: {penalti}")

    alasan = r.get("alasan", [])
    if alasan:
        lines.append("")
        lines.append(f"📋 <b>Mengapa {label}?</b>")
        for a in alasan[:8]:
            lines.append(f"   • {html_mod.escape(str(a))}")

    return "\n".join(lines)


# ── Recommendation list ───────────────────────────────────────


def format_rekomendasi(top: list[dict], budget: int, total_bisa: int,
                       total_all: int, jumlah: int,
                       timestamp: str | None = None) -> str:
    ts_line = fmt_timestamp(timestamp)
    lines = [
        f"🏆 <b>Top {jumlah} Rekomendasi</b>",
        f"💰 Budget: <b>{fmt_rp(budget)}</b>",
        f"📊 Terjangkau: {total_bisa} dari {total_all} saham",
    ]
    if ts_line:
        lines.append(ts_line)
    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n")
    for i, r in enumerate(top, 1):
        lines.append(format_stock_compact(r, budget, rank=i))
        lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💬 <i>Tap saham di bawah untuk detail lengkap</i>")
    lines.append("⚠️ <i>Bukan rekomendasi investasi resmi. DYOR.</i>")
    return "\n".join(lines)


# ── Dividen list ──────────────────────────────────────────────


def format_dividen_list(top: list[dict], budget: int, jumlah: int,
                        timestamp: str | None = None) -> str:
    ts_line = fmt_timestamp(timestamp)
    lines = [
        f"💰 <b>Top {jumlah} Dividen IDX</b>",
        f"💵 Budget: <b>{fmt_rp(budget)}</b>",
    ]
    if ts_line:
        lines.append(ts_line)
    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n")
    for i, r in enumerate(top, 1):
        dy = r.get("yield_ttm", r.get("div_yield", 0)) or 0
        price = r.get("price", r.get("harga", 0)) or 0
        lot = get_lot(r)
        bisa = "✅" if 0 < lot <= budget else "❌"
        lot_maks = int(budget // lot) if lot > 0 else 0
        em = status_emoji(r.get("label", r.get("status", "")))
        fire = " 🔥" if dy >= 7 else ""
        ticker = _display_ticker(r.get("ticker", "?"))
        name = html_mod.escape(r.get("name", r.get("nama", ""))[:35])

        div_per_lbr = get_div_ttm(r)
        div_per_lot = div_per_lbr * 100

        streak = r.get("div_streak", r.get("div_tahun", 0)) or 0
        if streak >= 10:
            kons = f"🏆{streak}thn"
        elif streak >= 5:
            kons = f"✓{streak}thn"
        elif streak >= 1:
            kons = f"{streak}thn"
        else:
            kons = "baru"

        ma = r.get("teknikal", {}).get("ma") if isinstance(r.get("teknikal"), dict) else r.get("ma")
        trend = _trend_icon(ma)

        lines.append(
            f"{i}. {em} <b>{ticker}</b>{fire}  Yield: <b>{dy:.1f}%</b>  {bisa}\n"
            f"   {name}\n"
            f"   💵 TTM: <b>{fmt_rp(int(div_per_lbr))}/lbr</b> · <b>{fmt_rp(int(div_per_lot))}/lot</b>/thn\n"
            f"   1 lot = {fmt_rp(lot)} · maks {lot_maks} lot · {kons}"
            + (f" · {trend}" if trend else "")
        )
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"✅ = bisa dibeli (budget {fmt_rp(budget)})")
    lines.append("💵 TTM = total dividen trailing 12 bulan")
    lines.append("🏆 = konsisten bagi dividen ≥10 tahun")
    lines.append("\n⚠️ <i>Yield historis, bukan jaminan dividen berikutnya.</i>")
    return "\n".join(lines)


# ── Watchlist (HOLD + JUAL) ──────────────────────────────────


def format_watchlist(stocks: list[dict], budget: int, jumlah: int,
                     timestamp: str | None = None) -> str:
    ts_line = fmt_timestamp(timestamp)
    lines = [
        "⚠️ <b>Watchlist — Saham HOLD & JUAL</b>",
        f"💰 Budget: <b>{fmt_rp(budget)}</b>",
    ]
    if ts_line:
        lines.append(ts_line)
    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n")

    holds = [r for r in stocks if r.get("label") == "HOLD"]
    juals = [r for r in stocks if r.get("label") == "JUAL"]

    if holds:
        lines.append("🟡 <b>HOLD</b> — pertimbangkan hold/cut\n")
        for i, r in enumerate(holds[:jumlah], 1):
            lines.append(format_stock_compact(r, budget, rank=i))
            lines.append("")
    else:
        lines.append("🟡 <b>HOLD</b> — tidak ada\n")

    if juals:
        lines.append("🔴 <b>JUAL</b> — pertimbangkan jual\n")
        for i, r in enumerate(juals[:jumlah], 1):
            lines.append(format_stock_compact(r, budget, rank=i))
            lines.append("")
    else:
        lines.append("🔴 <b>JUAL</b> — tidak ada\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🟡 HOLD = skor cukup tapi belum BUY")
    lines.append("🔴 JUAL = skor rendah, waspadai risiko")
    lines.append("\n⚠️ <i>Bukan rekomendasi investasi resmi. DYOR.</i>")
    return "\n".join(lines)


# ── Status ────────────────────────────────────────────────────


def format_status(status_data: dict) -> str:
    status = status_data.get("status", "idle")
    msg = status_data.get("message", "–")
    prog = int(status_data.get("progress", 0))
    ts = status_data.get("updated_at")
    log_lines = status_data.get("log", [])

    if status == "running":
        filled = int(prog / 10)
        bar = "█" * filled + "░" * (10 - filled)
        now_str = now_wib().strftime("%H:%M:%S")
        txt = (
            f"⏳ <b>Screening Sedang Berjalan</b>\n"
            f"[{bar}] {prog}%\n\n"
            f"🔍 <b>Sedang diproses:</b>\n{html_mod.escape(msg)}\n"
            f"🕐 Update: {now_str}\n"
        )
        if log_lines:
            txt += "\n<b>Log terakhir:</b>\n"
            for line in log_lines[-8:]:
                txt += f"<code>{html_mod.escape(str(line))}</code>\n"
        return txt

    if status == "done":
        waktu = "–"
        if ts:
            try:
                waktu = datetime.fromisoformat(ts).strftime("%d %b %Y %H:%M")
            except (ValueError, TypeError):
                pass
        return (
            f"✅ <b>Screening Selesai</b>\n\n"
            f"📊 {html_mod.escape(msg)}\n"
            f"⏱ Selesai: {waktu}"
        )

    if status == "error":
        return (
            f"❌ <b>Screening Error</b>\n\n"
            f"<code>{html_mod.escape(msg)}</code>\n\n"
            "Coba jalankan ulang dengan /screen"
        )

    return (
        "💤 <b>Belum Ada Screening</b>\n\n"
        "Tekan tombol di bawah untuk mulai analisis seluruh saham IDX."
    )


# ── Help ──────────────────────────────────────────────────────


def format_help() -> str:
    return (
        "📖 <b>Panduan IDX Screener Bot</b>\n\n"
        "<b>Command:</b>\n"
        "/start           — Menu utama\n"
        "/rekomendasi     — Top saham BUY sesuai budget\n"
        "/dividen         — Saham dividen tertinggi\n"
        "/bandingkan      — Bandingkan berbagai budget\n"
        "/screen          — Jalankan analisis baru\n"
        "/status          — Cek progress analisis\n"
        "/budget 500000   — Set budget kamu\n"
        "/jumlah 10       — Set jumlah rekomendasi (1–20)\n"
        "/help            — Bantuan ini\n\n"
        "💡 <b>Tip:</b> Ketik angka langsung (misal <code>5000000</code>) "
        "untuk set budget tanpa command.\n\n"
        "<b>Cara kerja:</b>\n"
        "Bot menganalisis seluruh saham IDX (~950 emiten):\n"
        "• Kualitas: ROE, DER, Margin, Current Ratio, Asset Turnover\n"
        "• Dividen: Konsistensi + Yield TTM\n"
        "• Growth: Revenue & Earnings CAGR 3 tahun\n"
        "• Valuasi: PER, PBV relatif median sektor\n"
        "• Teknikal: RSI, MACD, MA, Volume\n\n"
        f"Skor 0–100 → <b>STRONG BUY ≥{SKOR_STRONG_BUY} · BUY ≥{SKOR_BUY} · "
        f"HOLD ≥{SKOR_HOLD} · JUAL &lt;{SKOR_HOLD}</b>\n\n"
        "⚠️ <i>Bukan rekomendasi investasi resmi. Selalu DYOR.</i>"
    )


# ── Budget confirmation ───────────────────────────────────────


def format_budget_set(budget: int) -> str:
    return (
        f"✅ Budget diset: <b>{fmt_rp(budget)}</b>\n"
        f"Saham dengan harga ≤ {fmt_rp(budget // 100)}/lembar bisa dibeli (1 lot = 100 lembar)"
    )


# ── Perbandingan all levels ───────────────────────────────────


def _budget_label(b: int) -> str:
    if b >= 1_000_000_000:
        return f"{b / 1_000_000_000:.0f}M"
    if b >= 1_000_000:
        return f"{b / 1_000_000:.0f}jt"
    if b >= 1_000:
        return f"{b / 1_000:.0f}rb"
    return str(b)


def format_perbandingan_all(
    hasil: list[dict], user_budget: int, jumlah: int,
    timestamp: str | None = None,
) -> str:
    levels = [100_000, 500_000, 1_000_000, 5_000_000, 10_000_000]
    if user_budget not in levels:
        levels.append(user_budget)
        levels.sort()

    ts_line = fmt_timestamp(timestamp)
    lines = [
        "📊 <b>PERBANDINGAN SEMUA BUDGET</b>",
    ]
    if ts_line:
        lines.append(ts_line)
    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n")
    prev_tickers: set[str] = set()

    # Sort SEKALI di luar loop — filter per level mempertahankan urutan
    sorted_hasil = sorted(
        hasil, key=lambda r: r.get("skor_total", r.get("skor", 0)), reverse=True)

    for budget in levels:
        bisa = [
            r for r in sorted_hasil
            if get_lot(r) <= budget
            and r.get("label", r.get("status")) in ("STRONG BUY", "BUY", "HOLD")
        ]
        top = bisa[:3]

        marker = " 👈 <i>budget kamu</i>" if budget == user_budget else ""
        lines.append(f"💰 <b>{fmt_rp(budget)}</b> — {len(bisa)} saham BUY/HOLD{marker}")

        if not top:
            lines.append("   <i>Tidak ada saham BUY/HOLD di budget ini</i>")
        else:
            for i, r in enumerate(top, 1):
                em = status_emoji(r.get("label", r.get("status", "")))
                raw_ticker = r.get("ticker", "")
                ticker = _display_ticker(raw_ticker)
                skor = r.get("skor_total", r.get("skor", 0))
                lot_val = get_lot(r)
                new = " 🆕" if raw_ticker not in prev_tickers else ""
                dy = r.get("yield_ttm", r.get("div_yield", 0)) or 0
                dy_s = f" Div:{dy:.1f}%" if dy > 0 else ""
                lines.append(
                    f"   {i}. {em} <b>{ticker}</b> ({skor}) · "
                    f"{fmt_rp(lot_val)}/lot{dy_s}{new}"
                )
        for r in top:
            prev_tickers.add(r.get("ticker", ""))
        lines.append("")

    lines.append("👈 = budget kamu  ·  🆕 = baru muncul di level ini")
    lines.append("\n⚠️ <i>Bukan rekomendasi investasi resmi. DYOR.</i>")
    return "\n".join(lines)
