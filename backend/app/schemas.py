from typing import Annotated, Optional

from pydantic import BaseModel, StringConstraints, field_validator

SessionId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ChatRequest(BaseModel):
    session_id: SessionId
    message: Message
    system_prompt: Optional[str] = None

    @field_validator("system_prompt")
    @classmethod
    def clean_system_prompt(cls, value):
        if value is None:
            return None
        value = value.strip()
        if len(value) > 8000:
            raise ValueError("system_prompt must be at most 8000 characters")
        return value or None


class ChatMetadata(BaseModel):
    resolution_source: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: Optional[str]
    fallback_attempts: int


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata
