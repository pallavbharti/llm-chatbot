import os


def read_list(name):
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def read_int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


API_KEYS = read_list("LLM_API_KEYS")
DEFAULT_MODEL = os.getenv("LLM_MODEL", "gemini/gemini-3.5-flash-lite").strip()
KEY_MODELS = read_list("LLM_MODELS")
LLM_API_BASE = os.getenv("LLM_API_BASE", "").strip() or None
LLM_TIMEOUT_SECONDS = read_int("LLM_TIMEOUT_SECONDS", 30)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
ALLOWED_ORIGINS = read_list("ALLOWED_ORIGINS") or ["http://localhost:3000"]
ENABLE_FAILURE_SIMULATION = os.getenv("ENABLE_FAILURE_SIMULATION", "false").strip().lower() == "true"

DEFAULT_SYSTEM_PROMPT = os.getenv(
    "DEFAULT_SYSTEM_PROMPT",
    "You are a helpful, concise assistant. Answer clearly and keep replies short unless asked for detail.",
)
CACHE_TTL_SECONDS = read_int("CACHE_TTL_SECONDS", 86400)
MAX_HISTORY_MESSAGES = read_int("MAX_HISTORY_MESSAGES", 40)


def build_key_pool():
    pool = []
    for index, key in enumerate(API_KEYS):
        model = KEY_MODELS[index] if index < len(KEY_MODELS) else DEFAULT_MODEL
        pool.append({"id": f"key_{index + 1}", "key": key, "model": model})
    return pool


KEY_POOL = build_key_pool()
