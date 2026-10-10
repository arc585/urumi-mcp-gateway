import { useEffect, useState } from 'react';

export function PageHeading({ eyebrow, title, description, action }) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function Panel({ title, description, aside, children, className = '' }) {
  return (
    <div className={`panel ${className}`}>
      {(title || aside) && (
        <div className="panel-heading">
          <div>
            {title && <h2>{title}</h2>}
            {description && <p>{description}</p>}
          </div>
          {aside}
        </div>
      )}
      {children}
    </div>
  );
}

export function StatusBadge({ status }) {
  return (
    <span className={`status-badge ${String(status).toLowerCase()}`}>
      <i />
      {status}
    </span>
  );
}

export function Blank({ text }) {
  return (
    <div className="blank-state">
      <span>◌</span>
      <p>{text}</p>
    </div>
  );
}

export function CopyButton({ value, label = 'Copy' }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return undefined;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);
  return (
    <button
      type="button"
      className="button button-light button-small"
      onClick={() => navigator.clipboard.writeText(value).then(() => setCopied(true))}
    >
      {copied ? 'Copied' : label}
    </button>
  );
}

// A newly created key is shown exactly once; the gateway stores only its hash.
export function KeyReveal({ apiKey, owner, onClose }) {
  if (!apiKey) return null;
  return (
    <div className="key-reveal" role="status">
      <div>
        <strong>Copy {owner ? `${owner}'s` : 'this'} API key now</strong>
        <span>It is shown only once. Only a hash is stored, so it cannot be recovered later.</span>
      </div>
      <code>{apiKey}</code>
      <CopyButton value={apiKey} label="Copy key" />
      <button type="button" className="icon-button" aria-label="Dismiss" onClick={onClose}>
        ×
      </button>
    </div>
  );
}

export function Modal({ title, onClose, children }) {
  useEffect(() => {
    const onKey = (event) => event.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-heading">
          <h2>{title}</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function formatTime(value) {
  return value ? new Date(value).toLocaleString() : '—';
}

export function formatAgo(value) {
  if (!value) return 'never';
  const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(value).toLocaleDateString();
}

// Re-run `callback` every `ms` while `enabled`, e.g. to keep upstream health live.
export function useInterval(callback, ms, enabled = true) {
  useEffect(() => {
    if (!enabled) return undefined;
    const timer = setInterval(callback, ms);
    return () => clearInterval(timer);
  }, [callback, ms, enabled]);
}
