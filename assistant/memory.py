import json
import logging
from datetime import datetime, timezone
from .database import facts, projects
from .llm import ask_memory
from .config import MEMORY_PROMPT, ALLOWED_FACT_CATEGORIES, ALLOWED_PROJECT_STATUSES

logger = logging.getLogger(__name__)


def get_facts(user_id: int) -> list:
    """Получение всех фактов пользователя"""
    return list(facts.find({"user_id": user_id}, {"_id": 0}))


def get_projects(user_id: int) -> list:
    """Получение всех проектов пользователя"""
    return list(projects.find({"user_id": user_id}, {"_id": 0}))


def save_fact(user_id: int, fact: dict):
    """Сохранение или обновление факта"""
    value = fact.get("value", "").strip()
    if not value or len(value) > 200:
        return

    category = fact.get("category", "")
    if category not in ALLOWED_FACT_CATEGORIES:
        logger.warning(f"Неизвестная категория: {category}")
        return

    existing = facts.find_one({"user_id": user_id, "category": category, "value": value})
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
                "category": category,
                "value": value,
                "status": fact.get("status", ""),
                "confidence": fact.get("confidence", 0),
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        logger.info(f"Факт добавлен: {value}")


def save_project(user_id: int, project: dict):
    """Сохранение или обновление проекта"""
    name = project.get("name", "").strip()
    if not name or len(name) > 100:
        return

    status = project.get("status", "in_progress")
    if status not in ALLOWED_PROJECT_STATUSES:
        status = "in_progress"

    existing = projects.find_one({"user_id": user_id, "name": name})
    if existing:
        update = {"updated_at": datetime.now(timezone.utc)}
        if project.get("status"):
            update["status"] = status
        note = project.get("note")
        if note:
            last_note = existing.get("notes", [])[-1] if existing.get("notes") else None
            if note != last_note:
                projects.update_one({"_id": existing["_id"]}, {"$push": {"notes": {"$each": [note], "$slice": -20}}})
        projects.update_one({"_id": existing["_id"]}, {"$set": update})
        logger.info(f"Проект обновлён: {name}")
    else:
        projects.insert_one(
            {
                "user_id": user_id,
                "name": name,
                "description": project.get("description", ""),
                "status": status,
                "notes": [project.get("note")] if project.get("note") else [],
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        logger.info(f"Проект добавлен: {name}")


async def analyze_memory(user_id: int, message: str):
    """Анализ сообщения и извлечение памяти"""
    try:
        existing_facts = get_facts(user_id)
        existing_projects = get_projects(user_id)

        prompt = MEMORY_PROMPT.format(
            existing_facts=json.dumps(existing_facts, ensure_ascii=False),
            existing_projects=json.dumps(existing_projects, ensure_ascii=False),
            message=message,
        )

        raw = ask_memory(prompt)
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

        except json.JSONDecodeError as e:
            logger.error(f"Memory JSON ERROR: {e} | cleaned: {cleaned}")

    except Exception as e:
        logger.error(f"Ошибка analyze_memory: {e}")
