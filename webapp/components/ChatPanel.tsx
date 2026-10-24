"use client";

import { type FormEvent, type ReactNode, useState } from "react";
import { ApiError, type Reply, ReplySchema, api } from "@/lib/api";

const REMOVED = "[number removed: not in tool results]";
export const SUGGESTED = "How should I get from Fields Corner to Downtown Crossing?";

type Message =
  | { who: "user"; text: string }
  | { who: "agent"; reply: Reply }
  | { who: "error"; text: string };

type Props = { available: boolean; model: string; missingKeys: string[] };

function escape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Bold text and marked numbers, built as React nodes; model output is never HTML. */
function inline(text: string, unsupported: string[], key: string): ReactNode[] {
  const needles = [REMOVED, ...unsupported].map(escape).join("|");
  const marker = new RegExp(`(${needles})`, "g");
  return text.split(/(\*\*[^*]+\*\*)/g).flatMap((part, i) => {
    const bold = part.startsWith("**") && part.endsWith("**") && part.length > 4;
    const body = bold ? part.slice(2, -2) : part;
    const pieces = body.split(marker).map((piece, j) =>
      piece === REMOVED || unsupported.includes(piece) ? (
        <mark key={`${key}-${i}-${j}`} title="Not found in tool outputs">
          {piece}
        </mark>
      ) : (
        piece
      ),
    );
    return bold ? [<strong key={`${key}-${i}`}>{pieces}</strong>] : pieces;
  });
}

function AgentText({ reply }: { reply: Reply }) {
  const blocks: ReactNode[] = [];
  let bullets: ReactNode[] = [];
  const flush = (n: number) => {
    if (bullets.length) blocks.push(<ul key={`ul-${n}`}>{bullets}</ul>);
    bullets = [];
  };
  reply.text.split("\n").forEach((line, n) => {
    const item = line.match(/^\s*[-*]\s+(.*)$/);
    if (item) {
      bullets.push(<li key={n}>{inline(item[1], reply.unsupported_numbers, `l${n}`)}</li>);
      return;
    }
    flush(n);
    if (line.trim()) blocks.push(<p key={n}>{inline(line, reply.unsupported_numbers, `p${n}`)}</p>);
  });
  flush(-1);
  return <>{blocks}</>;
}

export default function ChatPanel({ available, model, missingKeys }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  function newChat() {
    setMessages([]);
    setConversationId(null);
    setDraft("");
  }

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setMessages((m) => [...m, { who: "user", text }]);
    setDraft("");
    setBusy(true);
    try {
      const reply = await api("/api/chat", ReplySchema, {
        method: "POST",
        body: { conversation_id: conversationId, message: text },
      });
      setConversationId(reply.conversation_id);
      setMessages((m) => [...m, { who: "agent", reply }]);
    } catch (error) {
      const text = error instanceof ApiError ? error.message : "The agent request failed.";
      setMessages((m) => [...m, { who: "error", text }]);
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void send(draft);
  }

  return (
    <section className="chat" aria-label="Chat with the agent">
      <header className="chat-head">
        <h2>Ask the agent</h2>
        <span className="model">{model}</span>
        {messages.length > 0 && (
          <button type="button" className="new-chat" onClick={newChat} disabled={busy}>
            New chat
          </button>
        )}
      </header>
      {!available && (
        <p className="warn">Chat needs {missingKeys.join(", ")}. The map and routes still work.</p>
      )}
      <div className="log" role="log" aria-live="polite">
        {messages.length === 0 && (
          <button type="button" className="suggest" disabled={!available || busy} onClick={() => void send(SUGGESTED)}>
            Try: “{SUGGESTED}”
          </button>
        )}
        {messages.map((m, i) => {
          if (m.who === "user") return <p key={i} className="msg user">{m.text}</p>;
          if (m.who === "error") return <p key={i} className="msg error">{m.text}</p>;
          const tools = m.reply.tool_calls.map((c) => c.name).join(" → ");
          return (
            <div key={i} className="msg agent">
              <AgentText reply={m.reply} />
              <p className="meta">
                Tools: {tools || "none"}
                {m.reply.first_draft_unsupported.length > 0 &&
                  ` · rewrote once to drop ${m.reply.first_draft_unsupported.length} unsupported number(s)`}
              </p>
            </div>
          );
        })}
        {busy && <p className="msg pending">Planning and checking numbers… (about 10 to 20 s)</p>}
      </div>
      <form className="composer" onSubmit={onSubmit}>
        <label htmlFor="chat-input" className="sr-only">
          Message
        </label>
        <input
          id="chat-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about a trip in Boston"
          disabled={!available}
        />
        <button type="submit" disabled={!available || busy || !draft.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
