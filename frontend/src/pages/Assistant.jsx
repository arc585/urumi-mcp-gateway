import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../components.jsx';

const SUGGESTIONS = [
  'Create a product called "Blue Ceramic Mug" priced at 299.',
  "Show today's orders.",
  "For today's newest order, estimate shipping to the customer's city.",
  'What is the store returns policy?',
];
const HISTORY_TURNS = 10; // recent turns sent with each message, so follow-ups have context

function ToolCall({ call }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`tool-call ${call.ok ? '' : 'call-error'}`}>
      <button type="button" className="tool-call-summary" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span>{call.ok ? '⚙' : '!'}</span>
        <code>{call.name}</code>
        <small>{call.duration_ms} ms</small>
        <i>{open ? '▾' : '▸'}</i>
      </button>
      {open && (
        <div className="tool-call-detail">
          <div>
            <strong>Server</strong> {call.upstream} · <strong>Tool</strong> {call.tool}
          </div>
          <pre>{JSON.stringify(call.arguments, null, 2)}</pre>
          {call.error && <div className="server-error">{call.error}</div>}
        </div>
      )}
    </div>
  );
}

export default function Assistant({ me, call }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, busy]);

  async function send(text) {
    const message = text.trim();
    if (!message || busy) return;
    const history = [...messages.filter((m) => !m.failed), { role: 'user', text: message }];
    setMessages((items) => [...items, { role: 'user', text: message }]);
    setInput('');
    setBusy(true);
    try {
      const result = await call('/chat', {
        method: 'POST',
        body: { messages: history.slice(-HISTORY_TURNS).map((m) => ({ role: m.role, content: m.text })) },
      });
      setMessages((items) => [...items, { role: 'assistant', text: result.reply, calls: result.tool_calls, model: result.model }]);
    } catch (err) {
      setMessages((items) => [...items, { role: 'assistant', text: err.message, calls: [], failed: true }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="TEAM ASSISTANT"
        title="Ask your store"
        description={`Uses the same gateway tools as external MCP clients. Every tool call runs and is audited as ${me.email}.`}
        action={
          messages.length > 0 && (
            <button type="button" className="button button-light" onClick={() => setMessages([])} disabled={busy}>
              New conversation
            </button>
          )
        }
      />
      <div className="chat-panel">
        <div className="chat-intro">
          <div className="assistant-mark">✳</div>
          <div>
            <strong>What can I help you with?</strong>
            <span>Products, orders, store policies, and shipping estimates — through the gateway’s tools.</span>
          </div>
        </div>
        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="suggestions">
              {SUGGESTIONS.map((text) => (
                <button type="button" key={text} onClick={() => send(text)}>
                  {text}
                </button>
              ))}
            </div>
          )}
          {messages.map((message, index) => (
            <div key={index} className={`chat-message ${message.role}`}>
              <div className="message-avatar">{message.role === 'user' ? me.display_name.slice(0, 1).toUpperCase() : '✳'}</div>
              <div className="message-body">
                <div className="message-author">
                  {message.role === 'user' ? 'You' : 'Urumi assistant'}
                  {message.model && <span className="subtle-count"> · {message.model}</span>}
                </div>
                {message.calls?.length > 0 && (
                  <div className="tool-calls">
                    {message.calls.map((item, i) => (
                      <ToolCall key={i} call={item} />
                    ))}
                  </div>
                )}
                <p className={message.failed ? 'server-error' : ''}>{message.text}</p>
              </div>
            </div>
          ))}
          {busy && (
            <div className="chat-message assistant">
              <div className="message-avatar">✳</div>
              <div className="message-body">
                <div className="message-author">Urumi assistant</div>
                <p className="thinking">Thinking and calling tools…</p>
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>
        <form
          className="chat-form"
          onSubmit={(event) => {
            event.preventDefault();
            send(input);
          }}
        >
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Ask about today's orders, or create a product…"
            rows={2}
            maxLength={4000}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                send(input);
              }
            }}
          />
          <div>
            <small>Enter to send · Shift + Enter for a new line · tool calls are listed above each answer</small>
            <button className="button button-primary" disabled={busy || !input.trim()}>
              Send <span>↑</span>
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
