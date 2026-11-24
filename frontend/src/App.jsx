import { useEffect, useRef, useState } from "react";
import { createSessionId, sendChat } from "./api";

const MAX_LENGTH = 4000;

const FAILURE_OPTIONS = [
  { label: "Off", value: "" },
  { label: "key_1 = 429", value: "key_1=429" },
  { label: "key_1 = 500", value: "key_1=500" },
  { label: "All keys = 429", value: "*=429" },
];

const AssistantMessage = ({ text, metadata }) => {
  const isCache = metadata.resolution_source === "LLM_CACHE";
  return (
    <div className="row row-assistant">
      <div className="bubble bubble-assistant" data-testid="assistant-message">
        <div className="reply-text">{text}</div>
        <div className="meta">
          <span className={isCache ? "chip chip-cache" : "chip chip-live"} data-testid="meta-source">
            {metadata.resolution_source}
          </span>
          <span className="chip" title="Model">
            <span data-testid="meta-model">{metadata.model}</span>
          </span>
          <span className="chip" title="Latency">
            <span data-testid="meta-latency">{metadata.latency_ms} ms</span>
          </span>
          <span className="chip" title="Input tokens for this request">
            in <span data-testid="meta-input-tokens">{metadata.input_tokens}</span>
          </span>
          <span className="chip" title="Output tokens for this reply">
            out <span data-testid="meta-output-tokens">{metadata.output_tokens}</span>
          </span>
          <span className="chip chip-muted" title="Key that answered">
            {metadata.key_id || "no key"}
            {metadata.fallback_attempts > 0 ? " · " + metadata.fallback_attempts + " failover" : ""}
          </span>
        </div>
      </div>
    </div>
  );
};

const App = () => {
  const [sessionId, setSessionId] = useState(createSessionId);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulateFailure, setSimulateFailure] = useState("");
  const [showSettings, setShowSettings] = useState(true);
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", text }]);
    setLoading(true);

    try {
      const data = await sendChat({ sessionId, message: text, systemPrompt, simulateFailure });
      setMessages((prev) => [...prev, { role: "assistant", text: data.reply, metadata: data.metadata }]);
    } catch (err) {
      setMessages((prev) => [...prev, { role: "error", text: err.message }]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const startNewChat = () => {
    setSessionId(createSessionId());
    setMessages([]);
    setInput("");
  };

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>LLM Chatbot</h1>
          <p className="subtitle">
            Session <code>{sessionId}</code>
          </p>
        </div>
        <div className="header-actions">
          <button className="ghost-button" onClick={() => setShowSettings((v) => !v)}>
            {showSettings ? "Hide settings" : "Settings"}
          </button>
          <button className="ghost-button" onClick={startNewChat}>
            New chat
          </button>
        </div>
      </header>

      {showSettings && (
        <section className="settings">
          <label className="field">
            <span>System prompt (optional, kept for this session)</span>
            <textarea
              data-testid="system-prompt-input"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="e.g. You are a friendly travel guide. Answer in 2 lines."
              rows={2}
            />
          </label>
          <label className="field field-small">
            <span>Simulate key failure</span>
            <select value={simulateFailure} onChange={(e) => setSimulateFailure(e.target.value)}>
              {FAILURE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </section>
      )}

      <main className="chat">
        {messages.length === 0 && (
          <div className="empty">
            <h2>Start a conversation</h2>
            <p>Every reply shows where it came from, which model answered, how long it took and the tokens it used.</p>
          </div>
        )}

        {messages.map((msg, index) => {
          if (msg.role === "user") {
            return (
              <div key={index} className="row row-user">
                <div className="bubble bubble-user">{msg.text}</div>
              </div>
            );
          }
          if (msg.role === "error") {
            return (
              <div key={index} className="row row-assistant">
                <div className="bubble bubble-error" role="alert" data-testid="error-message">
                  {msg.text}
                </div>
              </div>
            );
          }
          return <AssistantMessage key={index} text={msg.text} metadata={msg.metadata} />;
        })}

        {loading && (
          <div className="row row-assistant">
            <div className="bubble bubble-assistant typing">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </main>

      <footer className="composer">
        <textarea
          ref={inputRef}
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a message. Enter to send, Shift+Enter for a new line"
          rows={1}
          maxLength={MAX_LENGTH + 200}
          autoFocus
        />
        <button data-testid="send-button" onClick={handleSend} disabled={loading || !input.trim()}>
          {loading ? "Sending" : "Send"}
        </button>
        {input.length > MAX_LENGTH - 500 && (
          <span className={input.trim().length > MAX_LENGTH ? "counter counter-over" : "counter"}>
            {input.trim().length} / {MAX_LENGTH}
          </span>
        )}
      </footer>
    </div>
  );
};

export default App;
