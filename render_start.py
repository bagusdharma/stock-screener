"""Entry point deploy Render — SATU proses: FastAPI + bot Telegram.

Render free tier mensyaratkan web service listen di $PORT (kalau tidak,
deploy gagal & service tak bisa di-ping keep-alive). FastAPI jalan di
thread daemon; bot Telegram (main.py) tetap di main thread karena
python-telegram-bot memasang signal handler.

Start command Render: python render_start.py
Env yang dibutuhkan di Render:
  BOT_TOKEN, CHAT_ID, GEMINI_API_KEY
  RESULTS_URL, STATUS_URL  (raw.githubusercontent.com/.../main/...)
  SCREEN_SCHEDULE=off      (penjadwalan dikelola GitHub Actions)
  DISABLE_LOCAL_SCREEN=1   (RAM 512MB — screening lokal pasti OOM)
  ALLOWED_ORIGINS=https://NAMA-APP.vercel.app
"""

import os
import threading


def _run_api() -> None:
    import uvicorn

    uvicorn.run(
        "backend_api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_level="warning",
    )


if __name__ == "__main__":
    threading.Thread(target=_run_api, daemon=True, name="fastapi").start()

    import main

    main.main()
