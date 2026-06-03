import re
import json
from datetime import datetime, timezone
from aiogram import types
from aiogram.filters import Command, CommandStart

from ..database import conversations, facts, projects
from ..memory import get_facts, get_projects
from ..services import send_long_message


async def start(message: types.Message):
    await message.answer("Привет! Я твой AI ассистент. Напиши что-нибудь!")


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


async def clear_history(message: types.Message):
    conversations.delete_many({"user_id": message.from_user.id})
    await message.answer("История диалога очищена. Факты и проекты сохранены.")


async def reset_all(message: types.Message):
    user_id = message.from_user.id
    conversations.delete_many({"user_id": user_id})
    facts.delete_many({"user_id": user_id})
    projects.delete_many({"user_id": user_id})
    await message.answer("Полный сброс. История, факты и проекты удалены.")


async def forget_fact(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) < 2:
        await message.answer("Укажи что забыть.\n\nПример:\n/forget Python")
        return

    value = args[1].strip()

    result = facts.delete_one({"user_id": user_id, "value": {"$regex": f"^{re.escape(value)}$", "$options": "i"}})

    if result.deleted_count:
        await message.answer(f"Забыл: {value}")
    else:
        await message.answer(f"Не нашёл в памяти: {value}\n\nПосмотри /memory что именно записано.")


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


async def rename_project(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if len(args) < 2 or "|" not in args[1]:
        await message.answer("Использование:\n/rename_project Старое название | Новое название")
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


def register_commands(dp):
    """Регистрация всех команд"""
    dp.message.register(start, CommandStart())
    dp.message.register(help_cmd, Command("help"))
    dp.message.register(show_memory, Command("memory"))
    dp.message.register(clear_history, Command("clear"))
    dp.message.register(reset_all, Command("reset"))
    dp.message.register(forget_fact, Command("forget"))
    dp.message.register(show_project, Command("project"))
    dp.message.register(show_stats, Command("stats"))
    dp.message.register(rename_project, Command("rename_project"))
