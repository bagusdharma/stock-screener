import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ── Telegram ───────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

if not BOT_TOKEN:
    raise EnvironmentError(
        "BOT_TOKEN belum di-set. "
        "Buat file .env di root project dengan isi: BOT_TOKEN=token_dari_botfather"
    )

# ── Budget & Preferensi Default ────────────────────────────────
DEFAULT_BUDGET = 500_000
DEFAULT_JUMLAH_REKOMENDASI = 5

# ── Scoring Thresholds ─────────────────────────────────────────
# Skala kalibrasi 2026-07-04 (gaya IBD Composite Rating — lihat scorer._calibrate):
# STRONG BUY >90 = ~top 3% universe; BUY = skor 85-90an; HOLD >= 70.
SKOR_STRONG_BUY = 90
SKOR_BUY = 85
SKOR_HOLD = 70

# ── Data Source Config ─────────────────────────────────────────
IDX_XLSX_URL = "https://www.idx.co.id/primary/ListedCompany/GetFinancialReport"
IDX_API_BASE = "https://idx.co.id"
YFINANCE_SUFFIX = ".JK"
CONFLICT_THRESHOLD = 0.05

# ── Cache Settings ─────────────────────────────────────────────
CACHE_DIR = str(BASE_DIR / "cache")
TICKER_CACHE_DAYS = 30
IDX_XLSX_CACHE_DAYS = 30

# ── Logging Config ─────────────────────────────────────────────
LOG_FILE = str(BASE_DIR / "logs" / "bot.log")
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# ── Universe mode ─────────────────────────────────────────────
# "full"    = semua emiten saham IDX (~950) dari IDX API, cache 7 hari.
#             Screening penuh ±45-60 menit.
# "curated" = daftar statis 176 ticker di bawah (±9 menit).
UNIVERSE_MODE = os.getenv("UNIVERSE_MODE", "full").strip().lower()

