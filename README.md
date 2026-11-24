# LLM Chatbot with Key Failover and Response Caching

A full-stack chatbot: React frontend, FastAPI backend, LiteLLM for model calls and caching, SQLite for session history. When an API key fails (429, quota or 5xx), the backend moves to the next key on its own. Every reply shows where it came from, which model answered, the latency and the tokens used by that request.

```
React (Vite, nginx)  ──►  FastAPI  ──►  LiteLLM  ──►  Gemini (key_1, key_2, ...)
                             │            └── built-in response cache
                             └──► SQLite (/data/app.db on a Docker volume)
```

## 1. Live URLs

| Part | URL |
|---|---|
| Frontend | `https://<your-frontend>.vercel.app` |
| Backend | `https://<your-backend>.onrender.com` |
| Health check | `https://<your-backend>.onrender.com/health` |
| GitHub | `https://github.com/<you>/llm-chatbot` |
| Demo video | `<link>` |

## 2. Run locally: with Docker and without

### With Docker

```bash
cp .env.example .env
make up
```

- Frontend: http://localhost:3000
- Backend: http://localhost:8000 (try http://localhost:8000/health)

`make up` creates `.env` from `.env.example` if it is missing. Put your real keys in `.env` first. `make down` stops everything. `make clean` also removes the SQLite volume. `make logs` follows the logs.

The SQLite file lives in the named volume `chat-data`, mounted at `/data`, so history survives `make down` and `make up`.

### Without Docker

