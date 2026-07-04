"""
bot.py — IDX Screener Telegram Bot (Interaktif)
=================================================
Install: pip install python-telegram-bot==20.7
Jalankan: python bot.py

Fitur:
  /start        — Welcome + menu utama
  /rekomendasi  — Top BUY sesuai budget user
  /dividen      — Top dividend yield
  /screen       — Trigger screening baru (background)
  /status       — Status screening terakhir
  /budget       — Set budget kamu (contoh: /budget 1000000)
  /help         — Bantuan
"""

import html
import logging
import json
import os
import sys
import threading
import traceback
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PicklePersistence,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# ── Path & Config ─────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
RESULTS_FILE = os.path.join(BASE_DIR, "results_cache.json")
STATUS_FILE  = os.path.join(BASE_DIR, "screen_status.json")
sys.path.insert(0, BASE_DIR)

BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
DEFAULT_BUDGET = 500_000

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "bot.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

# ── State screening ────────────────────────────────────────
_screen_lock = threading.Lock()
_file_lock   = threading.Lock()   # protect baca/tulis status file


# ════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ════════════════════════════════════════════════════════════

def load_results() -> list:
    """Baca hasil screening dari cache."""
    if not os.path.exists(RESULTS_FILE):
        return []
    try:
        with open(RESULTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("data", []) if isinstance(data, dict) else data
    except Exception:
        return []


def load_status() -> dict:
    """Baca status dengan lock. Kalau file corrupt → hapus + return idle."""
    if not os.path.exists(STATUS_FILE):
        return {"status": "idle", "message": "Belum pernah dijalankan",
                "progress": 0, "log": [], "updated_at": None}
    with _file_lock:
        try:
            with open(STATUS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            # Validasi minimal
            if not isinstance(data, dict) or "status" not in data:
                raise ValueError("Format tidak valid")
            return data
        except Exception:
            # File corrupt → hapus supaya tidak terus error
            try:
                os.remove(STATUS_FILE)
            except Exception:
                pass
            return {"status": "idle", "message": "Status direset (file corrupt)",
                    "progress": 0, "log": [], "updated_at": None}


def save_status(status: str, progress: int = 0, message: str = ""):
    save_status_with_log(status, progress, message, [])


def save_status_with_log(status: str, progress: int, message: str, log_lines: list):
    """
    Simpan status dengan:
    - Lock supaya tidak ada race condition
    - Atomic write (tulis ke temp dulu, baru rename) supaya tidak corrupt
    """
    data = {
        "status":     status,
        "progress":   int(progress),
        "message":    message,
        "log":        log_lines[-20:] if log_lines else [],
        "updated_at": datetime.now().isoformat(),
    }
    tmp = STATUS_FILE + ".tmp"
    with _file_lock:
        try:
            # Tulis ke file .tmp dulu
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            # Kalau sukses, baru replace file asli
            # os.replace = atomic di Windows dan Linux
            os.replace(tmp, STATUS_FILE)
        except Exception as e:
            log.warning(f"Gagal tulis status: {e}")
            try:
                os.remove(tmp)
            except Exception:
                pass


def get_budget(context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ambil budget user. Default 500rb kalau belum di-set."""
    return context.user_data.get("budget", DEFAULT_BUDGET)


def budget_sudah_diset(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True kalau user sudah pernah set budget secara eksplisit."""
    return "budget" in context.user_data


def get_jumlah(context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ambil jumlah rekomendasi user. Default 5."""
    return context.user_data.get("jumlah", 5)


def fmt_rp(v) -> str:
    if not v or v == 0:
        return "–"
    return f"Rp {int(v):,}".replace(",", ".")


def fmt_num(v, d=1) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "–"
    return f"{float(v):.{d}f}"


def status_emoji(s: str) -> str:
    if s == "STRONG BUY": return "🚀"
    if s == "BUY":         return "🟢"
    if s == "HOLD":        return "🟡"
    return "🔴"


def get_contoh_saham(budget: int) -> list:
    """
    Contoh saham referensi berdasarkan harga ASLI dari data screening.
    Tidak hardcode — selalu up-to-date dengan harga pasar terakhir.
    """
    hasil = load_results()
    if not hasil:
        return ["⚠️ Belum ada data screening — jalankan /screen dulu"]

    # Saham referensi populer di IDX
    refs = ["BBCA", "BBRI", "BMRI", "TLKM", "UNVR"]
    contoh = []

    for kode in refs:
        r = next((x for x in hasil if x.get("ticker") == kode), None)
        if r and r.get("harga", 0) > 0:
            harga = r["harga"]
            lot   = r.get("harga_lot", int(harga * 100))
            bisa  = "✅" if lot <= budget else "❌"
            contoh.append(
                f"{bisa} {kode} (Rp {harga:,.0f}/lbr · 1 lot = {fmt_rp(lot)})"
            )

    # Kalau tidak ada saham referensi di data, ambil 3 teratas
    if not contoh:
        semua = sorted(hasil, key=lambda x: x.get("skor", 0), reverse=True)
        for r in semua[:3]:
            lot   = r.get("harga_lot", 0)
            harga = r.get("harga", 0)
            bisa  = "✅" if 0 < lot <= budget else "❌"
            contoh.append(
                f"{bisa} {r['ticker']} (Rp {harga:,.0f}/lbr · 1 lot = {fmt_rp(lot)})"
            )

    return contoh


def format_stock_compact(r: dict, budget: int, rank: int) -> str:
    """
    Format RINGKAS satu saham untuk list rekomendasi.
    3 baris per saham: header, price, indicators.
    Detail lengkap tersedia via tombol Detail.
    """
    sc   = status_emoji(r.get("status",""))
    lot  = r.get("harga_lot", 0)
    maks = int(budget // lot) if lot > 0 and lot <= budget else 0
    dy   = f"Div:{r['div_yield']*100:.1f}%" if r.get("div_yield",0) > 0 else ""
    rsi  = f"RSI:{r['rsi']:.0f}" if r.get("rsi",0) > 0 else ""

    # Trend indicator — penting untuk keputusan beli
    ma = r.get("ma", "-")
    if ma == "Uptrend Kuat":     trend = "📈Uptrend"
    elif ma == "Di Atas MA50":   trend = "↗MA50"
    elif ma == "Di Atas MA200":  trend = "→MA200"
    elif ma == "Downtrend":      trend = "📉Downtrend"
    else:                        trend = ""

    # Flow indicator — MFI + OBV ringkas
    mfi = r.get("mfi", 50)
    obv = r.get("obv", "-")
    flow = ""
    if mfi < 25:
        flow = "💰MFI↑"       # uang masuk kuat
    elif mfi > 80:
        flow = "⚠MFI↓"        # uang keluar
    if obv == "Bullish Div":
        flow = (flow + " " if flow else "") + "💰OBV↑"  # smart money masuk

    line1 = f"{rank}. {sc} <b>{r['ticker']}</b> — {r['status']} <code>({r['skor']}/100)</code>"

    line2_parts = [fmt_rp(r.get("harga")), f"1 lot = {fmt_rp(lot)}"]
    if maks > 0: line2_parts.append(f"maks {maks} lot")
    line2 = "   " + " · ".join(line2_parts)

    line3_parts = []
    if trend: line3_parts.append(trend)
    if flow:  line3_parts.append(flow)
    if dy:  line3_parts.append(dy)
    if rsi: line3_parts.append(rsi)
    line3 = "   " + " · ".join(line3_parts) if line3_parts else ""

    return line1 + "\n" + line2 + ("\n" + line3 if line3 else "")


def format_stock(r: dict, budget: int, rank: int = None) -> str:
    """Format satu saham jadi teks Telegram (HTML)."""
    sc       = status_emoji(r.get("status",""))
    dy       = f"{r['div_yield']*100:.1f}%" if r.get("div_yield", 0) > 0 else "–"
    rsi      = f"{r['rsi']:.0f}" if r.get("rsi", 0) > 0 else "–"
    lot      = r.get("harga_lot", 0)
    # Hitung dari user budget, bukan dari stored field (yang pakai global BUDGET)
    bisa     = lot > 0 and lot <= budget
    lot_maks = int(budget // lot) if lot > 0 else 0

    rank_str = f"{rank}. " if rank else ""

    lines = [
        f"{rank_str}{sc} <b>{r['ticker']}</b> — {r['status']} <code>({r['skor']}/100)</code>",
        f"   📌 {r.get('nama','')[:40]}",
        f"   📊 PER:<b>{fmt_num(r.get('per'))}</b>x  PBV:<b>{fmt_num(r.get('pbv'),2)}</b>x  ROE:<b>{fmt_num(r.get('roe_pct'))}%</b>",
        f"   💰 Dividen:<b>{dy}</b>  DER:{fmt_num(r.get('der'),2)}x  RSI:{rsi}",
        f"   💵 {fmt_rp(r.get('harga'))}/lembar · 1 lot = {fmt_rp(lot)}",
    ]

    if bisa:
        lines.append(
            f"   ✅ Bisa dibeli · 1 lot = {fmt_rp(lot)} · maks {lot_maks} lot"
        )
    else:
        lines.append(
            f"   ⚠️ 1 lot = {fmt_rp(lot)} · budget {fmt_rp(budget)} kurang · perlu {fmt_rp(lot - budget)} lagi"
        )

    # Alasan (max 3)
    alasan = r.get("alasan", [])
    if alasan:
        lines.append("")
        for a in alasan[:3]:
            lines.append(f"   {a}")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  MAIN KEYBOARD
# ════════════════════════════════════════════════════════════

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Top Rekomendasi", callback_data="rekomendasi"),
            InlineKeyboardButton("💰 Top Dividen",     callback_data="dividen"),
        ],
        [
            InlineKeyboardButton("🔍 Bandingkan Budget", callback_data="bandingkan"),
        ],
        [
            InlineKeyboardButton("🔄 Screening Baru", callback_data="screen"),
        ],
        [
            InlineKeyboardButton("📈 Status",     callback_data="status"),
            InlineKeyboardButton("⚙️ Budget",     callback_data="set_budget"),
            InlineKeyboardButton("🔢 Jumlah",     callback_data="set_jumlah"),
        ],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀ Kembali ke Menu", callback_data="menu")]
    ])


def budget_keyboard() -> InlineKeyboardMarkup:
    """Pilihan cepat budget untuk onboarding."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Rp 500.000",   callback_data="setup_budget_500000"),
            InlineKeyboardButton("Rp 1.000.000", callback_data="setup_budget_1000000"),
        ],
        [
            InlineKeyboardButton("Rp 2.000.000", callback_data="setup_budget_2000000"),
            InlineKeyboardButton("Rp 5.000.000", callback_data="setup_budget_5000000"),
        ],
        [
            InlineKeyboardButton("✏️ Ketik manual → /budget [nominal]",
                                 callback_data="setup_budget_manual"),
        ],
    ])


