import asyncio
import os

import django
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from bot.handlers.router import build_router


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required to run the bot")

    dispatcher = Dispatcher()
    dispatcher.include_router(build_router())
    async with Bot(token=token) as bot:
        await dispatcher.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user")
