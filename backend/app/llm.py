import logging

import litellm
from litellm.caching.caching import Cache

from app.config import CACHE_TTL_SECONDS, KEY_POOL, LLM_API_BASE, LLM_TIMEOUT_SECONDS

logger = logging.getLogger("llm")

SIMULATED_STATUSES = {429, 500, 502, 503}


class AllProvidersDown(Exception):
    pass


def setup_litellm():
    litellm.suppress_debug_info = True
    litellm.drop_params = True
    litellm.cache = Cache(type="local", ttl=CACHE_TTL_SECONDS, default_in_memory_ttl=CACHE_TTL_SECONDS)


def is_cache_enabled():
    return litellm.cache is not None


def parse_simulation_header(header):
    failures = {}
    if not header:
        return failures
    for part in header.split(","):
        if "=" not in part:
            continue
        key_id, status = part.split("=", 1)
        key_id = key_id.strip()
        status = status.strip()
        if not key_id or not status.isdigit() or int(status) not in SIMULATED_STATUSES:
            continue
        failures[key_id] = int(status)
    return failures


def simulated_error(status, model):
    message = f"Simulated provider failure ({status})"
    if status == 429:
        return litellm.RateLimitError(message, llm_provider="simulated", model=model)
    if status == 502:
        return litellm.BadGatewayError(message, llm_provider="simulated", model=model)
    if status == 503:
        return litellm.ServiceUnavailableError(message, llm_provider="simulated", model=model)
    return litellm.InternalServerError(message, llm_provider="simulated", model=model)


def unique_models():
    models = []
    for item in KEY_POOL:
        if item["model"] not in models:
            models.append(item["model"])
    return models


def read_usage(usage, name):
    if usage is None:
        return 0
    if isinstance(usage, dict):
        return int(usage.get(name) or 0)
    return int(getattr(usage, name, 0) or 0)


async def find_in_cache(messages):
    for model in unique_models():
        cached = await litellm.cache.async_get_cache(model=model, messages=messages)
        if not cached:
            continue
        reply = (cached["choices"][0]["message"]["content"] or "").strip()
        if not reply:
            continue
        usage = cached.get("usage")
        return {
            "reply": reply,
            "resolution_source": "LLM_CACHE",
            "model": model,
            "input_tokens": read_usage(usage, "prompt_tokens"),
            "output_tokens": read_usage(usage, "completion_tokens"),
            "key_id": None,
            "fallback_attempts": 0,
        }
    return None


async def call_with_key(item, messages, simulated_failures):
    status = simulated_failures.get(item["id"]) or simulated_failures.get("*")
    if status:
        raise simulated_error(status, item["model"])

    response = await litellm.acompletion(
        model=item["model"],
        messages=messages,
        api_key=item["key"],
        api_base=LLM_API_BASE,
        timeout=LLM_TIMEOUT_SECONDS,
        num_retries=0,
        caching=False,
    )
    reply = response.choices[0].message.content
    if not reply or not reply.strip():
        raise ValueError("Provider returned an empty reply")
    return response, reply.strip()


async def ask_llm(messages, simulated_failures):
    if is_cache_enabled():
        cached = await find_in_cache(messages)
        if cached:
            return cached

    failed_keys = 0
    for item in KEY_POOL:
        try:
            response, reply = await call_with_key(item, messages, simulated_failures)
        except Exception as error:
            failed_keys += 1
            status = getattr(error, "status_code", None)
            logger.warning("%s failed (%s %s), trying next key", item["id"], type(error).__name__, status)
            continue

        if is_cache_enabled():
            await litellm.cache.async_add_cache(response, model=item["model"], messages=messages)

        return {
            "reply": reply,
            "resolution_source": "LIVE_INFERENCE",
            "model": item["model"],
            "input_tokens": read_usage(response.usage, "prompt_tokens"),
            "output_tokens": read_usage(response.usage, "completion_tokens"),
            "key_id": item["id"],
            "fallback_attempts": failed_keys,
        }

    raise AllProvidersDown()
