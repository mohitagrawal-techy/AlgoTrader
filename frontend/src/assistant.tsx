import { Alert, Button } from "./components/ui";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { api, type ChatMessage } from "./api";

export interface AiTarget {
  component: string;
  title: string;
  params?: Record<string, string>;
}

interface AssistantApi {
  followUp: (title: string, explanation: string) => void;
}

const AssistantContext = createContext<AssistantApi>({
  followUp: () => undefined,
});
const STORAGE_KEY = "algoverse-chat";
const NOTICE =
  "AI summary from a local model. It can be wrong, so check figures against the page. Not advice.";
const SUGGESTIONS = [
  "How is the market doing right now?",
  "Which sectors are strongest over the past month?",
  "What is India VIX telling us?",
  "Where is the market weakest today?",
];

/** Shows model text safely as plain text, with simple bullets. */
function AiText({ text }: { text: string }) {
  const lines = text.replaceAll("**", "").split("\n");
  const blocks: ReactNode[] = [];
  let items: string[] = [];
  const flush = () => {
    if (items.length) {
      blocks.push(
        <ul key={`list-${blocks.length}`}>
          {items.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>,
      );
      items = [];
    }
  };
  for (const raw of lines) {
    const line = raw.trim().replace(/^#+\s*/, "");
    const bullet = line.match(/^(?:[-*•]|\d+\.)\s+(.*)$/);
    if (bullet) {
      items.push(bullet[1]);
    } else {
      flush();
      if (line) blocks.push(<p key={`text-${blocks.length}`}>{line}</p>);
    }
  }
  flush();
  return <div className="ai-text">{blocks}</div>;
}

export function AiButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      className={`ai-button ${active ? "active" : ""}`}
      onClick={onClick}
      aria-expanded={active}
      aria-label={`Explain ${label} with AI`}
      title="Explain this with AI"
    >
      <span aria-hidden>✦</span> AI
    </button>
  );
}

export function AiExplain({
  target,
  onClose,
}: {
  target: AiTarget;
  onClose: () => void;
}) {
  const { followUp } = useContext(AssistantContext);
  const [text, setText] = useState("");
  const [model, setModel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setText("");
    setError(null);
    setBusy(true);
    api
      .explain(
        target,
        setModel,
        (piece) => setText((current) => current + piece),
        controller.signal,
      )
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(
            reason instanceof Error
              ? reason.message
              : "The explanation failed.",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [target, attempt]);

  return (
    <div className="ai-explain" aria-live="polite">
      {error ? (
        <Alert tone="critical">{error}</Alert>
      ) : text ? (
        <AiText text={text} />
      ) : (
        <p className="ai-waiting">Reading the data…</p>
      )}
      <footer>
        <span>
          {NOTICE}
          {model ? ` ${model}.` : ""}
        </span>
        <span className="ai-actions">
          {!busy && !error && (
            <button type="button" onClick={() => followUp(target.title, text)}>
              Ask a follow-up
            </button>
          )}
          {!busy && (
            <button
              type="button"
              onClick={() => setAttempt((value) => value + 1)}
            >
              Regenerate
            </button>
          )}
          <button type="button" onClick={onClose}>
            Close
          </button>
        </span>
      </footer>
    </div>
  );
}

function readSaved(): ChatMessage[] {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

function ChatDrawer({
  messages,
  setMessages,
  focusSignal,
  onClose,
}: {
  messages: ChatMessage[];
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  focusSignal: number;
  onClose: () => void;
}) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [model, setModel] = useState("");
  const controller = useRef<AbortController | null>(null);
  const log = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    field.current?.focus();
  }, [focusSignal]);

  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [messages]);

  useEffect(() => () => controller.current?.abort(), []);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || busy) return;
    const history: ChatMessage[] = [
      ...messages,
      { role: "user", content: question },
    ];
    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");
    setError(null);
    setBusy(true);
    const active = new AbortController();
    controller.current = active;
    try {
      await api.chat(
        history,
        setModel,
        (piece) =>
          setMessages((current) => {
            const next = current.slice();
            const last = next[next.length - 1];
            next[next.length - 1] = { ...last, content: last.content + piece };
            return next;
          }),
        active.signal,
      );
    } catch (reason) {
      if (!active.signal.aborted) {
        setError(
          reason instanceof Error ? reason.message : "The assistant failed.",
        );
        setMessages(history);
      }
    } finally {
      setBusy(false);
      setMessages((current) => {
        const last = current[current.length - 1];
        return last?.role === "assistant" && !last.content
          ? current.slice(0, -1)
          : current;
      });
    }
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void send(input);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(input);
    }
  };

  return (
    <aside className="chat-drawer" aria-label="Market assistant">
      <header>
        <div>
          <strong>Market assistant</strong>
          <small>
            {model || "Local AI model"}, using this page's live figures
          </small>
        </div>
        <span className="ai-actions">
          {messages.length > 0 && !busy && (
            <button type="button" onClick={() => setMessages([])}>
              Clear
            </button>
          )}
          <button type="button" onClick={onClose} aria-label="Close assistant">
            ✕
          </button>
        </span>
      </header>
      <div className="chat-log" ref={log} aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-start">
            <p>
              Ask about indices, sectors, volatility, breadth or options
              positioning.
            </p>
            {SUGGESTIONS.map((suggestion) => (
              <button
                type="button"
                key={suggestion}
                onClick={() => void send(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        )}
        {messages.map((message, index) => (
          <div className={`chat-message ${message.role}`} key={index}>
            {message.role === "assistant" ? (
              message.content ? (
                <AiText text={message.content} />
              ) : (
                <p className="ai-waiting">Reading the market data…</p>
              )
            ) : (
              <p>{message.content}</p>
            )}
          </div>
        ))}
        {error && <Alert tone="critical">{error}</Alert>}
      </div>
      <form onSubmit={onSubmit}>
        <textarea
          ref={field}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about the market"
          rows={2}
          maxLength={2000}
          aria-label="Your question"
        />
        {busy ? (
          <Button onClick={() => controller.current?.abort()}>Stop</Button>
        ) : (
          <Button variant="primary" type="submit" disabled={!input.trim()}>
            Send
          </Button>
        )}
      </form>
      <footer>{NOTICE}</footer>
    </aside>
  );
}

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>(readSaved);
  const [focusSignal, setFocusSignal] = useState(0);

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // The conversation still works for this page view when storage is blocked.
    }
  }, [messages]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const followUp = useCallback((title: string, explanation: string) => {
    setMessages((current) => [
      ...current,
      { role: "assistant", content: `About ${title}:\n${explanation}` },
    ]);
    setOpen(true);
    setFocusSignal((value) => value + 1);
  }, []);

  const value = useMemo(() => ({ followUp }), [followUp]);
  return (
    <AssistantContext.Provider value={value}>
      {children}
      {open ? (
        <ChatDrawer
          messages={messages}
          setMessages={setMessages}
          focusSignal={focusSignal}
          onClose={() => setOpen(false)}
        />
      ) : (
        <button
          type="button"
          className="chat-launcher"
          aria-label="Ask AI"
          onClick={() => setOpen(true)}
        >
          <span aria-hidden>✦</span>
          <span className="chat-launcher-text">Ask AI</span>
        </button>
      )}
    </AssistantContext.Provider>
  );
}
