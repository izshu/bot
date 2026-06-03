from pymongo import MongoClient
from .config import MONGODB_URI

# MongoDB клиент
mongo = MongoClient(MONGODB_URI)
db = mongo["assistant"]

# Коллекции
conversations = db["conversations"]
facts = db["user_facts"]
projects = db["projects"]


def init_db():
    """Создание индексов для коллекций"""
    facts.create_index([("user_id", 1), ("category", 1), ("value", 1)], unique=True)
    projects.create_index([("user_id", 1), ("name", 1)], unique=True)
    conversations.create_index([("user_id", 1), ("created_at", -1)])
