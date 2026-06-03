import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
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

client = OpenAI(api_key=os.getenv("OMNI_API_KEY"), base_url=os.getenv("OMNI_URL"), timeout=60)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# =========================
# MONGODB
# =========================

mongo = MongoClient(os.getenv("MONGODB_URI"))
db = mongo["assistant"]
conversations = db["conversations"]
facts = db["user_facts"]
projects = db["projects"]

facts.create_index([("user_id", 1), ("category", 1), ("value", 1)], unique=True)
projects.create_index([("user_id", 1), ("name", 1)], unique=True)

# =========================
# ИСТОРИЯ
# =========================

MAX_HISTORY = 50

SYSTEM_PROMPT = """Ты персональный AI ассистент. Общайся только на русском языке.
У тебя есть память — история наших разговоров сохраняется в базе данных и передаётся тебе при каждом сообщении.
Давай конкретные и практические ответы. Не лей воду."""


def save_message(user_id: int, role: str, content: str):
    conversations.insert_one(
        {"user_id": user_id, "role": role, "content": content, "created_at": datetime.now(timezone.utc)}
    )


def get_history(user_id: int) -> list:
    docs = conversations.find({"user_id": user_id}, sort=[("created_at", -1)], limit=MAX_HISTORY)
    docs = list(docs)
    docs.reverse()
    return [{"role": d["role"], "content": d["content"]} for d in docs]


def trim_history(user_id: int, max_messages: int = 500):
    count = conversations.count_documents({"user_id": user_id})
    if count <= max_messages:
        return
    to_delete = count - max_messages
    old_docs = list(conversations.find({"user_id": user_id}, {"_id": 1}).sort("created_at", 1).limit(to_delete))
    ids = [doc["_id"] for doc in old_docs]
    if ids:
        conversations.delete_many({"_id": {"$in": ids}})


# =========================
# ПАМЯТЬ
# =========================

MEMORY_PROMPT = """Ты анализируешь сообщение пользователя и извлекаешь долгосрочную информацию.

Извлекай только:
- навыки (skill) — технологии, языки, инструменты, области знаний
- цели (goal) — чего хочет достичь
- личное (personal) — имя, город, профессия
- проекты — что разрабатывает или планирует

НЕ извлекай:
- случайные действия ("посмотрел видео")
- разовые события
- временные состояния
- предположения

Для skill используй только статусы: beginner, learning, proficient, advanced
Для goal используй только статусы: active, completed, abandoned
Для personal статус не нужен, оставь пустой строкой

Существующие факты:
{existing_facts}

Существующие проекты:
{existing_projects}

Сообщение: "{message}"

Верни только JSON, без пояснений, без markdown:
{{"facts": [{{"category": "skill|goal|personal", "value": "...", "status": "...", "confidence": 0.0}}], "projects": [{{"name": "...", "description": "...", "status": "in_progress|idea|completed|abandoned", "note": "..."}}]}}

Если запоминать нечего — верни {{"facts": [], "projects": []}}"""


def get_facts(user_id: int) -> list:
    return list(facts.find({"user_id": user_id}, {"_id": 0}))


def get_projects(user_id: int) -> list:
    return list(projects.find({"user_id": user_id}, {"_id": 0}))