Backend (Python 3.10+):

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export LLM_API_KEYS="key-one,key-two"
export DATABASE_URL="sqlite:///./data/app.db"
export ALLOWED_ORIGINS="http://localhost:3000"
export ENABLE_FAILURE_SIMULATION=true
uvicorn app.main:app --reload --port 8000
```

Frontend (Node 20+):

```bash
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev
```

Tests:

```bash
cd backend
python -m pytest -q
```

The tests replace `litellm.acompletion` with a fake provider. They need no real keys and no network.

### Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `LLM_API_KEYS` | backend | Comma-separated key pool. Ids are `key_1`, `key_2`, ... in this order |
| `LLM_MODEL` | backend | LiteLLM model name, default `gemini/gemini-3.5-flash-lite` |
| `LLM_MODELS` | backend | Optional. Comma-separated model per key, for a mixed pool such as Gemini + Groq |
| `LLM_API_BASE` | backend | Optional custom base URL for OpenAI-compatible providers |
| `LLM_TIMEOUT_SECONDS` | backend | Timeout per provider call, default 30 |
| `DATABASE_URL` | backend | SQLite location, e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | backend | Comma-separated CORS origins |
| `ENABLE_FAILURE_SIMULATION` | backend | `true` enables the `X-Simulate-Failure` header |
| `DEFAULT_SYSTEM_PROMPT` | backend | System prompt used when the session has none |
| `CACHE_TTL_SECONDS` | backend | How long a cached reply is kept, default 86400 |
| `MAX_HISTORY_MESSAGES` | backend | How many past messages are sent to the model, default 40 |
| `FRONTEND_API_URL` | docker compose | Backend URL the frontend container uses at runtime |
| `VITE_API_URL` | frontend build | Backend URL baked into a static build (Vercel) |

## 3. Design decisions: key fallback, caching, SQLite session storage

### Key fallback

The key pool is built from `LLM_API_KEYS` in order: `key_1`, `key_2`, and so on. For each request the backend tries the keys one by one with `litellm.acompletion(api_key=..., num_retries=0)`:

1. If the call works and the reply is not empty, return it with that `key_id`. `fallback_attempts` is the number of keys that failed before it.
2. If the call raises (LiteLLM maps provider errors to `RateLimitError` for 429 and quota, `InternalServerError`, `BadGatewayError` and `ServiceUnavailableError` for 5xx, plus timeouts and auth errors), log the key id and error type and move to the next key.
3. If every key fails, return `503 {"detail": "all providers unavailable"}`. Nothing is written to history.

Why a small loop instead of the LiteLLM `Router`: the Router puts a failing deployment into a cooldown that lasts across requests. The assignment says `X-Simulate-Failure` must affect "this request only", so a simulated 429 must not change how the next request is routed. The Router also does not report which key answered or how many keys failed before it. A plain ordered loop gives exactly `key_id` and `fallback_attempts`, is easy to test, and still uses LiteLLM for the calls, error mapping and caching.

`X-Simulate-Failure` (only when `ENABLE_FAILURE_SIMULATION=true`) is parsed into a map like `{"key_1": 429}` or `{"*": 429}`. Before a key is called, if it is in the map, the backend raises the same LiteLLM exception a real provider error would raise. The failure goes through the same failover code path as a real one, and no real quota is spent. Only 429, 500, 502 and 503 are accepted; other values are ignored.

Keys are never returned, logged or stored. Only the ids `key_1`, `key_2` appear anywhere.

### Caching

LiteLLM's built-in cache is enabled at startup with `litellm.cache = Cache(type="local")` (in-memory, TTL from `CACHE_TTL_SECONDS`).

- **Cache key:** LiteLLM builds it from the model and the full message list sent to the model: system prompt, history and the new message. The API key is not part of it, so a reply from `key_2` is found later no matter which key would answer. The session id is not part of the messages, so the same first message in a new session hits the cache, while any different message, or the same message later in a conversation, misses it.
- **Lookup first:** the backend calls `litellm.cache.async_get_cache(model, messages)` before trying any key. A hit returns the stored reply text and the stored token usage, with `resolution_source: LLM_CACHE`, `key_id: null` and `fallback_attempts: 0`. No provider is called, so a hit takes a few milliseconds while a live call takes hundreds.
- **Store after success:** after a live reply, `litellm.cache.async_add_cache(response, model, messages)` saves it.
- The cache is checked before the failover loop because a cached reply needs no provider. So a message that is already cached is still answered from cache even with `X-Simulate-Failure: *=429`. Use a new message to see the 503.
- The lookup and store are called directly rather than through `acompletion(caching=True)` so the label is exact and the hit path does not depend on LiteLLM's proxy extras.
- An in-memory cache is cleared on restart. That is allowed by the assignment and keeps the stack free of external services.

### SQLite session storage

- `DATABASE_URL` points to the file, `/data/app.db` in Docker, which is a named volume, so history survives container restarts.
- Tables are created on startup with `CREATE TABLE IF NOT EXISTS`. WAL mode is turned on.
- `messages` follows the required schema, with an index on `(session_id, id)`. A small `sessions` table stores the session's system prompt, so a prompt sent once is used for the rest of that session.
- The user message and the assistant reply are written together in one transaction, only after the model answered. A 422 or 503 request leaves no trace, and the session does not exist until its first successful reply. That is why `GET /sessions/{id}/messages` returns 404 for a session whose only request failed.
- Each request loads the session's history from SQLite and sends `system + history + new message` to the model. The client only sends the new message. The last `MAX_HISTORY_MESSAGES` messages are sent, to keep token use bounded in long chats.

### Validation and errors

- `session_id`: string, 1 to 128 characters after trimming. `message`: string, 1 to 4000 characters after trimming. `system_prompt`: optional string. Anything else, including malformed JSON, gets FastAPI's default 422 body with `detail`.
- An HTTP middleware catches any unexpected exception and returns `500 {"detail": "internal server error"}`. Stack traces are only logged, never sent.
- `latency_ms` is measured on the server with `time.perf_counter()` from the start of the request until the reply is ready.
- `input_tokens` and `output_tokens` come from the provider's `usage` for this request only. On a cache hit they are the usage stored with the cached response.

## 4. Verification: how to see failover, cache hits and per-message token usage

Set `API` to the backend URL:

```bash
API=http://localhost:8000
```

Health:

```bash
curl -s $API/health
# {"status":"ok","keys_configured":2,"cache_enabled":true,...}
```

Live call, then cache hit (same first message, new session):

```bash
curl -s -X POST $API/chat -H "Content-Type: application/json" \
  -d '{"session_id":"s-demo1","message":"What is the capital of France?"}'
