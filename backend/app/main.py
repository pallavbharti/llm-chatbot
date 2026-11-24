import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.config import (
    ALLOWED_ORIGINS,
    DEFAULT_SYSTEM_PROMPT,
    ENABLE_FAILURE_SIMULATION,
    KEY_POOL,
    MAX_HISTORY_MESSAGES,
)
from app.llm import AllProvidersDown, ask_llm, is_cache_enabled, parse_simulation_header, setup_litellm
from app.schemas import ChatRequest, ChatResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    setup_litellm()
    if len(KEY_POOL) < 2:
        logger.warning("Only %s API key(s) configured, at least 2 are expected", len(KEY_POOL))
    logger.info(
        "Started with %s key(s), failure simulation %s",
        len(KEY_POOL),
        "on" if ENABLE_FAILURE_SIMULATION else "off",
    )
    yield


app = FastAPI(title="LLM Chatbot", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def catch_unexpected_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})


allow_all = "*" in ALLOWED_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all else ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Simulate-Failure"],
)


@app.get("/health")
def health():
    database_ok = db.is_database_ok()
    keys_loaded = len(KEY_POOL) > 0
    body = {
        "status": "ok" if database_ok and keys_loaded else "unavailable",
        "keys_configured": len(KEY_POOL),
        "cache_enabled": is_cache_enabled(),
        "database_ok": database_ok,
    }
    if not database_ok or not keys_loaded:
        body["detail"] = "service not ready"
        return JSONResponse(status_code=503, content=body)
    return body


def build_messages(system_prompt, history, user_message):
    messages = [{"role": "system", "content": system_prompt}]
    for item in history:
        messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": user_message})
    return messages


@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, x_simulate_failure: str | None = Header(default=None)):
    started = time.perf_counter()

    session = db.get_session(payload.session_id)
    stored_prompt = session["system_prompt"] if session else None
    system_prompt = payload.system_prompt or stored_prompt or DEFAULT_SYSTEM_PROMPT

    history = db.get_history(payload.session_id, limit=MAX_HISTORY_MESSAGES)
    messages = build_messages(system_prompt, history, payload.message)

    simulated_failures = parse_simulation_header(x_simulate_failure) if ENABLE_FAILURE_SIMULATION else {}

    try:
        result = await ask_llm(messages, simulated_failures)
    except AllProvidersDown:
        raise HTTPException(status_code=503, detail="all providers unavailable")

    metadata = {
        "resolution_source": result["resolution_source"],
        "model": result["model"],
        "latency_ms": int(round((time.perf_counter() - started) * 1000)),
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "key_id": result["key_id"],
        "fallback_attempts": result["fallback_attempts"],
    }

    db.save_turn(payload.session_id, payload.system_prompt, payload.message, result["reply"], metadata)

    return {"session_id": payload.session_id, "reply": result["reply"], "metadata": metadata}


@app.get("/sessions/{session_id}/messages")
def session_messages(session_id: str):
    if not db.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {"session_id": session_id, "messages": db.get_history(session_id)}
