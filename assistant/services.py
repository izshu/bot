from datetime import datetime, timezone
from aiogram import types
from .database import conversations
from .config import CONTEXT_WINDOW_SIZE, MAX_STORED_MESSAGES


def save_message(user_id: int, role: str, content: str):
    """Сохранение сообщения в историю"""
    conversations.insert_one(
        {"user_id": user_id, "role": role, "content": content, "created_at": datetime.now(timezone.utc)}
    )


def get_history(user_id: int) -> list:
    """Получение истории диалога"""
    docs = conversations.find({"user_id": user_id}, sort=[("created_at", -1)], limit=CONTEXT_WINDOW_SIZE)
    docs = list(docs)
    docs.reverse()
    return [{"role": d["role"], "content": d["content"]} for d in docs if d["role"] in ("user", "assistant")]


def trim_history(user_id: int, max_messages: int = MAX_STORED_MESSAGES):
    """Обрезка старой истории"""
    count = conversations.count_documents({"user_id": user_id})
    if count <= max_messages:
        return
    to_delete = count - max_messages
    old_docs = list(conversations.find({"user_id": user_id}, {"_id": 1}).sort("created_at", 1).limit(to_delete))
    ids = [doc["_id"] for doc in old_docs]
    if ids:
        conversations.delete_many({"_id": {"$in": ids}})


async def send_long_message(message: types.Message, text: str, chunk_size: int = 4000):
    """Разбиение и отправка длинных сообщений"""
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