# ── Universe Saham IDX statis (176 ticker — fallback & mode curated) ──
TICKERS: list[str] = [
    # Perbankan
    "BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "BRIS.JK", "BBTN.JK",
    "BNGA.JK", "BDMN.JK", "BJBR.JK", "BJTM.JK", "MEGA.JK", "PNBN.JK",
    "NISP.JK", "BTPS.JK", "ARTO.JK", "AGRO.JK", "BNLI.JK", "BSIM.JK",
    # Keuangan Non-Bank
    "BFIN.JK", "ADMF.JK", "MFIN.JK", "WOMF.JK", "TRIM.JK",
    # Otomotif & Komponen
    "ASII.JK", "AUTO.JK", "MPMX.JK", "SMSM.JK", "IMAS.JK",
    "DRMA.JK", "GJTL.JK", "INDS.JK", "BOLT.JK", "MASA.JK",
    # Konsumer
    "UNVR.JK", "ICBP.JK", "INDF.JK", "KLBF.JK", "HMSP.JK", "GGRM.JK",
    "MAPI.JK", "AMRT.JK", "ACES.JK", "ULTJ.JK", "SIDO.JK", "DLTA.JK",
    "MLBI.JK", "ROTI.JK", "SKLT.JK", "STTP.JK", "CLEO.JK", "GOOD.JK",
    "LPPF.JK", "RALS.JK", "MAPA.JK", "ERAA.JK", "HRTA.JK", "WIIM.JK",
    "MIDI.JK", "HERO.JK", "HOKI.JK", "TBLA.JK",
    # Telekomunikasi
    "TLKM.JK", "ISAT.JK", "EXCL.JK", "TOWR.JK", "TBIG.JK", "LINK.JK", "MTEL.JK",
    # Teknologi
    "GOTO.JK", "BUKA.JK", "DCII.JK", "MTDL.JK", "EMTK.JK", "MLPT.JK", "ATIC.JK",
    # Energi
    "ADRO.JK", "PTBA.JK", "ITMG.JK", "HRUM.JK", "PGAS.JK", "MEDC.JK",
    "INDY.JK", "ESSA.JK", "PGEO.JK", "RAJA.JK", "DOID.JK", "GEMS.JK",
    "KKGI.JK", "MYOH.JK", "BYAN.JK", "ELSA.JK", "AKRA.JK", "RUIS.JK",
    "DEWA.JK", "BUMI.JK", "FIRE.JK", "ENRG.JK",
    # Material
    "ANTM.JK", "INCO.JK", "MDKA.JK", "NCKL.JK", "MBMA.JK", "BRPT.JK",
    "TPIA.JK", "INKP.JK", "TKIM.JK", "INTP.JK", "SMGR.JK", "ISSP.JK",
    "SMCB.JK", "WTON.JK", "EKAD.JK", "INCI.JK", "KRAS.JK", "NIKL.JK",
    # Infrastruktur
    "JSMR.JK", "WIKA.JK", "PTPP.JK", "WSKT.JK", "ADHI.JK", "WSBP.JK",
    # Properti
    "CTRA.JK", "PWON.JK", "SMRA.JK", "DMAS.JK", "BSDE.JK", "LPKR.JK",
    "APLN.JK", "MKPI.JK", "DART.JK", "MTLA.JK", "PPRO.JK", "KIJA.JK",
    "SSIA.JK", "PLIN.JK", "MMLP.JK",
    # Kesehatan
    "MIKA.JK", "HEAL.JK", "SILO.JK", "TSPC.JK", "DVLA.JK", "KAEF.JK",
    "SOHO.JK", "PYFA.JK", "MERK.JK", "PRDA.JK", "INAF.JK",
    # Agribisnis
    "AALI.JK", "SSMS.JK", "DSNG.JK", "TAPG.JK", "LSIP.JK", "SGRO.JK",
    "PALM.JK", "BWPT.JK", "JPFA.JK", "CPIN.JK", "MAIN.JK",
    # Transportasi
    "BIRD.JK", "GIAA.JK", "SMDR.JK", "ASSA.JK", "TMAS.JK", "NELY.JK", "IPCC.JK",
    # Media
    "SCMA.JK", "MNCN.JK", "FILM.JK",
    # Konglomerat & Multi-Sektor
    "UNTR.JK", "HEXA.JK", "AADI.JK", "ADMR.JK", "AMMN.JK", "CUAN.JK", "WIFI.JK",
    # Energi (lanjutan dari Keuangan lama — reklasifikasi)
    "ABMM.JK",
]

_seen: set[str] = set()
_dedup: list[str] = []
for _t in TICKERS:
    if _t not in _seen:
        _seen.add(_t)
        _dedup.append(_t)
TICKERS = _dedup

