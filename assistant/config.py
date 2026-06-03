import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Валидация обязательных переменных
required_vars = ["TELEGRAM_TOKEN", "OMNI_API_KEY", "OMNI_URL", "MONGODB_URI"]
missing = [v for v in required_vars if not os.getenv(v)]
if missing:
    raise ValueError(f"Отсутствуют переменные: {', '.join(missing)}")

# Environment
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OMNI_API_KEY = os.getenv("OMNI_API_KEY")
OMNI_URL = os.getenv("OMNI_URL")
MONGODB_URI = os.getenv("MONGODB_URI")

# Константы
CONTEXT_WINDOW_SIZE = 50  # Сколько сообщений передаётся в контекст модели
MAX_STORED_MESSAGES = 500  # Сколько сообщений хранится в БД на пользователя
MEMORY_MIN_CONFIDENCE = 0.8  # Минимальный порог для сохранения фактов
MODEL_NAME = "kr/claude-sonnet-4.5"

# Валидация памяти
ALLOWED_FACT_CATEGORIES = {"skill", "goal", "personal"}
ALLOWED_PROJECT_STATUSES = {"in_progress", "idea", "completed", "abandoned"}

# Промпты
PROMPTS_DIR = Path(__file__).parent / "prompts"

with open(PROMPTS_DIR / "system_prompt.txt", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read().strip()

with open(PROMPTS_DIR / "memory_prompt.txt", encoding="utf-8") as f:
    MEMORY_PROMPT = f.read().strip()
