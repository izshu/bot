import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from dotenv import load_dotenv
from openai import OpenAI
from pymongo import MongoClient

# =========================
# ЛОГИРОВАНИЕ
# =========================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# ENV
# =========================

load_dotenv()

required_vars = ["TELEGRAM_TOKEN", "OMNI_API_KEY", "OMNI_URL", "MONGODB_URI"]
missing = [v for v in required_vars if not os.getenv(v)]
if missing:
    raise ValueError(f"Отсутствуют переменные: {', '.join(missing)}")

# =========================
# КОНФИГ
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

client = OpenAI(
    api_key=os.getenv("OMNI_API_KEY"),
    base_url=os.getenv("OMNI_URL"),
)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# =========================
# MONGODB
# =========================

mongo = MongoClient(os.getenv("MONGODB_URI"))
db = mongo["assistant"]
conversations = db["conversations"]

# =========================
# ИСТОРИЯ
# =========================

MAX_HISTORY = 30
SYSTEM_PROMPT = """Ты персональный AI ассистент. Общайся только на русском языке.
У тебя есть память — история наших разговоров сохраняется в базе данных и передаётся тебе при каждом сообщении.
Давай конкретные и практические ответы. Не лей воду."""


def save_message(user_id: int, role: str, content: str):
    conversations.insert_one({"user_id": user_id, "role": role, "content": content, "created_at": datetime.utcnow()})


def get_history(user_id: int) -> list:
    docs = conversations.find({"user_id": user_id}, sort=[("created_at", -1)], limit=MAX_HISTORY)
    docs = list(docs)
    docs.reverse()
    return [{"role": d["role"], "content": d["content"]} for d in docs]


# =========================
# HANDLERS
# =========================


@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer("Привет! Я твой AI ассистент. Напиши что-нибудь!")


@dp.message()
async def handle(message: types.Message):
    user_id = message.from_user.id

    save_message(user_id, "user", message.text)

    try:
        history = get_history(user_id)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        response = client.chat.completions.create(model="kr/claude-sonnet-4.5", messages=messages, max_tokens=2000)
        reply = response.choices[0].message.content
        save_message(user_id, "assistant", reply)
        await message.answer(reply)
    except Exception as e:
        logger.error(f"Ошибка API: {e}")
        await message.answer("Произошла ошибка, попробуй позже.")


# =========================
# MAIN
# =========================


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
