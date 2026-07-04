# ============================================================
# config.py — Pengaturan IDX Stock Screener
#
# Sensitive data (BOT_TOKEN, CHAT_ID) dibaca dari .env
# File .env JANGAN diupload ke GitHub
# ============================================================

import os

# Coba load dari .env (kalau python-dotenv terinstall)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Tidak masalah — os.getenv tetap baca env var sistem

# ── Telegram ────────────────────────────────────────────────
# Isi di file .env:
#   BOT_TOKEN=token_kamu_dari_botfather
#   CHAT_ID=chat_id_kamu
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID   = os.getenv("CHAT_ID", "")

# ── Budget & preferensi ─────────────────────────────────────
BUDGET             = 500_000   # Rp — max harga 1 lot (100 lembar)
JUMLAH_REKOMENDASI = 5

# ── Threshold status (skor maks = 100) ─────────────────────
# Disesuaikan dengan distribusi skor IDX yang realistis (~50-80).
# Range lama (85/70/55) terlalu ketat — bahkan BBRI/BBCA tidak masuk BUY.
SKOR_STRONG_BUY = 75    # ← was 85 (tidak mungkin tercapai)
SKOR_BUY        = 63    # ← was 70 (BBRI 67 sekarang masuk BUY)
SKOR_HOLD       = 50    # ← was 55

# ── Universe Saham IDX (~140 ticker, sudah divalidasi) ──────
TICKERS = [
    # Perbankan
    "BBCA.JK","BBRI.JK","BMRI.JK","BBNI.JK","BRIS.JK","BBTN.JK",
    "BNGA.JK","BDMN.JK","BJBR.JK","BJTM.JK","MEGA.JK","PNBN.JK",
    "NISP.JK","BTPS.JK","ARTO.JK","AGRO.JK","BNLI.JK","BSIM.JK",
    # Keuangan
    "BFIN.JK","ADMF.JK","MFIN.JK","WOMF.JK","TRIM.JK","ABMM.JK",
    # Otomotif
    "ASII.JK","AUTO.JK","MPMX.JK","SMSM.JK","IMAS.JK",
    "DRMA.JK","GJTL.JK","INDS.JK","BOLT.JK","MASA.JK",
    # Konsumer
    "UNVR.JK","ICBP.JK","INDF.JK","KLBF.JK","HMSP.JK","GGRM.JK",
    "MAPI.JK","AMRT.JK","ACES.JK","ULTJ.JK","SIDO.JK","DLTA.JK",
    "MLBI.JK","ROTI.JK","SKLT.JK","STTP.JK","CLEO.JK","GOOD.JK",
    "LPPF.JK","RALS.JK","MAPA.JK","ERAA.JK","HRTA.JK","WIIM.JK",
    "MIDI.JK","HERO.JK","HOKI.JK","TBLA.JK",
    # Telekomunikasi
    "TLKM.JK","ISAT.JK","EXCL.JK","TOWR.JK","TBIG.JK","LINK.JK","MTEL.JK",
    # Teknologi
    "GOTO.JK","BUKA.JK","DCII.JK","MTDL.JK","EMTK.JK","MLPT.JK","ATIC.JK",
    # Energi
    "ADRO.JK","PTBA.JK","ITMG.JK","HRUM.JK","PGAS.JK","MEDC.JK",
    "INDY.JK","ESSA.JK","PGEO.JK","RAJA.JK","DOID.JK","GEMS.JK",
    "KKGI.JK","MYOH.JK","BYAN.JK","ELSA.JK","AKRA.JK","RUIS.JK",
    "DEWA.JK","BUMI.JK","FIRE.JK","ENRG.JK",
    # Material
    "ANTM.JK","INCO.JK","MDKA.JK","NCKL.JK","MBMA.JK","BRPT.JK",
    "TPIA.JK","INKP.JK","TKIM.JK","INTP.JK","SMGR.JK","ISSP.JK",
    "SMCB.JK","WTON.JK","EKAD.JK","INCI.JK","KRAS.JK","NIKL.JK",
    # Infrastruktur
    "JSMR.JK","WIKA.JK","PTPP.JK","WSKT.JK","ADHI.JK","WSBP.JK",
    # Properti
    "CTRA.JK","PWON.JK","SMRA.JK","DMAS.JK","BSDE.JK","LPKR.JK",
    "APLN.JK","MKPI.JK","DART.JK","MTLA.JK","PPRO.JK","KIJA.JK",
    "SSIA.JK","PLIN.JK","MMLP.JK",
    # Kesehatan
    "MIKA.JK","HEAL.JK","SILO.JK","TSPC.JK","DVLA.JK","KAEF.JK",
    "SOHO.JK","PYFA.JK","MERK.JK","PRDA.JK","INAF.JK",
    # Agribisnis
    "AALI.JK","SSMS.JK","DSNG.JK","TAPG.JK","LSIP.JK","SGRO.JK",
    "PALM.JK","BWPT.JK","JPFA.JK","CPIN.JK","MAIN.JK",
    # Transportasi
    "BIRD.JK","GIAA.JK","SMDR.JK","ASSA.JK","TMAS.JK","NELY.JK","IPCC.JK",
    # Media
    "SCMA.JK","MNCN.JK","FILM.JK",
    # Konglomerat & LQ45
    "UNTR.JK","HEXA.JK","AADI.JK","ADMR.JK","AMMN.JK","CUAN.JK","WIFI.JK",
]

# Hapus duplikat, jaga urutan
_seen, _dedup = set(), []
for _t in TICKERS:
    if _t not in _seen:
        _seen.add(_t)
        _dedup.append(_t)
TICKERS = _dedup