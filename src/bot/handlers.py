"""Telegram command & callback handlers.

9 commands + 14 callback types + 1 message handler.
All formatting delegated to formatters.py.
All keyboards from keyboards.py.
"""

from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import math
import os
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from src.bot.formatters import (
    fmt_rp,
    fmt_timestamp,
    format_budget_set,
    format_dividen_list,
    format_help,
    format_perbandingan_all,
    format_rekomendasi,
    format_status,
    format_stock_compact,
    format_stock_detail,
    format_watchlist,
    get_lot,
    now_wib,
    split_long_message,
    status_emoji,
)
from src.bot.keyboards import (
    back_keyboard,
    bandingkan_keyboard,
    budget_keyboard,
    cmp_nav_keyboard,
    detail_buttons,
    dividen_detail_buttons,
    jumlah_keyboard,
    main_keyboard,
    status_keyboard,
)
from src.config.settings import BASE_DIR, DEFAULT_BUDGET, GEMINI_API_KEY

log = logging.getLogger(__name__)

# ── File paths ────────────────────────────────────────────────

RESULTS_FILE = str(BASE_DIR / "results_cache.json")
STATUS_FILE = str(BASE_DIR / "screen_status.json")

# ── Locks (BUG-007, BUG-008 fixes) ───────────────────────────

_screen_lock = threading.Lock()
_file_lock = threading.Lock()
_log_lock = threading.Lock()

# ── Rate limiting ─────────────────────────────────────────────

_SCREEN_COOLDOWN = 300  # 5 minutes
_STALE_RUNNING_MINUTES = 15  # if "running" for >15 min, consider it stuck
_last_screen_time: float = 0.0
_screen_thread: threading.Thread | None = None

# ── Budget validation ─────────────────────────────────────────

_BUDGET_MIN = 100_000
_BUDGET_MAX = 100_000_000


# ═══════════════════════════════════════════════════════════════
#  Data I/O (with file locks — BUG-007 fix)
# ═══════════════════════════════════════════════════════════════

def _json_default(obj):
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return str(obj)


# ── Mode remote (deploy Render + GitHub Actions) ─────────────
# Disk Render ephemeral → data dibaca dari URL raw GitHub yang di-commit
# oleh workflow screening. Set env:
#   RESULTS_URL=https://raw.githubusercontent.com/USER/REPO/main/results_cache.json
#   STATUS_URL =https://raw.githubusercontent.com/USER/REPO/main/screen_status.json
RESULTS_URL = os.getenv("RESULTS_URL", "").strip()
STATUS_URL = os.getenv("STATUS_URL", "").strip()
_REMOTE_TTL = 300  # detik — data berubah maks 3x/hari, 5 menit cukup segar
_remote_mem: dict = {"fetched_at": 0.0, "cache": None}


