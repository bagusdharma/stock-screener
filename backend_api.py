"""FastAPI — lapisan baca + chat untuk web dashboard (deploy: Oracle Cloud).

Dijalankan di MESIN YANG SAMA dengan bot Telegram (main.py), sehingga:
- /results_cache.json & /screen_status.json dibaca langsung dari file
  (path identik dgn yang di-fetch frontend lib/data.ts via env DATA_URL)
- /chat memakai src.bot.conversation.get_response — SATU otak AI dengan
  bot Telegram (RULE_BOT.md, direct answers, chain fallback, history)

Jalankan: uvicorn backend_api:app --host 0.0.0.0 --port 8000
Env:
  ALLOWED_ORIGINS  daftar origin dipisah koma (default: * — set domain
                   Vercel di produksi, mis. https://idx-screener.vercel.app)
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
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


@app.get("/health")
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


@app.post("/chat")
async def chat(body: ChatIn):
    """Chat via otak AI yang sama dengan bot Telegram."""
    from src.bot.conversation import get_response

    # user_id web (string uuid) → int stabil utk session store conversation
    uid = abs(hash("web:" + body.user_id)) % (2**31)
    try:
        reply = await get_response(uid, body.message, load_results())
    except Exception as exc:  # pertahanan terakhir — jangan bocorkan detail
        raise HTTPException(status_code=500, detail="chat error") from exc
    return {"reply": reply}
