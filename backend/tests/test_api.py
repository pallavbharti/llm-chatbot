import uuid


def new_session():
    return "s-" + uuid.uuid4().hex[:8]


def send(client, session_id, message, headers=None, **extra):
    body = {"session_id": session_id, "message": message, **extra}
    return client.post("/chat", json=body, headers=headers or {})


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["keys_configured"] == 2
    assert response.json()["cache_enabled"] is True


def test_chat_live_inference_contract(client):
    response = send(client, new_session(), "What is the capital of France?")
    assert response.status_code == 200
    body = response.json()
    metadata = body["metadata"]
    assert body["reply"]
    assert metadata["resolution_source"] == "LIVE_INFERENCE"
    assert metadata["model"] == "gemini/test-model"
    assert isinstance(metadata["latency_ms"], int)
    assert metadata["input_tokens"] > 0
    assert metadata["output_tokens"] == 7
    assert metadata["key_id"] == "key_1"
    assert metadata["fallback_attempts"] == 0


def test_same_first_message_in_new_session_hits_cache(client, provider):
    first = send(client, new_session(), "Tell me a fact about Mars").json()
    second = send(client, new_session(), "Tell me a fact about Mars").json()
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]
    assert second["metadata"]["output_tokens"] == first["metadata"]["output_tokens"]
    assert len(provider.calls) == 1


def test_different_message_never_from_cache(client):
    send(client, new_session(), "Tell me about Jupiter")
    other = send(client, new_session(), "Tell me about Saturn").json()
    assert other["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_tokens_are_per_request_not_running_total(client):
    first = send(client, new_session(), "alpha beta gamma").json()
    second = send(client, new_session(), "delta epsilon zeta").json()
    assert first["metadata"]["input_tokens"] == second["metadata"]["input_tokens"]


def test_multi_turn_history_and_separate_sessions(client):
    session_a = new_session()
    session_b = new_session()
    send(client, session_a, "My name is Pallav")
    second = send(client, session_a, "What is my name?").json()
    assert "My name is Pallav" in second["reply"]

    other = send(client, session_b, "What is my name?").json()
    assert "Pallav" not in other["reply"]

    history = client.get(f"/sessions/{session_a}/messages").json()
    assert history["session_id"] == session_a
    assert [m["role"] for m in history["messages"]] == ["user", "assistant", "user", "assistant"]
    assert history["messages"][0]["content"] == "My name is Pallav"
    assert history["messages"][1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert "metadata" not in history["messages"][0]
    assert all(m["role"] != "system" for m in history["messages"])


def test_failover_on_simulated_429(client):
    response = send(client, new_session(), "failover 429", {"X-Simulate-Failure": "key_1=429"})
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"
    assert response.json()["metadata"]["fallback_attempts"] == 1
    assert response.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_failover_on_simulated_500(client):
    response = send(client, new_session(), "failover 500", {"X-Simulate-Failure": "key_1=500"})
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"


def test_all_keys_failing_returns_503_and_saves_nothing(client):
    session_id = new_session()
    response = send(client, session_id, "everything fails", {"X-Simulate-Failure": "*=429"})
    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}
    assert client.get(f"/sessions/{session_id}/messages").status_code == 404


def test_both_keys_listed_failing_returns_503(client):
    response = send(client, new_session(), "both fail", {"X-Simulate-Failure": "key_1=429,key_2=500"})
    assert response.status_code == 503


def test_real_provider_errors_fail_over(client, provider):
    provider.failing_keys = {"test-key-one": 429}
    response = send(client, new_session(), "real 429 from provider")
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"

    provider.failing_keys = {"test-key-one": 500, "test-key-two": 429}
    response = send(client, new_session(), "real outage")
    assert response.status_code == 503
    assert response.json()["detail"] == "all providers unavailable"


def test_simulation_is_per_request(client):
    send(client, new_session(), "first request", {"X-Simulate-Failure": "key_1=429"})
    response = send(client, new_session(), "second request").json()
    assert response["metadata"]["key_id"] == "key_1"


def test_api_key_never_leaks(client):
    response = send(client, new_session(), "show me secrets", {"X-Simulate-Failure": "key_1=503"})
    assert "test-key" not in response.text


def test_validation_errors_return_422(client):
    session_id = new_session()
    assert send(client, session_id, "").status_code == 422
    assert send(client, session_id, "    ").status_code == 422
    assert send(client, session_id, "x" * 4001).status_code == 422
    assert send(client, "", "hello").status_code == 422
    assert send(client, "s" * 129, "hello").status_code == 422
    assert client.post("/chat", json={"session_id": session_id}).status_code == 422
    assert client.post("/chat", json={"message": "hi"}).status_code == 422

    malformed = client.post("/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert malformed.status_code == 422
    assert "detail" in malformed.json()
    assert client.get(f"/sessions/{session_id}/messages").status_code == 404


def test_message_of_4000_chars_after_trim_is_valid(client):
    response = send(client, new_session(), "  " + "y" * 4000 + "  ")
    assert response.status_code == 200


def test_unknown_session_returns_404(client):
    response = client.get("/sessions/does-not-exist/messages")
    assert response.status_code == 404
    assert response.json() == {"detail": "session not found"}


def test_system_prompt_is_used_and_kept_for_session(client, provider):
    session_id = new_session()
    send(client, session_id, "hello", system_prompt="You are a pirate.")
    send(client, session_id, "hello again")
    assert provider.calls[0]["messages"][0] == {"role": "system", "content": "You are a pirate."}
    assert provider.calls[1]["messages"][0] == {"role": "system", "content": "You are a pirate."}
    history = client.get(f"/sessions/{session_id}/messages").json()
    assert all(m["role"] != "system" for m in history["messages"])


def test_cors_allows_frontend_origin(client):
    response = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
