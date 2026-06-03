import asyncio
import logging
from aiogram import Bot, Dispatcher

from .config import TELEGRAM_TOKEN
from .database import init_db
from .handlers.commands import register_commands
from .handlers.messages import register_messages

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    # Инициализация MongoDB
    init_db()
    logger.info("База данных инициализирована")

    # Инициализация бота
    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher()

    # Регистрация хендлеров
    register_commands(dp)
    register_messages(dp)
    logger.info("Хендлеры зарегистрированы")

    # Запуск бота
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
