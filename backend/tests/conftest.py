import os
import sys
import tempfile
from pathlib import Path

TEST_DIR = tempfile.mkdtemp()

os.environ["LLM_API_KEYS"] = "test-key-one,test-key-two"
os.environ["LLM_MODEL"] = "gemini/test-model"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DIR}/test.db"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
os.environ["ENABLE_FAILURE_SIMULATION"] = "true"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import litellm
import pytest
from fastapi.testclient import TestClient

from app.main import app


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.failing_keys = {}

    async def acompletion(self, **kwargs):
        api_key = kwargs["api_key"]
        self.calls.append(kwargs)
        if api_key in self.failing_keys:
            status = self.failing_keys[api_key]
            if status == 429:
                raise litellm.RateLimitError("quota exceeded", llm_provider="gemini", model=kwargs["model"])
            raise litellm.InternalServerError("server error", llm_provider="gemini", model=kwargs["model"])

        messages = kwargs["messages"]
        last = messages[-1]["content"]
        user_turns = [m["content"] for m in messages if m["role"] == "user"]
        reply = f"Answer to: {last} (seen {len(user_turns)} user messages: {' | '.join(user_turns)})"
        prompt_tokens = sum(len(m["content"].split()) for m in messages)
        return litellm.ModelResponse(
            model=kwargs["model"],
            choices=[{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            usage={"prompt_tokens": prompt_tokens, "completion_tokens": 7, "total_tokens": prompt_tokens + 7},
        )


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(litellm, "acompletion", fake.acompletion)
    return fake


@pytest.fixture
def client(provider):
    with TestClient(app) as test_client:
        litellm.cache.cache.flush_cache()
        yield test_client
