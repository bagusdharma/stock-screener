"""Entry point — initializes logging, Telegram bot, scheduler, runs polling."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def main() -> None:
    # ── 1. Load & validate settings ──────────────────────────────
    try:
        from src.config.settings import (
            BASE_DIR,
            BOT_TOKEN,
            GEMINI_API_KEY,
            LOG_FILE,
            LOG_MAX_BYTES,
            LOG_BACKUP_COUNT,
        )
    except EnvironmentError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"FATAL: Cannot load settings — {exc}", file=sys.stderr)
        sys.exit(1)

    # ── 2. Setup logging (RotatingFileHandler + console) ─────────
    try:
        log_path = Path(LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                RotatingFileHandler(
                    LOG_FILE,
                    maxBytes=LOG_MAX_BYTES,
                    backupCount=LOG_BACKUP_COUNT,
                    encoding="utf-8",
                ),
                logging.StreamHandler(),
            ],
        )
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
    except Exception as exc:
        print(f"FATAL: Cannot setup logging — {exc}", file=sys.stderr)
        sys.exit(1)

    log = logging.getLogger(__name__)
    log.info("Settings loaded")

    if not GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY not set — conversational AI disabled")

    # ── 3. Initialize Telegram bot application ───────────────────
    try:
        from telegram import Update
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            MessageHandler,
            PicklePersistence,
            TypeHandler,
            filters,
        )

        persistence_path = str(BASE_DIR / "bot_data.pkl")
        app = (
            Application.builder()
            .token(BOT_TOKEN)
            .persistence(PicklePersistence(filepath=persistence_path))
            .build()
        )
    except Exception as exc:
        log.error("Failed to initialize Telegram bot: %s", exc, exc_info=True)
        sys.exit(1)

    # ── 4. Register all handlers ─────────────────────────────────
    try:
        from src.bot.handlers import (
            cmd_bandingkan,
            cmd_budget,
            cmd_dividen,
            cmd_help,
            cmd_jumlah,
            cmd_rekomendasi,
            cmd_screen,
            cmd_start,
            cmd_status,
            error_handler,
            on_button,
            on_text,
            remember_chat_middleware,
        )

        # group=-1: jalan sebelum semua handler — simpan chat_id utk _send_long
        app.add_handler(TypeHandler(Update, remember_chat_middleware), group=-1)
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("rekomendasi", cmd_rekomendasi))
        app.add_handler(CommandHandler("dividen", cmd_dividen))
        app.add_handler(CommandHandler("bandingkan", cmd_bandingkan))
        app.add_handler(CommandHandler("status", cmd_status))
        app.add_handler(CommandHandler("screen", cmd_screen))
        app.add_handler(CommandHandler("budget", cmd_budget))
        app.add_handler(CommandHandler("jumlah", cmd_jumlah))
        app.add_handler(CommandHandler("help", cmd_help))
        app.add_handler(CallbackQueryHandler(on_button))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
        app.add_error_handler(error_handler)
    except Exception as exc:
        log.error("Failed to register handlers: %s", exc, exc_info=True)
        sys.exit(1)

    log.info("Registered 9 commands, 1 callback handler, 1 message handler")

    # ── 4b. Fix stale screening status from previous crash ──────
    try:
        from src.bot.handlers import fix_stale_on_startup
        fix_stale_on_startup()
    except Exception as exc:
        log.warning("Failed to fix stale status: %s", exc)

    # ── 5. Start scheduler ───────────────────────────────────────
    from src.scheduler.scheduler import start_scheduler, stop_scheduler

    sched = None
    try:
        sched = start_scheduler()
        log.info("Scheduler started — %d jobs", len(sched.get_jobs()))
    except Exception as exc:
        log.error("Scheduler failed to start: %s — bot runs without scheduler", exc)

    # ── 6. Run bot (polling) ─────────────────────────────────────
    log.info("Bot starting — polling mode")
    try:
        app.run_polling()
    finally:
        # ── 7. Graceful shutdown ─────────────────────────────────
        # Stop merger first — cancels in-flight ThreadPoolExecutor work
        try:
            from src.data.merger import shutdown_merger
            shutdown_merger()
        except Exception:
            pass
        # Shut down yfinance executor so daemon threads stop
        try:
            from src.data.fetcher_yfinance import shutdown_executor
            shutdown_executor()
        except Exception as exc:
            log.warning("Failed to shut down yfinance executor: %s", exc)
        # Reset stuck screening status so next startup is clean
        try:
            from src.bot.handlers import fix_stale_on_startup
            fix_stale_on_startup()
        except Exception:
            pass
        if sched is not None:
            stop_scheduler()
        log.info("Bot stopped cleanly")


if __name__ == "__main__":
    main()
