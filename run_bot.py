"""
Tetik Telegram Bot Runner & Webhook Setup Utility.

Usage:
  1) Configure cloud webhook + Mini App menu button on Vercel:
       python run_bot.py --setup-webhook https://doriscan.vercel.app/api/v1/telegram/webhook

  2) Run local long-polling mode (for offline local development without webhook):
       python run_bot.py --polling
"""
import argparse
import asyncio

import httpx

from app.core.config import settings
from app.db.database import SessionLocal, ensure_db_initialized
from app.services.telegram_bot import (
    configure_telegram_bot_webhook_and_menu,
    handle_telegram_update,
    tg_api_call,
)


async def run_polling() -> None:
    ensure_db_initialized()
    await configure_telegram_bot_webhook_and_menu(webhook_url=None)
    await tg_api_call("deleteWebhook", {"drop_pending_updates": False})
    me = await tg_api_call("getMe")
    username = (me.get("result") or {}).get("username", settings.TELEGRAM_BOT_USERNAME)
    print(f"[Tetik Bot] @{username} long-polling rejimida ishga tushdi! (Mini App: {settings.MINI_APP_URL})", flush=True)

    offset = 0
    token = settings.TELEGRAM_BOT_TOKEN
    async with httpx.AsyncClient(timeout=40.0) as client:
        while True:
            try:
                resp = await client.post(
                    f"https://api.telegram.org/bot{token}/getUpdates",
                    json={
                        "offset": offset,
                        "timeout": 25,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                data = resp.json()
                for upd in data.get("result") or []:
                    offset = max(offset, upd["update_id"] + 1)
                    db = SessionLocal()
                    try:
                        await handle_telegram_update(upd, db)
                    finally:
                        db.close()
            except Exception as exc:
                print(f"[Tetik Bot] Polling warning: {exc}", flush=True)
                await asyncio.sleep(2)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Tetik Telegram Bot Manager")
    parser.add_argument("--setup-webhook", type=str, help="Set HTTPS webhook URL and configure Mini App menu button")
    parser.add_argument("--polling", action="store_true", help="Run bot in local long-polling mode")
    args = parser.parse_args()

    if args.setup_webhook:
        res = await configure_telegram_bot_webhook_and_menu(webhook_url=args.setup_webhook)
        print("Webhook & Menu Button configured:", res)
    elif args.polling:
        await run_polling()
    else:
        default_wh = f"{settings.PUBLIC_WEB_URL}/api/v1/telegram/webhook"
        res = await configure_telegram_bot_webhook_and_menu(webhook_url=default_wh)
        print(f"Configured default cloud webhook ({default_wh}):", res)


if __name__ == "__main__":
    asyncio.run(main())
