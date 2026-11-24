const runtimeConfig = window.APP_CONFIG || {};
const API_URL = (runtimeConfig.API_URL || import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");

export const createSessionId = () => {
  if (window.crypto && window.crypto.randomUUID) {
    return "s-" + window.crypto.randomUUID().slice(0, 8);
  }
  return "s-" + Math.random().toString(16).slice(2, 10);
};

const readableDetail = (status, detail) => {
  if (status === 503) {
    return "All AI providers are unavailable right now. Please try again in a moment.";
  }
  if (status === 422) {
    if (Array.isArray(detail) && detail.length > 0) {
      const field = detail[0].loc ? detail[0].loc[detail[0].loc.length - 1] : "request";
      return "Invalid " + field + ": " + detail[0].msg;
    }
    return "The message is not valid. It must be 1 to 4000 characters.";
  }
  if (typeof detail === "string") {
    return detail;
  }
  return "Something went wrong (status " + status + ").";
};

export const sendChat = async ({ sessionId, message, systemPrompt, simulateFailure }) => {
  const body = { session_id: sessionId, message };
  if (systemPrompt && systemPrompt.trim()) {
    body.system_prompt = systemPrompt.trim();
  }

  const headers = { "Content-Type": "application/json" };
  if (simulateFailure) {
    headers["X-Simulate-Failure"] = simulateFailure;
  }

  let response;
  try {
    response = await fetch(API_URL + "/chat", {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new Error("Could not reach the server. Check your connection and try again.");
  }

  let data = null;
  try {
    data = await response.json();
  } catch (err) {
    data = null;
  }

  if (!response.ok) {
    throw new Error(readableDetail(response.status, data && data.detail));
  }
  if (!data || !data.reply) {
    throw new Error("The server sent an empty reply. Please try again.");
  }
  return data;
};
