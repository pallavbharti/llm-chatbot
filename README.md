# LLM Chatbot with Key Failover and Response Caching

A small chatbot I built for the Lyftr AI full-stack assignment. React on the frontend, FastAPI on the backend, LiteLLM to talk to Gemini, and SQLite to keep chat history.

The two main things it does:

- If one API key fails (429, quota, 5xx), it quietly moves to the next key. The user never sees a broken screen.
- If the exact same conversation was asked before, it answers from cache instead of calling the model again.

Every reply in the UI shows where the answer came from (`LIVE_INFERENCE` or `LLM_CACHE`), which model answered, how long it took, and the tokens used for that one request.

```
React (Vite)  ──►  FastAPI  ──►  LiteLLM  ──►  Gemini (key_1, key_2)
                      │            └── response cache
                      └──► SQLite (/data/app.db)
```

## 1. Live URLs

| Part | URL |
|---|---|
| Frontend | https://llm-chatbot-eosin.vercel.app |
| Backend | https://llm-chatbot-xdd9.onrender.com |
| Health check | https://llm-chatbot-xdd9.onrender.com/health |
| GitHub | https://github.com/pallavbharti/llm-chatbot |
| Demo video | add link here |

The backend is on Render's free plan. It goes to sleep after about 15 minutes of no traffic, so the first request can take around 50 seconds. After that it is fast.

## 2. Run locally: with Docker and without

### With Docker

```bash
cp .env.example .env
# put your two API keys in LLM_API_KEYS inside .env
make up
```

- Frontend: http://localhost:3000
- Backend: http://localhost:8000/health

`make down` stops it. `make clean` also deletes the database volume. `make logs` shows the logs.

On Windows, if `make` is not installed, run `docker compose up -d --build` instead. It does the same thing.

The SQLite file lives in a named Docker volume (`chat-data`, mounted at `/data`), so chat history is still there after a restart.

### Without Docker

Backend (Python 3.10+):

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
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

The tests swap the real model call with a fake one, so they don't need API keys or internet.

### Environment variables

| Variable | Where | What it does |
|---|---|---|
| `LLM_API_KEYS` | backend | Comma-separated keys. First one is `key_1`, second is `key_2`, and so on |
| `LLM_MODEL` | backend | Model name for LiteLLM. Default `gemini/gemini-3.5-flash-lite` |
| `LLM_MODELS` | backend | Optional. One model per key, if you want to mix providers (e.g. Gemini + Groq) |
| `LLM_API_BASE` | backend | Optional. Custom base URL for OpenAI-compatible providers |
| `LLM_TIMEOUT_SECONDS` | backend | Timeout for each model call. Default 30 |
| `DATABASE_URL` | backend | Where the SQLite file is, e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | backend | Comma-separated list of frontend URLs allowed by CORS |
| `ENABLE_FAILURE_SIMULATION` | backend | `true` turns on the `X-Simulate-Failure` header |
| `DEFAULT_SYSTEM_PROMPT` | backend | System prompt used when the session doesn't set one |
| `CACHE_TTL_SECONDS` | backend | How long a cached reply is kept. Default 1 day |
| `MAX_HISTORY_MESSAGES` | backend | How many old messages are sent to the model. Default 40 |
| `FRONTEND_API_URL` | docker compose | Backend URL the frontend container points to |
| `VITE_API_URL` | frontend build | Backend URL for the Vercel build |

## 3. Design decisions: key fallback, caching, SQLite session storage

### Key fallback

The keys from `LLM_API_KEYS` are tried in order. For each request:

1. Try `key_1` with `litellm.acompletion` (retries turned off, so the switch happens right away).
2. If it throws (rate limit, quota, 5xx, timeout, even a bad key), log the key id and move to `key_2`.
3. The first key that gives a non-empty reply wins. Its id goes into `key_id`, and the number of keys that failed before it goes into `fallback_attempts`.
4. If every key fails, the API returns `503 {"detail": "all providers unavailable"}` and nothing is saved.

I first looked at LiteLLM's `Router`, which has fallback built in. I didn't use it for two reasons:

- When a key fails, the Router puts it on a cooldown that also affects the next requests. The assignment says a simulated failure should only affect that one request.
- The Router doesn't easily tell you which key answered or how many failed before it, and the API needs both.

A simple loop does exactly what's needed and is easy to test. LiteLLM is still doing the actual calls, the error mapping and the caching.

The `X-Simulate-Failure` header (only when `ENABLE_FAILURE_SIMULATION=true`) is turned into a small map like `{"key_1": 429}` or `{"*": 429}`. Before a key is used, if it's in that map, the backend raises the same LiteLLM error a real provider would. So a fake failure goes through the exact same code as a real one, and no real quota is wasted. Only 429, 500, 502 and 503 are accepted.

API keys are never sent back, logged or saved. Only `key_1`, `key_2` show up anywhere.

### Caching

LiteLLM's built-in in-memory cache is turned on at startup: `litellm.cache = Cache(type="local")`.