# ── Sektor IDX-IC (11 sektor resmi) ───────────────────────────
_SEKTOR_GROUPS: dict[str, list[str]] = {
    "Financials": [
        "BBCA", "BBRI", "BMRI", "BBNI", "BRIS", "BBTN",
        "BNGA", "BDMN", "BJBR", "BJTM", "MEGA", "PNBN",
        "NISP", "BTPS", "ARTO", "AGRO", "BNLI", "BSIM",
        "BFIN", "ADMF", "MFIN", "WOMF", "TRIM",
    ],
    "Energy": [
        "ADRO", "PTBA", "ITMG", "HRUM", "PGAS", "MEDC",
        "INDY", "ESSA", "PGEO", "RAJA", "DOID", "GEMS",
        "KKGI", "MYOH", "BYAN", "ELSA", "AKRA", "RUIS",
        "DEWA", "BUMI", "FIRE", "ENRG", "ABMM",
        "AADI", "ADMR",
    ],
    "Basic Materials": [
        "ANTM", "INCO", "MDKA", "NCKL", "MBMA", "BRPT",
        "TPIA", "INKP", "TKIM", "INTP", "SMGR", "ISSP",
        "SMCB", "WTON", "EKAD", "INCI", "KRAS", "NIKL",
        "AMMN",
    ],
    "Industrials": [
        "ASII", "AUTO", "DRMA", "INDS", "BOLT", "MASA",
        "SMSM", "UNTR", "HEXA",
    ],
    "Consumer Non-Cyclical": [
        "UNVR", "ICBP", "INDF", "KLBF", "HMSP", "GGRM",
        "ULTJ", "SIDO", "DLTA", "MLBI", "ROTI", "SKLT",
        "STTP", "CLEO", "GOOD", "HOKI", "TBLA",
        "AALI", "SSMS", "DSNG", "TAPG", "LSIP", "SGRO",
        "PALM", "BWPT", "JPFA", "CPIN", "MAIN",
    ],
    "Consumer Cyclical": [
        "MAPI", "AMRT", "ACES", "LPPF", "RALS", "MAPA",
        "ERAA", "HRTA", "WIIM", "MIDI", "HERO",
        "IMAS", "MPMX", "GJTL", "CUAN",
    ],
    "Healthcare": [
        "MIKA", "HEAL", "SILO", "TSPC", "DVLA", "KAEF",
        "SOHO", "PYFA", "MERK", "PRDA", "INAF",
    ],
    "Property & Real Estate": [
        "CTRA", "PWON", "SMRA", "DMAS", "BSDE", "LPKR",
        "APLN", "MKPI", "DART", "MTLA", "PPRO", "KIJA",
        "SSIA", "PLIN", "MMLP",
    ],
    "Technology": [
        "GOTO", "BUKA", "DCII", "MTDL", "EMTK", "MLPT", "ATIC",
        "SCMA", "MNCN", "FILM", "WIFI",
    ],
    "Infrastructure": [
        "TLKM", "ISAT", "EXCL", "TOWR", "TBIG", "LINK", "MTEL",
        "JSMR", "WIKA", "PTPP", "WSKT", "ADHI", "WSBP",
    ],
    "Transportation & Logistic": [
        "BIRD", "GIAA", "SMDR", "ASSA", "TMAS", "NELY", "IPCC",
    ],
}

SEKTOR: dict[str, str] = {}
for _sector, _codes in _SEKTOR_GROUPS.items():
    for _code in _codes:
        SEKTOR[_code] = _sector

# ── Gemini AI Config (Google AI Studio) ───────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# ── Rantai fallback model (2026-07-04, permintaan user) ──────
# Model teratas dipakai dulu; kena limit (429/403) / hilang (404) /
# server error → otomatis turun ke model berikutnya.
# Semua ID diverifikasi live tersedia di akun API user.
GEMINI_MODEL_CHAIN = [
    "gemini-3.5-flash",
    "gemini-3-flash-preview",   # "Gemini 3 Flash" — ID resmi di API
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]
# Env GEMINI_MODEL (opsional) = override, disisipkan paling atas rantai.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip()
GEMINI_MAX_TOKENS = 2048  # balasan AI displit via _send_long, aman untuk jawaban panjang
GEMINI_TEMPERATURE = 0.3
CONV_HISTORY_LENGTH = 5
CONV_COOLDOWN_SEC = 2
CONV_TIMEOUT_SEC = 10

# ── Scheduler Config ──────────────────────────────────────────
# Mode jadwal (keputusan user 2026-07-04, hemat resource free-tier VM):
#   "weekly" (default) = 1x seminggu, Sabtu pagi — pakai harga tutup Jumat.
#     Fundamental/dividen/growth (80% bobot skor) memang hanya berubah
#     kuartalan; /screen manual tetap tersedia utk data segar.
#   "daily" = mode lama 3x sehari Sen-Jum (pagi/siang/sore).
SCREEN_SCHEDULE = os.getenv("SCREEN_SCHEDULE", "weekly").strip().lower()
SCHEDULE_WEEKLY_TIME = "08:00"  # Sabtu, WIB
SCHEDULE_PAGI = "08:45"
SCHEDULE_SIANG = "13:00"
SCHEDULE_SORE = "16:15"
TIMEZONE = "Asia/Jakarta"

IDX_IC_SECTORS: list[str] = [
    "Energy",
    "Basic Materials",
    "Industrials",
    "Consumer Non-Cyclical",
    "Consumer Cyclical",
    "Healthcare",
    "Financials",
    "Property & Real Estate",
    "Technology",
    "Infrastructure",
    "Transportation & Logistic",
]