def save_fact(user_id: int, fact: dict):
    value = fact["value"].strip()
    existing = facts.find_one({"user_id": user_id, "category": fact["category"], "value": value})
    if existing:
        facts.update_one(
            {"_id": existing["_id"]},
            {"$set": {"status": fact.get("status", ""), "updated_at": datetime.now(timezone.utc)}},
        )
        logger.info(f"Факт обновлён: {value}")
    else:
        facts.insert_one(
            {
                "user_id": user_id,
                "category": fact["category"],
                "value": value,
                "status": fact.get("status", ""),
                "confidence": fact.get("confidence", 0),
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        logger.info(f"Факт добавлен: {value}")


def save_project(user_id: int, project: dict):
    existing = projects.find_one({"user_id": user_id, "name": project["name"]})
    if existing:
        update = {"updated_at": datetime.now(timezone.utc)}
        if project.get("status"):
            update["status"] = project["status"]
        if project.get("note"):
            projects.update_one({"_id": existing["_id"]}, {"$push": {"notes": project["note"]}})
        projects.update_one({"_id": existing["_id"]}, {"$set": update})
        logger.info(f"Проект обновлён: {project['name']}")
    else:
        projects.insert_one(
            {
                "user_id": user_id,
                "name": project["name"],
                "description": project.get("description", ""),
                "status": project.get("status", "in_progress"),
                "notes": [project["note"]] if project.get("note") else [],
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        logger.info(f"Проект добавлен: {project['name']}")


async def analyze_memory(user_id: int, message: str):
    try:
        existing_facts = get_facts(user_id)
        existing_projects = get_projects(user_id)

        prompt = MEMORY_PROMPT.format(
            existing_facts=existing_facts,
            existing_projects=existing_projects,
            message=message,
        )

        response = client.chat.completions.create(
            model="kr/claude-sonnet-4.5",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )

        raw = response.choices[0].message.content
        logger.info(f"Memory raw: {raw}")

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        try:
            parsed = json.loads(cleaned)
            logger.info(f"Memory parsed OK: {parsed}")

            for fact in parsed.get("facts", []):
                if fact.get("confidence", 0) >= 0.8:
                    save_fact(user_id, fact)

            for project in parsed.get("projects", []):
                save_project(user_id, project)

        except Exception as e:
            logger.error(f"Memory JSON ERROR: {e} | cleaned: {cleaned}")

    except Exception as e:
        logger.error(f"Ошибка analyze_memory: {e}")


# =========================
# HANDLERS
# =========================


@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer("Привет! Я твой AI ассистент. Напиши что-нибудь!")


@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Доступные команды:\n\n"
        "/help — список команд\n"
        "/memory — показать что я помню о тебе\n"
        "/clear — очистить историю диалога\n"
        "/reset — удалить всё (историю, факты, проекты)\n\n"
        "Я автоматически запоминаю факты о тебе и твоих проектах из разговора."
    )


@dp.message(Command("memory"))
async def show_memory(message: types.Message):
    user_id = message.from_user.id
    user_facts = get_facts(user_id)
    user_projects = get_projects(user_id)

    if not user_facts and not user_projects:
        await message.answer("Память пуста.")
        return

    text = ""

    personal = [f for f in user_facts if f["category"] == "personal"]
    skills = [f for f in user_facts if f["category"] == "skill"]
    goals = [f for f in user_facts if f["category"] == "goal"]

    if personal:
        text += "👤 Личное:\n"
        for f in personal:
            text += f"  - {f['value']}\n"

    if skills:
        text += "\n🛠 Навыки:\n"
        for f in skills:
            text += f"  - {f['value']} ({f['status']})\n"

    if goals:
        text += "\n🎯 Цели:\n"
        for f in goals:
            text += f"  - {f['value']} ({f['status']})\n"

    if user_projects:
        text += "\n📁 Проекты:\n"
        for p in user_projects:
            text += f"  - {p['name']} ({p['status']})\n"

    await message.answer(text)


@dp.message(Command("clear"))
async def clear_history(message: types.Message):
    conversations.delete_many({"user_id": message.from_user.id})
    await message.answer("История диалога очищена. Факты и проекты сохранены.")


@dp.message(Command("reset"))
async def reset_all(message: types.Message):
    user_id = message.from_user.id
    conversations.delete_many({"user_id": user_id})
    facts.delete_many({"user_id": user_id})
    projects.delete_many({"user_id": user_id})
    await message.answer("Полный сброс. История, факты и проекты удалены.")


@dp.message()
async def handle(message: types.Message):
    if not message.text:
        await message.answer("Пока я умею работать только с текстом.")
        return

    user_id = message.from_user.id

    save_message(user_id, "user", message.text)

    trim_history(user_id)

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
                        for note in p["notes"]:
                            memory_context += f"\n  • {note}"

        logger.info(f"MEMORY CONTEXT:\n{memory_context}")
        messages = [{"role": "system", "content": SYSTEM_PROMPT + memory_context}] + history
        response = client.chat.completions.create(model="kr/claude-sonnet-4.5", messages=messages, max_tokens=2000)
        reply = response.choices[0].message.content

        if not reply:
            reply = "Не удалось получить ответ."

        save_message(user_id, "assistant", reply)
        await message.answer(reply)

        asyncio.create_task(analyze_memory(user_id, message.text))

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