- **Cache key:** the model plus the full list of messages sent to it (system prompt, history and the new message). The session id is not part of it. So the same first message in a new session hits the cache, but a different message, or the same message later in a longer chat, does not.
- **Check first:** before trying any key, the backend calls `litellm.cache.async_get_cache(...)`. On a hit it returns the saved reply and the saved token counts, with `LLM_CACHE`, `key_id: null` and `fallback_attempts: 0`. No model call happens, so it takes a few ms instead of a second or more.
- **Save after:** after a live answer, `litellm.cache.async_add_cache(...)` stores it.
- I call the cache directly instead of passing `caching=True` to `acompletion`. That way I know for sure whether it was a hit, so the label is always right.
- Since the cache is checked before the keys, a message that's already cached still comes back even with `X-Simulate-Failure: *=429`. To see the 503, send a new message.
- The cache is in memory, so it resets when the server restarts. The assignment allows that, and it means no Redis or other extra service.

### SQLite session storage

- The database path comes from `DATABASE_URL`. In Docker it's `/data/app.db` on a named volume.
- Tables are created on startup if they don't exist.
- `messages` uses the schema from the assignment, plus an index on `(session_id, id)`. I added a small `sessions` table to remember the system prompt, so if you set it once it stays for the whole session.
- The user message and the bot reply are saved together, in one transaction, only after the model has answered. If the request fails (422 or 503), nothing is written. That's also why `GET /sessions/{id}/messages` gives 404 for a session where nothing ever succeeded.
- On every message the backend loads the session history from SQLite and sends `system + history + new message`. The frontend only sends the new message. Only the last 40 messages are sent, so long chats don't blow up the token count.

### Validation and errors

- `session_id` must be 1 to 128 characters and `message` 1 to 4000 characters (after trimming spaces). `system_prompt` is optional. Anything wrong, including broken JSON, gets FastAPI's normal 422 response with `detail`.
- A middleware catches any unexpected error and returns `500 {"detail": "internal server error"}`. No stack traces go to the client.
- `latency_ms` is measured on the server with `time.perf_counter()`.
- `input_tokens` and `output_tokens` come from the model's usage for that single request, never a running total.

## 4. Verification: how to see failover, cache hits and per-message token usage

Easiest way: open the frontend. Every reply shows chips for the source, model, latency, input tokens, output tokens, and which key answered (plus how many failed before it). There's a **Simulate key failure** dropdown that sends the `X-Simulate-Failure` header, so you can test failover without curl.

To see a cache hit in the UI: send a message, reload the page (that starts a new session), and send the same message again.

With curl (set `API` to the backend URL):

```bash
API=https://llm-chatbot-xdd9.onrender.com
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
# LIVE_INFERENCE, key_1

curl -s -X POST $API/chat -H "Content-Type: application/json" \
  -d '{"session_id":"s-demo2","message":"What is the capital of France?"}'
# LLM_CACHE, same reply, key_id null, much lower latency
```

Failover:

```bash
curl -s -X POST $API/chat -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: key_1=429" \
  -d '{"session_id":"s-demo3","message":"Name three planets"}'
# 200, key_id key_2, fallback_attempts 1

curl -s -X POST $API/chat -H "Content-Type: application/json" \
  -H "X-Simulate-Failure: key_1=500" \
  -d '{"session_id":"s-demo3","message":"Name two oceans"}'
# 200, key_id key_2

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

About tokens: two first messages of the same length in two new sessions show about the same `input_tokens`. In a longer chat `input_tokens` goes up because the history is part of the prompt, while `output_tokens` is just the size of that one reply.

## 5. Setup Used

- Editor: VS Code
- AI tools: Claude, for planning, writing a big part of the code, and the deploy setup. I reviewed it, ran it locally with Docker, and tested every feature on the live URLs.
- Backend: Python 3.12, FastAPI, LiteLLM, SQLite (`sqlite3`), pytest
- Frontend: React 19, Vite, plain CSS, nginx in Docker
- Deploy: Vercel (frontend), Render (backend, Docker)

## 6. Bonus features (if any)

- **Failover from the UI:** a dropdown sends `X-Simulate-Failure`, so failover can be shown without curl.
- **System prompt per session:** saved in SQLite, so it applies to every later message in that chat.
- **Key and failover chip:** each reply also shows which key answered and how many failed first.
- **Mixed providers:** with `LLM_MODELS`, each key can use a different model or provider.
- **Runtime config for the frontend container:** it writes `config.js` from `API_URL` when it starts, so the same image works with any backend.
- **Non-root backend container:** the startup script fixes permissions on `/data` and then runs the app as a normal user.
- **History limit:** only the last 40 messages go to the model, to keep tokens in check.
- **18 backend tests:** API fields, cache hit and miss, per-request tokens, multi-turn, separate sessions, simulated and real failover, 503 with nothing saved, 422 cases, 404, system prompt and CORS.