# resolution_source: LIVE_INFERENCE, key_id: key_1, latency_ms: a few hundred

curl -s -X POST $API/chat -H "Content-Type: application/json" \
  -d '{"session_id":"s-demo2","message":"What is the capital of France?"}'
# resolution_source: LLM_CACHE, same reply, key_id: null, latency_ms: close to 0
```

Failover:

```bash
curl -s -X POST $API/chat -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: key_1=429" \
  -d '{"session_id":"s-demo3","message":"Name three planets"}'
# 200, key_id: key_2, fallback_attempts: 1

curl -s -X POST $API/chat -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: key_1=500" \
  -d '{"session_id":"s-demo3","message":"Name two oceans"}'
# 200, key_id: key_2

curl -s -i -X POST $API/chat -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: *=429" \
  -d '{"session_id":"s-demo4","message":"This should fail"}'
# 503 {"detail":"all providers unavailable"}

curl -s -i $API/sessions/s-demo4/messages
# 404, nothing was saved
```

Multi-turn and history:

```bash
curl -s -X POST $API/chat -H "Content-Type: application/json" \
  -d '{"session_id":"s-demo5","message":"My name is Pallav"}'
curl -s -X POST $API/chat -H "Content-Type: application/json" \
  -d '{"session_id":"s-demo5","message":"What is my name?"}'
curl -s $API/sessions/s-demo5/messages
```

Validation:

```bash
curl -s -i -X POST $API/chat -H "Content-Type: application/json" -d '{"session_id":"s-x","message":"   "}'
curl -s -i -X POST $API/chat -H "Content-Type: application/json" -d '{bad json'
# both 422 with a JSON detail
```

In the UI, each assistant reply shows chips for `LIVE_INFERENCE` or `LLM_CACHE`, the model, latency in ms, input tokens, output tokens, and the key that answered with the number of failovers. The **Simulate key failure** dropdown sends `X-Simulate-Failure` from the browser, so failover can be shown without curl. To see a cache hit in the UI, send a message, reload the page (a new session starts) and send the same message again.

Token usage is per request: two first messages of the same length in two new sessions show about the same `input_tokens`, and in a longer chat `input_tokens` grows because the history is part of the prompt, while `output_tokens` is only the size of that reply.

## 5. Setup Used

- Editor: VS Code
- AI tools: Claude, used for planning, scaffolding and reviewing code. Every part was run and tested locally.
- Backend: Python 3.12, FastAPI, LiteLLM, SQLite (standard `sqlite3`), pytest
- Frontend: React 19, Vite, plain CSS, nginx for the container
- Deploy: Vercel (frontend), Render (backend, Docker)

## 6. Bonus features (if any)

- **Failover from the UI:** a dropdown sends `X-Simulate-Failure`, which makes the demo possible without curl.
- **System prompt per session:** stored in SQLite, so it applies to every later message in that session.
- **Key and failover chip:** each reply also shows `key_id` and `fallback_attempts`, so failover is visible in the UI.
- **Mixed-provider pool:** `LLM_MODELS` lets each key use its own model, e.g. `gemini/gemini-3.5-flash-lite,groq/llama-3.3-70b-versatile`.
- **Runtime frontend config:** the frontend container writes `config.js` from `API_URL` at start, so the same image works against any backend.
- **Non-root backend container:** the entrypoint fixes `/data` ownership and drops to an unprivileged user, which also works on hosts that mount volumes as root.
- **Bounded history:** only the last `MAX_HISTORY_MESSAGES` messages go to the model.
- **18 backend tests:** contract fields, cache hit and miss, per-request tokens, multi-turn, separate sessions, simulated and real failover, 503 with no history, 422 cases, 404, system prompt and CORS.
