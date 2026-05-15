"use client";

import { useState, useRef, useEffect } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Message = { role: "user" | "model"; text: string };

export default function DevChat() {
  const [personalityId, setPersonalityId] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [personalityLoaded, setPersonalityLoaded] = useState<boolean | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function applyPersonality() {
    const id = personalityId.trim().toUpperCase();
    setActiveId(id || null);
    setPersonalityLoaded(null);
    setMessages([]);
    if (!id) return;

    try {
      const res = await fetch(`${API_URL}/ai/dev-chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: "__ping__", history: [], personality_id: id }),
      });
      const data = await res.json();
      setPersonalityLoaded(!!data.personality_loaded);
    } catch {
      setPersonalityLoaded(false);
    }
  }

  async function sendMessage(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    const next: Message[] = [...messages, { role: "user", text }];
    setMessages(next);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch(`${API_URL}/ai/dev-chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          history: messages,
          personality_id: activeId || null,
        }),
      });
      const data = await res.json();
      setMessages([...next, { role: "model", text: data.reply }]);
      if (personalityLoaded === null) setPersonalityLoaded(data.personality_loaded);
    } catch {
      setMessages([...next, { role: "model", text: "⚠️ Request failed" }]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  const statusLabel =
    activeId === null
      ? "No personality — default mode"
      : personalityLoaded === true
      ? `Personality loaded`
      : personalityLoaded === false
      ? `"${activeId}" not found — default mode`
      : `Searching for: ${activeId}…`;

  return (
    <div style={s.root}>
      {/* Header */}
      <div style={s.header}>
        <span style={s.headerTitle}>Dev Chat</span>
        <span style={s.headerSub}>Gemini 2.5 Flash Lite</span>
      </div>

      {/* Personality ID bar */}
      <div style={s.idBar}>
        <input
          style={s.idInput}
          type="text"
          placeholder="ID or 4-letter prefix"
          value={personalityId}
          onChange={(e) => setPersonalityId(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && applyPersonality()}
          maxLength={8}
        />
        <button style={s.idBtn} onClick={applyPersonality}>
          Apply
        </button>
      </div>

      {/* Status pill */}
      <div style={{
        ...s.statusPill,
        background: personalityLoaded === true ? "#0f2e1c" : "#131a22",
        borderColor: personalityLoaded === true ? "#3DDC97" : "#1E2A35",
        color: personalityLoaded === true ? "#3DDC97" : "#6B7A8D",
      }}>
        {statusLabel}
      </div>

      {/* Messages */}
      <div style={s.messages}>
        {messages.length === 0 && (
          <p style={s.empty}>Send a message to start chatting.</p>
        )}
        {messages.map((m, i) => (
          <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
            <div style={m.role === "user" ? s.bubbleUser : s.bubbleModel}>
              {m.text}
            </div>
          </div>
        ))}
        {loading && (
          <div style={{ display: "flex", justifyContent: "flex-start" }}>
            <div style={{ ...s.bubbleModel, ...s.typing }} className="typing-dot">
              <span /><span /><span />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form style={s.inputRow} onSubmit={sendMessage}>
        <input
          ref={inputRef}
          style={s.textInput}
          type="text"
          placeholder="Type a message…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          autoFocus
        />
        <button style={s.sendBtn} type="submit" disabled={loading || !input.trim()}>
          Send
        </button>
      </form>

      <style>{`
        @keyframes blink {
          0%,100% { opacity: 0.2 }
          50% { opacity: 1 }
        }
        .typing-dot span {
          display: inline-block;
          width: 6px; height: 6px;
          border-radius: 50%;
          background: #6B7A8D;
          margin: 0 2px;
          animation: blink 1.2s infinite;
        }
        .typing-dot span:nth-child(2) { animation-delay: 0.2s; }
        .typing-dot span:nth-child(3) { animation-delay: 0.4s; }
      `}</style>
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  root: {
    minHeight: "100vh",
    background: "#0B0F14",
    color: "#E8EEF4",
    fontFamily: "Inter, sans-serif",
    display: "flex",
    flexDirection: "column",
    maxWidth: 600,
    margin: "0 auto",
    padding: "0 0 24px",
  },
  header: {
    padding: "20px 20px 12px",
    borderBottom: "1px solid #1E2A35",
    display: "flex",
    alignItems: "baseline",
    gap: 10,
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: 700,
    color: "#E8EEF4",
  },
  headerSub: {
    fontSize: 12,
    color: "#6B7A8D",
  },
  idBar: {
    display: "flex",
    gap: 8,
    padding: "12px 16px 0",
  },
  idInput: {
    flex: 1,
    background: "#121A23",
    border: "1px solid #1E2A35",
    borderRadius: 8,
    color: "#E8EEF4",
    padding: "9px 12px",
    fontSize: 14,
    fontFamily: "monospace",
    letterSpacing: 2,
    outline: "none",
  },
  idBtn: {
    background: "#1E2A35",
    border: "none",
    borderRadius: 8,
    color: "#E8EEF4",
    padding: "9px 16px",
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  },
  statusPill: {
    margin: "10px 16px 0",
    padding: "5px 12px",
    borderRadius: 20,
    border: "1px solid",
    fontSize: 12,
    fontWeight: 500,
    display: "inline-flex",
    alignSelf: "flex-start",
  },
  messages: {
    flex: 1,
    overflowY: "auto",
    padding: "16px",
    display: "flex",
    flexDirection: "column",
    gap: 10,
    minHeight: "calc(100vh - 220px)",
  },
  empty: {
    color: "#3a4a5a",
    fontSize: 14,
    textAlign: "center",
    marginTop: 40,
  },
  bubbleUser: {
    background: "#4DA3FF",
    color: "#fff",
    borderRadius: "16px 16px 4px 16px",
    padding: "10px 14px",
    maxWidth: "75%",
    fontSize: 14,
    lineHeight: 1.5,
  },
  bubbleModel: {
    background: "#121A23",
    color: "#E8EEF4",
    borderRadius: "16px 16px 16px 4px",
    padding: "10px 14px",
    maxWidth: "75%",
    fontSize: 14,
    lineHeight: 1.5,
    border: "1px solid #1E2A35",
  },
  typing: {
    padding: "12px 16px",
  },
  inputRow: {
    display: "flex",
    gap: 8,
    padding: "12px 16px 0",
  },
  textInput: {
    flex: 1,
    background: "#121A23",
    border: "1px solid #1E2A35",
    borderRadius: 10,
    color: "#E8EEF4",
    padding: "11px 14px",
    fontSize: 14,
    outline: "none",
  },
  sendBtn: {
    background: "#4DA3FF",
    border: "none",
    borderRadius: 10,
    color: "#fff",
    padding: "11px 18px",
    fontSize: 14,
    fontWeight: 700,
    cursor: "pointer",
  },
};
