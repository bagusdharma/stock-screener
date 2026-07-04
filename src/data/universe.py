"""Universe saham IDX — daftar ticker dinamis dari IDX API.

Prioritas: cache file segar (≤7 hari) → fetch IDX API → cache basi →
daftar statis TICKERS di settings (fallback terakhir).

Screening seluruh universe (~950 emiten) memakan ±45-60 menit; daftar
statis 176 emiten ±9 menit. Mode dikontrol UNIVERSE_MODE di settings.
"""

from __future__ import annotations

import json
import logging
import os
import time

from src.config.settings import BASE_DIR, TICKERS, UNIVERSE_MODE

log = logging.getLogger(__name__)

_CACHE_FILE = BASE_DIR / "idx_universe.json"
_TTL_DAYS = 7
_MIN_VALID = 100  # respons < 100 ticker dianggap gagal/terpotong


def _read_cache() -> list[str]:
    try:
        tickers = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(tickers, list) and len(tickers) >= _MIN_VALID:
            return [str(t) for t in tickers]
    except (OSError, ValueError):
        pass
    return []


def get_universe(force_refresh: bool = False) -> list[str]:
    """Daftar ticker untuk screening, sesuai UNIVERSE_MODE."""
    if UNIVERSE_MODE != "full":
        return list(TICKERS)

    if not force_refresh and _CACHE_FILE.exists():
        age_days = (time.time() - os.path.getmtime(_CACHE_FILE)) / 86400
        if age_days <= _TTL_DAYS:
            cached = _read_cache()
            if cached:
                return cached

    from src.data.fetcher_idx_api import fetch_all_idx_tickers

    fresh = fetch_all_idx_tickers()
    if len(fresh) >= _MIN_VALID:
        try:
            _CACHE_FILE.write_text(json.dumps(fresh), encoding="utf-8")
        except OSError as exc:
            log.warning("Gagal tulis cache universe: %s", exc)
        return fresh

    stale = _read_cache()
    if stale:
        log.warning("IDX API gagal — pakai cache universe lama (%d ticker)", len(stale))
        return stale

    log.warning("Universe IDX tidak tersedia — fallback daftar statis %d ticker",
                len(TICKERS))
    return list(TICKERS)
