import asyncio
import logging
from aiogram import types

from ..services import save_message, get_history, trim_history, send_long_message
from ..memory import get_facts, get_projects, analyze_memory
from ..llm import ask_assistant
from ..config import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


async def handle(message: types.Message, bot):
    if not message.text:
        await message.answer("Пока я умею работать только с текстом.")
        return

    user_id = message.from_user.id

    save_message(user_id, "user", message.text)

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        history = get_history(user_id)
        user_facts = get_facts(user_id)
        user_projects = get_projects(user_id)

        memory_context = ""
        if user_facts or user_projects:
            memory_context = "\n\nЧто я знаю о пользователе:"
            if user_facts:
                for f in user_facts:
                    memory_context += f"\n- {f['category']}: {f['value']}"
                    if f.get("status"):
                        memory_context += f" ({f['status']})"
            if user_projects:
                memory_context += "\n\nПроекты:"
                for p in user_projects:
                    memory_context += f"\n- {p['name']} ({p['status']})"
                    if p.get("notes"):
                        for note in p["notes"][-3:]:
                            memory_context += f"\n  • {note}"

        logger.info(f"MEMORY CONTEXT:\n{memory_context}")
        messages = [{"role": "system", "content": SYSTEM_PROMPT + memory_context}] + history
        reply = ask_assistant(messages)

        if not reply:
            reply = "Не удалось получить ответ."

        save_message(user_id, "assistant", reply)
        trim_history(user_id)
        await send_long_message(message, reply)

        asyncio.create_task(analyze_memory(user_id, message.text))

    except Exception as e:
        logger.error(f"Ошибка API: {e}")
        await message.answer("Произошла ошибка, попробуй позже.")


def register_messages(dp):
    """Регистрация обработчика сообщений"""

    async def wrapper(message: types.Message):
        bot = message.bot
        await handle(message, bot)

    dp.message.register(wrapper)
