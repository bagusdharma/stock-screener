"""FastAPI — lapisan baca + chat untuk web dashboard (deploy: Oracle Cloud).

Dijalankan di MESIN YANG SAMA dengan bot Telegram (main.py), sehingga:
- /results_cache.json & /screen_status.json dibaca langsung dari file
  (path identik dgn yang di-fetch frontend lib/data.ts via env DATA_URL)
- /chat memakai src.bot.conversation.get_response — SATU otak AI dengan
  bot Telegram (RULE_BOT.md, direct answers, chain fallback, history)

Jalankan: uvicorn backend_api:app --host 0.0.0.0 --port 8000
Env:
  ALLOWED_ORIGINS  daftar origin dipisah koma (default: * — set domain
                   Vercel di produksi, mis. https://ceksaham.vercel.app)
  CHAT_SECRET      kalau di-set: /chat WAJIB header X-Chat-Secret yang sama
                   (dipasang di Vercel & Render — proxy /api/chat server-side,
                   jadi secret tidak pernah terlihat browser). Kosong = terbuka.
  CHAT_RATE_LIMIT  maks request /chat per IP per menit (default 10) —
                   /chat membakar kuota Gemini, endpoint lain hanya baca cache.
"""

from __future__ import annotations

import os
import time
from collections import deque
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

from src.bot.handlers import _load_results_cached, load_results, load_status

app = FastAPI(title="IDX Screener API", version="1.0")

# results_cache.json ~3MB → gzip ~10x lebih kecil. KRUSIAL di GCP free tier
# (egress cuma 1 GB/bulan) dan mempercepat load di HP.
app.add_middleware(GZipMiddleware, minimum_size=1024)

_origins = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# GET + HEAD dan tersedia juga di root "/": monitor uptime (UptimeRobot dkk)
# ada yang memakai HEAD atau di-set tanpa path — semua harus dijawab 200,
# kalau tidak service dianggap down & keep-alive gagal (Render tidur).
@app.api_route("/health", methods=["GET", "HEAD"])
@app.api_route("/", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}


@app.get("/results_cache.json")
def results():
    """Bentuk respons identik dgn file results_cache.json."""
    cached = _load_results_cached()
    return {
        "generated_at": cached.get("generated_at"),
        "total": len(cached.get("data", [])),
        "data": cached.get("data", []),
    }


@app.get("/screen_status.json")
def status():
    return load_status()


class ChatIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)


# ── Proteksi /chat: rate limit per-IP + shared secret ───────────────
# /chat = satu-satunya endpoint yang membakar kuota Gemini; tanpa limit,
# satu orang iseng bisa menghabiskan kuota harian dalam hitungan menit.
_CHAT_SECRET = os.getenv("CHAT_SECRET", "")
_RATE_MAX = max(1, int(os.getenv("CHAT_RATE_LIMIT", "10")))
_RATE_WINDOW = 60.0
_rate_hits: dict[str, deque] = {}
_rate_lock = Lock()


def _client_ip(request: Request) -> str:
    # Render/Vercel di belakang proxy → IP asli di X-Forwarded-For
    # (entri pertama). Fallback: koneksi langsung.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate(ip: str) -> float:
    """Return 0 kalau boleh; kalau kena limit, return detik sisa tunggu."""
    now = time.monotonic()
    with _rate_lock:
        hits = _rate_hits.setdefault(ip, deque())
        while hits and now - hits[0] > _RATE_WINDOW:
            hits.popleft()
        if len(hits) >= _RATE_MAX:
            return _RATE_WINDOW - (now - hits[0])
        hits.append(now)
        # jaga memori: buang IP yang sudah tidak aktif
        if len(_rate_hits) > 10_000:
            for k in [k for k, v in _rate_hits.items() if not v][:5_000]:
                _rate_hits.pop(k, None)
        return 0.0


@app.post("/chat")
async def chat(body: ChatIn, request: Request):
    """Chat via otak AI yang sama dengan bot Telegram."""
    if _CHAT_SECRET and request.headers.get("x-chat-secret") != _CHAT_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")

    retry_in = _check_rate(_client_ip(request))
    if retry_in > 0:
        raise HTTPException(
            status_code=429,
            detail="Terlalu banyak pesan — coba lagi sebentar lagi.",
            headers={"Retry-After": str(int(retry_in) + 1)},
        )

    from src.bot.conversation import get_response

    # user_id web (string uuid) → int stabil utk session store conversation
    uid = abs(hash("web:" + body.user_id)) % (2**31)
    try:
        reply = await get_response(uid, body.message, load_results())
    except Exception as exc:  # pertahanan terakhir — jangan bocorkan detail
        raise HTTPException(status_code=500, detail="chat error") from exc
    return {"reply": reply}
