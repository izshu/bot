import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
client = OpenAI(
    api_key=os.getenv("OMNI_API_KEY"),
    base_url=os.getenv("OMNI_URL"),
)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer("Привет! Я твой AI ассистент. Напиши что-нибудь!")


@dp.message()
async def handle(message: types.Message):
    response = client.chat.completions.create(
        model="kr/claude-sonnet-4.5", messages=[{"role": "user", "content": message.text}], max_tokens=1000
    )
    await message.answer(response.choices[0].message.content)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