# ════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Simpan chat_id agar bisa dipakai untuk notifikasi dari background thread
    context.user_data["_chat_id"] = update.effective_chat.id
    budget = get_budget(context)
    hasil  = load_results()
    ts_str = ""

    if isinstance(hasil, list) and len(hasil) > 0:
        # Coba baca metadata
        try:
            with open(RESULTS_FILE) as f:
                meta = json.load(f)
            if meta.get("generated_at"):
                ts = datetime.fromisoformat(meta["generated_at"])
                ts_str = f"\n🕐 Data terakhir: {ts.strftime('%d %b %Y %H:%M')}"
        except Exception:
            pass

    n    = len(hasil)
    buy  = len([r for r in hasil if r.get("status") in ("STRONG BUY","BUY")])
    bisa = len([r for r in hasil if r.get("harga_lot", 9e9) <= budget])

    msg = (
        "📊 <b>IDX Stock Screener</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Halo! Bot ini membantu kamu menemukan saham IDX terbaik "
        "berdasarkan analisis fundamental + teknikal.\n"
        f"{ts_str}\n\n"
    )

    if n > 0:
        # Hitung bisa_beli berdasarkan user budget (bukan global BUDGET dari config)
        bisa_user = len([r for r in hasil if r.get("harga_lot", 9e9) <= budget])
        msg += (
            f"<b>Ringkasan screening terkini:</b>\n"
            f"🔍 {n} saham dianalisis\n"
            f"🟢 {buy} saham BUY/STRONG BUY\n"
            f"💰 {bisa_user} saham bisa dibeli (budget {fmt_rp(budget)})\n\n"
        )
    else:
        msg += "⚠️ Belum ada data. Pilih <b>Mulai Screening</b> untuk analisis.\n\n"

    # Kalau belum pernah set budget → onboarding dulu
    if not budget_sudah_diset(context):
        msg += (
            "\n\n⚙️ <b>Sebelum mulai, set budget investasi kamu:</b>\n"
            "Budget menentukan saham mana yang bisa kamu beli (1 lot = 100 lembar).\n"
            "Contoh: budget Rp 500.000 → bisa beli saham ≤ Rp 5.000/lembar"
        )
        await update.message.reply_text(
            msg,
            parse_mode=ParseMode.HTML,
            reply_markup=budget_keyboard(),
        )
        return

    msg += "Pilih menu di bawah:"

    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Tangkap pesan angka biasa.
    - Kalau mode == 'bandingkan' + koma → multi-budget comparison
    - Kalau mode == 'bandingkan' → single budget comparison
    - Kalau tidak → set sebagai budget user
    """
    raw = (update.message.text or "").strip()

    # ── Multi-budget: cek koma di mode bandingkan SEBELUM strip ──
    if context.user_data.get("mode") == "bandingkan" and "," in raw:
        parts = [p.strip().replace(".", "").replace(" ", "") for p in raw.split(",")]
        budgets = []
        for p in parts:
            if p.isdigit() and 10_000 <= int(p) <= 100_000_000_000:
                budgets.append(int(p))
        if len(budgets) >= 2:
            budgets.sort()
            await show_multi_budget(
                update.message.reply_text, context, budgets
            )
            return
        elif len(budgets) == 1:
            # Hanya 1 angka valid → fallback ke single budget
            await show_perbandingan(
                update.message.reply_text, context, target_budget=budgets[0]
            )
            return
        else:
            await update.message.reply_text(
                "⚠️ Format tidak dikenali.\n"
                "Contoh: <code>100000, 250000, 500000</code>",
                parse_mode=ParseMode.HTML,
            )
            return

    text = raw.replace(".", "").replace(",", "").replace(" ", "")

    if not text.isdigit():
        return  # bukan angka, abaikan (tidak reply supaya tidak spam)

    val = int(text)

    if val < 10_000:
        await update.message.reply_text(
            "⚠️ Minimal Rp 10.000\n"
            "Contoh: ketik <code>500000</code> atau <code>5000000</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if val > 100_000_000_000:
        await update.message.reply_text(
            "⚠️ Angka terlalu besar. Masukkan dalam rupiah.\n"
            "Contoh: <code>5000000</code> untuk Rp 5 juta",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Mode Bandingkan: tampilkan perbandingan untuk budget yang diketik ──
    if context.user_data.get("mode") == "bandingkan":
        # JANGAN clear mode — biarkan user ketik angka lain langsung
        await show_perbandingan(
            update.message.reply_text,
            context,
            target_budget=val,
        )
        return

    # ── Default: set budget user ──
    context.user_data["budget"] = val
    per_lembar = val // 100

    contoh = get_contoh_saham(val)

    await update.message.reply_text(
        f"✅ Budget diset: <b>{fmt_rp(val)}</b>\n"
        f"Bisa beli saham ≤ <b>{fmt_rp(per_lembar)}/lembar</b>\n\n"
        + "\n".join(contoh) +
        "\n\n💡 <i>Tidak perlu screening ulang — rekomendasi otomatis menyesuaikan budget baru.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def cmd_rekomendasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_rekomendasi(update.message.reply_text, context)


async def cmd_dividen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_dividen(update.message.reply_text, context)


async def cmd_bandingkan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_perbandingan(update.message.reply_text, context, target_budget=None)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_status(update.message.reply_text)


async def cmd_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await trigger_screen(update.message.reply_text, context)


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set budget: /budget 1000000"""
    args = context.args
    if not args:
        budget = get_budget(context)
        await update.message.reply_text(
            f"💰 Budget kamu saat ini: <b>{fmt_rp(budget)}</b>\n\n"
            "Untuk mengubah:\n<code>/budget 1000000</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        new_budget = int(args[0].replace(".","").replace(",",""))
        if new_budget < 10_000:
            raise ValueError("Terlalu kecil")
        context.user_data["budget"] = new_budget
        per_lembar = new_budget // 100
        contoh = get_contoh_saham(new_budget)
        await update.message.reply_text(
            f"✅ Budget diset: <b>{fmt_rp(new_budget)}</b>\n"
            f"Bisa beli saham ≤ <b>{fmt_rp(per_lembar)}/lembar</b>\n\n"
            + "\n".join(contoh),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )
    except Exception:
        await update.message.reply_text(
            "❌ Format salah. Contoh:\n<code>/budget 500000</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_jumlah(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/jumlah 10 — Set berapa rekomendasi yang ditampilkan"""
    args = context.args
    if not args:
        n = get_jumlah(context)
        await update.message.reply_text(
            f"📋 Jumlah rekomendasi kamu: <b>{n}</b>\n\n"
            "Untuk mengubah:\n<code>/jumlah 10</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        n = int(args[0])
        if not 1 <= n <= 20:
            raise ValueError
        context.user_data["jumlah"] = n
        await update.message.reply_text(
            f"✅ Jumlah rekomendasi diubah ke <b>{n}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )
    except Exception:
        await update.message.reply_text(
            "❌ Format salah. Masukkan angka 1–20.\n"
            "Contoh: <code>/jumlah 10</code>",
            parse_mode=ParseMode.HTML,
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
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
        "Bot menganalisis 150+ saham IDX:\n"
        "• Kualitas: ROE, DER, Laba, Margin, Market Cap\n"
        "• Dividen: Konsistensi + Yield TTM\n"
        "• Valuasi: PER, PBV relatif median\n"
        "• Teknikal: RSI, MACD, MA, Volume, Momentum\n\n"
        "Skor 0–100 → <b>STRONG BUY ≥75 · BUY ≥63 · HOLD ≥50 · PERINGATAN &lt;50</b>\n\n"
        "⚠️ <i>Bukan rekomendasi investasi resmi. Selalu DYOR.</i>"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


# ════════════════════════════════════════════════════════════
#  SAFE EDIT — tangkap error Telegram yang umum
# ════════════════════════════════════════════════════════════

async def safe_edit(query, text: str, **kwargs):
    """
    Wrapper edit_message_text yang tangkap error umum:
    - "Message is not modified" → user klik tombol yg sama 2x, skip
    - "Message too long" → potong otomatis + kasih tau user
    - "Message to edit not found" → pesan expired, skip
    - "Can't parse entities" → HTML invalid, kirim tanpa parse_mode
    """
    from telegram.error import BadRequest

    MAX_LEN = 4000  # Telegram limit 4096, sisakan buffer

    # Potong kalau terlalu panjang
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN] + "\n\n<i>Pesan terlalu panjang, sebagian dipotong.</i>"

    try:
        await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        err_str = str(e).lower()
        if "message is not modified" in err_str:
            pass  # user klik tombol yang sama — harmless
        elif "message to edit not found" in err_str:
            pass  # pesan sudah expired/dihapus
        elif "can't parse entities" in err_str:
            # HTML invalid — coba kirim ulang tanpa parse_mode
            log.warning(f"safe_edit HTML parse error, retrying tanpa HTML: {e}")
            plain_kwargs = {k: v for k, v in kwargs.items() if k != "parse_mode"}
            try:
                await query.edit_message_text(text, **plain_kwargs)
            except Exception:
                pass
        else:
            log.warning(f"safe_edit BadRequest: {e}")


# ════════════════════════════════════════════════════════════
#  CALLBACK HANDLERS (tombol inline keyboard)
# ════════════════════════════════════════════════════════════

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Pastikan chat_id selalu tersimpan
    if update.effective_chat:
        context.user_data["_chat_id"] = update.effective_chat.id

    data = query.data

    # Wrapper: semua edit pakai safe_edit agar tidak error
    async def _edit(text, **kw):
        await safe_edit(query, text, **kw)

    # Clear mode bandingkan kalau user klik menu lain
    if data not in ("bandingkan",) and not data.startswith("cmp_budget_"):
        context.user_data.pop("mode", None)

    if data == "menu":
        budget = get_budget(context)
        await _edit(
            f"💰 Budget: <b>{fmt_rp(budget)}</b>\n\nPilih menu:",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

    elif data == "rekomendasi":
        await show_rekomendasi(_edit, context)

    elif data == "dividen":
        await show_dividen(_edit, context)

    elif data == "bandingkan":
        # Set mode bandingkan — angka berikutnya yang diketik user = budget perbandingan
        context.user_data["mode"] = "bandingkan"
        cmp_buttons = [
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
                InlineKeyboardButton("10 jt",  callback_data="cmp_budget_10000000"),
                InlineKeyboardButton("📊 Semua Level", callback_data="cmp_budget_all"),
            ],
            [InlineKeyboardButton("◀ Kembali ke Menu", callback_data="menu")],
        ]
        # Kirim sebagai pesan BARU supaya hasil perbandingan sebelumnya
        # tidak hilang (tetap bisa di-scroll ke atas)
        chat_id = update.effective_chat.id
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔍 <b>Bandingkan Budget</b>\n\n"
                "Pilih budget atau <b>ketik angka</b>:\n"
                "• Satu budget: <code>750000</code>\n"
                "• Multi-budget: <code>100000, 250000, 500000, 1000000</code>\n\n"
                "💡 <i>Multi-budget tampilkan side-by-side — "
                "lihat saham apa yang terbuka di tiap budget.</i>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(cmp_buttons),
        )

    elif data.startswith("cmp_budget_"):
        # Quick-select budget perbandingan — kirim sebagai PESAN BARU
        context.user_data.pop("mode", None)  # clear mode
        chat_id = update.effective_chat.id
        val = data.replace("cmp_budget_", "")

        if val == "all":
            await show_perbandingan(
                lambda text, **kw: context.bot.send_message(chat_id=chat_id, text=text, **kw),
                context,
                target_budget=None,
            )
        else:
            target = int(val)
            await show_perbandingan(
                lambda text, **kw: context.bot.send_message(chat_id=chat_id, text=text, **kw),
                context,
                target_budget=target,
            )

    elif data == "screen":
        await trigger_screen(_edit, context)

    elif data == "status":
        await show_status(_edit)

    elif data.startswith("setup_budget_"):
        # Callback dari onboarding / budget_keyboard
        val = data.replace("setup_budget_", "")
        if val == "manual":
            await _edit(
                "✏️ <b>Ketik budget kamu:</b>\n\n"
                "Contoh: <code>/budget 750000</code>\n\n"
                "Setelah set, ketik /start untuk mulai.",
                parse_mode=ParseMode.HTML,
            )
        else:
            try:
                new_budget = int(val)
                context.user_data["budget"] = new_budget
                await _edit(
                    f"✅ <b>Budget diset: {fmt_rp(new_budget)}</b>\n"
                    f"Maks harga/lembar: {fmt_rp(new_budget // 100)}\n\n"
                    "Sekarang pilih menu:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard(),
                )
            except Exception:
                await _edit(
                    "❌ Gagal set budget.",
                    reply_markup=budget_keyboard(),
                )

    elif data == "set_budget":
        budget = get_budget(context)
        await _edit(
            f"💰 <b>Set Budget Kamu</b>\n\n"
            f"Budget saat ini: <b>{fmt_rp(budget)}</b>\n\n"
            "Ketik command:\n<code>/budget 500000</code>\n\n"
            "Budget menentukan saham mana yang bisa kamu beli (1 lot = 100 lembar).\n"
            "Contoh: budget Rp 500.000 → bisa beli saham ≤ Rp 5.000/lembar",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )

    elif data == "set_jumlah":
        n = get_jumlah(context)
        buttons = [
            [
                InlineKeyboardButton("3",  callback_data="jumlah_3"),
                InlineKeyboardButton("5",  callback_data="jumlah_5"),
                InlineKeyboardButton("10", callback_data="jumlah_10"),
                InlineKeyboardButton("15", callback_data="jumlah_15"),
                InlineKeyboardButton("20", callback_data="jumlah_20"),
            ],
            [InlineKeyboardButton("◀ Kembali", callback_data="menu")],
        ]
        await _edit(
            f"🔢 <b>Jumlah Rekomendasi</b>\n\n"
            f"Saat ini: <b>{n} rekomendasi</b>\n\n"
            "Pilih berapa rekomendasi yang mau ditampilkan:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("jumlah_"):
        n = int(data.replace("jumlah_", ""))
        context.user_data["jumlah"] = n
        await _edit(
            f"✅ Jumlah rekomendasi diubah ke <b>{n}</b>\n\n"
            "Pilih menu:",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

    elif data.startswith("detail_"):
        ticker = data.replace("detail_", "")
        hasil  = load_results()
        stock  = next((r for r in hasil if r.get("ticker") == ticker), None)
        budget = get_budget(context)

        if stock:
            msg = format_stock(stock, budget)
            await _edit(
                msg, parse_mode=ParseMode.HTML,
                reply_markup=back_keyboard(),
            )
        else:
            await _edit(
                "Saham tidak ditemukan.", reply_markup=back_keyboard()
            )


# ════════════════════════════════════════════════════════════
#  SHARED DISPLAY FUNCTIONS
# ════════════════════════════════════════════════════════════

async def show_rekomendasi(send_fn, context):
    """Tampilkan top rekomendasi BUY sesuai budget user."""

    # Belum set budget → minta set dulu
    if not budget_sudah_diset(context):
        await send_fn(
            "⚙️ <b>Budget belum diset!</b>\n\n"
            "Pilih budget investasi kamu dulu.\n"
            "Budget menentukan saham mana yang bisa kamu beli (1 lot = 100 lembar):",
            parse_mode=ParseMode.HTML,
            reply_markup=budget_keyboard(),
        )
        return

    budget = get_budget(context)
    hasil  = load_results()

    if not hasil:
        await send_fn(
            "⚠️ Belum ada data. Jalankan /screen dulu.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    # Filter: bisa dibeli + BUY/STRONG BUY
    bisa_beli = [
        r for r in hasil
        if r.get("harga_lot", 9e9) <= budget
        and r.get("status") in ("STRONG BUY", "BUY", "HOLD")
    ]
    bisa_beli.sort(key=lambda r: r.get("skor", 0), reverse=True)
    jumlah = get_jumlah(context)
    top    = bisa_beli[:jumlah]

    if not top:
        # Cari saham termurah yang tersedia sebagai referensi
        semua = sorted(hasil, key=lambda r: r.get("harga_lot", 9e9))
        termurah = semua[0] if semua else None
        hint = ""
        if termurah:
            hint = (
                f"\n\nSaham termurah saat ini: "
                f"<b>{termurah['ticker']}</b> · "
                f"1 lot = {fmt_rp(termurah.get('harga_lot',0))}"
            )
        await send_fn(
            f"😕 Tidak ada saham BUY/HOLD yang bisa dibeli dengan budget <b>{fmt_rp(budget)}</b>.{hint}\n\n"
            f"Naikkan budget: /budget [nominal]",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    jumlah = get_jumlah(context)
    lines = [
        f"🏆 <b>Top {jumlah} Rekomendasi</b>",
        f"💰 Budget: <b>{fmt_rp(budget)}</b> · Maks/lembar: <b>{fmt_rp(budget // 100)}</b>",
        f"📊 Terjangkau: {len(bisa_beli)} dari {len(hasil)} saham",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    # Selalu pakai format kompak untuk list → aman sampai 20 saham
    # (~75 char/saham × 20 = 1500 char + header = ~1700 char, jauh di bawah 4096)
    for i, r in enumerate(top, 1):
        lines.append(format_stock_compact(r, budget, rank=i))

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💬 <i>Tap saham di bawah untuk detail lengkap</i>")
    lines.append("⚠️ <i>Bukan rekomendasi investasi resmi. DYOR.</i>")

    # Tombol detail — 2 per baris agar rapi dan tidak terlalu panjang
    buttons = []
    row = []
    for r in top:
        btn = InlineKeyboardButton(
            f"{status_emoji(r['status'])} {r['ticker']} ({r['skor']})",
            callback_data=f"detail_{r['ticker']}"
        )
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("◀ Kembali", callback_data="menu")])

    await send_fn(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_dividen(send_fn, context):
    """Tampilkan top dividen dengan nominal TTM per lembar & per lot."""
    budget = get_budget(context)
    jumlah = get_jumlah(context)
    hasil  = load_results()

    if not hasil:
        await send_fn(
            "⚠️ Belum ada data. Jalankan /screen dulu.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    top_div = sorted(
        [r for r in hasil if r.get("div_yield", 0) > 0],
        key=lambda r: r.get("div_yield", 0),
        reverse=True,
    )[:jumlah]

    lines = [
        f"💰 <b>Top {jumlah} Dividen IDX</b>",
        f"💵 Budget: <b>{fmt_rp(budget)}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    for i, r in enumerate(top_div, 1):
        dy    = f"{r['div_yield']*100:.1f}%"
        lot_d = r.get("harga_lot", 0)
        harga = r.get("harga", 0)
        bisa  = "✅" if 0 < lot_d <= budget else "❌"
        lot_maks_d = int(budget // lot_d) if lot_d > 0 else 0
        sc    = status_emoji(r.get("status",""))
        fire  = " 🔥" if r.get("div_yield",0) >= 0.07 else ""

        # Hitung nominal dividen TTM (trailing 12 bulan)
        div_per_lbr = r.get("div_yield", 0) * harga if harga > 0 else 0
        div_per_lot = div_per_lbr * 100

        # Konsistensi dividen (berapa tahun)
        div_thn = r.get("div_tahun", 0)
        if div_thn >= 10:
            kons = f"🏆{div_thn}thn"
        elif div_thn >= 5:
            kons = f"✓{div_thn}thn"
        elif div_thn >= 1:
            kons = f"{div_thn}thn"
        else:
            kons = "baru"

        # Trend harga — penting supaya investor tidak beli saham turun
        ma = r.get("ma", "-")
        if ma == "Uptrend Kuat":     trend = "📈Uptrend"
        elif ma == "Di Atas MA50":   trend = "↗MA50"
        elif ma == "Di Atas MA200":  trend = "→MA200"
        elif ma == "Downtrend":      trend = "📉Turun"
        else:                        trend = ""

        lines.append(
            f"{i}. {sc} <b>{r['ticker']}</b>{fire}  Yield: <b>{dy}</b>  {bisa}\n"
            f"   {r.get('nama','')[:35]}\n"
            f"   💵 TTM: <b>{fmt_rp(int(div_per_lbr))}/lbr</b> · <b>{fmt_rp(int(div_per_lot))}/lot</b>/thn\n"
            f"   1 lot = {fmt_rp(lot_d)} · maks {lot_maks_d} lot · {kons} · {trend}"
        )
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"✅ = bisa dibeli (budget {fmt_rp(budget)})")
    lines.append("💵 TTM = total dividen trailing 12 bulan")
    lines.append("🏆 = konsisten bagi dividen ≥10 tahun")
    lines.append("\n⚠️ <i>Yield historis, bukan jaminan dividen berikutnya.</i>")

    # Tombol detail — 2 per baris
    buttons = []
    row = []
    for r in top_div:
        btn = InlineKeyboardButton(
            f"💰 {r['ticker']} ({r['div_yield']*100:.1f}%)",
            callback_data=f"detail_{r['ticker']}"
        )
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("◀ Kembali", callback_data="menu")])

    await send_fn(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_multi_budget(send_fn, context, budgets: list):
    """
    Perbandingan MULTI-BUDGET side-by-side.
    Kolom = budget level, baris = ranking saham terbaik di budget itu.
    User langsung lihat: "nambah 100rb → bisa dapat saham apa."
    """
    hasil = load_results()
    jumlah = get_jumlah(context)

    if not hasil:
        await send_fn("⚠️ Belum ada data screening. Jalankan /screen dulu.")
        return

    # Cari top stocks per budget
    per_budget = {}  # budget → list of top stocks
    for b in budgets:
        bisa = [
            r for r in hasil
            if r.get("harga_lot", 9e9) <= b
            and r.get("status") in ("STRONG BUY", "BUY", "HOLD")
        ]
        bisa.sort(key=lambda r: r.get("skor", 0), reverse=True)
        per_budget[b] = bisa[:jumlah]

    max_rows = max(len(v) for v in per_budget.values()) if per_budget else 0

    if max_rows == 0:
        await send_fn(
            "⚠️ Tidak ada saham BUY/HOLD di budget-budget ini.",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Helper ──
    def _budget_label(b):
        if b >= 1_000_000_000: return f"{b/1_000_000_000:.0f}M"
        if b >= 1_000_000: return f"{b/1_000_000:.0f}jt"
        if b >= 1_000: return f"{b/1_000:.0f}rb"
        return str(b)

    cmp_buttons = [[
        InlineKeyboardButton("🔍 Budget lain", callback_data="bandingkan"),
        InlineKeyboardButton("◀ Menu", callback_data="menu"),
    ]]

    # ── Build tabel side-by-side, split per 4 kolom budget ──
    COLS_PER_BLOCK = 4
    messages = []  # list of text chunks to send

    header_text = (
        f"📊 <b>PERBANDINGAN MULTI-BUDGET</b>\n"
        f"Budget: {' · '.join(_budget_label(b) for b in budgets)}\n"
        f"Top {jumlah} saham per budget\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    messages.append(header_text)

    for blk_start in range(0, len(budgets), COLS_PER_BLOCK):
        blk_budgets = budgets[blk_start:blk_start + COLS_PER_BLOCK]

        col_w = 7
        rank_w = 4  # " 1. "
        labels = [_budget_label(b) for b in blk_budgets]

        # Header row
        tbl_header = " " * rank_w + "".join(l.rjust(col_w) for l in labels)
        tbl_sep = "─" * (rank_w + col_w * len(blk_budgets))

        tbl_lines = [tbl_header, tbl_sep]

        # Data rows — ambil max_rows terbanyak
        for rank in range(max_rows):
            cells = []
            for b in blk_budgets:
                stocks = per_budget[b]
                if rank < len(stocks):
                    r = stocks[rank]
                    cells.append(r["ticker"][:5])
                else:
                    cells.append("-")

            rank_label = f"{rank+1:>2}. "
            row = rank_label + "".join(c.rjust(col_w) for c in cells)
            tbl_lines.append(row)

        tbl_text = "<pre>" + "\n".join(tbl_lines) + "</pre>"

        # Score rows di bawah tabel
        score_lines = []
        for rank in range(min(max_rows, jumlah)):
            cells = []
            for b in blk_budgets:
                stocks = per_budget[b]
                if rank < len(stocks):
                    cells.append(f"({stocks[rank]['skor']})")
                else:
                    cells.append("")

            rank_label = f"{rank+1:>2}. "
            row = rank_label + "".join(c.rjust(col_w) for c in cells)
            score_lines.append(row)

        score_text = "<pre>" + "\n".join(score_lines) + "</pre>"

        if len(blk_budgets) > 1:
            block_label = ", ".join(labels)
        else:
            block_label = labels[0]

        blk_text = f"\n📈 <b>{block_label}</b>\n{tbl_text}\n📊 <b>Skor:</b>\n{score_text}"
        messages.append(blk_text)

    # ── Detail: saham BARU yang muncul kalau naikkan budget ──
    diff_lines = []
    prev_tickers = set()
    for b in budgets:
        stocks = per_budget[b]
        cur_tickers = set(r["ticker"] for r in stocks)
        new_tickers = cur_tickers - prev_tickers
        if new_tickers and prev_tickers:
            new_sorted = sorted(
                [r for r in stocks if r["ticker"] in new_tickers],
                key=lambda r: r["skor"], reverse=True,
            )[:5]  # max 5 saham baru ditampilkan
            new_str = ", ".join(f"<b>{r['ticker']}</b>({r['skor']})" for r in new_sorted)
            diff_lines.append(f"💰 +{_budget_label(b)}: {new_str}")
        prev_tickers = cur_tickers

    if diff_lines:
        diff_text = "\n🆕 <b>Saham baru yang terbuka:</b>\n" + "\n".join(diff_lines)
        messages.append(diff_text)

    messages.append("\n⚠️ <i>Bukan rekomendasi investasi resmi. DYOR.</i>")

    # ── Kirim: gabung blok, split kalau melebihi 3800 char ──
    MAX_LEN = 3800
    pending = ""

    for idx, chunk in enumerate(messages):
        candidate = pending + "\n" + chunk if pending else chunk

        if len(candidate) > MAX_LEN and pending:
            await send_fn(pending.strip(), parse_mode=ParseMode.HTML)
            pending = chunk
        else:
            pending = candidate

    # Kirim sisa terakhir dengan tombol
    if pending.strip():
        await send_fn(
            pending.strip(),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(cmp_buttons),
        )


async def show_perbandingan(send_fn, context, target_budget=None):
    """
    Perbandingan top rekomendasi.
    - target_budget=None → tampilkan SEMUA level budget (100rb s/d 10jt)
    - target_budget=angka → tampilkan top saham untuk budget tersebut saja
    Selalu dikirim sebagai PESAN BARU supaya tersimpan di chat history.
    """
    hasil = load_results()

    if not hasil:
        await send_fn(
            "⚠️ Belum ada data. Jalankan /screen dulu.",
            parse_mode=ParseMode.HTML,
        )
        return

    user_budget = get_budget(context)
    jumlah_top = get_jumlah(context)  # dinamis sesuai setting user

    if target_budget is not None:
        # ── Mode: satu budget spesifik — Bibit-style comparison ──
        # Setiap blok berisi 4 saham cards + tabel metrik yang SAMA.
        # Kalau ada 20 saham → 5 blok, masing-masing self-contained.
        budget = target_budget
        bisa = [
            r for r in hasil
            if r.get("harga_lot", 9e9) <= budget
            and r.get("status") in ("STRONG BUY", "BUY", "HOLD")
        ]
        bisa.sort(key=lambda r: r.get("skor", 0), reverse=True)
        top = bisa[:jumlah_top]

        if not top:
            no_lines = [
                f"📊 <b>PERBANDINGAN — Budget {fmt_rp(budget)}</b>",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "<i>Tidak ada saham BUY/HOLD di budget ini.</i>",
            ]
            semua = sorted(hasil, key=lambda r: r.get("harga_lot", 9e9))
            if semua:
                no_lines.append(
                    f"\nSaham termurah: <b>{semua[0]['ticker']}</b> · "
                    f"1 lot = {fmt_rp(semua[0].get('harga_lot', 0))}"
                )
            cmp_buttons = [[
                InlineKeyboardButton("🔍 Budget lain", callback_data="bandingkan"),
                InlineKeyboardButton("◀ Menu", callback_data="menu"),
            ]]
            await send_fn("\n".join(no_lines), parse_mode=ParseMode.HTML,
                          reply_markup=InlineKeyboardMarkup(cmp_buttons))
            return

        # ── Helper functions ──
        def _trend_short(ma_val):
            if ma_val == "Uptrend Kuat": return "Up"
            if ma_val == "Di Atas MA50": return ">50"
            if ma_val == "Di Atas MA200": return ">200"
            if ma_val == "Downtrend": return "Dn"
            return "-"

        def _lot_short(lot_val):
            if lot_val >= 1_000_000: return f"{lot_val/1_000_000:.1f}jt"
            if lot_val >= 1_000: return f"{lot_val/1_000:.0f}rb"
            return str(lot_val)

        def _build_card(i, r):
            """Build satu stock card (4-5 baris)."""
            sc   = status_emoji(r.get("status", ""))
            lot  = r.get("harga_lot", 0)
            maks = int(budget // lot) if lot > 0 else 0
            ma   = r.get("ma", "-")
            if ma == "Uptrend Kuat":     trend = "📈 Uptrend"
            elif ma == "Di Atas MA50":   trend = "↗ MA50"
            elif ma == "Di Atas MA200":  trend = "→ MA200"
            elif ma == "Downtrend":      trend = "📉 Downtrend"
            else:                        trend = "-"
            dy_val = r.get("div_yield", 0)
            div_thn = r.get("div_tahun", 0)
            if dy_val > 0:
                dy_str = f"Div {dy_val*100:.1f}%"
                if div_thn >= 10: dy_str += f" (🏆{div_thn} thn)"
                elif div_thn >= 1: dy_str += f" ({div_thn} thn)"
            else:
                dy_str = "Tanpa dividen"
            mfi = r.get("mfi", 50)
            obv = r.get("obv", "-")
            flow_parts = []
            if mfi < 25:    flow_parts.append(f"💰 MFI {mfi:.0f}")
            elif mfi > 80:  flow_parts.append(f"⚠ MFI {mfi:.0f}")
            else:           flow_parts.append(f"MFI {mfi:.0f}")
            if obv == "Bullish Div":   flow_parts.append("💰 Smart money masuk")
            elif obv == "Bearish Div": flow_parts.append("⚠ Smart money keluar")
            flow_str = " · ".join(flow_parts)
            return (
                f"{i}. {sc} <b>{r['ticker']}</b> — {r['status']} ({r['skor']}/100)\n"
                f"   <i>{r.get('nama', '')[:35]}</i>\n"
                f"   {fmt_rp(lot)}/lot · maks {maks} lot\n"
                f"   {trend} · {dy_str}\n"
                f"   {flow_str}"
            )

        def _build_table(stocks):
            """Build comparison table untuk sekelompok saham."""
            col_w, label_w = 7, 6
            tickers = [r["ticker"][:5] for r in stocks]
            rows = [
                ("Skor",  [str(r["skor"]) for r in stocks]),
                ("Yield", [f"{r.get('div_yield',0)*100:.1f}%" for r in stocks]),
                ("PER",   [f"{r['per']:.1f}" if r.get("per", 0) > 0 else "-" for r in stocks]),
                ("ROE",   [f"{r.get('roe_pct',0):.0f}%" for r in stocks]),
                ("Trend", [_trend_short(r.get("ma", "-")) for r in stocks]),
                ("MFI",   [f"{r.get('mfi',50):.0f}" for r in stocks]),
                ("RSI",   [f"{r.get('rsi',0):.0f}" for r in stocks]),
                ("1lot",  [_lot_short(r.get("harga_lot", 0)) for r in stocks]),
            ]
            header = " " * label_w + "".join(str(t).rjust(col_w) for t in tickers)
            sep = "─" * (label_w + col_w * len(tickers))
            tbl = [header, sep]
            for label, vals in rows:
                tbl.append(label.ljust(label_w) + "".join(str(v).rjust(col_w) for v in vals))
            return "<pre>" + "\n".join(tbl) + "</pre>"

        # ── Build blok-blok: 4 saham per blok ──
        BLOCK_SIZE = 4
        blocks = []  # list of text strings, satu per blok

        for blk_start in range(0, len(top), BLOCK_SIZE):
            blk = top[blk_start:blk_start + BLOCK_SIZE]
            blk_lines = []

            # Cards
            for j, r in enumerate(blk):
                rank = blk_start + j + 1
                blk_lines.append(_build_card(rank, r))

            # Comparison table (minimal 2 saham)
            if len(blk) >= 2:
                blk_lines.append("\n📈 <b>Perbandingan</b>")
                blk_lines.append(_build_table(blk))

            blocks.append("\n".join(blk_lines))

        # ── Kirim per blok, gabung kalau muat ──
        MAX_LEN = 3800
        cmp_buttons = [[
            InlineKeyboardButton("🔍 Budget lain", callback_data="bandingkan"),
            InlineKeyboardButton("◀ Menu", callback_data="menu"),
        ]]

        # Header pesan pertama
        header_text = (
            f"📊 <b>PERBANDINGAN — Budget {fmt_rp(budget)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>{fmt_rp(budget)}</b> — {len(bisa)} saham BUY/HOLD · top {len(top)} ditampilkan"
        )

        # Footer
        total_1lot = sum(r.get("harga_lot", 0) for r in top)
        footer_text = (
            f"\n💡 Beli masing-masing 1 lot = <b>{fmt_rp(total_1lot)}</b>\n"
            f"⚠️ <i>Bukan rekomendasi investasi resmi. DYOR.</i>"
        )

        # Gabung blok-blok, kirim pesan baru kalau melebihi limit
        pending = header_text
        total_blocks = len(blocks)

        for idx, blk_text in enumerate(blocks):
            is_last = (idx == total_blocks - 1)
            candidate = pending + "\n\n" + blk_text

            if is_last:
                candidate += "\n" + footer_text

            if len(candidate) > MAX_LEN and pending != header_text:
                # Kirim pending dulu (tanpa tombol)
                await send_fn(pending.strip(), parse_mode=ParseMode.HTML)
                pending = blk_text
                if is_last:
                    pending += "\n" + footer_text
            else:
                pending = candidate

        # Kirim sisa terakhir (dengan tombol)
        await send_fn(
            pending.strip(),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(cmp_buttons),
        )

    else:
        # ── Mode: semua level budget ──
        levels = [100_000, 500_000, 1_000_000, 5_000_000, 10_000_000]

        # Pastikan budget user termasuk
        if user_budget not in levels:
            levels.append(user_budget)
            levels.sort()

        lines = [
            "📊 <b>PERBANDINGAN SEMUA BUDGET</b>",
            f"📅 Data screening terkini",
            "━━━━━━━━━━━━━━━━━━━━━━\n",
        ]

        prev_tickers = set()

        for budget in levels:
            bisa = [
                r for r in hasil
                if r.get("harga_lot", 9e9) <= budget
                and r.get("status") in ("STRONG BUY", "BUY", "HOLD")
            ]
            bisa.sort(key=lambda r: r.get("skor", 0), reverse=True)
            top = bisa[:3]  # top 3 per level di mode ringkasan

            marker = " 👈 <i>budget kamu</i>" if budget == user_budget else ""
            lines.append(f"💰 <b>{fmt_rp(budget)}</b> — {len(bisa)} saham BUY/HOLD{marker}")

            if not top:
                lines.append("   <i>Tidak ada saham BUY/HOLD di budget ini</i>")
            else:
                for i, r in enumerate(top, 1):
                    sc  = status_emoji(r.get("status", ""))
                    new = " ⭐" if r.get("ticker","") not in prev_tickers else ""
                    dy  = f" Div:{r['div_yield']*100:.1f}%" if r.get("div_yield",0) > 0 else ""
                    lines.append(
                        f"   {i}. {sc} <b>{r['ticker']}</b> ({r['skor']}) · "
                        f"{fmt_rp(r.get('harga_lot',0))}/lot{dy}{new}"
                    )

            for r in top:
                prev_tickers.add(r.get("ticker",""))

            lines.append("")

        lines.append("👈 = budget kamu  ·  ⭐ = saham baru di level ini")
        lines.append("\n💡 <i>Pesan ini tersimpan di chat. Scroll ke atas untuk lihat hasil sebelumnya.</i>")
        lines.append("⚠️ <i>Bukan rekomendasi investasi resmi. Selalu DYOR.</i>")

        # Tombol untuk bandingkan budget spesifik
        cmp_buttons = [
            [
                InlineKeyboardButton("🔍 Budget spesifik", callback_data="bandingkan"),
                InlineKeyboardButton("◀ Menu", callback_data="menu"),
            ],
        ]

        await send_fn(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(cmp_buttons),
        )


async def show_status(send_fn):
    """Tampilkan status screening dengan progress detail + tombol refresh."""
    s      = load_status()
    status = s.get("status", "idle")
    msg    = s.get("message", "–")
    prog   = int(s.get("progress", 0))
    ts     = s.get("updated_at")
    log_lines = s.get("log", [])  # riwayat progress terakhir

    if status == "running":
        filled = int(prog / 10)
        bar    = "█" * filled + "░" * (10 - filled)
        txt    = (
            f"⏳ <b>Screening Sedang Berjalan</b>\n"
            f"[{bar}] {prog}%\n\n"
            f"🔍 <b>Sedang diproses:</b>\n{msg}\n"
        )
        if log_lines:
            txt += "\n<b>Log terakhir:</b>\n"
            for l in log_lines[-8:]:  # tampilkan 8 log terakhir
                txt += f"<code>{html.escape(str(l))}</code>\n"
        txt += "\n⏱ Auto-refresh dengan tombol di bawah."

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh Status", callback_data="status")],
            [InlineKeyboardButton("◀ Kembali", callback_data="menu")],
        ])

    elif status == "done":
        hasil = load_results()
        buy   = len([r for r in hasil if r.get("status") in ("STRONG BUY", "BUY")])
        # Gunakan DEFAULT_BUDGET untuk status (tidak ada context user di sini)
        bisa  = len([r for r in hasil if r.get("harga_lot", 9e9) <= DEFAULT_BUDGET])
        waktu = datetime.fromisoformat(ts).strftime("%d %b %Y %H:%M") if ts else "–"
        txt   = (
            f"✅ <b>Screening Selesai</b>\n\n"
            f"📊 {len(hasil)} saham dianalisis\n"
            f"🟢 {buy} saham BUY/STRONG BUY\n"
            f"💰 {bisa} saham bisa dibeli\n"
            f"⏱ Selesai: {waktu}"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Lihat Rekomendasi", callback_data="rekomendasi")],
            [InlineKeyboardButton("◀ Kembali", callback_data="menu")],
        ])

    elif status == "error":
        txt = (
            f"❌ <b>Screening Error</b>\n\n"
            f"<code>{msg}</code>\n\n"
            "Coba jalankan ulang dengan /screen"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Coba Lagi", callback_data="screen")],
            [InlineKeyboardButton("◀ Kembali", callback_data="menu")],
        ])

    else:  # idle
        txt = (
            "💤 <b>Belum Ada Screening</b>\n\n"
            "Tekan tombol di bawah untuk mulai analisis 150+ saham IDX."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Mulai Screening", callback_data="screen")],
            [InlineKeyboardButton("◀ Kembali", callback_data="menu")],
        ])

    await send_fn(txt, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def trigger_screen(send_fn, context):
    """Trigger screening baru di background thread."""
    s = load_status()
    if s.get("status") == "running":
        await send_fn(
            f"⏳ Screening sedang berjalan ({s.get('progress',0)}%)\n{s.get('message','')}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    budget = get_budget(context)

    await send_fn(
        f"⚙️ <b>Screening dimulai!</b>\n\n"
        f"💰 Budget: {fmt_rp(budget)}\n"
        f"🔍 Menganalisis 150+ saham IDX...\n\n"
        f"⏱ Estimasi waktu: 5–15 menit\n"
        f"📨 Kamu akan dapat notifikasi saat selesai.\n\n"
        f"Cek progress: /status",
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard(),
    )

    # Ambil chat_id sebelum masuk thread (context tidak thread-safe)
    try:
        chat_id_notif = context.user_data.get("_chat_id")
    except Exception:
        chat_id_notif = None

    # Log disimpan di memori — tidak baca file setiap callback (cegah race condition)
    _cb_logs      = []
    _cb_prog      = [0]       # list supaya bisa dimodifikasi dari nested function
    _cb_last_save = [0.0]     # timestamp terakhir kali tulis ke file

    def _progress_cb(msg: str):
        """
        Callback dari run_screener.
        Log disimpan di memori. File hanya ditulis:
        - Setiap 2 detik (throttle), ATAU
        - Saat milestone penting (Validasi selesai, Fetch selesai, dll)
        """
        import time as _time

        ts_now = _time.strftime("%H:%M:%S")
        _cb_logs.append(f"[{ts_now}] {msg[:80]}")
        if len(_cb_logs) > 20:
            _cb_logs.pop(0)

        # Estimasi progress
        prog = _cb_prog[0]
        milestone = False

        if "Validasi selesai" in msg:
            prog = 15; milestone = True
        elif msg.startswith("Validasi ["):
            # Validasi ticker progress: "Validasi [10/180] BBCA"
            try:
                part = msg.split("[")[1].split("]")[0]
                cur, tot = part.split("/")
                prog = int(int(cur.strip()) / int(tot.strip()) * 15)
            except Exception:
                pass
            # Tulis setiap 10 ticker supaya tidak terlalu sering
            try:
                cur_i = int(msg.split("[")[1].split("/")[0])
                if cur_i % 10 == 0:
                    milestone = True
            except Exception:
                pass
        elif msg.startswith("[") and "/" in msg:
            try:
                part = msg[1:msg.index("]")]
                cur, tot = part.split("/")
                prog = 15 + int(int(cur.strip()) / int(tot.strip()) * 45)
            except Exception:
                pass
        elif "Fetch selesai" in msg:
            prog = 60; milestone = True
        elif "Scoring selesai" in msg:
            prog = 85; milestone = True
        elif "CSV disimpan" in msg:
            prog = 90; milestone = True

        _cb_prog[0] = prog

        # Tulis ke file hanya kalau milestone ATAU sudah 5 detik sejak terakhir tulis
        now = _time.time()
        if milestone or (now - _cb_last_save[0]) >= 2:
            save_status_with_log("running", prog, msg, list(_cb_logs))
            _cb_last_save[0] = now

    def _run():
        if not _screen_lock.acquire(blocking=False):
            return
        try:
            save_status_with_log("running", 0, "Memulai validasi ticker...", [])
            from run import run_screener
            hasil = run_screener(kirim_tg=False, progress_cb=_progress_cb)

            if hasil:
                cache = {
                    "generated_at": datetime.now().isoformat(),
                    "budget":       budget,
                    "total":        len(hasil),
                    "data":         hasil,
                }
                with open(RESULTS_FILE, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False, indent=2)

                buy = len([r for r in hasil if r.get("status") in ("STRONG BUY","BUY")])
                save_status_with_log("done", 100,
                    f"Selesai! {len(hasil)} saham, {buy} BUY", [])
            else:
                save_status_with_log("error", 0, "Tidak ada data — cek koneksi internet", [])
        except Exception as e:
            save_status_with_log("error", 0, str(e)[:150], [])
            log.error(traceback.format_exc())
        finally:
            _screen_lock.release()

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN belum di-set di .env!")
        print("   Buat file .env dengan isi: BOT_TOKEN=token_kamu")
        return

    print("=" * 50)
    print("  IDX Screener Bot — Starting...")
    print("=" * 50)

    # Bersihkan file status corrupt dari sesi sebelumnya
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            # Kalau status "running" tapi bot baru start → berarti crash sebelumnya
            if data.get("status") == "running":
                save_status_with_log("idle", 0,
                    "Bot restart — screening sebelumnya dibatalkan", [])
                print("  ⚠ Status sebelumnya direset (bot restart)")
            print(f"  📋 Status terakhir: {data.get('status','?')} — {data.get('message','')[:50]}")
        except Exception:
            # File corrupt → hapus
            try:
                os.remove(STATUS_FILE)
                print("  🗑 File status corrupt dihapus, mulai fresh")
            except Exception:
                pass

    # Persistence: budget & setting tersimpan walau bot restart
    persistence = PicklePersistence(
        filepath=os.path.join(BASE_DIR, "bot_data.pkl")
    )
    app = Application.builder().token(BOT_TOKEN).persistence(persistence).build()

    # ── Error handler ──────────────────────────────────────
    # PENTING: BadRequest adalah subclass dari NetworkError di python-telegram-bot v20.
    # Jadi BadRequest HARUS dicek SEBELUM NetworkError, kalau tidak semua BadRequest
    # akan tertangkap oleh isinstance(err, NetworkError) duluan.
    async def error_handler(update, context):
        from telegram.error import TimedOut, NetworkError, RetryAfter, BadRequest
        err = context.error

        # 1. BadRequest — HARUS sebelum NetworkError (karena BadRequest extends NetworkError)
        if isinstance(err, BadRequest):
            err_str = str(err).lower()
            if "message is not modified" in err_str:
                # User klik tombol yang sama 2x — tidak perlu di-log, harmless
                return
            if "message to edit not found" in err_str:
                # Pesan sudah dihapus/expired — skip
                return
            log.warning(f"BadRequest: {err}")
            if "message is too long" in err_str and update and update.effective_message:
                try:
                    await update.effective_message.reply_text(
                        "⚠️ Pesan terlalu panjang untuk Telegram. "
                        "Coba kurangi jumlah rekomendasi dengan /jumlah 5"
                    )
                except Exception:
                    pass
            return

        # 2. RetryAfter
        if isinstance(err, RetryAfter):
            log.warning(f"Rate limited, retry after {err.retry_after}s")
            return

        # 3. TimedOut & NetworkError (polling biasa)
        if isinstance(err, (TimedOut, NetworkError)):
            # Suppress "message is not modified" yg lolos ke sini
            if "message is not modified" in str(err).lower():
                return
            log.info(f"Network/Timeout (normal): {err}")
            return

        # 4. Error lain — log detail
        log.error(f"Unhandled error: {err}")
        log.error(traceback.format_exc())

    app.add_error_handler(error_handler)

    # Command handlers
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("rekomendasi",  cmd_rekomendasi))
    app.add_handler(CommandHandler("dividen",      cmd_dividen))
    app.add_handler(CommandHandler("bandingkan",   cmd_bandingkan))
    app.add_handler(CommandHandler("screen",       cmd_screen))
    app.add_handler(CommandHandler("status",       cmd_status))
    app.add_handler(CommandHandler("budget",       cmd_budget))
    app.add_handler(CommandHandler("jumlah",       cmd_jumlah))
    app.add_handler(CommandHandler("help",         cmd_help))

    # Inline keyboard callback
    app.add_handler(CallbackQueryHandler(on_button))

    # Tangkap pesan angka biasa sebagai budget (tanpa /budget)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("  Bot aktif. Ctrl+C untuk stop.")
    print("  Coba /start di Telegram sekarang!\n")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()