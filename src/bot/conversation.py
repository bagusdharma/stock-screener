"""Conversational AI layer — Gemini-powered (Google AI Studio) assistant.

Receives user messages, builds context from screening data,
queries Gemini LLM, returns natural language responses.

Security:
- Input sanitized before sending to LLM (injection patterns stripped)
- No sensitive data (API keys, file paths) in LLM context
- Hardcoded scope rejection for out-of-scope topics
- Response converted from markdown to Telegram HTML before sending
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import time
from typing import Any

from src.bot.formatters import fmt_rp, get_div_ttm, get_lot
from src.config.settings import (
    CONV_COOLDOWN_SEC,
    CONV_HISTORY_LENGTH,
    CONV_TIMEOUT_SEC,
    GEMINI_API_KEY,
    GEMINI_MAX_TOKENS,
    GEMINI_MODEL,
    GEMINI_MODEL_CHAIN,
    GEMINI_TEMPERATURE,
    SKOR_BUY,
    SKOR_HOLD,
    SKOR_STRONG_BUY,
)

log = logging.getLogger(__name__)

# ── Gemini client (module-level cache — jangan buat per request) ──

_gemini_client = None

# ── Rantai fallback model ─────────────────────────────────────
# Model yang kena limit/error di-skip sementara (per-model cooldown)
# supaya pesan berikutnya tidak menghantam model mati berulang kali.

_MODEL_CHAIN: list[str] = (
    ([GEMINI_MODEL] if GEMINI_MODEL else [])
    + [m for m in GEMINI_MODEL_CHAIN if m != GEMINI_MODEL]
)
_model_block_until: dict[str, float] = {}
_BLOCK_RATE_LIMIT_SEC = 15 * 60   # 429/403: quota — coba lagi 15 menit
_BLOCK_NOT_FOUND_SEC = 24 * 3600  # 404: model tidak ada — skip 24 jam


def _thinking_cfg(model: str, genai_types):
    """Konfigurasi thinking per keluarga model.

    - gemini-2.x: thinking_budget=0 (tanpa ini jawaban sering kosong karena
      thinking tokens memakan max_output_tokens — kasus terverifikasi).
    - gemini-3.x: pakai thinking_level "low" (3.x menolak thinking_budget).
    Kalau API tetap menolak (400 menyebut thinking), caller retry sekali
    tanpa thinking_config.
    """
    try:
        if model.startswith("gemini-2"):
            return genai_types.ThinkingConfig(thinking_budget=0)
        return genai_types.ThinkingConfig(thinking_level="low")
    except Exception:
        return None


def _get_client():
    """Lazy singleton genai.Client — dipakai ulang antar pesan.

    Client baru per request = 2 httpx pool baru + TLS handshake per pesan
    dan tidak pernah ditutup (leak). Satu client hidup selama proses.
    """
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        from google.genai import types as genai_types

        _gemini_client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=genai_types.HttpOptions(
                timeout=int(CONV_TIMEOUT_SEC * 1000)),  # milidetik
        )
    return _gemini_client

# ── System prompt ────────────────────────────────────────────
# Persona & aturan gaya diambil dari RULE_BOT.md (editable user, dibaca
# ulang otomatis saat file berubah). Aturan data teknis di bawah SELALU
# ditambahkan setelahnya — tidak boleh hilang apa pun isi RULE_BOT.md.

_DEFAULT_PERSONA = """\
Kamu adalah IDXBot, asisten investasi saham IDX Indonesia.

KEPRIBADIAN:
- Santai tapi profesional, seperti teman yang paham saham
- Jawab langsung ke inti, tidak bertele-tele
- Gunakan emoji secukupnya
- Jawaban maksimal 300 kata"""

_rule_bot_cache: dict = {"mtime": None, "text": ""}


def _load_rule_bot() -> str:
    """Baca RULE_BOT.md dari root project (cache per-mtime, tahan error)."""
    import os
    from src.config.settings import BASE_DIR

    path = BASE_DIR / "RULE_BOT.md"
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return ""
    if _rule_bot_cache["mtime"] != mtime:
        try:
            _rule_bot_cache["text"] = path.read_text(encoding="utf-8").strip()
            _rule_bot_cache["mtime"] = mtime
            log.info("RULE_BOT.md dimuat (%d char)", len(_rule_bot_cache["text"]))
        except OSError as exc:
            log.warning("Gagal baca RULE_BOT.md: %s", exc)
    return _rule_bot_cache["text"]


_SYSTEM_PROMPT = """\
{persona}