def _load_results_remote() -> dict:
    import requests

    now = time.time()
    if (_remote_mem["cache"] is not None
            and now - _remote_mem["fetched_at"] < _REMOTE_TTL):
        return _remote_mem["cache"]
    try:
        resp = requests.get(RESULTS_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            _remote_mem["cache"] = {
                "mtime": None,
                "data": data.get("data", []),
                "generated_at": data.get("generated_at"),
            }
            _remote_mem["fetched_at"] = now
    except Exception as exc:
        log.warning("Gagal fetch RESULTS_URL (pakai cache lama): %s", exc)
        _remote_mem["fetched_at"] = now  # jangan hammer saat sumber down
    return _remote_mem["cache"] or {"mtime": None, "data": [],
                                    "generated_at": None}


# In-memory cache hasil screening, di-refresh hanya saat mtime file berubah.
# Tanpa ini file ~240 KB di-parse DUA KALI (data + timestamp) per command.
_results_mem: dict = {"mtime": None, "data": [], "generated_at": None}


def _load_results_cached() -> dict:
    if RESULTS_URL:
        return _load_results_remote()
    try:
        mtime = os.path.getmtime(RESULTS_FILE)
    except OSError:
        return {"mtime": None, "data": [], "generated_at": None}
    with _file_lock:
        if _results_mem["mtime"] == mtime:
            return _results_mem
        try:
            with open(RESULTS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {"mtime": None, "data": [], "generated_at": None}
        if isinstance(data, dict):
            _results_mem.update(mtime=mtime, data=data.get("data", []),
                                generated_at=data.get("generated_at"))
        else:
            _results_mem.update(mtime=mtime, data=data, generated_at=None)
        return _results_mem


def load_results() -> list:
    return _load_results_cached()["data"]


def load_results_timestamp() -> str | None:
    return _load_results_cached()["generated_at"]


def load_status() -> dict:
    default = {
        "status": "idle", "message": "Belum pernah dijalankan",
        "progress": 0, "log": [], "updated_at": None,
    }
    if STATUS_URL:
        import requests
        try:
            resp = requests.get(STATUS_URL, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "status" in data:
                return data
        except Exception as exc:
            log.warning("Gagal fetch STATUS_URL: %s", exc)
        return default
    if not os.path.exists(STATUS_FILE):
        return default
    with _file_lock:
        try:
            with open(STATUS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "status" not in data:
                raise ValueError("Invalid format")
            return data
        except Exception:
            try:
                os.remove(STATUS_FILE)
            except Exception:
                pass
            return default


def _save_status(status: str, progress: int, message: str, log_lines: list):
    """Atomic write with lock (BUG-007 fix)."""
    data = {
        "status": status,
        "progress": int(progress),
        "message": message,
        "log": log_lines[-20:] if log_lines else [],
        "updated_at": now_wib().isoformat(),
    }
    tmp = STATUS_FILE + ".tmp"
    with _file_lock:
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, STATUS_FILE)
        except Exception as e:
            log.warning("Failed to write status: %s", e)
            try:
                os.remove(tmp)
            except Exception:
                pass


# ── Stale status detection ────────────────────────────────────

def _is_status_stale(status_data: dict) -> bool:
    """Detect if a 'running' status is stale (bot crashed/restarted mid-screening).

    Stale if: thread is dead, or updated_at older than _STALE_RUNNING_MINUTES.
    """
    if status_data.get("status") != "running":
        return False
    if _screen_thread is not None and not _screen_thread.is_alive():
        return True
    if _screen_thread is None:
        return True
    ts = status_data.get("updated_at")
    if not ts:
        return True
    try:
        updated = datetime.fromisoformat(ts)
        elapsed = (now_wib() - updated).total_seconds() / 60
        return elapsed > _STALE_RUNNING_MINUTES
    except (ValueError, TypeError):
        return True


def _fix_stale_status() -> dict:
    """Reset a stale 'running' status to 'error' so new screening can proceed."""
    _save_status("error", 0, "Screening sebelumnya terputus (bot restart)", [])
    return load_status()


# ── User settings ─────────────────────────────────────────────

def _get_budget(context: ContextTypes.DEFAULT_TYPE) -> int:
    return context.user_data.get("budget", DEFAULT_BUDGET)


def _budget_set(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return "budget" in context.user_data


def _get_jumlah(context: ContextTypes.DEFAULT_TYPE) -> int:
    return context.user_data.get("jumlah", 5)


# ── Chat tracking (dibutuhkan _send_long untuk kirim pesan lanjutan) ──

async def remember_chat_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simpan chat_id supaya _send_long bisa kirim bagian pesan lanjutan.

    Didaftarkan sebagai TypeHandler(Update, ...) di group=-1 (main.py) —
    jalan otomatis SEBELUM handler apa pun, jadi handler baru tidak mungkin
    lupa memanggilnya.
    """
    if update.effective_chat and context.user_data is not None:
        context.user_data["_chat_id"] = update.effective_chat.id


# ── Safe edit (handles Telegram API errors) ───────────────────

async def _safe_edit(query, text: str, **kwargs):
    if len(text) > 4096:
        # Guard terakhir — konten panjang seharusnya lewat _send_long().
        log.warning("safe_edit: teks %d char > 4096, dipotong — caller seharusnya pakai _send_long", len(text))
        # 3900 (bukan 4050): Telegram menghitung limit dalam UTF-16 code unit,
        # emoji non-BMP = 2 unit — sisakan headroom.
        cut = text[:3900]
        nl = cut.rfind("\n")
        if nl > 3000:
            cut = cut[:nl]
        text = cut + "\n\n<i>[... pesan dipotong]</i>"
    try:
        await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err or "message to edit not found" in err:
            return
        if "can't parse entities" in err:
            plain = {k: v for k, v in kwargs.items() if k != "parse_mode"}
            try:
                await query.edit_message_text(text, **plain)
            except Exception:
                pass
            return
        log.warning("safe_edit BadRequest: %s", e)


# ── Multi-message sending ─────────────────────────────────────

async def _send_long(send_fn, context, text: str,
                     reply_markup=None, **kwargs):
    """Send text, splitting into multiple messages if it exceeds Telegram limit.

    First part goes via send_fn (which may be edit_message_text).
    Extra parts are sent as new messages to the chat.
    reply_markup is attached to the LAST message only.
    """
    parts = split_long_message(text)
    if len(parts) == 1:
        await send_fn(parts[0], reply_markup=reply_markup, **kwargs)
        return

    await send_fn(parts[0], **kwargs)

    chat_id = context.user_data.get("_chat_id") if context else None
    if not chat_id:
        log.warning("_send_long: chat_id tidak tersedia — %d bagian pesan tidak terkirim",
                    len(parts) - 1)
        return
    for i, part in enumerate(parts[1:]):
        kw = dict(kwargs)
        if i == len(parts) - 2:
            kw["reply_markup"] = reply_markup
        try:
            await context.bot.send_message(chat_id=chat_id, text=part, **kw)
        except Exception as e:
            log.warning("Failed to send message part %d: %s", i + 2, e)


# ── Input validation ──────────────────────────────────────────

def _parse_budget(raw: str) -> int | None:
    text = raw.replace(".", "").replace(",", "").replace(" ", "")
    if not text.isdigit():
        return None
    val = int(text)
    if val < _BUDGET_MIN or val > _BUDGET_MAX:
        return None
    return val


def _parse_jumlah(raw: str) -> int | None:
    text = raw.strip()
    if not text.isdigit():
        return None
    val = int(text)
    if not 1 <= val <= 20:
        return None
    return val


# ═══════════════════════════════════════════════════════════════
#  COMMAND HANDLERS (9)
# ═══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    budget = _get_budget(context)
    hasil = load_results()
    ts = load_results_timestamp()

    msg = (
        "📊 <b>IDX Stock Screener</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Halo! Bot ini membantu kamu menemukan saham IDX terbaik "
        "berdasarkan analisis fundamental + teknikal.\n\n"
    )

    if hasil:
        buy = len([r for r in hasil if r.get("label", r.get("status")) in ("STRONG BUY", "BUY")])
        bisa = len([r for r in hasil if get_lot(r) <= budget])
        ts_line = fmt_timestamp(ts)
        msg += (
            f"<b>Ringkasan screening terkini:</b>\n"
            f"🔍 {len(hasil)} saham dianalisis\n"
            f"🟢 {buy} saham BUY/STRONG BUY\n"
            f"💰 {bisa} saham bisa dibeli (budget {fmt_rp(budget)})\n"
        )
        if ts_line:
            msg += f"{ts_line}\n"
        msg += "\n"
    else:
        msg += "⚠️ Belum ada data. Pilih <b>Screening Baru</b> untuk mulai.\n\n"

    if not _budget_set(context):
        msg += (
            "⚙️ <b>Sebelum mulai, set budget investasi kamu:</b>\n"
            "Budget menentukan saham mana yang bisa kamu beli (1 lot = 100 lembar)."
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML,
                                         reply_markup=budget_keyboard())
        return

    msg += "Pilih menu di bawah:"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML,
                                     reply_markup=main_keyboard())


async def cmd_rekomendasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_rekomendasi(update.message.reply_text, context)


async def cmd_dividen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_dividen(update.message.reply_text, context)


async def cmd_bandingkan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /bandingkan 100rb 200rb → langsung bandingkan sesuai input user.
    # /bandingkan tanpa argumen → tanya budget dulu (mode input), BUKAN
    # langsung dump tabel default yang tidak diminta user.
    args_text = " ".join(context.args) if context.args else ""
    if args_text:
        from src.bot.conversation import _extract_budgets
        budgets = [b for b in _extract_budgets(args_text)
                   if _BUDGET_MIN <= b <= _BUDGET_MAX]
        if len(budgets) >= 2:
            await _show_multi_budget(update.message.reply_text, context, budgets)
            return
        if len(budgets) == 1:
            await _show_perbandingan(update.message.reply_text, context,
                                     target_budget=budgets[0])
            return
    context.user_data["mode"] = "bandingkan"
    await update.message.reply_text(
        "🔍 <b>Bandingkan Budget</b>\n\n"
        "Pilih budget atau <b>ketik nominal yang mau dibandingkan</b>:\n"
        "• Satu budget: <code>750rb</code>\n"
        "• Bandingkan: <code>100rb dan 200rb</code> atau <code>100000, 500000</code>\n"
        "• Semua level: tombol 📊 di bawah",
        parse_mode=ParseMode.HTML,
        reply_markup=bandingkan_keyboard(),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_status(update.message.reply_text, context)


async def cmd_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _trigger_screen(update.message.reply_text, context)


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        budget = _get_budget(context)
        await update.message.reply_text(
            f"💰 Budget kamu saat ini: <b>{fmt_rp(budget)}</b>\n\n"
            "Untuk mengubah:\n<code>/budget 1000000</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    val = _parse_budget(args[0])
    if val is None:
        await update.message.reply_text(
            f"❌ Budget harus angka {fmt_rp(_BUDGET_MIN)} – {fmt_rp(_BUDGET_MAX)}.\n"
            "Contoh: <code>/budget 500000</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    context.user_data["budget"] = val
    await update.message.reply_text(
        format_budget_set(val),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def cmd_jumlah(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        n = _get_jumlah(context)
        await update.message.reply_text(
            f"📋 Jumlah rekomendasi kamu: <b>{n}</b>\n\n"
            "Untuk mengubah:\n<code>/jumlah 10</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    val = _parse_jumlah(args[0])
    if val is None:
        await update.message.reply_text(
            "❌ Masukkan angka 1–20.\nContoh: <code>/jumlah 10</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    context.user_data["jumlah"] = val
    await update.message.reply_text(
        f"✅ Jumlah rekomendasi diubah ke <b>{val}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        format_help(), parse_mode=ParseMode.HTML, reply_markup=main_keyboard()
    )


# ═══════════════════════════════════════════════════════════════
#  MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()

    if context.user_data.get("mode") == "bandingkan":
        # Terima format apa pun: "100000, 500000", "100rb dan 200rb",
        # "1jt vs 5jt" — bukan cuma angka dipisah koma.
        from src.bot.conversation import _extract_budgets
        budgets = [b for b in _extract_budgets(raw)
                   if _BUDGET_MIN <= b <= _BUDGET_MAX]
        if len(budgets) >= 2:
            await _show_multi_budget(update.message.reply_text, context, budgets)
            return
        if len(budgets) == 1:
            await _show_perbandingan(update.message.reply_text, context,
                                     target_budget=budgets[0])
            return
        await update.message.reply_text(
            f"⚠️ Tidak menemukan nominal budget di pesanmu.\n"
            f"Budget harus {fmt_rp(_BUDGET_MIN)} – {fmt_rp(_BUDGET_MAX)}.\n"
            "Contoh: <code>100rb dan 500rb</code> atau <code>100000, 500000</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    text = raw.replace(".", "").replace(",", "").replace(" ", "")
    if not text.isdigit():
        if not GEMINI_API_KEY:
            await update.message.reply_text(
                "⚙️ Fitur percakapan belum dikonfigurasi. "
                "Gunakan /help untuk command.",
                parse_mode=ParseMode.HTML,
            )
            return
        from src.bot.conversation import get_response
        hasil = load_results()
        # effective_user bisa None (anonymous admin/channel di grup)
        user = update.effective_user
        user_id = user.id if user else update.effective_chat.id
        reply = await get_response(user_id, raw, hasil)
        try:
            await _send_long(update.message.reply_text, context, reply,
                             parse_mode=ParseMode.HTML)
        except BadRequest as e:
            if "can't parse entities" in str(e).lower():
                # HTML dari LLM bisa misnested — kirim ulang tanpa parse_mode
                await _send_long(update.message.reply_text, context, reply)
            else:
                raise
        return

    val = int(text)
    if val < _BUDGET_MIN:
        await update.message.reply_text(
            f"⚠️ Minimal {fmt_rp(_BUDGET_MIN)}\n"
            "Contoh: ketik <code>500000</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if val > _BUDGET_MAX:
        await update.message.reply_text(
            f"⚠️ Maksimal {fmt_rp(_BUDGET_MAX)}\n"
            "Contoh: <code>5000000</code> untuk Rp 5 juta",
            parse_mode=ParseMode.HTML,
        )
        return

    if context.user_data.get("mode") == "bandingkan":
        await _show_perbandingan(update.message.reply_text, context, target_budget=val)
        return

    context.user_data["budget"] = val
    await update.message.reply_text(
        format_budget_set(val),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════
#  CALLBACK HANDLER (15 callback types)
# ═══════════════════════════════════════════════════════════════

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    async def _edit(text, **kw):
        await _safe_edit(query, text, **kw)

    # Clear bandingkan mode when navigating away
    if data not in ("bandingkan",) and not data.startswith("cmp_budget_"):
        context.user_data.pop("mode", None)

    # 1. menu
    if data == "menu":
        budget = _get_budget(context)
        await _edit(
            f"💰 Budget: <b>{fmt_rp(budget)}</b>\n\nPilih menu:",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

    # 2. rekomendasi
    elif data == "rekomendasi":
        await _show_rekomendasi(_edit, context)

    # 3. dividen
    elif data == "dividen":
        await _show_dividen(_edit, context)

    # 4. bandingkan
    elif data == "bandingkan":
        context.user_data["mode"] = "bandingkan"
        chat_id = update.effective_chat.id
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔍 <b>Bandingkan Budget</b>\n\n"
                "Pilih budget atau <b>ketik angka</b>:\n"
                "• Satu budget: <code>750000</code>\n"
                "• Multi-budget: <code>100000, 500000, 1000000</code>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=bandingkan_keyboard(),
        )

    # 5-6. cmp_budget_{N} / cmp_budget_all
    elif data.startswith("cmp_budget_"):
        context.user_data.pop("mode", None)
        chat_id = update.effective_chat.id
        val = data.replace("cmp_budget_", "")
        send = lambda text, **kw: context.bot.send_message(chat_id=chat_id, text=text, **kw)
        if val == "all":
            await _show_perbandingan(send, context, target_budget=None)
        else:
            try:
                target = int(val)
            except ValueError:
                return
            await _show_perbandingan(send, context, target_budget=target)

    # 7. screen
    elif data == "screen":
        await _trigger_screen(_edit, context)

    # 7b. watchlist
    elif data == "watchlist":
        await _show_watchlist(_edit, context)

    # 8. status
    elif data == "status":
        await _show_status(_edit, context)

    # 9-10. setup_budget_{N} / setup_budget_manual
    elif data.startswith("setup_budget_"):
        val = data.replace("setup_budget_", "")
        if val == "manual":
            await _edit(
                "✏️ <b>Ketik budget kamu:</b>\n\n"
                "Contoh: <code>/budget 750000</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            try:
                new_budget = int(val)
            except ValueError:
                await _edit("❌ Gagal set budget.", reply_markup=budget_keyboard())
                return
            context.user_data["budget"] = new_budget
            await _edit(
                format_budget_set(new_budget) + "\n\nSekarang pilih menu:",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard(),
            )

    # 11. set_budget
    elif data == "set_budget":
        budget = _get_budget(context)
        await _edit(
            f"💰 <b>Set Budget Kamu</b>\n\n"
            f"Budget saat ini: <b>{fmt_rp(budget)}</b>\n\n"
            "Ketik command:\n<code>/budget 500000</code>\n\n"
            f"Range: {fmt_rp(_BUDGET_MIN)} – {fmt_rp(_BUDGET_MAX)}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )

    # 12. set_jumlah
    elif data == "set_jumlah":
        n = _get_jumlah(context)
        await _edit(
            f"🔢 <b>Jumlah Rekomendasi</b>\n\n"
            f"Saat ini: <b>{n} rekomendasi</b>\n\n"
            "Pilih berapa rekomendasi yang mau ditampilkan:",
            parse_mode=ParseMode.HTML,
            reply_markup=jumlah_keyboard(n),
        )

    # 13. jumlah_{N}
    elif data.startswith("jumlah_"):
        try:
            n = int(data.replace("jumlah_", ""))
        except ValueError:
            return
        if not 1 <= n <= 20:
            return
        context.user_data["jumlah"] = n
        await _edit(
            f"✅ Jumlah rekomendasi diubah ke <b>{n}</b>\n\nPilih menu:",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

    # 14. detail_{TICKER}
    elif data.startswith("detail_"):
        ticker = data.replace("detail_", "")
        if not ticker.replace(".JK", "").isalnum():
            await _edit("❌ Ticker tidak valid.", reply_markup=back_keyboard())
            return
        hasil = load_results()
        stock = next((r for r in hasil if r.get("ticker") == ticker), None)
        budget = _get_budget(context)
        if stock:
            await _send_long(
                _edit, context,
                format_stock_detail(stock, budget),
                parse_mode=ParseMode.HTML,
                reply_markup=back_keyboard(),
            )
        else:
            await _edit("Saham tidak ditemukan.", reply_markup=back_keyboard())


# ═══════════════════════════════════════════════════════════════
#  SHARED DISPLAY FUNCTIONS
# ═══════════════════════════════════════════════════════════════

async def _show_rekomendasi(send_fn, context):
    if not _budget_set(context):
        await send_fn(
            "⚙️ <b>Budget belum diset!</b>\n\nPilih budget investasi kamu dulu:",
            parse_mode=ParseMode.HTML,
            reply_markup=budget_keyboard(),
        )
        return

    budget = _get_budget(context)
    hasil = load_results()
    if not hasil:
        await send_fn("⚠️ Belum ada data. Jalankan /screen dulu.",
                       parse_mode=ParseMode.HTML, reply_markup=back_keyboard())
        return

    bisa = [
        r for r in hasil
        if get_lot(r) <= budget
        and r.get("label", r.get("status")) in ("STRONG BUY", "BUY", "HOLD")
    ]
    bisa.sort(key=lambda r: r.get("skor_total", r.get("skor", 0)), reverse=True)
    jumlah = _get_jumlah(context)
    top = bisa[:jumlah]

    if not top:
        await send_fn(
            f"😕 Tidak ada saham BUY/HOLD yang bisa dibeli dengan budget <b>{fmt_rp(budget)}</b>.\n"
            "Naikkan budget: /budget [nominal]",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    ts = load_results_timestamp()
    msg = format_rekomendasi(top, budget, len(bisa), len(hasil), jumlah, timestamp=ts)
    await _send_long(send_fn, context, msg,
                     parse_mode=ParseMode.HTML, reply_markup=detail_buttons(top))


async def _show_dividen(send_fn, context):
    budget = _get_budget(context)
    jumlah = _get_jumlah(context)
    hasil = load_results()

    if not hasil:
        await send_fn("⚠️ Belum ada data. Jalankan /screen dulu.",
                       parse_mode=ParseMode.HTML, reply_markup=back_keyboard())
        return

    top_div = sorted(
        [r for r in hasil if (r.get("yield_ttm", r.get("div_yield", 0)) or 0) > 0],
        key=lambda r: r.get("yield_ttm", r.get("div_yield", 0)) or 0,
        reverse=True,
    )[:jumlah]

    if not top_div:
        await send_fn("😕 Tidak ada saham dengan dividen di data screening.",
                       parse_mode=ParseMode.HTML, reply_markup=back_keyboard())
        return

    ts = load_results_timestamp()
    msg = format_dividen_list(top_div, budget, jumlah, timestamp=ts)
    await _send_long(send_fn, context, msg,
                     parse_mode=ParseMode.HTML,
                     reply_markup=dividen_detail_buttons(top_div))


async def _show_watchlist(send_fn, context):
    budget = _get_budget(context)
    jumlah = _get_jumlah(context)
    hasil = load_results()

    if not hasil:
        await send_fn("⚠️ Belum ada data. Jalankan /screen dulu.",
                       parse_mode=ParseMode.HTML, reply_markup=back_keyboard())
        return

    watchlist = [
        r for r in hasil
        if r.get("label", r.get("status")) in ("HOLD", "JUAL")
    ]
    watchlist.sort(key=lambda r: r.get("skor_total", r.get("skor", 0)), reverse=True)

    if not watchlist:
        await send_fn("✅ Tidak ada saham HOLD/JUAL saat ini — semua BUY atau STRONG BUY!",
                       parse_mode=ParseMode.HTML, reply_markup=back_keyboard())
        return

    ts = load_results_timestamp()
    msg = format_watchlist(watchlist, budget, jumlah, timestamp=ts)
    await _send_long(send_fn, context, msg,
                     parse_mode=ParseMode.HTML,
                     reply_markup=detail_buttons(watchlist[:jumlah]))


async def _show_status(send_fn, context):
    s = load_status()
    if _is_status_stale(s):
        s = _fix_stale_status()
    txt = format_status(s)
    kb = status_keyboard(s.get("status", "idle"))
    await _send_long(send_fn, context, txt, parse_mode=ParseMode.HTML,
                     reply_markup=kb)


async def _show_perbandingan(send_fn, context, target_budget=None):
    hasil = load_results()
    if not hasil:
        await send_fn("⚠️ Belum ada data. Jalankan /screen dulu.",
                       parse_mode=ParseMode.HTML)
        return

    user_budget = _get_budget(context)
    jumlah = _get_jumlah(context)
    ts = load_results_timestamp()
    ts_line = fmt_timestamp(ts)

    if target_budget is not None:
        bisa = [
            r for r in hasil
            if get_lot(r) <= target_budget
            and r.get("label", r.get("status")) in ("STRONG BUY", "BUY", "HOLD")
        ]
        bisa.sort(key=lambda r: r.get("skor_total", r.get("skor", 0)), reverse=True)
        top = bisa[:jumlah]

        if not top:
            await send_fn(
                f"📊 <b>Budget {fmt_rp(target_budget)}</b>\n"
                "<i>Tidak ada saham BUY/HOLD di budget ini.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=cmp_nav_keyboard(),
            )
            return

        lines = [
            f"📊 <b>PERBANDINGAN — Budget {fmt_rp(target_budget)}</b>",
        ]
        if ts_line:
            lines.append(ts_line)
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 {len(bisa)} saham BUY/HOLD · top {len(top)} ditampilkan\n")
        for i, r in enumerate(top, 1):
            lines.append(format_stock_compact(r, target_budget, rank=i))
        lines.append("\n⚠️ <i>Bukan rekomendasi investasi resmi. DYOR.</i>")
        msg = "\n".join(lines)
        await _send_long(send_fn, context, msg,
                         parse_mode=ParseMode.HTML,
                         reply_markup=cmp_nav_keyboard())
    else:
        msg = format_perbandingan_all(hasil, user_budget, jumlah, timestamp=ts)
        await _send_long(send_fn, context, msg,
                         parse_mode=ParseMode.HTML,
                         reply_markup=cmp_nav_keyboard())


async def _show_multi_budget(send_fn, context, budgets: list[int]):
    hasil = load_results()
    jumlah = _get_jumlah(context)
    if not hasil:
        await send_fn("⚠️ Belum ada data. Jalankan /screen dulu.",
                      parse_mode=ParseMode.HTML)
        return

    from src.bot.formatters import _budget_label, _display_ticker

    per_budget: dict[int, list[dict]] = {}
    for b in budgets:
        bisa = [
            r for r in hasil
            if get_lot(r) <= b
            and r.get("label", r.get("status")) in ("STRONG BUY", "BUY", "HOLD")
        ]
        bisa.sort(key=lambda r: r.get("skor_total", r.get("skor", 0)), reverse=True)
        per_budget[b] = bisa[:jumlah]

    max_rows = max((len(v) for v in per_budget.values()), default=0)
    if max_rows == 0:
        await send_fn("⚠️ Tidak ada saham BUY/HOLD di budget-budget ini.",
                       parse_mode=ParseMode.HTML)
        return

    ts = load_results_timestamp()
    ts_line = fmt_timestamp(ts)
    lines = [
        f"📊 <b>PERBANDINGAN MULTI-BUDGET</b>",
        f"Budget: {' · '.join(_budget_label(b) for b in budgets)}",
    ]
    if ts_line:
        lines.append(ts_line)
    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n")

    for b in budgets:
        stocks = per_budget[b]
        lines.append(f"💰 <b>{_budget_label(b)}</b> — {len(stocks)} saham")
        for i, r in enumerate(stocks[:5], 1):
            em = status_emoji(r.get("label", r.get("status", "")))
            ticker = _display_ticker(r.get("ticker", ""))
            skor = r.get("skor_total", r.get("skor", 0))
            lines.append(f"   {i}. {em} <b>{ticker}</b> ({skor})")
        lines.append("")

    lines.append("⚠️ <i>Bukan rekomendasi investasi resmi. DYOR.</i>")

    msg = "\n".join(lines)
    await _send_long(send_fn, context, msg,
                     parse_mode=ParseMode.HTML,
                     reply_markup=cmp_nav_keyboard())


# ═══════════════════════════════════════════════════════════════
#  SCREENING TRIGGER (with rate limit + thread safety)
# ═══════════════════════════════════════════════════════════════

async def _trigger_screen(send_fn, context):
    global _last_screen_time

    # Deploy Render (RAM 512MB): screening lokal DIMATIKAN — 957 saham
    # akan OOM. Screening dikelola GitHub Actions (3x sehari + manual).
    if os.getenv("DISABLE_LOCAL_SCREEN", "").strip() == "1":
        await send_fn(
            "⚙️ Screening berjalan otomatis 3x sehari (08:45, 13:00, 16:15 WIB) "
            "via GitHub Actions.\n\n"
            "Trigger manual: buka repo GitHub → tab Actions → "
            "<b>Screening IDX</b> → Run workflow.\n"
            "Pantau progres: /status",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    s = load_status()
    if s.get("status") == "running":
        if _is_status_stale(s):
            _fix_stale_status()
            log.info("Stale running status cleared — allowing new screening")
        else:
            await send_fn(
                f"⏳ Screening sedang berjalan ({s.get('progress', 0)}%)\n"
                f"{html_mod.escape(s.get('message', ''))}",
                parse_mode=ParseMode.HTML,
                reply_markup=back_keyboard(),
            )
            return

    now = time.time()
    if now - _last_screen_time < _SCREEN_COOLDOWN:
        remaining = int(_SCREEN_COOLDOWN - (now - _last_screen_time))
        await send_fn(
            f"⏳ Rate limit: tunggu {remaining} detik lagi sebelum screening baru.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    _last_screen_time = now
    budget = _get_budget(context)

    await send_fn(
        f"⚙️ <b>Screening dimulai!</b>\n\n"
        f"💰 Budget: {fmt_rp(budget)}\n"
        f"🔍 Menganalisis seluruh saham IDX (universe penuh ±950 emiten)...\n\n"
        f"⏱ Estimasi waktu: 10–60 menit tergantung jumlah saham\n"
        f"Cek progress: /status",
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard(),
    )

    # Capture notification info before starting thread
    chat_id = None
    try:
        chat_id = context.user_data.get("_chat_id")
    except Exception:
        pass
    bot = context.bot
    loop = asyncio.get_running_loop()

    # BUG-008 fix: _cb_logs protected by _log_lock
    cb_logs: list[str] = []

    def _notify(text: str):
        """Send Telegram notification from background thread."""
        if not chat_id:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=main_keyboard(),
                ),
                loop,
            )
            future.result(timeout=10)
        except Exception as exc:
            log.warning("Notifikasi gagal: %s", exc)

    def _run():
        if not _screen_lock.acquire(blocking=False):
            # Tanpa notifikasi ini, user kedua dibilang "dimulai" padahal
            # screening-nya tidak jalan (lock dipegang thread lain).
            _notify("⏳ Screening lain sedang berjalan.\n"
                    "Tunggu selesai, lalu cek /rekomendasi.")
            return
        try:
            from src.data.universe import get_universe
            from src.data.merger import get_all_merged
            from src.analysis.scorer import score_all

            tickers = get_universe()
            _save_status("running", 0, "Memulai fetch data...", [])
            with _log_lock:
                cb_logs.append(f"[{time.strftime('%H:%M:%S')}] Fetching {len(tickers)} tickers...")

            _save_status("running", 5, f"Fetching & merging {len(tickers)} tickers...", cb_logs)

            _last_progress_ticker = [0]

            def _on_fetch_progress(done: int, total: int, ticker: str):
                if done < total and (done - _last_progress_ticker[0]) < 5:
                    return
                _last_progress_ticker[0] = done
                pct = 5 + int((done / total) * 55)
                with _log_lock:
                    cb_logs.append(f"[{time.strftime('%H:%M:%S')}] [{done}/{total}] {ticker}")
                _save_status("running", pct, f"Fetching {done}/{total} — {ticker}", cb_logs)

            all_merged = get_all_merged(tickers, force_refresh=True,
                                        on_progress=_on_fetch_progress)

            with _log_lock:
                cb_logs.append(f"[{time.strftime('%H:%M:%S')}] Fetch selesai: {len(all_merged)} tickers")
            _save_status("running", 60, "Scoring...", cb_logs)

            scored = score_all(all_merged)
            if not scored:
                raise RuntimeError("Scoring returned empty results")

            with _log_lock:
                cb_logs.append(f"[{time.strftime('%H:%M:%S')}] Scoring selesai")

            results_list: list[dict] = []
            for ticker, merged in all_merged.items():
                score_data = scored.get(ticker, {})
                price = merged.get("price")
                entry = {
                    "ticker": ticker,
                    "name": merged.get("name", ""),
                    "sector": merged.get("sector", "Unknown"),
                    "sub_sector": merged.get("sub_sector", ""),
                    "price": price,
                    "harga_lot": int(price * 100) if price else 0,
                    "pe": merged.get("pe"),
                    "pbv": merged.get("pbv"),
                    "roe": merged.get("roe"),
                    "der": merged.get("der"),
                    "net_profit_margin": merged.get("net_profit_margin"),
                    "market_cap": merged.get("market_cap"),
                    "yield_ttm": merged.get("yield_ttm"),
                    "div_streak": merged.get("div_streak"),
                    "div_amount_ttm": merged.get("div_amount_ttm"),
                    "revenue_cagr_3y": merged.get("revenue_cagr_3y"),
                    "earnings_cagr_3y": merged.get("earnings_cagr_3y"),
                }
                entry.update(score_data)
                results_list.append(entry)

            results_list.sort(key=lambda r: r.get("skor_total", 0), reverse=True)

            _save_status("running", 90, "Menyimpan hasil...", cb_logs)
            cache = {
                "generated_at": now_wib().isoformat(),
                "total": len(results_list),
                "data": results_list,
            }
            cache_path = Path(RESULTS_FILE)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(cache_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2, default=_json_default)
            with _file_lock:
                os.replace(tmp, RESULTS_FILE)

            buy_count = sum(1 for r in results_list if r.get("label") in ("STRONG BUY", "BUY"))
            _save_status("done", 100, f"Selesai! {len(results_list)} saham, {buy_count} BUY", cb_logs)

            _notify(
                f"✅ <b>Screening selesai!</b>\n\n"
                f"📊 {len(results_list)} saham dianalisis\n"
                f"🟢 {buy_count} saham BUY/STRONG BUY\n\n"
                f"Lihat hasil: /rekomendasi"
            )
        except Exception as e:
            _save_status("error", 0, str(e)[:150], cb_logs)
            log.error(traceback.format_exc())
            _notify(
                f"❌ <b>Screening gagal</b>\n\n"
                f"<code>{html_mod.escape(str(e)[:100])}</code>\n\n"
                f"Coba lagi: /screen"
            )
        finally:
            _screen_lock.release()

    global _screen_thread
    t = threading.Thread(target=_run, daemon=True)
    _screen_thread = t
    t.start()


def fix_stale_on_startup():
    """Call at bot startup to reset any stuck 'running' status from a previous crash."""
    s = load_status()
    if s.get("status") == "running":
        _save_status("error", 0, "Screening sebelumnya terputus (bot restart)", [])
        log.info("Reset stale 'running' status from previous session")


# ═══════════════════════════════════════════════════════════════
#  ERROR HANDLER
# ═══════════════════════════════════════════════════════════════

async def error_handler(update, context):
    from telegram.error import TimedOut, NetworkError, RetryAfter

    err = context.error
    if isinstance(err, BadRequest):
        err_str = str(err).lower()
        if "message is not modified" in err_str or "message to edit not found" in err_str:
            return
        log.warning("BadRequest: %s", err)
        if "message is too long" in err_str and update and update.effective_message:
            # Seharusnya tak pernah terjadi setelah semua path lewat _send_long —
            # kalau muncul berarti ada jalur yang lolos audit, bukan salah user.
            log.error("Pesan >4096 lolos dari _send_long — audit jalur pengirimnya!")
            try:
                await update.effective_message.reply_text(
                    "⚠️ Pesan terlalu panjang, sebagian isi tidak terkirim. "
                    "Coba ulangi permintaannya."
                )
            except Exception:
                pass
        return
    if isinstance(err, RetryAfter):
        log.warning("Rate limited, retry after %ss", err.retry_after)
        return
    if isinstance(err, (TimedOut, NetworkError)):
        if "message is not modified" in str(err).lower():
            return
        log.info("Network/Timeout: %s", err)
        return
    log.error("Unhandled error: %s", err)
    log.error(traceback.format_exc())
