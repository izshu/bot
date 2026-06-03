import re
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
conversations.create_index([("user_id", 1), ("created_at", -1)])

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
# УТИЛИТЫ
# =========================


async def send_long_message(message: types.Message, text: str, chunk_size: int = 4000):
    if len(text) <= chunk_size:
        await message.answer(text)
        return
    parts = []
    while len(text) > chunk_size:
        split_at = text.rfind("\n", 0, chunk_size)
        if split_at == -1:
            split_at = chunk_size
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    parts.append(text)
    for part in parts:
        await message.answer(part)


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
            last_note = existing.get("notes", [])[-1] if existing.get("notes") else None
            if project["note"] != last_note:
                projects.update_one(
                    {"_id": existing["_id"]}, {"$push": {"notes": {"$each": [project["note"]], "$slice": -20}}}
                )
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
            existing_facts=json.dumps(existing_facts, ensure_ascii=False),
            existing_projects=json.dumps(existing_projects, ensure_ascii=False),
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
        "Я автоматически запоминаю факты о тебе и твоих проектах из разговора.\n\n"
        "/forget [факт] — удалить факт из памяти (не проекты)\n"
        "/project — список проектов\n"
        "/project [название] — детали проекта\n"
        "/stats — статистика\n"
        "/memory raw — сырые данные из базы\n"
        "/rename_project Старое | Новое — переименовать проект\n"
    )


@dp.message(Command("memory"))
async def show_memory(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    # RAW режим
    if len(args) > 1 and args[1].strip().lower() == "raw":
        user_facts = get_facts(user_id)
        user_projects = get_projects(user_id)
        raw = {"facts": user_facts, "projects": user_projects}
        text = json.dumps(raw, ensure_ascii=False, indent=2, default=str)
        await send_long_message(message, text)
        return

    # обычный режим
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


@dp.message(Command("forget"))
async def forget_fact(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) < 2:
        await message.answer("Укажи что забыть.\n\n" "Пример:\n" "/forget Python")
        return

    value = args[1].strip()

    result = facts.delete_one({"user_id": user_id, "value": {"$regex": f"^{re.escape(value)}$", "$options": "i"}})

    if result.deleted_count:
        await message.answer(f"Забыл: {value}")
    else:
        await message.answer(f"Не нашёл в памяти: {value}\n\n" "Посмотри /memory что именно записано.")


@dp.message(Command("project"))
async def show_project(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) < 2:
        user_projects = get_projects(user_id)
        if not user_projects:
            await message.answer("Проектов нет.")
            return
        text = "📁 Проекты:\n\n"
        for p in user_projects:
            text += f"• {p['name']} ({p['status']})\n"
        text += "\nДетали: /project [название]"
        await message.answer(text)
        return

    name = args[1].strip()
    project = projects.find_one({"user_id": user_id, "name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})

    if not project:
        await message.answer(f"Проект не найден: {name}\n\nСписок: /project")
        return

    text = f"📁 {project['name']}\n"
    text += f"Статус: {project['status']}\n"
    if project.get("description"):
        text += f"Описание: {project['description']}\n"
    if project.get("notes"):
        text += f"\nЗаметки:\n"
        for note in project["notes"]:
            text += f"  • {note}\n"
    else:
        text += "\nЗаметок пока нет.\n"
    await send_long_message(message, text)


@dp.message(Command("stats"))
async def show_stats(message: types.Message):
    user_id = message.from_user.id

    msg_count = conversations.count_documents({"user_id": user_id})
    facts_count = facts.count_documents({"user_id": user_id})
    projects_count = projects.count_documents({"user_id": user_id})

    first = conversations.find_one({"user_id": user_id}, sort=[("created_at", 1)])
    last = conversations.find_one({"user_id": user_id}, sort=[("created_at", -1)])

    text = "📊 Статистика\n\n"
    text += f"Сообщений: {msg_count}\n"
    text += f"Фактов: {facts_count}\n"
    text += f"Проектов: {projects_count}\n"

    if first and last:
        first_date = first["created_at"].strftime("%d.%m.%Y %H:%M")
        last_date = last["created_at"].strftime("%d.%m.%Y %H:%M")
        text += f"\nПервое сообщение: {first_date}\n"
        text += f"Последняя активность: {last_date}\n"

    await message.answer(text)


@dp.message(Command("rename_project"))
async def rename_project(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) < 2 or "|" not in args[1]:
        await message.answer("Использование:\n" "/rename_project Старое название | Новое название")
        return

    parts = args[1].split("|", 1)
    old_name = parts[0].strip()
    new_name = parts[1].strip()

    if not old_name or not new_name:
        await message.answer("Оба названия должны быть заполнены.")
        return

    existing = projects.find_one({"user_id": user_id, "name": {"$regex": f"^{re.escape(old_name)}$", "$options": "i"}})

    if not existing:
        await message.answer(f"Проект не найден: {old_name}\n\nСписок: /project")
        return

    try:
        projects.update_one(
            {"_id": existing["_id"]}, {"$set": {"name": new_name, "updated_at": datetime.now(timezone.utc)}}
        )
        await message.answer(f"Переименовано: {old_name} → {new_name}")
    except Exception:
        await message.answer(f"Проект с названием «{new_name}» уже существует.")


@dp.message()
async def handle(message: types.Message):
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
        response = client.chat.completions.create(model="kr/claude-sonnet-4.5", messages=messages, max_tokens=2000)
        reply = response.choices[0].message.content

        if not reply:
            reply = "Не удалось получить ответ."

        save_message(user_id, "assistant", reply)
        trim_history(user_id)
        await send_long_message(message, reply)

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
