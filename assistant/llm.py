from openai import OpenAI
from .config import OMNI_API_KEY, OMNI_URL, MODEL_NAME

client = OpenAI(api_key=OMNI_API_KEY, base_url=OMNI_URL, timeout=60)


def ask_assistant(messages: list, max_tokens: int = 2000) -> str:
    """Основной запрос к модели для ответа пользователю"""
    response = client.chat.completions.create(model=MODEL_NAME, messages=messages, max_tokens=max_tokens)
    return response.choices[0].message.content


def ask_memory(prompt: str, max_tokens: int = 500) -> str:
    """Запрос к модели для извлечения памяти"""
    response = client.chat.completions.create(
        model=MODEL_NAME, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens
    )
    return response.choices[0].message.content