ATURAN DATA SANGAT PENTING:
- Jawab HANYA berdasarkan data screening di bawah
- DILARANG KERAS mengarang atau mengubah angka skor, ROE, PER, yield
- Salin PERSIS angka dari data — jangan bulatkan, jangan ubah
- Jika data saham ada, WAJIB kutip skor persis dari data
- Jika data saham TIDAK ada, jawab: saham tersebut tidak ada di database screening
- DILARANG menyarankan MEMBELI saham berlabel JUAL atau HOLD —
  rekomendasi beli HANYA untuk label STRONG BUY dan BUY
- Jika user menyebut "saham di atas" / "sebelumnya", itu merujuk daftar
  di riwayat percakapan ini — jawab berdasarkan riwayat, jangan buat daftar baru
- Saat menyebut dividen, sebutkan NOMINAL rupiah per lembar (field DivTTM),
  persen yield hanya sebagai pelengkap dalam kurung

ATURAN LOT:
- Saham dibeli dalam LOT (1 lot = 100 lembar)
- Harga per lot = harga per lembar x 100
- Minimum pembelian = 1 lot

FORMAT:
- Saat menyebut saham: KODE (XX/100) LABEL — lalu alasan dari data
- XX harus PERSIS sama dengan angka skor di data, bukan karangan
- JANGAN pakai format markdown (** __ #), gunakan plain text saja

LABEL:
- STRONG BUY >= {t_sb}, BUY >= {t_buy}, HOLD >= {t_hold}, JUAL < {t_hold}

{data_konteks}"""

# ── Per-user session store ───────────────────────────────────

_sessions: dict[int, dict[str, Any]] = {}
_SESSION_TTL = 1800  # 30 menit

# ── Security: prompt injection patterns ──────────────────────

_INJECTION_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions?|prompts?|rules?)"
    r"|forget\s+(?:everything|all|your)\s+(?:above|previous|prior)"
    r"|you\s+are\s+now\s+"
    r"|act\s+as\s+(?:if\s+you|a\s+)"
    r"|pretend\s+(?:to\s+be|you\s+are)"
    r"|system\s*prompt"
    r"|new\s+instructions?"
    r"|override\s+(?:your|all|the)"
    r"|jailbreak"
    r"|DAN\s+mode"
    r"|<\s*/?\s*system\s*>"
    r")"
)

_MAX_INPUT_LEN = 500

# ── Scope rejection ──────────────────────────────────────────

_REJECT_RULES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"(?i)(?:"
            r"(?:prediksi|predict|forecast|target)\s*(?:harga|price|saham)"
            r"|harga.{0,20}(?:besok|minggu\s*depan|bulan\s*depan|tahun\s*depan)"
            r"|(?:akan|bakal|bisa)\s+(?:naik|turun)\s+(?:berapa|sampai|ke)"
            r"|target\s*price"
            r")"
        ),
        "Maaf, saya tidak bisa memprediksi harga saham. "
        "Saya hanya menganalisis data historis dan kondisi saat ini. "
        "Gunakan /rekomendasi untuk melihat saham terbaik.",
    ),
    (
        re.compile(
            r"(?i)(?:"
            r"(?:berita|news|sentimen|sentiment)\s*(?:pasar|market|saham|terbaru|hari\s*ini)"
            r"|apa\s+yang\s+terjadi\s+(?:di|dengan)\s*(?:pasar|market|ihsg)"
            r")"
        ),
        "Saya tidak punya akses ke berita atau sentimen pasar. "
        "Saya hanya bisa membantu berdasarkan data screening. "
        "Coba /rekomendasi atau /dividen.",
    ),
    (
        re.compile(
            r"(?i)(?:"
            r"(?:NYSE|NASDAQ|S&P|Dow\s*Jones|crypto|bitcoin|ethereum|forex|komoditas|commodity|emas|gold|silver)"
            r"|saham\s+(?:luar|asing|amerika|us|china|jepang)"
            r")"
        ),
        "Saya hanya bisa membantu untuk saham IDX (Bursa Efek Indonesia). "
        "Untuk saham luar negeri, crypto, atau komoditas, silakan gunakan platform lain.",
    ),
]


# ═══════════════════════════════════════════════════════════════
#  Input processing
# ═══════════════════════════════════════════════════════════════

def _sanitize_input(text: str) -> str:
    text = text[:_MAX_INPUT_LEN].strip()
    text = _INJECTION_RE.sub("", text).strip()
    return text


def _check_rejection(text: str) -> str | None:
    for pattern, message in _REJECT_RULES:
        if pattern.search(text):
            return message
    return None


# ═══════════════════════════════════════════════════════════════
#  Markdown → Telegram HTML converter
# ═══════════════════════════════════════════════════════════════

def _md_to_telegram_html(text: str) -> str:
    """Convert LLM markdown output to Telegram-safe HTML.

    Handles: **bold**, *italic*, `code`, and escapes HTML entities.
    """
    text = html_mod.escape(text)

    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'<i>\1</i>', text)

    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)

    text = re.sub(r'^#{1,3}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    text = re.sub(r'^[-*]\s+', '- ', text, flags=re.MULTILINE)

    return text


# ═══════════════════════════════════════════════════════════════
#  Data context builder
# ═══════════════════════════════════════════════════════════════

_TICKER_ALIASES = {
    "BCA": "BBCA", "BRI": "BBRI", "BNI": "BBNI", "BTN": "BBTN",
    "MANDIRI": "BMRI", "DANAMON": "BDMN", "TELKOM": "TLKM",
    "UNILEVER": "UNVR", "INDOFOOD": "INDF", "ASTRA": "ASII",
    "GUDANG GARAM": "GGRM", "HM SAMPOERNA": "HMSP",
}


def _extract_tickers(text: str, results: list[dict]) -> list[dict]:
    """Find stocks mentioned in user message and return their data.

    Handles common aliases (BCA→BBCA, BRI→BBRI, etc).
    """
    upper = text.upper()

    resolved: set[str] = set()
    for alias, code in _TICKER_ALIASES.items():
        if alias in upper:
            resolved.add(code)

    for r in results:
        code = r.get("ticker", "").replace(".JK", "").upper()
        if code and len(code) >= 3 and code in upper:
            resolved.add(code)

    found = []
    seen = set()
    for r in results:
        code = r.get("ticker", "").replace(".JK", "").upper()
        if code in resolved and code not in seen:
            found.append(r)
            seen.add(code)
    return found


def _format_stock_detail(r: dict) -> str:
    """Format a single stock for direct display (no LLM involved)."""
    ticker = r.get("ticker", "?").replace(".JK", "")
    skor = r.get("skor_total", r.get("skor", 0)) or 0
    label = r.get("label", r.get("status", ""))
    price = r.get("price", r.get("harga", 0)) or 0
    lot = get_lot(r)

    roe = r.get("roe", r.get("roe_pct"))
    dy = r.get("yield_ttm", r.get("div_yield", 0)) or 0
    streak = r.get("div_streak", r.get("div_tahun", 0)) or 0
    pe = r.get("pe", r.get("per"))
    sector = r.get("sector", "")
    npm = r.get("net_profit_margin")

    lines = [f"<b>{ticker}</b> ({skor}/100) {label}"]
    lines.append(f"Sektor: {sector}" if sector else "")
    lines.append(f"Harga: Rp {int(price):,}/lbr — Rp {lot:,}/lot".replace(",", "."))

    metrics = []
    v = _fmt(roe)
    if v:
        metrics.append(f"ROE: {v}%")
    v = _fmt(pe)
    if v:
        metrics.append(f"PER: {v}x")
    v = _fmt(npm)
    if v:
        metrics.append(f"Margin: {v}%")
    div_ttm = get_div_ttm(r)
    if div_ttm > 0:
        metrics.append(f"Div TTM: {fmt_rp(int(div_ttm))}/lbr ({dy:.1f}%)")
    elif dy > 0:
        metrics.append(f"Yield: {dy:.1f}%")
    if streak > 0:
        metrics.append(f"Dividen: {streak} tahun berturut")
    if metrics:
        lines.append(" | ".join(metrics))

    alasan = r.get("alasan", [])
    if alasan:
        lines.append("Alasan:")
        for a in alasan[:5]:
            lines.append(f"  - {html_mod.escape(str(a))}")

    return "\n".join(l for l in lines if l)


def _extract_budgets(text: str) -> list[int]:
    """Extract SEMUA nominal budget dari teks natural, urut naik & unik.

    Handles: 100rb, 500ribu, 1jt, 1juta, 100000, 500.000, "100 ribu dan
    200 ribu", "100rb vs 1jt". Dipakai untuk single budget maupun
    perbandingan multi-budget.
    """
    low = text.lower().replace(".", "").replace(",", " ")
    amounts: list[int] = []

    for m in re.finditer(r'(\d+)\s*(?:jt|juta)', low):
        amounts.append(int(m.group(1)) * 1_000_000)
    for m in re.finditer(r'(\d+)\s*(?:rb|ribu)', low):
        amounts.append(int(m.group(1)) * 1_000)
    for m in re.finditer(r'\b(\d{5,})\b', low):
        val = int(m.group(1))
        if 50_000 <= val <= 100_000_000:
            amounts.append(val)

    return sorted(set(amounts))


def _extract_budget(text: str) -> int | None:
    """Nominal budget terbesar di teks (None kalau tidak ada)."""
    amounts = _extract_budgets(text)
    return amounts[-1] if amounts else None


def _compare_budgets_answer(budgets: list[int], results: list[dict]) -> str:
    """Perbandingan langsung dari data untuk 2-4 budget (tanpa LLM)."""
    budgets = sorted(set(budgets))[:4]
    parts = ["📊 <b>Perbandingan Budget</b> (1 lot = 100 lembar)\n"]
    prev_tickers: set[str] = set()
    prev_b: int | None = None

    for b in budgets:
        ok = [r for r in results
              if 0 < get_lot(r) <= b
              and r.get("label", r.get("status")) in ("STRONG BUY", "BUY", "HOLD")]
        ok.sort(key=lambda x: x.get("skor_total", x.get("skor", 0)), reverse=True)

        parts.append(f"💰 <b>{fmt_rp(b)}</b> — {len(ok)} saham layak (BUY/HOLD):")
        if not ok:
            parts.append("   <i>tidak ada yang terjangkau</i>")
        for r in ok[:5]:
            t = r.get("ticker", "?").replace(".JK", "")
            skor = r.get("skor_total", r.get("skor", 0))
            parts.append(f"   • <b>{t}</b> ({skor}) — {fmt_rp(get_lot(r))}/lot")

        if prev_b is not None:
            baru = [r.get("ticker", "?").replace(".JK", "") for r in ok
                    if r.get("ticker") not in prev_tickers]
            if baru:
                parts.append(
                    f"   ➕ Baru terjangkau dibanding {fmt_rp(prev_b)}: "
                    + ", ".join(baru[:8]))
            else:
                parts.append(
                    f"   = Tidak ada saham baru dibanding {fmt_rp(prev_b)}")

        prev_tickers = {r.get("ticker") for r in ok}
        prev_b = b
        parts.append("")

    parts.append("Gunakan /bandingkan untuk tampilan lengkap dengan tombol.")
    return "\n".join(parts)


def _format_budget_stock(r: dict, budget: int) -> str:
    """Format a stock for budget display with lot calculation."""
    ticker = r.get("ticker", "?").replace(".JK", "")
    skor = r.get("skor_total", r.get("skor", 0)) or 0
    label = r.get("label", r.get("status", ""))
    lot_price = get_lot(r)

    max_lot = int(budget // lot_price) if lot_price > 0 else 0
    total_cost = max_lot * lot_price

    dy = r.get("yield_ttm", r.get("div_yield", 0)) or 0
    dy_str = f" | Yield: {dy:.1f}%" if dy > 0 else ""

    return (
        f"<b>{ticker}</b> ({skor}/100) {label}\n"
        f"  1 lot = Rp {lot_price:,} — bisa beli <b>{max_lot} lot</b>"
        f" (Rp {total_cost:,}){dy_str}"
    ).replace(",", ".")


def _try_direct_answer(message: str, results: list[dict]) -> str | None:
    """Answer stock/budget queries directly from data — no LLM.

    Handles:
    - Specific ticker queries: "skor BCA berapa", "gimana TRIM"
    - Budget queries: "uang 500rb bisa beli apa", "modal 1jt"
    - Comparison: "BRI vs BCA"

    Returns None for general questions → those go to Gemini.
    """
    if not results:
        return None

    low = message.lower()

    # ── Top-N rekomendasi (deterministik — LLM sering salah hitung) ──
    # "20 saham rekomendasi" dijawab dari data langsung supaya jumlahnya
    # PASTI tepat (LLM pernah menampilkan 19 dari 20 diminta).
    if any(k in low for k in ("rekomendasi", "terbaik", "top", "unggulan")):
        m = re.search(r'\b(\d{1,2})\b(?!\s*(?:rb|jt|ribu|juta|%|tahun|thn|hari|lot))', low)
        n = int(m.group(1)) if m else 0
        if 1 <= n <= 30:
            ranked = sorted(
                results,
                key=lambda x: x.get("skor_total", x.get("skor", 0)),
                reverse=True,
            )[:n]
            parts = [f"🏆 <b>Top {len(ranked)} saham skor tertinggi:</b>\n"]
            for i, r in enumerate(ranked, 1):
                t = r.get("ticker", "?").replace(".JK", "")
                skor = r.get("skor_total", r.get("skor", 0))
                lbl = r.get("label", r.get("status", ""))
                dy = r.get("yield_ttm", r.get("div_yield", 0)) or 0
                dy_s = f" · Yield {dy:.1f}%" if dy > 0 else ""
                parts.append(
                    f"{i}. <b>{t}</b> ({skor}/100) {lbl} — "
                    f"{fmt_rp(get_lot(r))}/lot{dy_s}")
            if len(ranked) < n:
                parts.append(f"\n(hanya {len(ranked)} saham tersedia di data)")
            parts.append("\nDetail: tap tombol di /rekomendasi")
            return "\n".join(parts)

    # ── Budget query ─────────────────────────────────────────
    has_amount_suffix = bool(re.search(r'\d\s*(?:rb|jt)\b', low))
    is_budget_query = has_amount_suffix or any(k in low for k in (
        "uang", "modal", "budget", "dana", "bisa beli",
        "terjangkau", "murah", "ribu", "juta", "duit",
    ))
    budgets = _extract_budgets(message)
    budget = budgets[-1] if budgets else None

    # ── Perbandingan multi-budget natural language ───────────
    # "apa bedanya kalau aku punya 100 ribu dan 200 ribu" → jawab dari data
    if len(budgets) >= 2 and (is_budget_query or any(
            k in low for k in ("beda", "banding", "vs", "dibanding"))):
        return _compare_budgets_answer(budgets, results)

    if is_budget_query and budget:
        buyable = []
        for r in results:
            lot_price = get_lot(r)
            lbl = r.get("label", r.get("status", ""))
            if lot_price > 0 and lot_price <= budget and lbl in ("STRONG BUY", "BUY"):
                buyable.append(r)

        buyable.sort(key=lambda x: x.get("skor_total", x.get("skor", 0)), reverse=True)

        budget_str = f"Rp {budget:,}".replace(",", ".")
        if not buyable:
            hold = []
            for r in results:
                lot_price = get_lot(r)
                if lot_price > 0 and lot_price <= budget:
                    hold.append(r)
            hold.sort(key=lambda x: x.get("skor_total", x.get("skor", 0)), reverse=True)

            if not hold:
                return (
                    f"Dengan budget {budget_str}, belum ada saham yang bisa dibeli.\n"
                    f"Harga 1 lot termurah saat ini sekitar Rp 50.000-100.000.\n"
                    f"Coba naikkan budget atau gunakan /bandingkan."
                )
            parts = [f"Dengan budget {budget_str}, belum ada saham BUY.\n"
                     f"Berikut saham HOLD yang terjangkau:\n"]
            for r in hold[:5]:
                parts.append(_format_budget_stock(r, budget))
            return "\n\n".join(parts)

        parts = [
            f"Dengan budget {budget_str}, saham BUY yang bisa dibeli:\n"
            f"(1 lot = 100 lembar, minimum beli 1 lot)\n"
        ]
        for r in buyable[:8]:
            parts.append(_format_budget_stock(r, budget))

        if len(buyable) > 8:
            parts.append(f"\n(+{len(buyable) - 8} saham BUY lain yang terjangkau)")

        parts.append(f"\nGunakan /bandingkan untuk perbandingan lebih lengkap.")
        return "\n\n".join(parts)

    # ── Specific ticker query ────────────────────────────────
    found = _extract_tickers(message, results)
    if not found:
        return None

    is_score_query = any(k in low for k in (
        "skor", "score", "berapa", "gimana", "bagaimana",
        "info", "detail", "analisis", "analisa", "review",
        "layak", "beli", "jual", "hold",
    ))
    is_compare = any(k in low for k in ("banding", "compar", "vs", "atau", "mana yang"))

    if not is_score_query and not is_compare:
        return None

    if len(found) == 1:
        r = found[0]
        skor = r.get("skor_total", r.get("skor", 0)) or 0
        ticker = r.get("ticker", "?").replace(".JK", "")
        detail = _format_stock_detail(r)
        if skor >= SKOR_BUY:
            verdict = f"{ticker} termasuk layak dipertimbangkan."
        elif skor >= SKOR_HOLD:
            verdict = f"{ticker} masih di zona tahan — belum cukup kuat untuk beli."
        else:
            verdict = f"{ticker} skornya rendah — perlu hati-hati."
        return f"{detail}\n\n{verdict}"

    if len(found) >= 2:
        parts = []
        for r in sorted(found, key=lambda x: x.get("skor_total", x.get("skor", 0)), reverse=True):
            parts.append(_format_stock_detail(r))
        best = sorted(found, key=lambda x: x.get("skor_total", x.get("skor", 0)), reverse=True)[0]
        best_ticker = best.get("ticker", "?").replace(".JK", "")
        best_skor = best.get("skor_total", best.get("skor", 0)) or 0
        conclusion = f"\nDari yang ditanyakan, {best_ticker} ({best_skor}/100) punya skor tertinggi."
        return "\n\n".join(parts) + "\n" + conclusion

    return None


def _fmt(val, fmt: str = ".1f") -> str:
    if val is None:
        return ""
    try:
        f = float(val)
        if f != f:
            return ""
        return format(f, fmt)
    except (TypeError, ValueError):
        return ""


def _stock_line(r: dict) -> str:
    """Build a compact one-line summary for a stock."""
    ticker = r.get("ticker", "?").replace(".JK", "")
    skor = r.get("skor_total", r.get("skor", 0)) or 0
    label = r.get("label", r.get("status", ""))
    price = r.get("price", r.get("harga", 0)) or 0
    lot = get_lot(r)

    segs = [f"{ticker}", f"skor:{skor}", f"{label}",
            f"harga:{int(price)}/lbr", f"lot:{lot}"]

    roe = r.get("roe", r.get("roe_pct"))
    dy = r.get("yield_ttm", r.get("div_yield", 0)) or 0
    streak = r.get("div_streak", r.get("div_tahun", 0)) or 0
    pe = r.get("pe", r.get("per"))
    sector = r.get("sector", "")

    v = _fmt(roe)
    if v:
        segs.append(f"ROE:{v}%")
    v = _fmt(pe)
    if v:
        segs.append(f"PER:{v}x")
    if dy > 0:
        segs.append(f"Yield:{dy:.1f}%")
        dttm = get_div_ttm(r)
        if dttm > 0:
            segs.append(f"DivTTM:Rp{int(dttm)}/lbr")
    if streak > 0:
        segs.append(f"Div:{streak}thn")
    if sector:
        segs.append(f"sektor:{sector}")

    return "|".join(segs)


def _build_data_context(results: list[dict]) -> str:
    """Build LLM context berisi SEMUA saham hasil screening (sama dengan isi
    results_cache.json), urut skor tertinggi.

    ~170 saham × ~1 baris kompak ≈ 5-6rb token — jauh di bawah limit konteks
    Gemini, jadi tidak perlu dipotong seperti era Groq (dulu hanya top 40).
    """
    if not results:
        return "Belum ada data screening. User perlu jalankan /screen dulu."

    total = len(results)
    by_label: dict[str, int] = {}
    for r in results:
        lb = r.get("label", r.get("status", "UNKNOWN"))
        by_label[lb] = by_label.get(lb, 0) + 1

    parts: list[str] = [f"Data screening {total} saham IDX:"]
    for lb in ("STRONG BUY", "BUY", "HOLD", "JUAL"):
        if lb in by_label:
            parts.append(f"  {lb}: {by_label[lb]} saham")
    parts.append("")

    sorted_all = sorted(
        results,
        key=lambda r: r.get("skor_total", r.get("skor", 0)),
        reverse=True,
    )

    parts.append(f"Semua {total} saham (urut skor tertinggi):")
    for rank, r in enumerate(sorted_all, 1):
        try:
            if rank <= 100:
                line = _stock_line(r)
                if rank <= 40:
                    alasan = r.get("alasan", [])
                    if alasan:
                        line += f"|alasan:{';'.join(str(a) for a in alasan[:2])}"
            else:
                # Baris ultra-kompak utk sisa universe — hemat token besar
                # (universe 957 saham; token input = latensi respons)
                t = r.get("ticker", "?").replace(".JK", "")
                line = (f"{t}|{r.get('skor_total', r.get('skor', 0))}"
                        f"|{r.get('label', r.get('status', ''))}"
                        f"|lot:{get_lot(r)}")
            parts.append(line)
        except Exception:
            code = r.get("ticker", "?").replace(".JK", "")
            parts.append(f"{code}|data error")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
#  Session management
# ═══════════════════════════════════════════════════════════════

def _get_session(user_id: int) -> dict:
    now = time.time()
    # Prune session user lain yang sudah kedaluwarsa — tanpa ini _sessions
    # tumbuh tak terbatas selama proses hidup (memory leak pelan).
    expired = [uid for uid, s in _sessions.items()
               if now - s.get("last_active", 0) > _SESSION_TTL]
    for uid in expired:
        del _sessions[uid]
    session = _sessions.get(user_id)
    if session is None or (now - session.get("last_active", 0)) > _SESSION_TTL:
        session = {"history": [], "last_active": now, "last_request": 0.0}
        _sessions[user_id] = session
    else:
        session["last_active"] = now
    return session


def _check_cooldown(session: dict) -> int | None:
    now = time.time()
    elapsed = now - session.get("last_request", 0.0)
    if elapsed < CONV_COOLDOWN_SEC:
        return max(1, int(CONV_COOLDOWN_SEC - elapsed))
    return None


# ═══════════════════════════════════════════════════════════════
#  Main entry point
# ═══════════════════════════════════════════════════════════════

async def get_response(
    user_id: int,
    message: str,
    results: list[dict],
) -> str:
    """Process user message via Gemini and return Telegram HTML response.

    Handles all errors internally — never raises.
    """
    clean = _sanitize_input(message)
    if not clean:
        return "Maaf, pesan tidak valid. Coba ketik pertanyaan tentang saham IDX."

    rejection = _check_rejection(clean)
    if rejection:
        return rejection

    session = _get_session(user_id)

    direct = _try_direct_answer(clean, results)
    if direct:
        # WAJIB masuk history — tanpa ini follow-up ("kenapa aku harus beli
        # saham di atas?") tidak punya konteks dan LLM menjawab ngawur.
        plain = re.sub(r"<[^>]+>", "", direct)
        session["history"].append({"role": "user", "content": clean})
        session["history"].append({"role": "assistant", "content": plain[:3000]})
        if len(session["history"]) > CONV_HISTORY_LENGTH * 2:
            session["history"] = session["history"][-(CONV_HISTORY_LENGTH * 2):]
        return direct

    remaining = _check_cooldown(session)
    if remaining is not None:
        return f"Tunggu sebentar ya ({remaining} detik)"

    try:
        data_ctx = _build_data_context(results)
    except Exception:
        log.warning("Failed to build data context, using summary")
        data_ctx = f"Total: {len(results)} saham dianalisis. Detail tidak tersedia."
    persona = _load_rule_bot() or _DEFAULT_PERSONA
    system_msg = _SYSTEM_PROMPT.format(
        persona=persona,
        data_konteks=data_ctx,
        t_sb=SKOR_STRONG_BUY, t_buy=SKOR_BUY, t_hold=SKOR_HOLD,
    )

    if not GEMINI_API_KEY:
        return _fallback_response(clean, results)

    try:
        import httpx
        from google.genai import errors as genai_errors
        from google.genai import types as genai_types

        # History: format internal "assistant" → role Gemini "model"
        contents: list[genai_types.Content] = []
        for h in session["history"][-(CONV_HISTORY_LENGTH * 2):]:
            role = "model" if h["role"] == "assistant" else "user"
            contents.append(genai_types.Content(
                role=role, parts=[genai_types.Part(text=h["content"])]))
        contents.append(genai_types.Content(
            role="user", parts=[genai_types.Part(text=clean)]))

        client = _get_client()

        completion = None
        model_used: str | None = None
        last_err: Exception | None = None
        now_t = time.time()

        for model in _MODEL_CHAIN:
            if _model_block_until.get(model, 0) > now_t:
                continue  # model ini sedang cooldown (limit/404)

            for cfg_attempt in (0, 1):  # attempt 1 = tanpa thinking_config
                cfg_kwargs: dict = {
                    "system_instruction": system_msg,
                    "max_output_tokens": GEMINI_MAX_TOKENS,
                    "temperature": GEMINI_TEMPERATURE,
                }
                if cfg_attempt == 0:
                    tc = _thinking_cfg(model, genai_types)
                    if tc is not None:
                        cfg_kwargs["thinking_config"] = tc
                gen_config = genai_types.GenerateContentConfig(**cfg_kwargs)

                try:
                    completion = await client.aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=gen_config,
                    )
                    model_used = model
                    break
                except genai_errors.APIError as e:
                    last_err = e
                    code = getattr(e, "code", 0) or 0
                    msg_low = str(e).lower()
                    if code == 400 and "thinking" in msg_low and cfg_attempt == 0:
                        continue  # config thinking ditolak → coba tanpa
                    if code in (429, 403):
                        _model_block_until[model] = time.time() + _BLOCK_RATE_LIMIT_SEC
                        log.warning("Gemini %s kena limit (%d) — fallback ke "
                                    "model berikutnya", model, code)
                    elif code == 404:
                        _model_block_until[model] = time.time() + _BLOCK_NOT_FOUND_SEC
                        log.warning("Gemini %s tidak ditemukan — fallback", model)
                    elif code in (500, 503):
                        log.warning("Gemini %s server error %d — fallback", model, code)
                    else:
                        raise
                    break  # → model berikutnya di rantai
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_err = e
                    log.warning("Gemini %s timeout/transport — fallback: %s", model, e)
                    break

            if completion is not None:
                break

        if completion is None:
            raise last_err if last_err else RuntimeError("Semua model Gemini gagal")
        if model_used and model_used != _MODEL_CHAIN[0]:
            log.info("Jawaban dari model fallback: %s", model_used)

        try:
            reply = (completion.text or "").strip()
        except Exception:
            reply = ""
        if not reply:
            reply = "Maaf, saya tidak bisa menjawab pertanyaan itu saat ini."

        session["last_request"] = time.time()
        session["history"].append({"role": "user", "content": clean})
        session["history"].append({"role": "assistant", "content": reply})
        if len(session["history"]) > CONV_HISTORY_LENGTH * 2:
            session["history"] = session["history"][-(CONV_HISTORY_LENGTH * 2):]

        return _md_to_telegram_html(reply)

    except ImportError:
        log.error("google-genai package not installed — run: pip install google-genai")
        return (
            "Package <code>google-genai</code> belum terinstall.\n"
            "Jalankan: <code>pip install google-genai</code>"
        )
    except Exception as e:
        log.error("Gemini error (%s): %s", type(e).__name__, e)
        session["last_request"] = time.time()
        return _fallback_response(clean, results)


# ═══════════════════════════════════════════════════════════════
#  Fallback (Gemini down / error)
# ═══════════════════════════════════════════════════════════════

def _fallback_response(message: str, results: list[dict]) -> str:
    low = message.lower()

    if not results:
        return (
            "Layanan AI sedang tidak tersedia.\n"
            "Belum ada data screening — jalankan /screen dulu."
        )

    if any(k in low for k in ("rekomendasi", "beli", "buy", "terbaik", "bagus")):
        return (
            "Layanan AI sedang tidak tersedia.\n"
            "Gunakan /rekomendasi untuk melihat saham terbaik."
        )

    if any(k in low for k in ("dividen", "dividend", "yield")):
        return (
            "Layanan AI sedang tidak tersedia.\n"
            "Gunakan /dividen untuk melihat saham dividen terbaik."
        )

    if any(k in low for k in ("budget", "modal", "uang", "bisa beli")):
        return (
            "Layanan AI sedang tidak tersedia.\n"
            "Gunakan /bandingkan untuk membandingkan budget."
        )

    return (
        "Layanan AI sedang tidak tersedia.\n"
        "Gunakan /help untuk melihat command yang tersedia."
    )
